# 面板图标来源与许可

`webui/icons/` 下的图标是用 **[AppIcon Forge](https://github.com/zhangyu1818/appicon-forge)**
（作者 [zhangyu1818](https://github.com/zhangyu1818)，MIT License）生成的：底层图形来自
[Iconify](https://iconify.design/) 的公开图标集，由 AppIcon Forge 套上圆角底板导出为 1024×1024。
图标集本身的许可按下表执行，**图标集许可证优先于生成工具的许可证**。

| 文件 | 面板里的用途 | 图标集 | 许可 |
|---|---|---|---|
| `settings-apache2.0.png` | 软件设置 / 实例设置 / AI 设置 | Google Material Icons（`ic`） | Apache License 2.0 |
| `yaml-mit.png` | `.yml` / `.yaml` | Fluent UI System Icons（`fluent`） | MIT |
| `json-own.png` | `.json` | 作者自绘 | 随本项目 GPL-3.0 分发 |
| `java-apache2.0.png` | `.jar`、实例详情里的 Java | Remix Icon（`ri`） | Apache License 2.0 |
| `photo-apache2.0.png` | 图片文件 | Material Symbols（`material-symbols`） | Apache License 2.0 |
| `file-apache2.0.png` | 没有专属图标的普通文件（默认） | Material Design Icons（`mdi`） | Apache License 2.0 |

原始文件名里的 `apache2.0` / `mit` 后缀就是各图标集的许可，本表与之一致。

## 生成工具

- **AppIcon Forge** — <https://github.com/zhangyu1818/appicon-forge> — MIT License
  只做「把图标套进圆角底板并导出 PNG」，仓库本身不附带图标版权。

## 图标集

| 前缀 | 项目 | 许可证 |
|---|---|---|
| `ic` | Google Material Icons — <https://github.com/google/material-design-icons> | Apache 2.0 |
| `material-symbols` | Material Symbols — <https://github.com/google/material-design-icons> | Apache 2.0 |
| `mdi` | Material Design Icons — <https://github.com/Templarian/MaterialDesign> | Apache 2.0 |
| `ri` | Remix Icon — <https://github.com/Remix-Design/RemixIcon> | Apache 2.0 |
| `fluent` | Fluent UI System Icons — <https://github.com/microsoft/fluentui-system-icons> | MIT |

Apache 2.0 要求保留版权与许可声明：以上图标的使用遵循 Apache License 2.0
（<https://www.apache.org/licenses/LICENSE-2.0>），版权归各上游项目所有。
MIT 图标遵循 MIT License，版权归 Microsoft 所有，随附许可声明见上游仓库。

## `*-mask.png` 是怎么来的

原图是**纯黑白**的应用图标（浅色底板 + 深色图形）。面板有深浅两套主题，直接贴原图的话，
暗色主题下会是一块刺眼的浅色方块。因此构建时把原图转成**单色蒙版**：按亮度取 alpha、
裁掉圆角底板、统一缩放到 56×56 居中；界面用 CSS `mask` + `currentColor` 着色——
亮色主题显示为黑，暗色主题自动变白（等价于「颜色反转」），不必维护两套图。

蒙版是对原图的程序化衍生（只保留图形轮廓，不新增版权内容）；原图一并保留在同目录，
便于追溯、替换或按需重新导出。
