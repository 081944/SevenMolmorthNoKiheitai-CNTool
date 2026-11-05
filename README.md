# 七勇士 PS2 简中汉化工具链

PS2 游戏《Seven: Molmorth no Kiheitai》（七勇士）的简体中文汉化构建工程。
仓库只放**脚本**（`tools/`）、**文档**（`docs/`）和**人工维护内容**（`work/`：译稿、改图），
不含游戏原始数据、商业字体和成品 ISO。

## 快速开始

```bat
pip install pillow
build_translation.bat
```

`build_translation.bat` 依次完成：校验原版镜像（2718 个条目）→ 解包出日文基准 → 统计汉字并
分配 CP932 槽位 → 渲染字库 → 回写文本 → 重打包 CDIMAGE.BIN → 调用 mkps2iso 出 ISO。
产物为 `mkps2iso-1.1.1-win64\Seven.iso`。

需要连改图一起回写时用 `build_translation_with_images.bat`，它额外要求
`work\imageedit\manifest.csv` 与改好的 PNG。

GIM 图片太透明、不便改字时，可以用 `python tools\image_alpha_7w.py prepare`
将待改 PNG 转为清晰编辑形式。之后直接编辑 `work/imageedit/` 下的原路径 PNG，
照常运行带图片构建脚本，反写程序会自动恢复透明度，不需要手动执行 `apply`。
操作方法见 [GIM 透明图片编辑流程](docs/GIM透明图片编辑流程.md)。

## 流程

```text
original\CDIMAGE.BIN（原版）
   └─ unpack → temp\original_build_tree（日文基准，回写前恢复用）
        ├─ collect / alloc  统计汉字、追加式分配槽位（不动旧存档编码）
        ├─ write-font       work\font\chinese.ttf 渲染字形 → common.fhm sub0 + font16.sfo
        ├─ write            spl / nht / fhm / fhn 按槽位回写
        ├─ write-movie      MOVIMAGE.BIN 字幕重写
        └─ repack → pack    CDIMAGE.BIN → mkps2iso → Seven.iso
```

## 需要自备（仓库不提供）

| 文件 | 用途 |
| --- | --- |
| `original\CDIMAGE.BIN` | 原版日文镜像，构建的唯一输入 |
| `work\font\chinese.ttf` | 渲染简体字形的源字体，商业字体需自行取得授权 |
| `mkps2iso-1.1.1-win64\mkps2iso.exe` 与 `Seven\` | 第三方 ISO 打包器及其工程目录 |
| `unpacked\CDIMAGE_FULL` | 解包树，首次可自行生成（见下） |

解包树缺失时先在项目根执行一次：

```bat
python tools\unpack_cdimage.py unpack original\CDIMAGE.BIN unpacked\CDIMAGE_FULL
```

字体属授权材料，**请勿提交进版本库**。`work/font/` 不在 `.gitignore` 的放行名单里，
代码也不会回退到系统字体——指定的字体找不到会直接报错并列出尝试过的路径。

## 目录

```text
tools\     构建与逆向脚本（文本、字库、图片、影片、打包）
docs\      格式分析与操作说明
work\      译稿（translation）、改图（imageedit）、字库工作区（font，不入库）
unpacked\  解包树（不入库）
original\  原版镜像（不入库）
```

## 文档

- [工具与产物目录规范](docs/工具与产物目录规范.md) — 目录约定、各脚本用途、完整命令
- [SPL 格式与重建](docs/SPL格式与重建说明.md) / [NHT](docs/NHT格式与重建说明.md) / [FHM](docs/FHM格式与重建说明.md)
- [ITE 图片编解码](docs/ITE图片格式与编解码说明.md) / [GIM 逆向笔记](docs/GIM图片格式逆向笔记.md)
- [MOVIMAGE 字幕格式](docs/MOVIMAGE影片字幕格式与解析说明.md)
- [7W 文本图片字库分类](docs/7W文本图片字库分类说明.md) — 资源全貌与定位

## 注意

- `build_all.py` 的命令行相对路径以**项目根**为基准，可从任意目录调用；直接运行
  `tools\` 下的其它脚本时请在项目根执行。
- 仅供个人学习与汉化技术交流。请自持正版，勿分发游戏原始数据、商业字体或成品 ISO。
