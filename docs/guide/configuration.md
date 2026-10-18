# 配置说明

MSTL 有两层配置：全局的 `config.json`，以及每个实例自己的 `.server.json`。

## 全局配置 `config.json`

位于项目根目录（打包版与 exe 同级），不存在时自动按默认值生成。

```json
{
  "java_path": null,
  "interactive": true,
  "language": "",
  "curseforge_api_key": "",
  "download_threads": 16,
  "auto_start_server": "",
  "web": {
    "host": "127.0.0.1",
    "port": 8765,
    "token": ""
  },
  "ai": {
    "enabled": false,
    "base_url": "",
    "model": "",
    "api_key": ""
  }
}
```

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `java_path` | string \| null | `null` | 指定 Java 可执行文件路径。`null` 表示自动检测 |
| `interactive` | bool | `true` | 服务器启动后是否进入交互控制台，即在终端里直接输入服务端指令 |
| `language` | string | `""` | 界面语言。空字符串表示跟随系统（中文环境用 `zh`，其余用 `en`），也可显式填 `zh` / `en` / `ru` |
| `curseforge_api_key` | string | `""` | CurseForge API key。留空则 CurseForge 模组搜索不可用 |
| `download_threads` | int | `16` | 多线程下载的并发分块数。镜像不支持 Range 请求时会自动回退单线程 |
| `auto_start_server` | string | `""` | 记录「设置」里指定的默认启动服务器名称，留空表示不自动启动。这一台会**前台交互式**启动并跳过主菜单 |
| `web.host` | string | `"127.0.0.1"` | 网页面板监听地址，`0.0.0.0` 表示所有网卡 |
| `web.port` | int | `8765` | 网页面板监听端口；填 `0` 表示随便挑一个空闲端口 |
| `web.token` | string | `""` | 网页面板访问 token，留空则首次启动自动生成 |
| `web.auto_start` | bool | `false` | 面板是否随软件启动。开启后在后台线程里跑，不挡住终端菜单 |
| `ai.enabled` | bool | `false` | 是否启用网页面板里的 AI 助手标签页 |
| `ai.base_url` | string | `""` | OpenAI 兼容接口的 `/v1` 地址，例如 `https://api.openai.com/v1` |
| `ai.model` | string | `""` | 模型标识，例如 `gpt-4o-mini` |
| `ai.api_key` | string | `""` | 接口密钥。环境变量 `MSTL_AI_API_KEY` 优先于这里的值，面板永远不回显已保存的密钥 |

其中 `language`、`download_threads`、`auto_start_server` 三项通常不需要手改，在菜单 `[7] 设置` 里操作即可：

- `[1] 语言`：列出 `locales/` 下可用的语言，另有 `[A]` 选项切回跟随系统
- `[2] 下载线程`：实时调整并发数
- `[3] 检查更新`：读取 `version.json` 里的当前版本，与 GitHub Releases 的最新版比较
- `[4] 自动启动服务器`：指定主菜单默认启动哪一个
- `[5] 开机自启`：写 Windows 注册表 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`，非 Windows 平台不可用

`web.*` 里的监听地址、端口、token 与「面板随软件启动」都在主菜单 `[8] 网页面板` 的子菜单里，不必手改 `config.json`。用法与安全须知见[网页面板](../features/web-panel.md)。

!!! tip "两种「自动启动」不是一回事"
    - **`auto_start_server`（全局，一台）**：软件启动时跳过主菜单，把那一台**前台**跑起来，终端归它用，Ctrl+C 结束。
    - **实例的 `auto_start`（每台一个）**：软件启动时在**后台**拉起，终端还能留在菜单上；面板里能看它的控制台，关掉软件时会被一并停止。

    两者同时命中同一台实例时只走前台那条，不会启动两次。

!!! note "关于 config.json 的版本管理"
    `config.json` 属于机器相关配置，仓库里只提交 `config.example.json` 作为模板。自己 clone 下来后 `config.json` 会按默认值生成，不会被 git 跟踪。

## 单服务器配置 `.server.json`

位于 `servers/<服务器名>/.server.json`。

```json
{
  "name": "生存服",
  "mc_version": "1.21.1",
  "jar": "paper.jar",
  "java_path": null,
  "min_mem": "2G",
  "max_mem": "8G",
  "extra_jvm_args": [],
  "extra_server_args": [],
  "process_priority": "normal",
  "auto_restart": false,
  "restart_max": 5,
  "openfrp": {
    "token": "",
    "proxy_id": 0,
    "auto_start": false
  }
}
```

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `name` | string | `""` | 显示名，菜单里用它标识实例 |
| `mc_version` | string | `""` | Minecraft 版本，用于推导所需 Java 版本 |
| `jar` | string | `"server.jar"` | 核心 jar 文件名，相对服务器目录 |
| `java_path` | string \| null | `null` | 覆盖全局 Java 设置，`null` 表示沿用自动检测结果 |
| `min_mem` | string | `"1G"` | `-Xms`，支持 `G` / `M` 单位 |
| `max_mem` | string | `"4G"` | `-Xmx` |
| `extra_jvm_args` | list[str] | `[]` | 追加的 JVM 参数，如 `-XX:+UseG1GC` |
| `extra_server_args` | list[str] | `[]` | 追加的服务端参数，如 `--nogui` |
| `process_priority` | string | `"normal"` | 进程优先级，可选 `normal` / `below`（低于正常）/ `low`（低） |
| `kind` | string | `"minecraft"` | 实例种类，`minecraft` 或 `generic`（通用实例）。创建后不可更改 |
| `auto_start` | bool | `false` | 随软件启动而启动：软件一起来就在**后台**把它拉起（可被面板管理） |
| `auto_restart` | bool | `false` | 崩溃自动重启：进程以非 0 退出码结束时自动拉起（退避 5 秒起、每次翻倍、封顶 60 秒）。手动停止、控制台里敲 `stop`、正常关服（退出码 0）都不会触发 |
| `restart_max` | int | `5` | 连续崩溃多少次后放弃自动重启，范围 `0`–`20`，`0` 等于关闭。进程活过 60 秒后再崩会重置这个计数 |
| `launch` | object | 见下 | **通用实例**的启动配置，`kind` 为 `minecraft` 时忽略 |
| `openfrp.token` | string | `""` | 绑定到本实例的 OpenFrp 用户 token |
| `openfrp.proxy_id` | int | `0` | 绑定的隧道 ID，`0` 表示未绑定 |
| `openfrp.auto_start` | bool | `false` | 启动服务器时是否自动拉起 frpc |

通用实例的 `launch`：

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `mode` | string | `"exec"` | `exec`（直接运行可执行文件，不经 shell）/ `cmd` / `powershell` |
| `program` | string | `""` | `mode=exec` 时的可执行文件，相对路径按实例目录算 |
| `args` | list[str] | `[]` | `mode=exec` 时的参数，一项一个 |
| `command` | string | `""` | `mode=cmd` / `powershell` 时要执行的命令原文 |
| `workdir` | string | `""` | 工作目录，相对实例目录；留空即实例目录 |

`process_priority`、`extra_jvm_args`、`extra_server_args`、`auto_start`、`auto_restart`（以及通用实例的整套 `launch`）在主菜单 `[1] 启动服务器` 选中实例后的配置菜单里可以直接改，不需要手写 JSON；网页面板里选中实例点标题栏的 `⚙` 也能改。

## 自动生成的文件

| 文件 | 说明 | 是否纳入版本控制 |
|---|---|---|
| `config.json` | 全局配置 | 否 |
| `java_list.json` | Java 检测结果与上次选择，避免每次全盘扫描 | 否 |
| `msl_cache.json` | MSL 镜像的服务端类型与版本列表，按天缓存 | 否 |
| `version.json` | 由 CI 在打包时写入，记录版本号，供更新检查使用 | 否 |

这些文件都可以安全删除，MSTL 会在下次运行时重建。

## Java 版本要求

| MC 版本 | 最低 Java | 推荐 Java | 说明 |
|---|---|---|---|
| ≤ 1.12.2 | 8 | 8 | 许多老牌 Mod 强制要求 Java 8 |
| 1.13 – 1.16.5 | 8 | 11 | Java 11 性能更好 |
| 1.17 – 1.17.1 | 16 | 17 | Java 8 不再兼容 |
| 1.18 – 1.20.4 | 17 | 17 | |
| ≥ 1.20.5 | 21 | 21 | 官方强制版本 |
| ≥ 1.25 | 25 | 25 | 最新快照版 |

不满足要求时启动会给出警告，并提供 JDK 自动下载（17 / 21 / 25，来自 GraalVM）。
