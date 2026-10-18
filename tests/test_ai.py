"""AI 助手的工具层、确认流程与护栏。

这些用例关心的不是「模型会不会正确调用工具」（那是上游的事），而是我们家这
一侧的三条硬约束：

1. 只读工具自动跑，写工具必须先挂起等人点；
2. 没被确认过的写操作，绝不能落到磁盘或进程上；
3. 工具内部的报错要变成给模型看的一段文字，不能把整轮对话炸掉。
"""

import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

import i18n
from config import load_config, save_config
from core.errors import CoreError
from server_manager import list_servers
from web import ai


def make_instance(root: str, name: str, **overrides) -> str:
    """照 server_manager 认识的样子摆一个实例目录出来。"""
    path = os.path.join(root, name)
    os.makedirs(path, exist_ok=True)
    data = {
        "name": name,
        "kind": "minecraft",
        "mc_version": "1.20.1",
        "jar": "server.jar",
        "min_mem": "2G",
        "max_mem": "4G",
        "extra_jvm_args": ["-XX:+UseG1GC"],
        "extra_server_args": ["nogui"],
        "openfrp": {"token": "super-secret-token", "proxy_id": 7, "auto_start": False},
        "launch": {"mode": "exec", "program": "", "args": ["a"],
                   "command": "", "workdir": ""},
    }
    data.update(overrides)
    with open(os.path.join(path, ".server.json"), "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)
    return path


def write_ai_config(project_dir: str, **overrides) -> dict:
    cfg = load_config(project_dir)
    section = dict(cfg.get("ai") or {})
    section.update({
        "enabled": True,
        "base_url": "https://example.test/v1",
        "model": "demo-model",
        "api_key": "sk-test",
    })
    section.update(overrides)
    cfg["ai"] = section
    save_config(cfg, project_dir)
    return cfg


def say(text: str) -> dict:
    return {"role": "assistant", "content": text}


def tool_call(name: str, args: dict, call_id: str = "c1") -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
        }],
    }


class FakeManager:
    """只回答助手会用到的几个问题，不真的去拉进程。"""

    def __init__(self) -> None:
        self.started: list[str] = []
        self.stopped: list[str] = []
        self.sent: list[tuple[str, str]] = []

    def find(self, servers_dir: str, name: str):
        for entry in list_servers(servers_dir):
            if str(entry.get("name") or "") == name:
                return entry, os.path.join(servers_dir, name)
        raise CoreError("web.error.unknown_instance", name=name)

    def get(self, name: str):
        return None

    def state_of(self, name: str):
        return {"state": "stopped", "exit_code": None}

    def start_by_name(self, servers_dir: str, name: str):
        self.started.append(name)
        runtime = mock.Mock()
        runtime.snapshot.return_value = {"pid": 4242}
        return runtime

    def stop(self, name: str) -> None:
        self.stopped.append(name)

    def send(self, name: str, command: str) -> None:
        self.sent.append((name, command))


class ScriptedAssistant(ai.AiAssistant):
    """模型换成一段写死的剧本。"""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.script: list[dict] = []
        self.model_calls = 0

    def _call_model(self, settings: dict) -> dict:
        self.model_calls += 1
        if not self.script:
            raise AssertionError("剧本已经用完了，模型不该再被调用")
        return self.script.pop(0)


class AiTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="mstl-ai-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.project = os.path.join(self.root, "project")
        self.servers = os.path.join(self.project, "servers")
        os.makedirs(self.servers)
        self.instance = make_instance(self.servers, "survival")
        self.manager = FakeManager()

    def assistant(self, *, configured: bool = True) -> ScriptedAssistant:
        if configured:
            write_ai_config(self.project)
        helper = ScriptedAssistant(self.project, self.servers, self.manager)
        return helper

    def audit_lines(self) -> list:
        path = os.path.join(self.project, ".mstl-ai-audit.log")
        if not os.path.isfile(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]


class AiSettingsTest(AiTestBase):
    def test_disabled_and_unconfigured_by_default(self):
        state = ai.ai_settings(load_config(self.project))
        self.assertFalse(state["enabled"])
        self.assertEqual(state["base_url"], "")
        helper = self.assistant(configured=False)
        snapshot = helper.state()
        self.assertFalse(snapshot["enabled"])
        self.assertFalse(snapshot["configured"])

    def test_environment_key_wins_over_config(self):
        write_ai_config(self.project, api_key="")
        with mock.patch.dict(os.environ, {"MSTL_AI_API_KEY": "sk-env"}):
            settings = ai.ai_settings(load_config(self.project))
        self.assertEqual(settings["api_key"], "sk-env")
        self.assertTrue(settings["key_from_env"])

    def test_base_url_loses_its_trailing_slash(self):
        write_ai_config(self.project, base_url="https://example.test/v1///")
        settings = ai.ai_settings(load_config(self.project))
        self.assertEqual(settings["base_url"], "https://example.test/v1")

    def test_configured_needs_url_model_and_key(self):
        write_ai_config(self.project, api_key="")
        helper = self.assistant(configured=False)
        with mock.patch.dict(os.environ, {"MSTL_AI_API_KEY": ""}):
            self.assertFalse(helper.state()["configured"])

    def test_as_bool_reads_strings_literally(self):
        for value in ("", "0", "false", "no", "off", "FALSE"):
            self.assertFalse(ai._as_bool(value), value)
        for value in ("1", "true", "yes", "on", True):
            self.assertTrue(ai._as_bool(value), value)

    def test_api_key_never_leaves_state(self):
        helper = self.assistant()
        payload = json.dumps(helper.state(), ensure_ascii=False)
        self.assertNotIn("sk-test", payload)
        self.assertTrue(helper.state()["key_set"])


class ChatGateTest(AiTestBase):
    def test_empty_text_is_rejected(self):
        helper = self.assistant()
        with self.assertRaises(CoreError) as ctx:
            helper.chat("   ")
        self.assertEqual(ctx.exception.key, "web.ai.empty_text")

    def test_disabled_assistant_refuses(self):
        helper = self.assistant(configured=False)
        with self.assertRaises(CoreError) as ctx:
            helper.chat("hi")
        self.assertEqual(ctx.exception.key, "web.ai.disabled")

    def test_missing_base_url(self):
        helper = self.assistant(configured=False)
        write_ai_config(self.project, base_url="")
        with self.assertRaises(CoreError) as ctx:
            helper.chat("hi")
        self.assertEqual(ctx.exception.key, "web.ai.missing_base_url")

    def test_missing_model(self):
        helper = self.assistant(configured=False)
        write_ai_config(self.project, model="")
        with self.assertRaises(CoreError) as ctx:
            helper.chat("hi")
        self.assertEqual(ctx.exception.key, "web.ai.missing_model")

    def test_text_too_long(self):
        helper = self.assistant()
        with self.assertRaises(CoreError) as ctx:
            helper.chat("x" * (ai.MAX_TEXT_CHARS + 1))
        self.assertEqual(ctx.exception.key, "web.ai.text_too_long")


class ReadToolTest(AiTestBase):
    def test_read_tool_runs_without_confirmation(self):
        helper = self.assistant()
        helper.script = [tool_call("list_instances", {}), say("看过了")]
        result = helper.chat("有哪些服务器")
        self.assertEqual(result["reply"], "看过了")
        self.assertEqual(result["pending"], [])
        self.assertEqual(helper.model_calls, 2)
        # 工具结果必须作为 tool 消息回到历史里，否则模型下一轮看不到
        tool_messages = [m for m in helper.messages if m.get("role") == "tool"]
        self.assertEqual(len(tool_messages), 1)
        self.assertIn("<tool_result>", tool_messages[0]["content"])
        self.assertIn("survival", tool_messages[0]["content"])
        self.assertEqual(self.audit_lines()[-1]["decision"], "auto")

    def test_tunnel_token_is_redacted_before_reaching_the_model(self):
        helper = self.assistant()
        helper.script = [tool_call("get_instance", {"name": "survival"}), say("ok")]
        helper.chat("看看 survival 的配置")
        payload = "".join(
            str(m.get("content") or "") for m in helper.messages if m.get("role") == "tool")
        self.assertNotIn("super-secret-token", payload)
        self.assertIn("***", payload)

    def test_read_console_without_runtime_says_so(self):
        helper = self.assistant()
        helper.script = [tool_call("read_console", {"name": "survival"}), say("ok")]
        helper.chat("日志里有什么")
        tool_text = [m["content"] for m in helper.messages if m.get("role") == "tool"][0]
        self.assertIn(i18n.t("web.ai.no_console"), tool_text)


class ConfirmFlowTest(AiTestBase):
    def test_write_tool_waits_for_a_human(self):
        helper = self.assistant()
        helper.script = [tool_call("start_instance", {"name": "survival"})]
        result = helper.chat("把 survival 开起来")
        self.assertEqual(self.manager.started, [])
        self.assertEqual(len(result["pending"]), 1)
        pending = result["pending"][0]
        self.assertEqual(pending["tool"], "start_instance")
        self.assertEqual(pending["summary"],
                         i18n.t("web.ai.confirm.start", name="survival"))
        self.assertGreater(pending["expires_in"], 0)

    def test_approval_runs_the_tool(self):
        helper = self.assistant()
        helper.script = [tool_call("start_instance", {"name": "survival"}),
                         say("已经启动")]
        pending = helper.chat("开服")["pending"][0]
        result = helper.confirm(pending["id"], True)
        self.assertEqual(self.manager.started, ["survival"])
        self.assertEqual(result["reply"], "已经启动")
        self.assertEqual(helper.pending, {})
        self.assertEqual(self.audit_lines()[-1]["decision"], "approved")

    def test_rejection_leaves_everything_alone(self):
        helper = self.assistant()
        helper.script = [tool_call("start_instance", {"name": "survival"}),
                         say("好，不动")]
        pending = helper.chat("开服")["pending"][0]
        helper.confirm(pending["id"], False)
        self.assertEqual(self.manager.started, [])
        self.assertEqual(self.audit_lines()[-1]["decision"], "denied")

    def test_expired_confirmation_does_not_run(self):
        helper = self.assistant()
        helper.script = [tool_call("start_instance", {"name": "survival"}),
                         say("算了")]
        pending = helper.chat("开服")["pending"][0]
        helper.pending[pending["id"]].created -= ai.CONFIRM_TTL + 5
        helper.confirm(pending["id"], True)
        self.assertEqual(self.manager.started, [])
        self.assertEqual(self.audit_lines()[-1]["decision"], "expired")

    def test_unknown_confirmation_is_reported(self):
        helper = self.assistant()
        with self.assertRaises(CoreError) as ctx:
            helper.confirm("nope", True)
        self.assertEqual(ctx.exception.key, "web.ai.no_such_pending")

    def test_abandoned_pending_is_settled_on_the_next_message(self):
        helper = self.assistant()
        helper.script = [tool_call("start_instance", {"name": "survival"}),
                         say("好的")]
        helper.chat("开服")
        helper.chat("算了，先别开")   # 上一轮那条确认必须收尾，否则历史不合法
        self.assertEqual(self.manager.started, [])
        tool_messages = [m for m in helper.messages if m.get("role") == "tool"]
        self.assertTrue(any(i18n.t("web.ai.result.abandoned") in m["content"]
                            for m in tool_messages))


class GuardTest(AiTestBase):
    def test_server_json_cannot_be_rewritten(self):
        helper = self.assistant()
        helper.script = [tool_call("write_file", {
            "name": "survival", "path": ".server.json", "content": "{}"}), say("被拦了")]
        result = helper.chat("把 .server.json 清空")
        self.assertEqual(result["pending"], [])
        tool_text = [m["content"] for m in helper.messages if m.get("role") == "tool"][-1]
        self.assertIn(i18n.t("web.ai.protected_path", path=".server.json"), tool_text)

    def test_path_escape_is_refused(self):
        helper = self.assistant()
        helper.script = [tool_call("write_file", {
            "name": "survival", "path": "../../escape.txt", "content": "x"}), say("不行")]
        result = helper.chat("往上级目录写个文件")
        self.assertEqual(result["pending"], [])
        self.assertFalse(os.path.exists(os.path.join(self.root, "escape.txt")))

    def test_unknown_tool_becomes_a_message_for_the_model(self):
        helper = self.assistant()
        helper.script = [tool_call("rm_rf", {"path": "/"}), say("没有这个工具")]
        result = helper.chat("删库")
        self.assertEqual(result["pending"], [])
        tool_text = [m["content"] for m in helper.messages if m.get("role") == "tool"][-1]
        self.assertIn(i18n.t("web.ai.unknown_tool", name="rm_rf"), tool_text)

    def test_unknown_instance_is_reported_before_confirmation(self):
        helper = self.assistant()
        helper.script = [tool_call("start_instance", {"name": "nope"}), say("没这个实例")]
        result = helper.chat("把 nope 开起来")
        self.assertEqual(result["pending"], [])
        self.assertEqual(self.manager.started, [])

    def test_command_with_newline_is_refused(self):
        helper = self.assistant()
        helper.script = [tool_call("send_command", {
            "name": "survival", "command": "say hi\nop someone"}), say("不行")]
        result = helper.chat("发两条命令")
        self.assertEqual(result["pending"], [])

    def test_write_file_pending_then_approved(self):
        helper = self.assistant()
        helper.script = [tool_call("write_file", {
            "name": "survival", "path": "motd.txt", "content": "hello"}), say("写好了")]
        pending = helper.chat("改 motd")["pending"][0]
        target = os.path.join(self.instance, "motd.txt")
        self.assertFalse(os.path.exists(target))     # 没确认之前不能落盘
        helper.confirm(pending["id"], True)
        with open(target, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "hello")


class BudgetTest(AiTestBase):
    def test_tool_rounds_are_capped(self):
        helper = self.assistant()
        helper.script = [tool_call("list_instances", {}, call_id=f"c{i}")
                         for i in range(ai.MAX_TOOL_ROUNDS)]
        result = helper.chat("一直调工具")
        self.assertEqual(helper.model_calls, ai.MAX_TOOL_ROUNDS)
        self.assertEqual(result["reply"], i18n.t("web.ai.rounds_exceeded",
                                                 max=ai.MAX_TOOL_ROUNDS))

    def test_concurrent_chat_is_rejected(self):
        helper = self.assistant()
        helper._chat_lock.acquire()
        try:
            with self.assertRaises(CoreError) as ctx:
                helper.chat("hi")
            self.assertEqual(ctx.exception.key, "web.ai.busy")
        finally:
            helper._chat_lock.release()


class RedactAndTrimTest(unittest.TestCase):
    def test_redact_walks_nested_structures(self):
        raw = {
            "token": "abc",
            "nested": {"api_key": "k", "list": [{"secret": "s"}, {"keep": 1}]},
            "empty_token": "",
        }
        out = ai._redact(raw)
        self.assertEqual(out["token"], "***")
        self.assertEqual(out["nested"]["api_key"], "***")
        self.assertEqual(out["nested"]["list"][0]["secret"], "***")
        self.assertEqual(out["nested"]["list"][1]["keep"], 1)
        self.assertEqual(out["empty_token"], "")

    def test_trim_history_never_starts_with_a_tool_message(self):
        messages = [{"role": "user", "content": "u0"}]
        for index in range(ai.MAX_HISTORY_MESSAGES):
            messages.append({"role": "assistant", "content": f"a{index}"})
            messages.append({"role": "tool", "tool_call_id": f"c{index}", "content": "r"})
        ai._trim_history(messages)
        self.assertLessEqual(len(messages), ai.MAX_HISTORY_MESSAGES)
        self.assertNotEqual(messages[0]["role"], "tool")

    def test_trim_history_leaves_short_histories_alone(self):
        messages = [{"role": "user", "content": "u"}]
        ai._trim_history(messages)
        self.assertEqual(messages, [{"role": "user", "content": "u"}])


class AuditTest(AiTestBase):
    def test_audit_records_redacted_args(self):
        helper = self.assistant()
        helper._audit("update_config", {"name": "survival",
                                        "changes": {"openfrp": {"token": "tok"}}},
                      "approved")
        entry = self.audit_lines()[0]
        self.assertEqual(entry["tool"], "update_config")
        self.assertEqual(entry["decision"], "approved")
        self.assertEqual(entry["args"]["changes"]["openfrp"]["token"], "***")

    def test_audit_failure_does_not_break_the_operation(self):
        helper = self.assistant()
        with mock.patch("builtins.open", side_effect=OSError("disk full")):
            helper._audit("list_instances", {}, "auto")   # 不该抛


class ToolCatalogTest(unittest.TestCase):
    def test_every_tool_declares_a_known_risk(self):
        for tool in ai.TOOLS:
            self.assertIn(tool["risk"], (ai.RISK_READ, ai.RISK_CONFIRM), tool["name"])
            self.assertTrue(tool["description"])
            self.assertEqual(tool["parameters"]["type"], "object")

    def test_destructive_tools_require_confirmation(self):
        confirm = {tool["name"] for tool in ai.TOOLS if tool["risk"] == ai.RISK_CONFIRM}
        self.assertEqual(confirm, {
            "start_instance", "stop_instance", "restart_instance",
            "send_command", "update_config", "write_file",
        })

    def test_openai_payload_shape(self):
        payload = ai._tools_payload()
        self.assertEqual(len(payload), len(ai.TOOLS))
        for item in payload:
            self.assertEqual(item["type"], "function")
            self.assertIn("parameters", item["function"])


class CleanChangesTest(unittest.TestCase):
    def test_strings_are_coerced_to_real_booleans(self):
        changes = ai.AiAssistant._clean_changes({"auto_restart": "false"})
        self.assertIs(changes["auto_restart"], False)

    def test_restart_max_is_clamped(self):
        self.assertEqual(ai.AiAssistant._clean_changes({"restart_max": 99})["restart_max"], 20)
        self.assertEqual(ai.AiAssistant._clean_changes({"restart_max": -3})["restart_max"], 0)
        self.assertNotIn("restart_max",
                         ai.AiAssistant._clean_changes({"restart_max": "many"}))

    def test_unknown_fields_are_dropped(self):
        self.assertEqual(ai.AiAssistant._clean_changes({"evil": "x"}), {})

    def test_argument_strings_are_split_into_lists(self):
        changes = ai.AiAssistant._clean_changes({"extra_jvm_args": "-Xmx4G -Xms2G"})
        self.assertEqual(changes["extra_jvm_args"], ["-Xmx4G", "-Xms2G"])

    def test_openfrp_only_accepts_objects(self):
        self.assertNotIn("openfrp", ai.AiAssistant._clean_changes({"openfrp": "x"}))


class AiHttpTest(unittest.TestCase):
    """把四条 AI 路由真正跑一遍 HTTP（鉴权、JSON 形状、key 的存与清）。"""

    token = "TESTTOKEN"

    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="mstl-ai-http-")
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, "servers"))
        make_instance(os.path.join(self.root, "servers"), "survival")
        with open(os.path.join(self.root, "config.json"), "w", encoding="utf-8") as handle:
            json.dump({"language": "zh",
                       "web": {"host": "127.0.0.1", "port": 0, "token": self.token}},
                      handle)
        from core.runtime import RuntimeManager
        from web import server as webserver

        self.manager = RuntimeManager(self.root)
        self.servers_dir = os.path.join(self.root, "servers")
        self.port = _free_port()
        self.httpd = webserver.build_server(
            self.root, host="127.0.0.1", port=self.port, token=self.token,
            manager=self.manager, servers_dir=self.servers_dir,
            token_provider=lambda: self.token)
        import threading
        threading.Thread(
            target=lambda: self.httpd.serve_forever(poll_interval=0.05),
            daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def call(self, path: str, body=None, token: str = "") -> tuple[int, dict]:
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        headers = {"X-MSTL-Token": token or self.token}
        payload = None
        if body is not None:
            payload = json.dumps(body)
            headers["Content-Type"] = "application/json"
        conn.request("POST" if body is not None else "GET", path, payload, headers)
        response = conn.getresponse()
        raw = response.read().decode("utf-8", "replace")
        conn.close()
        try:
            return response.status, json.loads(raw)
        except ValueError:
            return response.status, {"raw": raw}

    def test_state_requires_a_token(self):
        status, _payload = self.call("/api/ai", token="wrong")
        self.assertEqual(status, 401)

    def test_state_starts_disabled(self):
        status, payload = self.call("/api/ai")
        self.assertEqual(status, 200)
        self.assertFalse(payload["enabled"])
        self.assertEqual(payload["transcript"], [])

    def test_config_round_trip_keeps_the_key_unless_cleared(self):
        status, payload = self.call("/api/ai/config", {
            "enabled": True, "base_url": "https://example.test/v1",
            "model": "demo", "api_key": "sk-live"})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ai"]["enabled"])
        self.assertTrue(payload["ai"]["configured"])
        self.assertTrue(payload["ai"]["key_set"])

        # 第二次不带 api_key：后端拿不到旧值，绝不能把它当成「清空」
        status, payload = self.call("/api/ai/config", {
            "enabled": True, "base_url": "https://example.test/v1", "model": "demo2"})
        self.assertTrue(payload["ai"]["key_set"])
        self.assertEqual(payload["ai"]["model"], "demo2")

        status, payload = self.call("/api/ai/config", {"clear_key": True})
        self.assertEqual(status, 200)
        self.assertFalse(payload["ai"]["key_set"])

    def test_chat_returns_the_model_reply(self):
        self.call("/api/ai/config", {
            "enabled": True, "base_url": "https://example.test/v1",
            "model": "demo", "api_key": "sk-live"})
        canned = {"choices": [{"message": {"role": "assistant", "content": "服务器都停着。"}}]}
        with mock.patch.object(ai, "_post_json", return_value=canned):
            status, payload = self.call("/api/ai/chat", {"text": "现在什么状态"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["reply"], "服务器都停着。")
        status, state = self.call("/api/ai")
        self.assertEqual([item["role"] for item in state["transcript"]],
                         ["user", "assistant"])

    def test_unknown_confirmation_is_a_400_with_a_message(self):
        self.call("/api/ai/config", {
            "enabled": True, "base_url": "https://example.test/v1",
            "model": "demo", "api_key": "sk-live"})
        status, payload = self.call("/api/ai/confirm", {"id": "nope", "approve": True})
        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["key"], "web.ai.no_such_pending")

    def test_reset_clears_the_transcript(self):
        self.call("/api/ai/config", {
            "enabled": True, "base_url": "https://example.test/v1",
            "model": "demo", "api_key": "sk-live"})
        canned = {"choices": [{"message": {"role": "assistant", "content": "hi"}}]}
        with mock.patch.object(ai, "_post_json", return_value=canned):
            self.call("/api/ai/chat", {"text": "hello"})
        status, _payload = self.call("/api/ai/reset", {})
        self.assertEqual(status, 200)
        _status, state = self.call("/api/ai")
        self.assertEqual(state["transcript"], [])


def _free_port() -> int:
    import socket
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


if __name__ == "__main__":
    unittest.main()
