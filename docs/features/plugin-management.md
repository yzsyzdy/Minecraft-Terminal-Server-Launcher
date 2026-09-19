# 插件管理

入口：主菜单 → `[4] 管理插件/模组`，选择服务器 → `插件`。

## 数据源

| 源 | 接口 | 说明 |
|---|---|---|
| SpigotMC | Spiget API（`api.spiget.org/v2`） | Spigot 资源站的第三方只读 API，插件最全 |
| Modrinth | `api.modrinth.com/v2` | 现代平台，无需 API key |
| Hangar | `hangar.papermc.io/api/v1` | PaperMC 官方平台，Paper / Velocity 插件集中在这里 |

搜索会同时打到三个源，每个源取前若干条，再按下载量排序展示，避免把冷门插件顶到前面。

## 搜索与安装

1. 输入关键词，中文也可以（取决于平台索引，见下）
2. 从合并结果里选一个
3. MSTL 取该插件的下载地址并落到 `plugins/`
4. 如果平台返回的是外部托管链接，见下节

## 外部托管插件

部分 Spigot 插件并不把 jar 放在 SpigotMC 上，而是托管在 GitHub Releases。遇到这种：

1. MSTL 识别出外部链接
2. 调用 GitHub API 找到仓库的最新 release
3. 从 release assets 里挑出 jar 并下载

如果 GitHub 那边也拿不到（私有仓库、release 没传 jar 等），会提示手动下载。

## 启用、禁用与删除

- `启用` / `禁用`：靠改名实现。禁用后文件名变成 `xxx.jar.disabled`，服务端不会加载，想恢复随时改回来。适合临时关掉某个插件排查冲突，不用反复下载
- `删除`：二次确认后删除文件
- `导入本地 jar`：从磁盘任意路径复制 jar 进 `plugins/`，适合自己编译或从别处拿到的插件

## 语言与搜索建议

平台索引依赖插件简介里的文字。绝大多数插件用的是英文名称和简介，因此**英文关键词命中率明显更高**；简介里出现中文的插件才能被中文关键词搜到。

搜不到时换个更通用的英文词，或者直接去平台网页版找到插件名再回来搜。

## 相关

- 模组（Fabric / Forge / NeoForge 侧）见[模组管理](mod-management.md)
- 插件目录与启用状态的底层逻辑见[服务器管理](../guide/server-management.md)
