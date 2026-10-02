# 内网穿透

入口：主菜单 → `[6] 内网穿透`。

家里或校园网里的服务器默认没有公网地址，别人连不上。内网穿透就是在本机跑一个客户端，跟服务商的公网节点保持长连接，把外部流量转发到本地端口。

MSTL 提供两条互相独立的路线，按需选一条即可。

## 路线一：OpenFrp 云端隧道

适合不想自己写配置文件、想要个现成公网地址的场景。

1. 到 [OpenFrp 控制台](https://console.openfrp.net) 注册并拿到**用户 token**
2. `[6] 内网穿透` → `OpenFrp` → 查看隧道列表，填入 token
3. 在列表里选一个隧道，或直接在菜单里新建隧道（选节点、选区域、指定本地端口）
4. 用 `绑定隧道到服务器` 把隧道挂到某个实例上，此时会写入该服务器 `.server.json` 的 `openfrp` 字段
5. 之后启动服务器，frpc 会跟着一起起来

`openfrp.auto_start` 控制是否随服启动。绑定时会问你一次。

**不需要自己装 frpc。** MSTL 会识别当前操作系统与 CPU 架构，去 OpenFrp 的发布接口取对应二进制并下载。下载下来的 `frpc.exe` 被 `.gitignore` 排除，不会进版本库。

## 路线二：本地 frp（frpc.toml）

适合手上已经有自己的 frp 服务端（自建、或者服务商给的 `frpc.toml` 模板），想完全手动控制的场景。

菜单里能做的事：

- `下载 frpc`：从 frp 官方 release 取对应平台的压缩包并解压。下载不通时会直接打印发布页地址与该下的文件名，并允许你填入**本地已下好**的压缩包或二进制路径，由 MSTL 解出 `frpc` 放到位
- `编辑配置`：读写 `frp/frpc.toml`，包含 `serverAddr`、`serverPort`、`auth.token` 以及各条 proxy
- `代理列表` / `新增代理` / `删除代理`：维护 `[[proxies]]` 段落，指定 `localPort` 与 `remotePort`
- `启动 frpc` / `停止 frpc`：手动拉起或结束进程

OpenFrp 那条路线同理：官方 CDN 全挂时会打印 OpenFrp 的发布页地址，你手动下好压缩包丢进 `openfrp/` 即可，启动时会自动解压。

`frpc.toml` 里通常含 `auth.token`，所以它和 frpc 二进制一起被 `.gitignore` 排除，只跟踪 `frp/*.py` 的源码。

## 两条路线的区别

| | OpenFrp | 本地 frp |
|---|---|---|
| 公网节点 | 用 OpenFrp 提供的 | 你自己或服务商的 |
| token | 用户 token，隧道在云端管理 | 写在 `frpc.toml` 里 |
| 配置位置 | 存进服务器 `.server.json` | 独立文件 `frp/frpc.toml` |
| 随服启动 | 支持 | 手动启动 |

代码上这两条也确实是两套独立实现（`openfrp/` 与 `frp/`），互不覆盖，可以同时装着。

## 排查

- 隧道显示在线但连不上：确认本地端口填的是服务器实际监听端口（`server.properties` 里的 `server-port`，默认 25565），而不是别的什么端口
- frpc 起不来：看窗口里的报错，多半是 token 失效或 remotePort 被占用
- 想让服务器只在内网跑：不绑隧道就行，`config.json` 里也不用动
