# 功能详解

MSTL 的三个功能模块，共同点是都要联网取数据，因此都依赖统一的 `http_utils` 请求层。

- [插件管理](plugin-management.md)：SpigotMC / Modrinth / Hangar 三源搜索安装
- [模组管理](mod-management.md)：Modrinth 与 CurseForge，按版本与加载器过滤
- [内网穿透](tunnel.md)：本地 frpc 与 OpenFrp 云端隧道
