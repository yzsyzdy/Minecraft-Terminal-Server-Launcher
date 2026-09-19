# 常见问题

## 启动相关

### 提示 Java 找不到或版本不兼容

MSTL 会按 MC 版本推荐 Java 版本（见[配置说明](guide/configuration.md)里的 Java 版本要求一节），不匹配时会给出警告并提供下载入口。

想手动指定，有三种方式：改 `config.json` 的 `java_path`（全局）、改某个实例 `.server.json` 的 `java_path`（只影响它）、或者把 Java 加进系统 `PATH` 让它自己扫到。

### 服务器一闪就退出，说没同意 EULA

首次运行必经步骤。MSTL 会检测 `eula=false` 并询问，同意后自动改写 `eula.txt` 再重启。如果手动改过 `eula.txt` 又改回 `false`，下次启动还是会问。

### 服务端起不来，日志里是内存不足

调 `.server.json` 的 `min_mem` / `max_mem`。注意 `max_mem` 不能超过物理内存的合理范围，给太多反而会让 JVM 起不来。

### 想让服务器在后台跑，不要那个交互控制台

把 `config.json` 的 `interactive` 改成 `false`。这时 MSTL 不再接管 stdin，服务端输出照样打印，但你就没法在终端里敲指令了。

## 下载相关

### 服务端核心下载很慢

默认 16 线程并发分块。菜单 `[7] 设置` → `[2] 下载线程` 可以调，也可以直接改 `config.json` 的 `download_threads`。

如果镜像站不支持 Range 请求，MSTL 会自动回退单线程，这时候线程数调多高都没用。

### 下载失败 / 版本列表是空的

MSL 镜像接口（`api.mslmc.cn`）此刻可能不可用。版本列表按天缓存，失败时可以删掉 `msl_cache.json` 让它重新拉一次。

## 插件与模组

### CurseForge 搜索没反应

需要在 `config.json` 里配置 `curseforge_api_key`。到 [CurseForge Console](https://console.curseforge.com/) 免费申请。

不想折腾就用 Modrinth，同一个模组大概率两边都有。

### 搜不到中文名模组

平台索引依赖模组简介中的文字，而绝大多数模组的名称和简介是英文的，所以中文关键词命中率低。建议用英文关键词搜索。

### 插件下载失败

部分插件托管在外部平台（如 GitHub），MSTL 会自动识别并走 GitHub API 取 release 里的 jar。如果仍然失败，按提示手动下载，再用「导入本地 jar」放进去。

### 禁用一个插件后想恢复

禁用只是把文件名改成 `xxx.jar.disabled`，去掉 `.disabled` 后缀即可，菜单里的「启用」就是干这个的。

## 内网穿透

### 隧道在线但朋友连不上

确认绑定的本地端口与 `server.properties` 里的 `server-port` 一致（默认 25565）。另外如果服务器还没启动，隧道自然转发不到东西。

### frpc 是什么，要自己装吗

不需要。OpenFrp 路线的 frpc 二进制由 MSTL 按系统和架构自动下载；本地 frp 路线在菜单里也有「下载 frpc」。这些二进制被 `.gitignore` 排除，不会进版本库。

## 界面与语言

### 怎么换成英文界面

主菜单 `[7] 设置` → `[1] 语言`，从列表里选 `English`；`[A]` 表示跟随系统语言。

`locales/` 目录下有什么语言就能选什么，目前内置 中文、English、Русский 三种。

## 其他

### 打包版的配置文件在哪里

与 `MSTL.exe` 同级目录。`servers/`、`config.json`、各种缓存都在那一层，整个目录整体搬走就能迁移。

### 可以直接删缓存文件吗

可以。`java_list.json`、`msl_cache.json`、`version.json` 都是可重建的，删掉只会让对应流程重新跑一遍。`config.json` 删掉会回到默认配置。

### 多个服务器能同时开吗

能。每个实例独立目录、独立配置、独立 Java 选择。唯一要自己注意的是端口别撞，以及内存总量别超过物理内存。
