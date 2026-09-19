# 服务器管理

## 目录结构

`servers/` 下每个子目录就是一个服务器实例，彼此完全独立：

```
servers/
├── 生存服/
│   ├── .server.json        # 该服务器的配置
│   ├── paper.jar           # 核心
│   ├── eula.txt
│   ├── server.properties
│   ├── plugins/            # 插件
│   ├── mods/               # 模组（Fabric / Forge 等）
│   └── world/              # 世界数据
└── 空岛服/
    └── ...
```

部署多个服务器时，目录名就是实例标识，建议用 ASCII 之外的字符也没问题（内部按 UTF-8 处理），但含有 `\ / : * ? " < > |` 的路径在 Windows 上会出问题。

## 版本隔离

每个实例自己记录 MC 版本、核心 jar、Java 路径、内存上限和 JVM 参数。允许同时存在 1.20.1 的 Forge 服和 1.21.1 的 Paper 服，各自用各自的 Java。

启动时按 `.server.json` 里的 `mc_version` 推导所需 Java，因此同一个 `MSTL.exe` 能同时伺候新老版本。

## 导入

主菜单 → `[2] 导入服务器压缩包`。

行为细节：

- 支持 `.zip`。解压走临时目录，结束后清理，不会污染 `servers/`
- 只扫描压缩包**第一层**的 jar 作为候选核心，避免把 `plugins/` 里的 jar 误认成核心
- 候选多于一个时交互选择
- 压缩包内如果已经带 `.server.json`，导入时会读取其中的配置
- 若压缩包多套了一层目录（比如压缩的是文件夹而不是内容），MSTL 会自行处理层级

## 导出

主菜单 → `[5] 导出服务器`，把已有实例按类别打包，用于迁移或备份。

可选类别：

| 类别 | 内容 |
|---|---|
| `world` | `world`、`world_nether`、`world_the_end` |
| `datapacks` | `world/datapacks` |
| `plugins` | `plugins/*.jar` |
| `plugindata` | `plugins/*/`，即各插件的数据目录 |
| `playerdata` | 玩家存档、统计、进度、`ops.json`、`whitelist.json`、封禁名单等 |
| `icon` | `server-icon.png` |
| `config` | `server.properties`、`bukkit.yml`、`paper.yml`、`purpur.yml`、`eula.txt`、`.server.json` 等 |

`playerdata` 涉及玩家隐私，默认**不勾选**，需要时手动开。

勾选后 MSTL 按类别收集文件写进 zip。注意核心 jar 不在任何类别里（版本升级时通常要换新的），导入后按需补核心。

## 插件与模组的日常操作

主菜单 → `[4] 管理插件/模组`，选中服务器后进入：

- 列出目录里的 jar，显示启用/禁用状态
- `启用` / `禁用`：通过重命名实现，禁用即 `.jar.disabled`，服务端就不会加载
- `删除`：确认后移除
- `导入本地 jar`：把磁盘上已有的 jar 复制进来
- `搜索并下载`：走网络源搜索安装

详见[插件管理](../features/plugin-management.md)与[模组管理](../features/mod-management.md)。
