# 从源码构建

## 跑测试

测试只用标准库 `unittest`，**不需要装任何依赖**（项目本身也是零运行时依赖），
在仓库根目录跑：

```bash
python -m unittest discover -s tests -t . -v
```

CI（`.github/workflows/tests.yml`）在每次 push / PR 上会用 Python 3.11 与 3.13
各跑一遍；`build.yml` 在打包前也会跑一次，免得带着失败的测试发版。

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
  --product-version="1.1.37" ^
  --file-version="1.1.37" ^
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

`.github/workflows/build.yml` 在推送 `v*` 标签时触发，也可以在 Actions 页面手动跑：

1. checkout 代码，装 Python 3.11 与 nuitka、imageio
2. 用 tag 推导版本号（去掉开头的 `v`）执行上面的 Nuitka 命令
3. 在产物目录写入 `version.json`
4. 打包成 `MSTL-<tag>.zip`
5. 上传 artifact，并创建 / 更新对应的 GitHub Release 附上 zip

所以发版的完整动作就是：改好版本、提交，然后

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
