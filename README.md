# Minecraft 服务器启动器

一个轻量级的 Minecraft 服务端管理器，支持多服务器实例、版本隔离、自动 Java 检测与 JDK 下载、MSL 镜像源高速下载。

## 功能

- **多服务器管理** — `servers/` 下每个服务器独立目录，互不干扰
- **版本隔离** — 每个服务器独立配置 Java 版本、核心 jar、内存、JVM 参数
- **自动 Java 检测** — 扫描系统环境变量、默认安装目录，自动发现已安装的 JDK
- **JDK 自动下载** — 系统无 Java 或版本不兼容时，从镜像自动下载 GraalVM JDK
- **Java 版本校验** — 根据 MC 版本自动匹配所需 Java，不兼容时警告并提供下载
- **MSL 镜像源下载** — 从 MSL API V4 获取服务端核心（Paper、Purpur、Fabric 等 30+ 种），每日缓存
- **压缩包导入** — 导入已有的服务器压缩包，自动识别核心 jar
- **控制台交互** — 支持在终端输入服务器指令（stop、say 等）

## 快速开始

```bash
python start.py
```

首次运行会自动创建 `config.json` 和 `servers/` 目录。

### 配置说明

全局配置 `config.json`：

```json
{
  "java_path": null,
  "interactive": true
}
```

每个服务器在 `servers/<服务器名>/.server.json` 中独立配置：

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
├── start.py             # 启动入口
├── main.py              # re-export 模块（兼容旧导入）
├── config.py            # 全局配置读写
├── server_manager.py    # 服务器目录管理、压缩包导入
├── download_msl.py      # MSL API 服务端下载
├── java_tools.py        # Java 检测、JDK 下载、版本兼容
├── server_launcher.py   # 服务器进程启动
├── menu.py              # 交互菜单
├── config.json          # 全局配置（自动生成）
├── .gitignore
└── README.md
```

### 运行流程

```
start.py
  │
  ├─ 读取 config.json
  ├─ 创建 servers/ 目录
  │
  ├─ 没有服务器 → 菜单：[1]导入  [2]下载  [0]退出
  │
  └─ 有服务器 → 列表 + [导入] [下载] [退出]
                  │
                  ▼ 选择服务器
                  │
                  ├─ 解析 Java（缓存 → 扫描 → 下载）
                  ├─ Java 版本兼容检查
                  └─ 启动服务器
```

## MSL 镜像源

支持 30+ 种服务端核心，通过 `https://api.mslmc.cn/v4` 获取：
[MSL 服务端镜像源文档](https://www.mslmc.cn/docs/msl/msl-mirrors/)

## Java 版本兼容表

| MC 版本 | 最低 Java | 推荐 Java |
|---|---|---|
| ≤ 1.12.2 | 8 | 8 |
| 1.13 – 1.16.5 | 8 | 11 |
| 1.17 – 1.17.1 | 16 | 17 |
| 1.18 – 1.20.4 | 17 | 17 |
| ≥ 1.20.5 | 21 | 21 |
| ≥ 1.25 | 25 | 25 |

不兼容时启动器会提示并提供 JDK 自动下载（支持 17 / 21 / 25）。

## 依赖

- Python 3.10+
- 标准库（无第三方依赖）
- （可选）JDK 21+ 用于运行 Minecraft 服务器
