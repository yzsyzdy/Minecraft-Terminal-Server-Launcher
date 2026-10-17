<div class="mstl-band">
  <span class="mstl-band__mark">
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48" aria-hidden="true">
      <defs><linearGradient id="mstl-band-mark" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#6B78E6"/><stop offset="1" stop-color="#4C5BD4"/></linearGradient></defs>
      <rect width="48" height="48" rx="11" fill="url(#mstl-band-mark)"/>
      <path d="M14 16.5 L22 24 L14 31.5" fill="none" stroke="#FFFFFF" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>
      <path d="M26 31.5 L35 31.5" fill="none" stroke="#FFFFFF" stroke-width="3" stroke-linecap="round"/>
    </svg>
  </span>
  <span class="mstl-band__name">MINECRAFT TERMINAL SERVER LAUNCHER</span>
  <span class="mstl-band__chips"><i>Windows</i><i>Linux</i><i>macOS</i></span>
</div>

# MSTL

**Minecraft Terminal Server Launcher** — 轻量级、零外部依赖的 Minecraft 服务端管理器。

用 Python 标准库写成的终端工具，负责把「下载核心 → 装 Java → 配内存 → 开服 → 管插件模组」这条链路收进一个交互菜单里。Windows、Linux 和 macOS 都能跑。

## 它解决什么

手动开服要来回折腾：去镜像站找核心、确认 Java 版本对不对、翻 wiki 查启动参数、插件还得一个个下。MSTL 把这些动作按依赖顺序串起来，每个服务器一个独立目录，互不干扰。

## 特性概览

| 方向 | 能力 |
|---|---|
| 服务端 | 30+ 种核心（Paper / Purpur / Leaf / Fabric / Forge / NeoForge / Velocity …），经 MSL 镜像下载 |
| Java | 自动检测系统 JDK，按 MC 版本校验兼容性，缺失或不匹配时可自动下载 GraalVM（17 / 21 / 25） |
| 插件 | SpigotMC（Spiget）、Modrinth、Hangar 三源搜索与下载 |
| 模组 | Modrinth（免 key）、CurseForge（需 key），按 MC 版本与加载器过滤 |
| 实例 | 多服务器、版本隔离、压缩包导入、按类别导出 |
| 内网穿透 | 本地 frpc（frpc.toml）与 OpenFrp 云端隧道两条路线 |
| 界面 | 终端交互菜单，内置 中文 / English / Русский 翻译 |

## 开始使用

- [快速开始](guide/quickstart.md)：从零到跑起第一个服务器
- [配置说明](guide/configuration.md)：`config.json` 与 `.server.json` 的完整字段表
- [项目架构](advanced/architecture.md)：想读源码或改代码，从这里入手
- [常见问题](faq.md)：下载失败、搜索不到、速度慢之类

## 许可

本项目基于 **GNU General Public License v3.0** 开源，完整协议见仓库根目录的 [LICENSE](https://github.com/yzsyzdy/Minecraft-Terminal-Server-Launcher/blob/main/LICENSE)。
