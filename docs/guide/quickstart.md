# 快速开始

## 前置条件

| 项目 | 要求 |
|---|---|
| Python | 3.10 或更高（源码模式运行需要） |
| 第三方依赖 | 无，全部使用标准库 |
| Java | 可选。缺 Java 或版本不匹配时，MSTL 可以自动下载 JDK |

如果使用 release 里打包好的产物（Windows 的 `MSTL.exe`，或 Linux / macOS 的可执行文件），机器上连 Python 都不用装。

## 安装

=== "从源码运行"

    ```bash
    git clone https://github.com/yzsyzdy/Minecraft-Terminal-Server-Launcher.git
    cd Minecraft-Terminal-Server-Launcher
    python start.py
    ```

=== "使用打包版本"

    从 [Releases](https://github.com/yzsyzdy/Minecraft-Terminal-Server-Launcher/releases) 下载对应平台的包。

    **Windows**：`MSTL-v*.zip`，解压后双击 `MSTL.exe`。

    **Linux（x86_64）**：

    ```bash
    mkdir mstl && tar xzf MSTL-v*-linux-x86_64.tar.gz -C mstl
    cd mstl && ./MSTL
    ```

    **macOS**：Apple 芯片用 `-macos-arm64`，Intel 机器用 `-macos-x86_64`。

    ```bash
    mkdir mstl && tar xzf MSTL-v*-macos-arm64.tar.gz -C mstl
    cd mstl && ./MSTL
    ```

    !!! warning "非 Windows 平台的两点限制"

        - **macOS 产物没有签名**（签名需要 Apple 开发者证书）。首次运行如果被 Gatekeeper
          拦下，右键 →「打开」，或者执行 `xattr -d com.apple.quarantine ./MSTL`。
        - **Linux 产物要求 glibc 不低于 2.35**（构建机是 Ubuntu 22.04）。更老的发行版请
          按上面的「从源码运行」跑。

## 首次运行

第一次启动会在当前目录生成三样东西：

```
config.json      # 全局配置
servers/         # 服务器实例目录，每个服务器一个子目录
java_list.json   # Java 检测缓存（首次检测后生成）
```

如果 `servers/` 是空的，主菜单会引导你先导入或下载一个服务端。

## 拿到一个服务器

### 方式一：下载服务端核心

主菜单 → `[3] 新建实例` → `[1] 下载 Minecraft 服务端核心`：

1. 选择服务端类型（Paper、Purpur、Fabric、Forge、Velocity 等 30 余种）
2. 选择版本（版本列表会缓存一天，避免重复请求）
3. 确认后并发下载核心，写入新的服务器目录
4. 按提示补充服务器名称、内存等配置

### 方式二：导入已有服务器

主菜单 → `[2] 导入服务器压缩包`：

1. 填入压缩包路径
2. MSTL 解压到临时目录，自动识别核心 jar
3. 如果识别到多个 jar，会让你挑一个
4. 确认后落到 `servers/<名称>/`

### 方式三：新建通用实例

主菜单 → `[3] 新建实例` → `[2] 通用实例`。它不跑 Minecraft，而是跑你指定的一个可执行文件，或者一段 `cmd` / `powershell` 命令；一样有启停、实时控制台与日志。详见[通用实例](../features/web-panel.md#generic-instances)。

## 启动

主菜单 → `[1] 启动服务器`，选择目标服务器。启动前 MSTL 会依次做几件事：

1. **解析 Java**：缓存 → 扫描系统 → 必要时下载
2. **校验兼容性**：比对 MC 版本要求的 Java 版本，不满足时给出警告并提供下载入口
3. **校验核心包**：确认 java 可执行文件和 jar 都存在
4. **拼装启动命令**：`-Xms` / `-Xmx`、额外 JVM 参数、额外服务端参数

启动后进入交互控制台。直接输入服务器指令（`stop`、`say hello`、`op 玩家名` 之类）即可，输入内容会原样喂给服务端进程。

## EULA

Minecraft 服务端首次启动会因未同意 EULA 直接退出。MSTL 会识别到 `eula=false`，询问你同不同意：

- 同意 → 自动改写 `eula.txt` 并重启服务器
- 拒绝 → 结束本次启动

## 下一步

- 不想守在终端跟前：主菜单 `[8] 网页面板`，浏览器里启停、看控制台、管文件、配 frp —— 见[网页面板](../features/web-panel.md)
- 想把内存、JVM 参数调一调：[配置说明](configuration.md)
- 想装插件或模组：[插件管理](../features/plugin-management.md)、[模组管理](../features/mod-management.md)
- 想让朋友连进来：[内网穿透](../features/tunnel.md)
