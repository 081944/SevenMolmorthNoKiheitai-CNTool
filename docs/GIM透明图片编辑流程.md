# GIM 透明图片的清晰编辑与反写

## 日常操作

直接修改 `work/imageedit/gim/` 下原路径的 PNG，改完照常运行：

```powershell
.\build_translation_with_images.bat
```

`image_7w.py` 的 GIM 反写步骤会自动识别清晰编辑模式，在内存中还原透明度，
再编码进 GIM。不需要手动执行 `apply` 或 `apply --dry-run`，也不用换目录。
反写不会把编辑中的清晰 PNG 改回半透明；后续可以继续改同一个文件。

本次 `work/imageedit/gim/seven_data_link/takayama/tex/` 下 9 张 PNG 已转换为
清晰编辑形式，包括用户授权覆盖的中文 `endlessmonsgim.arc__gim7.png`。
转换以当前 PNG 为基础，RGB 和中文内容不变，只拉伸 Alpha。其他目录的改图
保留原样。

编辑时保持尺寸和透明背景，不要将查看用的棋盘格或底色合并到成稿。

## 为以后增加的 GIM 启用清晰编辑

在项目根目录执行一次：

```powershell
python tools\image_alpha_7w.py prepare --filter takayama/tex
```

这会为尚未处理的 GIM 保存原图快照，然后覆盖原路径的 PNG 为清晰版本。
再次执行不会覆盖已经启用清晰模式的 PNG，因此不会丢失后续修改。

默认跳过与 `work/image/` 原导出像素不同的图片；缺少对应导出时也跳过。
如果明确要把已经改成中文的图片也变清晰，使用具体路径过滤并加上
`--include-edited`，工具会以当前中文 PNG 为基础保存和转换，不会重画内容。

## 原理与保留数据

`takayama/tex` 的 8bpp GIM 原始 Alpha 上限为 128，普通 PNG 软件以 255 为完全
不透明，因此显示得很淡。清晰模式将最大 Alpha 拉伸为 255，保留中间透明度
梯度、RGB、尺寸和位置。这只是编辑显示换算，不改变 GIM 的原导出规则。

`work/imagealpha/.state/` 保存原始 PNG 快照和清晰模式标记，**需要随改图保留**。
该目录已加入版本控制放行规则；它是反写所需数据，不是可删除的临时缓存。
`work/imagealpha/gim/` 下早期生成的副本不再用于本次日常编辑。

反写时将当前 PNG 与原图的清晰形式逐像素比较：

- 没有修改的像素直接保留原始 RGBA，避免透明度放大、缩小引入累积误差。
- 修改过的索引色像素同时按 RGB 和 Alpha 匹配原调色板，避免单色蒙版和
  抗锯齿边缘因仅比较 RGB 而选择错误的透明度。
- 原有中文图以转换前的中文 PNG 为快照，未修改内容直接沿用。
- 换算只在内存中进行，原路径的 PNG 持续保持清晰，支持重复打包和继续改图。
- 未启用此模式的图片继续使用原来的反写行为。

GIM 的 8bpp/4bpp 仍受 256/16 色调色板限制，新颜色会量化。32bpp 仍使用原
编码器保留原位置 Alpha 的规则。ITE、视频字幕等格式没有自动应用此换算。

## 验证

回归测试包含 Alpha 梯度、新像素和擦除、尺寸校验、已有改图保护、原路径
直接编辑、重复打包后继续编辑，以及可选副本流程的冲突保护。

```powershell
python -m unittest discover -s tools/tests -p test_image_alpha_7w.py -v
```

2026-11-05 已在隔离目录验证该目录的 9 张 PNG：直接通过原有 GIM 反写函数
编码，结果与转换前 PNG 通过旧编码器得到的结果一致，中文内容和容器尺寸
保持。重复反写不改动清晰 PNG。本次未构建 ISO，未进行游戏内显示验证。

## 可选的分离副本模式

仅当需要单独的编辑目录时，才使用 `prepare --copies-only`，这类副本需要
`apply` 合并。`apply --dry-run` 只是可选预览。当前已启用原路径编辑的图片
会被 `apply` 跳过，日常操作无需使用这两个命令。
