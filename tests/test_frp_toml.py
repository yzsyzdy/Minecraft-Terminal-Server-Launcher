"""frpc.toml 的读写。

这个极简 TOML 实现是「读一次、改几个键、原样写回」。写坏的代价很大：用户
手写的 [[visitors]] 这类段落会被写成字符串数组，frpc 之后连整个配置都读不
进去。所以这里用 tomllib 当裁判，确认写回去的是合法 TOML 且内容没丢。
"""

import os
import shutil
import tempfile
import tomllib
import unittest

import core.frp as frpops
import frp.tunnel_manager as TM


class RoundTripTest(unittest.TestCase):
    def _round_trip(self, text):
        parsed = TM._parse_toml(text)
        return parsed, TM._build_toml(parsed)

    def _check(self, text):
        parsed, out = self._round_trip(text)
        back = tomllib.loads(out)          # 不合法就直接抛，测试失败
        self.assertEqual(back, parsed)
        return parsed, back

    def test_scalars_only(self):
        self._check('serverAddr = "1.1.1.1"\nserverPort = 7000\n')

    def test_proxies(self):
        self._check(
            'serverAddr = "1.2.3.4"\n'
            'serverPort = 7000\n'
            '\n'
            '[[proxies]]\n'
            'name = "p1"\n'
            'type = "tcp"\n'
            'localPort = 25565\n'
            'remotePort = 25565\n'
        )

    def test_other_array_of_tables_is_preserved(self):
        """[[visitors]] 必须还是数组表，不能变成字符串。"""
        parsed, back = self._check(
            'serverAddr = "1.2.3.4"\n'
            '\n'
            '[[proxies]]\n'
            'name = "p1"\n'
            'type = "stcp"\n'
            'secretKey = "k"\n'
            '\n'
            '[[visitors]]\n'
            'name = "v1"\n'
            'type = "stcp"\n'
            'serverName = "p1"\n'
            'bindPort = 25565\n'
        )
        self.assertIsInstance(back["visitors"], list)
        self.assertEqual(back["visitors"][0]["name"], "v1")
        self.assertEqual(back["visitors"][0]["bindPort"], 25565)
        self.assertEqual(parsed, back)

    def test_two_proxies_plus_nested_table(self):
        self._check(
            'serverAddr = "1.2.3.4"\n'
            '\n'
            '[transport]\n'
            'tcpMux = true\n'
            'heartbeatInterval = 30\n'
            '\n'
            '[[proxies]]\n'
            'name = "a"\n'
            'type = "tcp"\n'
            'localPort = 1\n'
            'remotePort = 2\n'
            '\n'
            '[[proxies]]\n'
            'name = "b"\n'
            'type = "udp"\n'
            'localPort = 3\n'
            'remotePort = 4\n'
        )

    def test_quotes_and_backslashes_are_escaped(self):
        parsed, out = self._round_trip('auth.token = "a\\\\b\\"c"\n')
        self.assertEqual(tomllib.loads(out)["auth"]["token"], 'a\\b"c')

    def test_arrays_stay_arrays(self):
        parsed, out = self._round_trip(
            'customDomains = ["a.example.com", "b.example.com"]\n')
        self.assertEqual(tomllib.loads(out)["customDomains"],
                         ["a.example.com", "b.example.com"])

    def test_booleans_and_ints(self):
        parsed, out = self._round_trip("tcpMux = true\nport = 7000\n")
        loaded = tomllib.loads(out)
        self.assertIs(loaded["tcpMux"], True)
        self.assertEqual(loaded["port"], 7000)


class LogicalLinesTest(unittest.TestCase):
    """行尾注释与多行数组。

    frpc.toml 是给人手写的，官方文档的示例两样都有。旧实现按物理行处理：
    `serverPort = 7000  # 端口` 被当成字符串存回去，frpc 解不进 int 字段
    直接退出；多行数组则只剩一个 "["，整个数组消失。
    """

    # -- 剥注释 ------------------------------------------------------------

    def test_trailing_comment_is_stripped(self):
        self.assertEqual(
            TM._strip_toml_comment("serverPort = 7000  # the frps port").strip(),
            "serverPort = 7000")

    def test_hash_inside_quotes_is_not_a_comment(self):
        line = 'auth.token = "a#b#c"'
        self.assertEqual(TM._strip_toml_comment(line), line)

    def test_escaped_quote_does_not_end_the_string(self):
        """`\\"` 之后仍在字符串里，后面的 # 不是注释。"""
        line = 'x = "a\\"#b"'
        self.assertEqual(TM._strip_toml_comment(line), line)

    def test_comment_only_line_becomes_empty(self):
        self.assertEqual(TM._strip_toml_comment("  # nothing here").strip(), "")

    # -- 合并逻辑行 --------------------------------------------------------

    def test_multiline_array_becomes_one_logical_line(self):
        text = ('customDomains = [\n'
                '  "a.example.com",\n'
                '  "b.example.com",\n'
                ']\n')
        self.assertEqual(TM._toml_logical_lines(text),
                         ['customDomains = [ "a.example.com", "b.example.com", ]'])

    def test_blank_and_comment_lines_are_dropped(self):
        self.assertEqual(TM._toml_logical_lines("# c\n\n   \n"), [])

    def test_scalars_stay_one_line_each(self):
        self.assertEqual(TM._toml_logical_lines('a = 1\nb = "x"\n'),
                         ['a = 1', 'b = "x"'])

    def test_bracket_state_ignores_brackets_inside_strings(self):
        self.assertEqual(TM._bracket_state('x = "["'), (0, ""))
        self.assertEqual(TM._bracket_state("x = ["), (1, ""))
        self.assertEqual(TM._bracket_state('x = "abc'), (0, '"'))

    # -- 解析结果 ----------------------------------------------------------

    def test_parse_reads_int_through_trailing_comment(self):
        cfg = TM._parse_toml("serverPort = 7000  # the frps port\n")
        self.assertEqual(cfg["serverPort"], 7000)
        self.assertIsInstance(cfg["serverPort"], int)

    def test_parse_keeps_table_header_with_comment(self):
        cfg = TM._parse_toml('[log]  # logging section\n'
                             'to = "console"\n'
                             'level = "info"\n')
        self.assertEqual(cfg["log"], {"to": "console", "level": "info"})

    def test_parse_keeps_multiline_array(self):
        cfg = TM._parse_toml('customDomains = [\n'
                             '  "a.example.com",\n'
                             '  "b.example.com",\n'
                             ']\n')
        self.assertEqual(cfg["customDomains"],
                         ["a.example.com", "b.example.com"])

    def test_parse_keeps_array_of_tables_after_a_comment(self):
        cfg = TM._parse_toml('[[visitors]]  # 访问者\n'
                             'name = "v1"\n'
                             'bindPort = 25565\n')
        self.assertEqual(cfg["visitors"], [{"name": "v1", "bindPort": 25565}])

    def test_escaped_quote_inside_a_single_line_array(self):
        """单行数组里的 `\\"` 不能被当成字符串结束。"""
        cfg = TM._parse_toml('customDomains = ["a\\"b.example.com", "c.example.com"]\n')
        self.assertEqual(cfg["customDomains"],
                         ['a"b.example.com', "c.example.com"])

    def test_comment_after_a_quoted_value_is_stripped(self):
        cfg = TM._parse_toml('auth.token = "tk"  # 别外传\n')
        self.assertEqual(cfg["auth"]["token"], "tk")

    def test_documented_style_file_round_trips(self):
        """整份「官方文档风格」的文件读进来、写回去，tomllib 要认，内容不丢。"""
        text = ('serverAddr = "1.2.3.4"  # 主控地址\n'
                'serverPort = 7000  # 端口\n'
                '\n'
                'auth.token = "a#b"  # 值里有 #\n'
                '\n'
                'customDomains = [\n'
                '  "a.example.com",\n'
                '  "b.example.com",\n'
                ']\n')
        parsed = TM._parse_toml(text)
        out = TM._build_toml(parsed)
        self.assertEqual(tomllib.loads(out), parsed)
        self.assertEqual(parsed["serverAddr"], "1.2.3.4")
        self.assertEqual(parsed["serverPort"], 7000)
        self.assertEqual(parsed["auth"]["token"], "a#b")


class FileApiTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="mstl-test-frp-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_write_settings_then_read_back(self):
        saved = frpops.write_settings(self.dir, server_addr="5.6.7.8",
                                      server_port=7000, token="tk")
        self.assertEqual(saved.get("serverAddr"), "5.6.7.8")
        path = TM.get_frpc_toml_path(self.dir)
        with open(path, "rb") as f:
            cfg = tomllib.load(f)
        self.assertEqual(cfg["serverAddr"], "5.6.7.8")
        self.assertEqual(cfg["serverPort"], 7000)

    def test_load_of_existing_file_keeps_extra_sections(self):
        path = TM.get_frpc_toml_path(self.dir)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write('serverAddr = "1.1.1.1"\n'
                    '\n'
                    '[[visitors]]\n'
                    'name = "v1"\n'
                    'bindPort = 25565\n')
        cfg = frpops.read_settings(self.dir)
        self.assertIsInstance(cfg, dict)

        # 再保存一次，visitors 仍在
        frpops.write_settings(self.dir, server_addr="2.2.2.2", server_port=7000,
                              token="")
        with open(path, "rb") as f:
            after = tomllib.load(f)
        self.assertEqual(after["serverAddr"], "2.2.2.2")
        self.assertEqual(after["visitors"][0]["name"], "v1")


if __name__ == "__main__":
    unittest.main()
