# 7W ITE 图片定位与调试方法

本文复盘如何从 PCSX2 起名界面上的“ネーミング”木牌，定位到解包目录中的具体
FHN/ITE 数据，并说明后续遇到类似“屏幕上看到一张图，但不知道它在哪个资源里”
时应采用的排查流程。

本文只记录已经获得证据支持的结论。修改后的 FHN 已完成离线写回和反提取验证，
但尚未完成 PCSX2 加载修改文件的最终运行验证。

## 1. 本次目标与最终定位

目标是 PCSX2 起名界面顶部显示的蓝色木边木牌，文字为“ネーミング”。

最终定位结果：

| 项目 | 结果 |
| --- | --- |
| FHN | `seven_data_link/takeshi/screen/name/name.fhn` |
| 工具中的 ITE 索引 | `12`（第 13 个内嵌 ITE） |
| FHN 偏移 | `0x225C0` |
| 原 slot 长度 | `0x17A0`（6048 字节） |
| ITE 尺寸 | `200x68` |
| tile 网格 | `4x3`，共 12 tile |
| 原 tile 类型 | 10 个 type 0，2 个 type 2 |
| 目标 ITE SHA-256 | `c8de74695dd2ba7c164c5d6ac616710917131e62620925af68898339e7d53692` |

原始 FHN 当前 SHA-256：

```text
5c12154afe740f903e965f9f648aea745a0e3affb497e6b6518bf7d969681177
```

解码结果：`work/image/ite/name/name_original.png`。

## 2. 定位过程概览

```text
观察界面目标
  -> 根据界面语义筛选解包路径
  -> 发现 takeshi/screen/name/name.fhn
  -> 解析 FHN 资源表并枚举 13 个 ITE
  -> 按尺寸/长宽比筛出 200x68 的 ITE 12
  -> 离线解码，内容与截图逐项匹配
  -> 对比 FHN 原始切片与提取副本 SHA-256
  -> PCSX2/IDA 确认 FHN/ITE 确实被运行时加载
  -> 修改颜色、重编码、写回 FHN、再次提取
```

真正完成定位的是多条证据相互闭合，而不是只依赖文件名、内存地址或某一张 GS
dump。

## 3. 第一步：从界面语义缩小目录

目标来自“起名/命名”界面，屏幕文字是“ネーミング”。解包树中存在明显对应路径：

```text
unpacked/CDIMAGE_FULL/seven_data_link/takeshi/screen/name/name.fhn
```

这一步可以用文件名搜索快速完成：

```powershell
rg --files unpacked\CDIMAGE_FULL | rg "screen[\\/]name|name\.fhn$"
```

路径语义只是候选证据，不能单独证明资源对应关系。同一个界面包通常包含背景、
按钮、说明文字、光标和装饰等多张图，仍需继续解析包内条目。

当路径没有直观英文名时，可扩大搜索范围：

```powershell
rg --files unpacked\CDIMAGE_FULL | rg "\.(ite|fhn)$"
```

并结合界面所属模块、相邻资源命名、文件修改/加载时机进一步筛选。

## 4. 第二步：枚举 FHN 中的 ITE

运行：

```powershell
python tools\ite_7w.py fhn-info `
  unpacked\CDIMAGE_FULL\seven_data_link\takeshi\screen\name\name.fhn
```

`name.fhn` 的第一个 FHN 资源不是 ITE，其后有 13 个 ITE。工具只对这 13 个 ITE
编号。各候选如下：

| ITE 索引 | FHN 偏移 | slot 大小 | 声明尺寸 |
| ---: | ---: | ---: | ---: |
| 0 | `0x00000780` | `0x103D0` | `640x448` |
| 1 | `0x00010B50` | `0x14F0` | `192x24` |
| 2 | `0x00012040` | `0x3AD0` | `336x80` |
| 3 | `0x00015B10` | `0x1DE0` | `156x144` |
| 4 | `0x000178F0` | `0x1A60` | `420x28` |
| 5 | `0x00019350` | `0x14D0` | `304x28` |
| 6 | `0x0001A820` | `0x2FB0` | `452x100` |
| 7 | `0x0001D7D0` | `0x1E90` | `456x96` |
| 8 | `0x0001F660` | `0x0CD0` | `108x56` |
| 9 | `0x00020330` | `0x0C10` | `96x56` |
| 10 | `0x00020F40` | `0x0DC0` | `120x56` |
| 11 | `0x00021D00` | `0x08C0` | `68x56` |
| 12 | `0x000225C0` | `0x17A0` | `200x68` |

目标木牌是横向、约 3:1 的独立 UI 元素。`200x68` 的索引 12 在尺寸和长宽比上
最符合，但这仍只是强候选，需要实际解码确认。

`fhn-info` 会同时显示每个 ITE 的偏移、slot 大小、声明宽高和 tile 数。例如目标
条目输出为：

```text
12 offset=0x000225c0 size=0x17a0 (6048) dimensions=200x68 tiles=12
```

宽高只用于排序候选。游戏可能缩放纹理、裁剪 UV，或者把多张小图拼成最终界面，
不能要求声明尺寸与截图像素严格相同。

## 5. 第三步：确认运行时加载路径

### 5.1 FHN/ITE 内存字节核对

在一次 PCSX2 运行中，`name.fhn` 加载后，目标 ITE 曾出现在 EE 内存地址
`0x01DA25C0`。该地址不是固定地址，只是当次运行的观测值。

从该地址读取的开头为：

```text
49 54 45 00 C8 00 00 00 44 00 00 00 ...
```

即：

```text
magic  = ITE\0
width  = 0xC8 = 200
height = 0x44 = 68
```

内存中的目标 ITE 与 FHN 在 `0x225C0` 处的磁盘数据逐字节一致。这证明：

- 游戏确实加载了该 FHN/ITE。
- FHN 中保存的是运行时所引用的原始 ITE 码流。
- 不能把“内存里仍是压缩数据”误解为游戏没有解码；解码可能由随后调用的 IPU
  路径完成，结果不必覆盖原始资源缓冲区。

磁盘目标段与独立提取副本的 SHA-256 相同：

```text
c8de74695dd2ba7c164c5d6ac616710917131e62620925af68898339e7d53692
```

### 5.2 IDA/DebugServer 中的相关位置

本游戏当前 ELF 分析中观察到：

- `0x00104228`：FHN 加载/处理路径的一部分。
- `0x001044B0`：按资源 magic 分发，能看到 `GIM`/`ITE` 分支。
- `0x00225060` 一带：起名界面布局、裁剪和尺寸计算相关路径。

这些地址只适用于当前 SLPS-25025 ELF 和当前装载基址，后续版本或不同 ELF 不能
直接套用。

在已经进入界面后才给 FHN 加载器下断点，往往不会命中，因为资源已经缓存。正确
做法是冷启动，或回到首次进入 NEW GAME/起名流程之前再断。

对 ITE 数据区下 read watchpoint 时，命中 `0x00225060` 一类布局函数并不等于找到
像素解码器。必须检查实际读取的是 ITE 头部的宽高/坐标，还是 tile 压缩数据。

## 6. 第四步：使用 GS dump 做视觉对照

PCSX2 的 `SaveTexture`、`SaveTransferImages` 和 GS draw dump 对定位有两个价值：

1. 获取运行时最终纹理或传输图，确认目标的实际外观、透明边缘和大致尺寸。
2. 确认进入目标界面后哪些纹理被使用，以及纹理上传/绘制发生的时间窗口。

本次在起名界面观察到 C_32 真彩色纹理输出，内容与蓝色“ネーミング”木牌一致。
这帮助确认目标确实是 RGBA UI 元素，并为离线解码结果提供视觉基准。

但 GS dump 不能单独回答“磁盘上的哪一个文件”：

- GS dump 可能是整页 VRAM，而不是单个资源。
- 游戏可能把多个 ITE 拼到同一纹理页。
- TEX0/TBP 地址是运行时 VRAM 地址，不是 CDIMAGE/FHN 文件偏移。
- 画面经过采样、缩放和混合后，像素可能与源图不完全相同。

因此 GS dump 应用于视觉确认和缩小时间窗口，最终文件映射仍要靠容器解析、内容
解码、内存字节或加载调用链闭合。

## 7. 第五步：识别 ITE 压缩数据

初期容易把 tile 开头的 `49 F0`、`49 F1`、`49 F2`、`49 F3` 当成自定义压缩头，
也容易根据 GS 最终纹理格式误判磁盘数据是 T4/CLUT。

推翻该判断的关键证据：

- tile 数据高熵，大小明显小于未压缩 RGBA。
- 非空 tile 末尾稳定出现 `00 00 01 B0` 并按 16 字节对齐。
- 位流可按 MPEG-2 alternate intra VLC、DC VLC 和连续 intra macroblock 解析。
- 解析 8/16 个宏块后，结束码前只剩正常的 0 至 8 个 padding bits。
- `IPU_CTRL=0x01600000` 对应 MPEG-2、nonlinear quantizer、alternate intra VLC、
  normal scan，与位流特征一致。

随后确定 type 0 中颜色和 mask 的交错顺序，将宏块重新包装为标准 MPEG-2 slice，
FFmpeg 才能输出正确的 `64x32` tile。

## 8. 第六步：用内容而不是文件名完成确认

提取候选：

```powershell
python tools\ite_7w.py fhn-extract `
  unpacked\CDIMAGE_FULL\seven_data_link\takeshi\screen\name\name.fhn `
  12 work\image\ite\name\name_original.png
```

解码结果同时满足：

- 尺寸为 `200x68`。
- 蓝绿色布面、左右木边和透明轮廓与截图一致。
- 日文文字精确为“ネーミング”。
- FHN 源切片与独立 ITE 副本 SHA-256 一致。

到这里可以确认“屏幕目标图 -> name.fhn -> ITE 12”的静态资源映射，而不仅是
“疑似同一张图”。

## 9. 第七步：写回闭环确认

为了让结果肉眼可辨，本次保留文字、木边和 alpha，只把蓝绿色布面改成红色：

```text
work/imageedit/ite/name/name_edited_red.png
```

写回：

```powershell
python tools\ite_7w.py fhn-write `
  unpacked\CDIMAGE_FULL\seven_data_link\takeshi\screen\name\name.fhn `
  12 work\imageedit\ite\name\name_edited_red.png `
  work\imageedit\ite\name\name_edited_red.fhn
```

反提取：

```powershell
python tools\ite_7w.py fhn-extract `
  work\imageedit\ite\name\name_edited_red.fhn `
  12 work\imageedit\ite\name\name_edited_red_roundtrip.png
```

已确认：

- 新 FHN 的资源表和目标前的所有字节保持不变。
- 目标仍位于 `0x225C0`。
- 新 ITE 长度为 `0x16F0`（5872 字节）。
- 新 ITE 的 12 个 tile 均能再次解码。
- 从新 FHN 提取出的图正确显示红色布面，文字、木边和透明轮廓仍存在。

这证明离线的“提取 -> 修改 -> 编码 -> FHN 写回 -> 再提取”闭环成立。

尚未完成的最后一步是：把新 FHN 放入重打包 CDIMAGE，使用 PCSX2 冷启动进入
起名界面，确认游戏端实际接受重建码流。验证时不要载入已经缓存旧 FHN 的存档状态。

### 9.1 本次发现的 GS alpha 约定

首次导出的 PNG 中，左侧和下方主要区域的 alpha 众数是 `127`，而右上两块 type 2
tile 的 alpha 是 `255`。把 PNG 放在白底查看时，type 0 区域因此被按约 50% 透明度
混合，表现为一层白雾。

这不是颜色宏块损坏，而是 GS alpha 标度与 PNG alpha 标度不同：GS 混合使用 7-bit
系数，`0x7F/0x80` 已表示不透明。修复后的工具在导出时将 `0..127` 映射到 PNG
`0..255`，写回时再反向映射。目标图主布面像素的 alpha 因而从旧导出的 `127` 变为
`255`；RGB 不变，白底查看不再发白。

## 10. 后续同类图片的推荐定位流程

### 10.1 第一阶段：静态候选筛选

1. 记录目标出现在哪个界面、文字内容、主色、长宽比和透明特征。
2. 用 `rg --files` 搜索界面名、角色名、模块名和 `.ite/.fhn`。
3. 对候选 FHN 执行 `fhn-info`，确认内嵌 ITE 数量和 slot。
4. 读取候选 ITE 的声明宽高，优先检查长宽比相符者。
5. 提取候选 PNG，以文字、轮廓、装饰和 alpha 逐项比对。

只要离线解码已支持，这一阶段通常不需要 PCSX2 或 IDA。

### 10.2 第二阶段：路径不明确时的运行时确认

当多个候选外观相似，或文件名完全没有语义时：

1. 冷启动到目标界面前，在 FHN 加载器/magic 分发器设置断点。
2. 记录加载的文件缓冲区、ITE 指针、宽高和调用时机。
3. 将 EE 内存中的 ITE 字节与解包树候选做 SHA-256 或逐字节比较。
4. 同时保存 GS texture/transfer/draw，建立运行时视觉基准。
5. 不要用 VRAM TBP 直接推导文件偏移；应通过加载缓冲区或调用链关联。

### 10.3 第三阶段：明显修改验证

1. 先做颜色块、边框等肉眼明显但可逆的修改，不要一开始改细小文字。
2. 写到新的 ITE/FHN，不覆盖原文件。
3. 执行 `verify`，再从新 FHN 反提取 PNG。
4. 检查 FHN 后续资源是否保持不变。
5. 重打包到新的 CDIMAGE，冷启动 PCSX2 验证。
6. 游戏验证通过后，再进行正式汉化图片编辑。

## 11. 常见误区

### 把 GS 最终格式当成磁盘格式

GS 显示 C_32、T4 或某种 TEX0 配置，只说明运行时 VRAM 的采样方式，不代表 FHN
里按相同格式存放。ITE 磁盘 tile 实际是 IPU IDEC/MPEG-2 intra。

### 直接把 GS mask 数值当 PNG alpha

GS 的 `0x7F/0x80` 是不透明系数，不等于 PNG 的 127/255 半透明。若 type 0 区域
在白底图片查看器中发白、而 type 2 区域正常，应首先检查是否遗漏了这一步
`0..127 <-> 0..255` 的归一化。

### 把 `49 f?` 当成自定义 magic

这些字节是 MPEG-2 位字段组合，不是长度或压缩类型头。判断流格式应结合结束码、
宏块语法和 IPU 配置。

### 在资源已缓存后才断 FHN 加载

重新进入界面不一定重新加载 FHN。应冷启动或在首次进入该流程前设置断点。

### 只根据尺寸确认

尺寸和长宽比只能排序候选。最终必须解码并比较文字、轮廓、配色和透明区域。

### 混淆 FHN 资源索引和 ITE 索引

FHN 中含非 ITE 资源，`ite_7w.py fhn-*` 使用过滤后的 ITE 索引。记录结果时应同时
写明 FHN 路径、ITE 索引和绝对偏移。

### 认为最后一个 ITE 一定能变大

最后一个 ITE 后面可能还有非 ITE 资源。只有位于整个 FHN 文件末尾的资源才能改变
slot 长度；工具已经对此做保护。

### 用旧 save state 验证修改

save state 可能包含旧资源缓存或旧 VRAM。写回测试必须优先使用冷启动，并重新走到
资源首次加载点。

## 12. 本次证据强度

| 结论 | 证据 | 状态 |
| --- | --- | --- |
| 目标图在 `name.fhn` | 路径语义 + 200x68 解码内容完全匹配 | 已确认 |
| 目标是 ITE 12 | FHN 偏移表 + `0x225C0` 解码 + SHA-256 | 已确认 |
| ITE 是 IDEC/MPEG-2 intra | 结束码、VLC/宏块完整解析、FFmpeg 重包装解码 | 已确认 |
| RGBA/alpha 布局 | color/mask 分离后按 GS 7-bit alpha 归一化，与截图和 GS texture 对照 | 已确认 |
| FHN 写回结构正确 | 新 FHN 可再次扫描、提取，后续资源保护测试通过 | 已确认 |
| 游戏接受重建 ITE | 尚未用新 CDIMAGE 冷启动验证 | 待确认 |
