# 模组管理

入口：主菜单 → `[4] 管理插件/模组`，选择服务器 → `模组`。

模组管理只对模组端（Fabric / Forge / NeoForge / Quilt）有意义。纯插件端（Paper / Purpur / Velocity 等）没有 `mods/` 目录的概念。

## 数据源

| 源 | 是否需要 key | 说明 |
|---|---|---|
| Modrinth | 不需要 | 支持按 MC 版本与加载器过滤，原生支持 Unicode 查询 |
| CurseForge | 需要 | 在 `config.json` 的 `curseforge_api_key` 里填。未填时该源自动跳过 |

CurseForge 优先走官方 API（`api.curseforge.com/v1`），失败时回退到社区代理（`api.curse.tools/v1/cf`）。

## 过滤机制

搜索请求会带上当前服务器的两个参数：

- **MC 版本**：取自 `.server.json` 的 `mc_version`
- **模组加载器**：从服务器核心文件名推断，识别 `fabric` / `forge` / `neoforge` / `quilt`

因此在新版服务器里搜模组，不会给你推 1.16 时代的老版本。

如果加上这两个条件后结果太少，MSTL 会自动放宽范围做模糊搜索回退，先保证搜得到，再让你自己确认版本是否合适。

## 安装

1. 选中的模组会匹配出适配当前 MC 版本与加载器的文件
2. 下载到服务器目录下的 `mods/`
3. 完成后按需重启服务器

同一个模组可能有多个适配文件（不同 loader、不同 MC 版本），MSTL 会挑最匹配的一个，不匹配的会明确提示，不会硬装。

## 启用、禁用与删除

与插件一致：

- `启用` / `禁用`：`.jar` ↔ `.jar.disabled` 改名切换
- `删除`：二次确认后移除
- `导入本地 jar`：把已有的模组文件复制进 `mods/`

## 常见问题

CurseForge 搜索没反应，基本都是缺 key。到 [CurseForge Console](https://console.curseforge.com/) 免费申请一个填进 `config.json` 即可。Modrinth 不需要任何配置，建议先用它。

## 相关

- 服务端插件见[插件管理](plugin-management.md)
- 配置字段表见[配置说明](../guide/configuration.md)
