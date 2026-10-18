# 从源码构建

## 跑测试

测试只用标准库 `unittest`，**不需要装任何依赖**（项目本身也是零运行时依赖），
在仓库根目录跑：

```bash
python -m unittest discover -s tests -t . -v
```

CI（`.github/workflows/tests.yml`）在每次 push / PR 上会在 Windows / Linux / macOS
三个平台上各用 Python 3.11 与 3.13 跑一遍；`build.yml` 在打包前也会在每个平台各跑一次，
免得带着失败的测试发版。

平台矩阵不是铺张，是必需：只在 Windows 上跑过的用例会把平台假设藏起来。
`test_frpc_files.py` 里就有两条用例拿 PE 的 `MZ` 当「合法可执行文件」，而
`frp/frpc_files.py` 的 `looks_like_executable()` 是按平台判魔数的（Windows 认 PE、
Linux 认 ELF、macOS 认 Mach-O）——它们在 Windows 上永远绿，一到 Linux/macOS
就会被判成「拿错平台的包」。写这类用例时要像同文件的其他用例那样把平台钉住
（`mock.patch.object(frpc_files.platform, "system", ...)`）。

测试盯的是「契约」而不是实现细节，重点在这些容易悄悄坏掉的地方：

| 文件 | 管什么 |
| --- | --- |
| `test_web_api.py` | 面板鉴权（三条 token 通道）、`/api/state` 回显必须等于磁盘值、设置写入 |
| `test_config.py` | `config.json` 读写、未知键不能丢、写盘要原子 |
| `test_server_manager.py` | 实例扫描、类型归一化、解析缓存与隔离 |
| `test_paths.py` | 打包版路径解析（用伪造的 `__compiled__` 模拟，不用真编译） |
| `test_runtime_plan.py` | 启动计划、进程优先级、运行时快照 |
| `test_frp_toml.py` | `frpc.toml` 往返仍然合法（用 `tomllib` 校验） |
| `test_frpc_files.py` / `test_tunnel_install.py` | 两套隧道共用的 frpc 识别、提取、校验 |
| `test_archive.py` | 在线解压的越界与炸弹防护 |
| `test_i18n.py` | 缺翻译回退到中文、占位符一致性 |
| `test_static.py` | 未定义名扫描、面板 id/文案前后端一致性、JS 语法 |

## 前置条件

- Python 3.11（CI 用的版本，3.10+ 基本都能跑）
- Nuitka：`pip install nuitka imageio`

`imageio` 用来让 Nuitka 处理 `icon.png`，缺了会在打图标那一步失败。

## 本地打包

仓库根目录有现成的 `build.bat`：

```bat
python -m nuitka --standalone ^
  --assume-yes-for-downloads ^
  --enable-plugin=anti-bloat ^
  --windows-console-mode=force ^
  --windows-icon-from-ico=icon.png ^
  --output-dir=build ^
  --product-name=MSTL ^
  --product-version="1.1.39" ^
  --file-version="1.1.39" ^
  --file-description="Minecraft Terminal Server Launcher" ^
  --copyright="MSTL" ^
  --include-data-dir=locales=locales ^
  --include-data-dir=webui=webui ^
  --output-filename=MSTL.exe ^
  start.py
```

参数逐个说：

| 参数 | 作用 |
|---|---|
| `--standalone` | 打出目录形式的产物（`start.dist/`），不是单文件。资源文件与 exe 并排，启动更快 |
| `--assume-yes-for-downloads` | 缺 C 编译器/依赖时不交互确认，直接下载，方便 CI |
| `--enable-plugin=anti-bloat` | 裁掉没用到的标准库模块，减小体积 |
| `--windows-console-mode=force` | 强制保留控制台窗口。MSTL 是终端程序，这条不能省 |
| `--windows-icon-from-ico=icon.png` | 设置 exe 图标 |
| `--include-data-dir=locales=locales` | 把 `locales/` 目录打进产物，**运行时目录名必须是 `locales`**，写错会导致界面全是 key |
| `--include-data-dir=webui=webui` | 把 `webui/` 打进产物。**漏了它网页面板就废了**：一打开就是 `webui/index.html missing` |
| `--output-dir=build` | 输出到 `build/` |

产物在 `build/start.dist/MSTL.exe`。整个 `start.dist/` 目录需要一起分发，不能只给 exe。

> `build.bat` 是 Windows 专用的。在 Linux / macOS 上从源码打包时，把
> `--windows-console-mode`、`--windows-icon-from-ico` 两项去掉，`--output-filename` 写成
> `MSTL`（不带 `.exe`），Linux 上还要先 `sudo apt install patchelf`。其余参数一样。

### 运行时资源清单

exe 里硬编码要读这些随包资源，**打包时缺一个，对应功能就是坏的**：

| 资源 | 谁在读 | 缺了会怎样 |
|---|---|---|
| `locales/*.json` | `i18n` | 界面全部显示成 i18n key |
| `webui/index.html` | `web/server.py` | 网页面板打不开（HTTP 500） |
| `version.json` | `check_update.py` | 更新检查静默跳过（CI 打包后写入，源码模式下本就没有） |

CI 的 `build.yml` 里有一道 `Verify bundled assets` 步骤专门检查前两项，缺了直接让构建失败。
源码模式或手工打包时，启动面板也会自查一遍并打印 `[警告] 缺少随包资源: …`，
不必等到浏览器里报错才发现。

## 版本号

`--product-version` / `--file-version` 是 exe 属性里的版本。本地 `build.bat` 里写死的值仅作示意；CI 版本会用 git tag 推导。

另有一个运行时版本文件 `version.json`，由 CI 在打包后写进产物目录：

```json
{
  "version": "1.0.0",
  "tag_name": "v1.0.0"
}
```

`check_update.py` 读它拿到当前版本，再和 GitHub Releases 的最新版比较。源码模式没有这个文件，更新检查会静默跳过。

> **这个文件必须写成不带 BOM 的 UTF-8。** CI 用的是 Windows PowerShell 5.1，
> 它的 `Set-Content -Encoding UTF8` 会加上 BOM，而 Python 侧 `json.load` 会因此抛
> `JSONDecodeError`——历史上真的这么漏过一版：更新检查从此变成哑的，界面上只是
> 永远显示「已是最新版」，一点报错都没有。现在写入用
> `[IO.File]::WriteAllText(..., UTF8Encoding $false)`，读取用 `utf-8-sig`，
> CI 里还有一条断言专门拦 BOM。

## CI 发布

`.github/workflows/build.yml` 在推送 `v*` 标签时触发，也可以在 Actions 页面手动跑。
它用一个四腿矩阵同时打出四个平台 / 架构的产物：

| 运行器 | 产物 |
|---|---|
| `windows-latest` | `MSTL-v<版本>.zip`（与旧版同名，老链接不会失效） |
| `ubuntu-22.04` | `MSTL-v<版本>-linux-x86_64.tar.gz` |
| `macos-latest`（arm64） | `MSTL-v<版本>-macos-arm64.tar.gz` |
| `macos-15-intel` | `MSTL-v<版本>-macos-x86_64.tar.gz` |

每条腿做的事：

1. checkout 代码，装 Python 3.11 与 nuitka、imageio。Linux 那条腿还要
   `apt-get install patchelf`——**Nuitka 在 Linux 上做 standalone 打包必须有它**，缺了直接报错退出
2. 用 tag 推导版本号，执行上面的 Nuitka 命令。Windows 额外带
   `--windows-console-mode` 与图标参数，其余平台不带；产物名也按平台给，
   Windows 是 `MSTL.exe`，Linux / macOS 是 `MSTL`
3. 在产物目录写入 `version.json`
4. **冒烟测试**：直接跑一次 `./MSTL --help`，退出码非 0 就让这条腿失败——证明打出来的二进制真能启动
5. 校验随包资源（`webui/index.html`、`locales/*.json`），缺了就从源码补拷并给出 `::warning::`
6. 打包：Windows 用 zip，其余平台用 **tar.gz**（zip 不保存可执行位，解压出来跑不了）
7. 上传 artifact

全部腿跑完后，单独的 `release` job 收齐所有 artifact，一次性创建 / 更新 GitHub Release。
它只在 tag 上跑，所以手动触发（`workflow_dispatch`）只产出 artifact、不会发版。

### 两个平台限制

- **macOS 产物没有签名**：签名需要 Apple 开发者证书，CI 里没有。用户首次运行需要
  右键 →「打开」，或 `xattr -d com.apple.quarantine ./MSTL`。
- **Linux 产物的 glibc 下限就是构建机的版本**（Ubuntu 22.04 → glibc 2.35）。Nuitka 的
  standalone 产物**不会**把 glibc 一起打包，想要更宽的兼容面就得用更老的构建机。

所以发版的完整动作还是：改好版本、提交，然后

```bash
git tag v1.1.0
git push origin v1.1.0
```

剩下交给 CI。

## 文档站

文档用 MkDocs Material，配置在仓库根目录的 `mkdocs.yml`，内容在 `docs/`。

```bash
pip install mkdocs-material mkdocs-static-i18n mkdocs-minify-plugin mkdocs-git-revision-date-localized-plugin
mkdocs serve          # 本地预览，默认 http://127.0.0.1:8000
mkdocs build --strict # 严格模式构建，警告即失败，提交前建议跑一次
```

中文是默认语言，英文版放在 `docs/en/` 下。未翻译的页面会回落到默认语言，因此可以先把中文写完再补英文。

!!! warning "中文标题的锚点"

    默认的 slugify 会把中文字符整段剥掉：`## 通用实例` 生成的 id 是 `_8` 这种按出现顺序排的串，别的页面写 `#通用实例` 是链不过去的（mkdocs 只打一条 INFO，`--strict` 也不会失败，所以坏链很容易漏掉）。

    需要跨页引用某个中文标题时，用 `attr_list` 给它挂一个显式锚点，锚点用 ASCII：

    ```markdown
    ## 通用实例 {#generic-instances}
    ```

    链接写成 `[通用实例](../features/web-panel.md#generic-instances)`。英文标题不受影响，`## Generic instances` 天然就是 `generic-instances`。
