# 面板内置字体

| 文件 | 用途 | 许可 |
|---|---|---|
| `HarmonyOS_Sans_SC_Regular.ttf` | 面板正文（400） | HarmonyOS Sans Fonts License Agreement |
| `HarmonyOS_Sans_SC_Medium.ttf` | 标题与强调（500） | 同上 |
| `LICENSE-HarmonyOS-Sans.txt` | 许可原文 | — |

字体：**HarmonyOS Sans SC**，版权归 Huawei Device Co., Ltd.（Copyright 2021）。
许可允许随软件**原样**嵌入、再分发、免版税使用，但要求：

1. 在软件里显著声明使用了 HarmonyOS Sans Fonts（面板「设置 → 关于」与本文档都写了）；
2. **不得修改字体**——所以这里放的是未经裁剪、未转 WOFF2 的原始 TTF；
3. 不得单独售卖/再分发字体本身；
4. 保留版权声明与许可原文（`LICENSE-HarmonyOS-Sans.txt`）；
5. 许可可撤销，撤销后应停止分发。

只带 Regular 与 Medium 两支：正文 400、强调 500。Light/Thin 在 13px 下偏虚，
Black/Condensed 与面板的层级不匹配，Italic 面板不用（语法高亮的注释斜体由浏览器合成）。

等宽字体不在这里：控制台、代码编辑器与所有 mono 标注继续用系统字体
（Cascadia Code / Consolas），因为 HarmonyOS Sans 没有等宽设计。
