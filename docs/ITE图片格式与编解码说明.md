# 7W ITE 图片格式与编解码说明

本文说明《Seven Warriors》（SLPS-25025）中 ITE 图片的容器结构、tile 压缩格式、
RGBA 解码过程、PNG 重编码过程，以及 ITE 嵌入 FHN 时的安全写回规则。

对应实现：

- `tools/ite_7w.py`：ITE/FHN 解析、PNG 提取、重建和写回
- `tools/ite_mpeg2.py`：PS2 IPU IDEC/MPEG-2 位流解析与重组

## 1. 格式层级

ITE 可能是独立 `.ite` 文件，也可能作为一个资源条目存放在 `.fhn` 中：

```text
FHN
├── 普通资源
├── ITE 0
├── ITE 1
└── ...

ITE
├── 图片宽高
├── tile 偏移/类型表
└── 64x32 tile 压缩流
    ├── color plane
    └── alpha mask plane（仅 type 0）
```

解码后的逻辑图片是 RGBA；ITE 内部并不直接存放线性 RGBA 像素，而是按
`64x32` tile 切分后存储 PS2 IPU IDEC/MPEG-2 intra 裸流。

## 2. FHN 资源容器

FHN 是通用资源容器，不是所有资源都是 ITE。

### 2.1 文件头

所有整数均为 little-endian：

```text
偏移                类型          含义
0x00                u32           资源条目数 N
0x04                N * u32       每个资源在 FHN 内的绝对偏移
offset[0] ...                    资源数据
```

FHN 没有单独的资源长度表：

```text
size[i] = offset[i + 1] - offset[i]
size[last] = file_size - offset[last]
```

某条资源以 `49 54 45 00`（`ITE\0`）开头时，该资源才是 ITE。

### 2.2 工具中的 ITE 索引

`fhn-info`、`fhn-extract` 和 `fhn-write` 使用的是“内嵌 ITE 索引”，即过滤掉
非 ITE 资源后的连续编号，不等于 FHN 原始资源索引。

例如 `name.fhn` 有 14 个资源：第 0 个不是 ITE，后面 13 个是 ITE。工具显示的
ITE `0..12` 对应 FHN 原始资源 `1..13`。

### 2.3 FHN 写回约束

对非末尾资源，后续资源偏移不能被移动：

- 新 ITE 小于原 slot：写入后补零至原 slot 长度。
- 新 ITE 等于原 slot：直接替换。
- 新 ITE 大于原 slot：拒绝写入，防止覆盖下一个资源。
- ITE 确实是 FHN 最后一个资源：允许 FHN 文件随之变大或变小。

工具判断的是“是否位于整个 FHN 文件末尾”，不是“是否为最后一个 ITE”。因为
最后一个 ITE 后面仍可能存在非 ITE 资源。

## 3. ITE 容器结构

### 3.1 文件头

```text
偏移                类型          含义
0x00                char[4]       `ITE\0`
0x04                u32           图片宽度 width
0x08                u32           图片高度 height
0x0C                u32           reserved，重建时原值保留
0x10                u32[]         tile 偏移/类型表
```

tile 网格固定按 `64x32` 计算：

```text
tiles_x = ceil(width / 64)
tiles_y = ceil(height / 32)
tile_count = tiles_x * tiles_y
```

tile 采用行优先顺序：

```text
tile_index = tile_y * tiles_x + tile_x
```

右侧或底部不足 `64x32` 的边缘 tile 仍按完整 tile 编码；合成最终图片后再裁剪到
ITE 声明的 `width x height`。

### 3.2 tile 表项

每条表项是一个 `u32`：高 2 位为类型，低 30 位为 ITE 内绝对偏移。

```text
raw_offset = table[index]
kind       = raw_offset >> 30
offset     = raw_offset & 0x3FFFFFFF
```

tile 结束位置由下一个更大的 tile 数据偏移决定；最后一个 tile 延伸到 ITE 资源
末尾。多个表项允许指向同一数据块，因此不能简单假定偏移严格递增。

已在当前资源库确认的类型：

| 类型 | 宏块数 | 内容 | 输出 alpha |
| --- | ---: | --- | --- |
| type 0 | 16 | 8 个颜色宏块 + 8 个 mask 宏块 | mask `0..127` 归一化为 PNG `0..255` |
| type 2 | 8 | 只有颜色宏块 | 固定 255 |

尚未在样本中确认 type 1 和 type 3，工具不会把它们视为已支持格式。

### 3.3 共享透明空白 tile

部分 ITE 用一个 16 字节全零块代表透明空白，并让多个 tile 表项共享该偏移。这类
数据没有 MPEG-2 结束码，必须在进入 IDEC 解码器前识别：

```text
tile_data 全为 0 -> 输出 64x32 的 RGBA(0, 0, 0, 0)
```

`enemy/tt_m01.ite` 已验证包含这种共享空白 tile。

## 4. tile 的 IDEC/MPEG-2 数据

### 4.1 格式识别

非空 tile 是 PS2 IPU IDEC 使用的无标准序列头 MPEG-2 I-picture 裸流。它不是
GS T4 调色板纹理，也不是以 `49 f?` 为头的自定义压缩格式。

已确认的编码模式与 `IPU_CTRL=0x01600000` 一致：

- MPEG-2 intra picture
- 8-bit intra DC precision
- nonlinear quantizer scale
- alternate intra VLC（MPEG-2 Table B-15）
- normal scan（非 alternate scan）
- 连续 intra macroblock

每个 tile 流以以下字节结束，并补零到 16 字节边界：

```text
00 00 01 B0
```

`49 F0`、`49 F1`、`49 F2`、`49 F3` 等常见开头是位流前几个字段组合后的结果，
不是 tile magic 或自定义类型字节。

### 4.2 颜色与 mask 宏块布局

一个 `64x32` 的 YUV420P 颜色平面包含 `4x2 = 8` 个 `16x16` MPEG-2 宏块。

type 2 只保存这 8 个颜色宏块。type 0 把 8 个颜色宏块和 8 个灰度 mask 宏块
交错在同一个 IDEC 流中：

```text
左 32x32 区域：4 color MB + 4 mask MB
右 32x32 区域：4 color MB + 4 mask MB
```

在 16 个源宏块中的选择关系：

```text
color: 0, 1, 2, 3, 8, 9, 10, 11
mask:  4, 5, 6, 7, 12, 13, 14, 15
```

每个平面内部不是普通的逐行宏块顺序，而是按每列的上下两个宏块成对保存：

```text
(x=0,y=0), (x=0,y=1),
(x=1,y=0), (x=1,y=1),
(x=2,y=0), (x=2,y=1),
(x=3,y=0), (x=3,y=1)
```

### 4.3 为什么不能直接交给 FFmpeg

原始 IDEC 没有 MPEG-2 sequence/picture header，而且一条连续码流跨越所有宏块，
DC predictor 也按 Sony/PS2 的布局连续累积。标准 FFmpeg 解码器不能直接把它当作
普通 `.m2v` 解码。

解码器执行以下转换：

1. 位级解析 macroblock、DC VLC 和 alternate-intra AC VLC。
2. 按 color/mask 索引分别取出 8 个宏块。
3. 将每个宏块包装成独立标准 slice。
4. 把每个 slice 的 DC predictor 重置为 128，并重写 DC differential。
5. 添加 `64x32` MPEG-2 sequence、picture 和 coding extension 头。
6. 交给 FFmpeg 输出 `64x32 RGBA`。

颜色平面提供 RGB。type 0 的 mask 平面按灰度图解码。GS 混合使用 7-bit 系数，
`0x7F/0x80` 表示不透明；工具会把 mask R 从 `0..127` 归一化为 PNG 的 `0..255`
alpha。type 2 直接使用 alpha 255。

## 5. ITE 解包为 PNG

完整算法如下：

```text
读取 ITE 头
  -> 计算 tile_count
  -> 解析每个 tile 的 kind/offset/end
  -> 对每个 tile：
       全零块 -> 透明 RGBA
       type 0 -> 解码 color + mask -> 合成 RGBA
       type 2 -> 解码 color -> alpha=255
  -> 按 tile 坐标粘贴到完整画布
  -> 裁剪为 width x height
  -> 保存 PNG
```

独立 ITE：

```powershell
python tools\ite_7w.py info input.ite
python tools\ite_7w.py extract input.ite output.png
```

FHN 内嵌 ITE：

```powershell
python tools\ite_7w.py fhn-info input.fhn
python tools\ite_7w.py fhn-extract input.fhn <ITE索引> output.png
```

## 6. PNG 重编码为 ITE

输入 PNG 的尺寸必须与原 ITE 完全一致。原 ITE 用来提供宽、高和 reserved 字段。

重建过程：

1. 将 PNG 转为 RGBA。
2. 按 `64x32` 切 tile，边缘不足部分补透明像素。
3. 颜色平面以 RGBA 输入 FFmpeg，转换为 YUV420P MPEG-2 intra。
4. PNG alpha 从 `0..255` 映射为 GS 的 `0..127` 系数，复制成灰度 RGBA mask，
   再编码为第二个 MPEG-2 intra picture。
5. 解析 FFmpeg 生成的两个标准 picture，提取各自 8 个宏块。
6. 按 ITE 的 color/mask 顺序交错宏块，重写连续 DC differential。
7. 追加 `00 00 01 B0`，补零到 16 字节边界。
8. 重建 ITE tile 表和数据区。

FFmpeg 的关键编码参数：

```text
codec              mpeg2video
pixel format       yuv420p
GOP                 1
B frames            0
threads             1
intra_vlc           1
non_linear_quant    1
alternate_scan      0
```

默认 quantizer 为 4，可通过 `--quantizer 1..28` 调整。数值越低通常画质越高，
码流也越大。批量回写时，固定槽位的 FHN 图片若以 `q=4` 重建后超出槽位，
会自动按 `q=5..10` 逐级降低质量重试；全部失败才跳过该图片。

独立 ITE 重建：

```powershell
python tools\ite_7w.py write `
  original.ite edited.png rebuilt.ite

python tools\ite_7w.py verify rebuilt.ite
```

FHN 内嵌 ITE 写回：

```powershell
python tools\ite_7w.py fhn-write `
  original.fhn <ITE索引> edited.png rebuilt.fhn
```

工具始终写到新的输出文件，不会原地覆盖输入文件。

## 7. 当前编码器策略和限制

### 7.1 重建 tile 统一输出 type 0

当前 `rebuild_ite` 为所有 tile 编码 color + mask，并将表项类型写为 type 0。它
不会尝试判断某 tile 是否完全不透明后改用 type 2，也不会复用全零 tile。

原始“ネーミング”ITE 有 10 个 type 0 和 2 个 type 2；重建版本有 12 个 type 0。
重建文件能被本工具完整反解，但这种类型归一化仍应以 PCSX2 游戏实机加载作为最终
兼容性验证。对目标图而言 alpha 信息在重建结果中完整保留。

### 7.2 MPEG-2 有损

即使 PNG 没有修改，解码后再编码也不会生成与原 ITE 相同的字节或完全相同的像素。
目标图使用 quantizer 4 的无修改 round-trip 误差：

```text
平均绝对误差 RGBA = 1.56, 1.22, 1.44, 2.05
最大绝对误差 RGBA = 13, 9, 12, 20
```

因此验证应比较可接受的视觉误差，而不是要求新旧 ITE SHA-256 相等。

### 7.3 已验证范围

- 37 个独立 ITE 均能解析容器，共 3212 个 tile。
- 已观察到 type 0 和 type 2。
- `name.fhn` 目标图完成提取、重建、FHN 写回和反提取闭环。
- `enemy/tt_m01.ite` 的 80 个 tile 全部解码通过，覆盖 type 0、type 2 和共享
  透明空白块。
- 库内 88 个 FHN 的资源偏移表均通过检查，共识别 477 个内嵌 ITE。
- PCSX2 中加载修改后 FHN 的最终运行验证尚未完成。

## 8. “ネーミング”完整示例
原资源：

```text
FHN      unpacked/CDIMAGE_FULL/seven_data_link/takeshi/screen/name/name.fhn
ITE索引  12
偏移     0x225C0
slot     0x17A0（6048 bytes）
尺寸     200x68
```

提取：

```powershell
python tools\ite_7w.py fhn-extract `
  unpacked\CDIMAGE_FULL\seven_data_link\takeshi\screen\name\name.fhn `
  12 work\image\ite\name\name_original.png
```

修改 PNG 后写回新的 FHN：

```powershell
python tools\ite_7w.py fhn-write `
  unpacked\CDIMAGE_FULL\seven_data_link\takeshi\screen\name\name.fhn `
  12 work\imageedit\ite\name\name_edited_red.png `
  work\imageedit\ite\name\name_edited_red.fhn
```

反向提取验证：

```powershell
python tools\ite_7w.py fhn-extract `
  work\imageedit\ite\name\name_edited_red.fhn `
  12 work\imageedit\ite\name\name_edited_red_roundtrip.png
```

原始导出产物位于 `work/image/ite/name/`，编辑及回写输入位于
`work/imageedit/ite/name/`。

## 9. 批量导出与筛选回写（汉化主流程）

手工逐张 `fhn-extract`/`fhn-write` 只适合单图验证。正式汉化使用统一工具
`tools/image_7w.py`（scan → export → 人工筛选编辑 → write-back）：

```powershell
python tools\image_7w.py scan --unpacked unpacked\CDIMAGE_FULL
python tools\image_7w.py export --unpacked unpacked\CDIMAGE_FULL
# 人工删除无文字的 PNG、编辑要汉化的 PNG
python tools\image_7w.py write-back --unpacked unpacked\CDIMAGE_FULL
```

命名、规则、命令细节见 `docs/工具与产物目录规范.md` 的"图片导出回写规范"一
节。要点：

- 导出时镜像目录结构到 `work/image/ite/<相对路径>/`；回写前将编辑后的 PNG
  放入 `work/imageedit/ite/<相对路径>/`，FHN 内嵌用
  `<stem>__ite<NNN>.png`（NNN 为内嵌 ITE 序号）。
- write-back 只处理**仍存在**的 PNG；删除 PNG = 该图保持原版。
- 尺寸守卫：PNG 尺寸必须等于原 ITE，否则拒写。
- 依赖 FFmpeg：`ite_7w.py`/`image_7w.py` 会自动查找 `imageio-ffmpeg` 包自带
  的 ffmpeg（`pip install imageio-ffmpeg`），也可 `--ffmpeg` 指定。
