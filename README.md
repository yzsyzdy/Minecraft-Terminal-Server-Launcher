# MSTL — Minecraft Terminal Server Launcher

一个轻量级、零外部依赖的 Minecraft 服务端管理器。支持多服务器实例、版本隔离、自动 Java 检测与下载、插件和模组管理，兼容 Windows 和 Linux。

## 功能一览

### 核心管理

- **多服务器实例** — `servers/` 下每个服务器独立目录，互不干扰。支持 Paper、Purpur、Fabric、Forge、NeoForge、Velocity 等 30+ 种服务端类型
- **版本隔离** — 每个服务器独立配置 MC 版本、核心 jar、Java 版本、内存、JVM 参数
- **自动 Java 检测** — 扫描系统环境变量、标准安装路径，自动发现已安装的 JDK（支持 Windows 和 Linux）
- **JDK 自动下载** — 系统无 Java 或版本不兼容时，自动从镜像下载 GraalVM JDK（支持 17 / 21 / 25）
- **Java 版本校验** — 根据 MC 版本自动匹配所需 Java 版本，不兼容时提供警告和一键下载
- **多线程下载** — 服务端核心和插件下载默认使用 16 线程并发分块下载，自动检测 Range 支持并回退
- **EULA 自动处理** — 检测到 `eula=false` 时自动询问用户，同意后修改并重启服务器
- **控制台交互** — 支持在终端输入服务器指令（stop、say 等）
- **跨平台** — 同时支持 Windows 和 Linux

### 插件管理

- 支持 **SpigotMC**（通过 Spiget API）、**Modrinth**、**Hangar** 三个插件源搜索
- 按下载量排序展示搜索结果
- 直接搜索并下载，自动多线程加速
- 导入本地 jar 文件
- 插件启用/禁用（`.jar.disabled`）
- 外部托管插件自动识别（如 GitHub 托管的插件，通过 GitHub API 获取 jar）

### 模组管理

- 支持 **Modrinth**（无需 API key）和 **CurseForge**（需 API key）搜索
- 自动按当前服务器的 MC 版本和模组加载器（Fabric / Forge / NeoForge / Quilt）过滤
- 中文搜索支持（Modrinth 原生支持 Unicode 查询）
- 结果不足时自动放宽搜索范围（模糊搜索回退）
- 搜索到匹配版本的模组后一键下载
- 本地 jar 导入、启用/禁用

### 服务端下载

- 通过 **MSL 镜像源**（`api.mslmc.cn/v4`）下载 30+ 种服务端核心
- 每日缓存版本列表，避免重复请求
- 交互式选择服务端类型和版本，一键下载并生成配置文件

### 压缩包导入

- 导入已有服务器的压缩包，自动解压并识别核心 jar
- 多 jar 时交互式选择

## 快速开始

```bash
python start.py
```

首次运行会自动创建 `config.json` 和 `servers/` 目录。

### 前置条件

- **Python 3.10+**（标准库，**零第三方依赖**）
- **Java**（可选，启动器可在无 Java 时自动下载 JDK 21）

## 配置说明

### 全局配置 `config.json`

```json
{
  "java_path": null,
  "interactive": true,
  "curseforge_api_key": ""
}
```

| 字段 | 说明 |
|---|---|
| `java_path` | 指定 Java 路径，`null` 表示自动检测 |
| `interactive` | 是否启用控制台交互模式 |
| `curseforge_api_key` | CurseForge API key（可选，填入后可通过 CurseForge 搜索模组） |

### 服务器配置 `servers/<服务器名>/.server.json`

```json
{
  "name": "生存服1",
  "mc_version": "1.21.1",
  "jar": "leaves.jar",
  "java_path": null,
  "min_mem": "2G",
  "max_mem": "8G",
  "extra_jvm_args": [],
  "extra_server_args": []
}
```

`java_path` 为 `null` 时自动检测；`min_mem` / `max_mem` 支持 `G` / `M` 单位。

## 项目结构

```
mc-server-launcher/
├── start.py              # 启动入口
├── main.py               # re-export 模块（兼容旧导入）
├── config.py             # 全局配置读写
├── clear.py              # 终端清屏工具
├── server_manager.py     # 服务器目录管理、压缩包导入
├── download_msl.py       # MSL API 服务端下载 + 多线程下载引擎
├── java_tools.py         # Java 检测、JDK 下载、版本兼容（Win/Linux）
├── server_launcher.py    # 服务器进程启动
├── menu.py               # 交互菜单（服务器选择、插件/模组管理）
├── plugin_sources.py     # 插件搜索与下载（SpigotMC、Modrinth、Hangar）
├── mod_sources.py        # 模组搜索与下载（Modrinth、CurseForge）
├── config.json           # 全局配置（自动生成）
├── java_list.json        # Java 检测缓存（自动生成）
├── msl_cache.json        # MSL 镜像缓存（自动生成）
└── servers/              # 服务器实例目录（自动创建）
```

## 运行流程

```
start.py
  │
  ├─ 清屏
  ├─ 读取 config.json
  ├─ 创建 servers/ 目录
  │
  ├─ 没有服务器 → 菜单：[1]导入  [2]下载  [0]退出
  │
  └─ 有服务器 → 列表 + [导入] [下载] [管理插件/模组] [退出]
                  │
                  ▼ 选择服务器
                  │
                  ├─ 解析 Java（缓存 → 扫描 → 下载）
                  ├─ Java 版本兼容检查
                  └─ 启动服务器（交互控制台）
```

## MSL 镜像源

支持 30+ 种服务端核心，通过 `https://api.mslmc.cn/v4` 获取：
[MSL 服务端镜像源文档](https://www.mslmc.cn/docs/msl/msl-mirrors/)

服务端类型包括：Paper、Purpur、Leaf、Leaves、Spigot、Folia、Fabric、Forge、NeoForge、Quilt、Vanilla、Velocity、BungeeCord 等。

## Java 版本兼容表

| MC 版本 | 最低 Java | 推荐 Java | 说明 |
|---|---|---|---|
| ≤ 1.12.2 | 8 | 8 | 许多老牌 Mod 强制要求 Java 8 |
| 1.13 – 1.16.5 | 8 | 11 | Java 11 性能更好 |
| 1.17 – 1.17.1 | 16 | 17 | Java 8 不再兼容 |
| 1.18 – 1.20.4 | 17 | 17 | |
| ≥ 1.20.5 | 21 | 21 | 官方强制版本 |
| ≥ 1.25 | 25 | 25 | 最新快照版 |

不兼容时启动器会提示并提供 JDK 自动下载（支持 17 / 21 / 25）。

## 依赖

- **Python 3.10+**
- **标准库**（零第三方依赖）
- **JDK 21+**（可选，用于运行 Minecraft 服务器，启动器可自动下载）

## 许可

本项目基于 **GNU General Public License v3.0** 开源。
完整协议见 [LICENSE](LICENSE) 文件。

## 常见问题

### CurseForge 搜索不生效？

需要在 `config.json` 中配置 `curseforge_api_key`。可前往 [CurseForge Console](https://console.curseforge.com/) 免费申请。

### 插件从 SpigotMC 下载失败？

部分插件托管在外部平台（如 GitHub），启动器会自动识别并跳转到 GitHub API 获取 jar 文件。如果仍然失败，可按提示手动下载。

### 模组搜索不到中文名模组？

Modrinth 的搜索索引依赖模组简介中的文字。绝大多数模组的名称和简介是英文，建议用英文关键词搜索。中文描述出现在简介中的模组可以被搜到。

### 下载速度慢？

多线程下载默认 16 线程并发。可以在代码中调用 `set_download_threads(n)` 调整线程数。如果镜像站不支持 Range 请求，会自动回退到单线程下载。
