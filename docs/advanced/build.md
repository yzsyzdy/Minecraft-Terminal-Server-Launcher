# 从源码构建

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
  --product-version="1.1.36" ^
  --file-version="1.1.36" ^
  --file-description="Minecraft Terminal Server Launcher" ^
  --copyright="MSTL" ^
  --include-data-dir=locales=locales ^
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
| `--output-dir=build` | 输出到 `build/` |

产物在 `build/start.dist/MSTL.exe`。整个 `start.dist/` 目录需要一起分发，不能只给 exe。

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
