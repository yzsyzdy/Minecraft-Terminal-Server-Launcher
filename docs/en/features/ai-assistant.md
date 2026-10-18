# AI assistant

The third tab in the web panel. Talk to a model in plain language and it will look up status, search logs, browse files and change settings for you — and it can start, stop and send commands to your servers. **Anything that changes state has to be confirmed by you in the panel first.**

## What it can do

| You say | Tool it calls | Who runs it |
| --- | --- | --- |
| "What's the status right now?" | `list_instances`, `get_instance` | automatic |
| "Any errors in the recent log?" | `read_console` | automatic |
| "What's in the plugins directory?" | `list_files` | automatic |
| "Show me server.properties" | `read_file` | automatic |
| "Start survival for me" | `start_instance` | **after you confirm** |
| "Stop creative" | `stop_instance` | **after you confirm** |
| "Restart survival" | `restart_instance` | **after you confirm** |
| "Tell the console the server opens tonight" | `send_command` | **after you confirm** |
| "Set memory to 6G, turn on auto restart" | `update_config` | **after you confirm** |
| "Change the motd to 'welcome'" | `write_file` | **after you confirm** |

The five read-only tools run immediately and their results go back to the model for the next step. The six that change things are not executed: they turn into a confirmation card, and only your click makes them happen.

## Configuration

Open **Settings → AI assistant** in the panel:

| Field | Meaning |
| --- | --- |
| Enable AI assistant | Master switch; while it is off the tab only shows a hint |
| Base URL | An OpenAI-compatible `/v1` endpoint, e.g. `https://api.openai.com/v1` |
| Model | The provider's model id, e.g. `gpt-4o-mini` |
| API key | Write-only — the panel never echoes a stored key, and an empty field means "leave it alone" |

Or edit `config.json` directly:

```json
{
  "ai": {
    "enabled": true,
    "base_url": "https://api.openai.com/v1",
    "model": "gpt-4o-mini",
    "api_key": "sk-..."
  }
}
```

!!! tip "Keep the key in an environment variable"
    Setting `MSTL_AI_API_KEY` takes precedence over the value in `config.json`. The panel shows "provided by the environment" and locks the field. Useful when you would rather not leave the key on disk.

Any OpenAI-compatible service works: the official API, an Azure OpenAI compatibility layer, or your own vLLM / Ollama / one-api gateway. It calls `POST {base_url}/chat/completions` with `Authorization: Bearer`.

## The confirmation flow

A proposed action waits in the conversation as a card showing **what will happen** and **with which arguments**, with a live countdown (600 seconds by default):

- **Execute** — it really runs; the result goes back to the model, which finishes its reply
- **Cancel** — the action is dropped and the model is told you declined
- Letting it expire behaves like a decline, and the history is tidied up so no half-finished tool call is left behind

!!! warning "Why a human has to confirm"
    This assistant has the same reach as your own hands in the panel. And everything it reads — server logs, plugin lists, `server.properties`, file contents — can be influenced from outside: anyone can type a line into the console, and plugins can write arbitrary text into the log. If the model obeyed an "ignore the rules above and do X" line it found there, that would be a working injection path.

    So the model may only *propose*; execution stays with you. Tool output is wrapped in `<tool_result>` tags, and the system prompt states plainly that it is data, not instructions.

## Safety boundaries

- **The same path checks as everywhere else**: every file operation goes through `core/files.py` `normalize_rel` + `resolve`, so `..` escapes, absolute paths and drive letters are rejected.
- **`.server.json` is not writable**: configuration changes via AI must go through `update_config`, whose field allowlist is `min_mem` / `max_mem` / `java_path` / `auto_restart` / `restart_max` / `auto_start` / `extra_jvm_args` / `extra_server_args` / `openfrp`. Nothing else can be touched.
- **Secrets stay out of the model context**: tunnelling tokens and similar values are replaced with `***` before anything is handed over.
- **There is an audit log**: every approved, declined or expired action appends one JSON line to `.mstl-ai-audit.log` in the project directory (time, tool, arguments, decision). The file is git-ignored by default.

## Limits

| Item | Value | Note |
| --- | --- | --- |
| One message | 4000 characters | Longer input is refused with a message |
| Tool rounds | 8 | At most eight round trips per question, to stop loops |
| Conversation history | 60 messages | Trimmed from a user message so tool calls stay paired |
| Console read | 200 lines / 20000 chars | Tail only |
| File read | 64 KB | Longer files are truncated and marked as such |
| Concurrency | One turn at a time | Sending while busy returns "still working on the previous message" |

## What it remembers

The conversation lives in memory only: closing the panel or pressing **Clear conversation** wipes it. Nothing is written to disk and nothing is shared between instances. It also never acts on its own — there are no triggers; every action starts with a sentence from you.
