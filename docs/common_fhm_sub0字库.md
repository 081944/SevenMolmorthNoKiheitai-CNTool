# 7W common.fhm sub0 字库解析结论（对白渲染字库）

本文档记录《Seven Warriors》PS2 对白渲染实际使用的字库——内嵌在
`common.fhm` 容器中的第一个 SFO 子资源（下称 **sub0**）的实测结论。

> ⚠️ 这是**重要更正**：`docs/font16.sfo-7w.md` 记录的是 `arita/font16.sfo`
> （16x16），但**对白/剧情文本渲染不使用它**。游戏把对话文本的字形从
> `chiibaya/common/common.fhm` 的 sub0 字库读取。实测：把该对白"还没结束啊，
> 你们这帮见习的！"的中文字形写入 sub0 对应槽位后，游戏内显示正常。

## 文件

```text
unpacked/CDIMAGE_FULL/seven_data_link/chiibaya/common/common.fhm
```

- 总大小：`0x100530` = 1,049,904 字节
- `common.fhm` 是多子资源容器，首个子资源 `SFO\0` 位于文件偏移 `0x20`
- sub0 头部：`53 46 4F 00 16 00 00 00 EE 0E 00 00 01 00 00 00`
  - `u32@0x08` = `0xEEE` = 3822（索引项数）
  - `u32@0x0C` = 1（与 aria font16.sfo 的 0 不同，可能是字号/版本标志）

## 索引结构（与 aria font16.sfo 同源）

sub0 与 `font16.sfo` 共享同一套 SFO 骨架：

```text
lead-byte lookup 表: 偏移 0x20 + 0x10        (256 个 little-endian u16)
(A, B) 索引表:       偏移 0x20 + 0x210       (3822 项，每项 4 字节)
```

- `A` 低字节 = 该槽位字形的 CP932 lead byte；`A` 高字节 = `0x16`
  （aria 是 `0x10`，故 sub0 是更高分辨率的字库）
- 字形索引公式（与 font16.sfo 完全一致）：

```text
index = lead_lookup[lead] + trail        # trail 为 CP932 低字节原值
```

- `A=0xFFFF`（实际 `0x00FF`）的项是**占位项**（0x7F 空位），无字形数据、不可写入

## ⚠️ 字形定位公式与 aria 不同：B 的单位是 0x20

aria font16.sfo 用 `file_offset = B * 0x10 + 0x08`，但 **sub0 的 B 单位是
0x20**：

```text
字形数据偏移 = B * 0x20 + 0x20          （相对 common.fhm 文件头）
```

验证：`maxB = 0x7E00`，`0x7E00 * 0x20 + 0x20 = 0xFC020`，恰好覆盖到 sub0 的
数据区末尾（`0xFC140`），与 aria 的"maxB × 单位 ≈ 文件末尾"同构。

- 双字节字形相邻 `B` 差 = **9** → 每字形 **288 字节**（`9 * 0x20`）
- 变宽半角区相邻 `B` 差为 5/6/7（大小不等），位于索引 0~94

## 字形像素格式：24x24 4bpp

```text
每字形 288 字节 = 24 行 × 12 字节
每行 12 字节 = 24 像素 4bpp
每字节低 nibble = 左侧像素，高 nibble = 右侧像素   （与 GIM 4bpp 惯例一致）
```

已通过渲染核验的锚点（sub0 索引）：

| 索引 | CP932 | 字形 | 状态 |
|---|---|---|---|
| 96 | 0x8141 | 、 | ✓ |
| 284 | 0x824F | ０ | ✓ |
| 534 | 0x889F | 亜 | ✓ 用户确认（汉字区起点） |
| 907 | 0x8A9A | 噛 | ✓ 用户确认（对白中"还"误显示为日文） |

## 汉字区（0x88 区起）

各 lead byte 的 lookup base（索引 = base + trail）：

```text
0x81 -> 0x001F    0x88 -> 0x0177    0x89 -> 0x0234    0x8A -> 0x02F1
0x8B -> 0x03AE    0x8C -> 0x046B    0x8D -> 0x0528    0x8E -> 0x05E5
0x8F -> 0x06A2    0x90 -> 0x075F    0x91 -> 0x081C    0x92 -> 0x08D9
0x93 -> 0x0996    0x94 -> 0x0A53    0x95 -> 0x0B10    0x96 -> 0x0BCD
0x97 -> 0x0C8A    0x98 -> 0x0D47
```

## 工具

`tools/font_sub0_7w.py`（24x24 sub0 字库专用工具）：

```powershell
# 检查结构 / 锚点
python tools\font_sub0_7w.py inspect unpacked\CDIMAGE_FULL\seven_data_link\chiibaya\common\common.fhm

# 导出单个字形为 PNG（核验用）
python tools\font_sub0_7w.py export unpacked\CDIMAGE_FULL\seven_data_link\chiibaya\common\common.fhm --idx 907 --out temp\g.png

# 按映射表写入中文字形（TTF 渲染 24x24 4bpp）
python tools\font_sub0_7w.py write unpacked\CDIMAGE_FULL\seven_data_link\chiibaya\common\common.fhm work\font\common_sub0_ch.fhm --map work\font\sub0_map.tsv
```

### 双字库兼容写入（sub0 + font16.sfo 同时）

部分文本（非对白 UI/菜单）可能渲染自 `arita/font16.sfo`。工具支持按同一份
映射表**同时**写入两个字库（各自用各自的 lookup/定位公式）：

```powershell
python tools\font_sub0_7w.py write `
  unpacked\CDIMAGE_FULL\seven_data_link\chiibaya\common\common.fhm `
  work\font\common_both_ch.fhm --map work\font\sub0_map.tsv `
  --target both --font16 unpacked\CDIMAGE_FULL\seven_data_link\arita\font16.sfo
```

输出两个文件：`common_both_ch.fhm`（sub0 24x24）+ `common_both_ch_font16.sfo`（16x16）。
也可单独写 font16：`--target font16`（此时 `fhm` 位置传 font16.sfo）。

### inspect / export 也支持 --target

```powershell
python tools\font_sub0_7w.py inspect unpacked\CDIMAGE_FULL\seven_data_link\arita\font16.sfo --target font16
python tools\font_sub0_7w.py export unpacked\CDIMAGE_FULL\seven_data_link\arita\font16.sfo --idx 910 --out temp\f16.png --target font16
```

锚点对照（同一 CP932 在两套字库的索引不同）：

| CP932 | sub0 索引 | font16 索引 |
|---|---|---|
| 0x889F（亜） | 534 | 537 |
| 0x8A9A（噛） | 907 | 910 |
| 0x8141（、） | 96 | 97 |
| 0x824F（０） | 284 | 285 |

映射表 `work/font/sub0_map.tsv` 格式（UTF-8，每行一字符）：

```text
还	8A9A
没	8995
```

## 中文字形写入

- 字形用系统 TTF（默认 msyh/simhei 等）渲染为 24x24 灰度，量化到 4bpp
  （`(v+8)//17`），每行 2 像素打包（低 nibble = 左像素）。
- 写入只覆盖目标槽位 288 字节，**不改变文件大小**，`common.fhm` 总量保持
  `0x100530`。
- 写入后回读渲染校验通过，游戏内实测显示正常。

## 相关产物

```text
temp/common_sub0_test/common_test.fhm    测试写入副本
work/font/sub0_map.tsv                   汉字→CP932 映射表
work/font/common_sub0_ch.fhm             （待生成）中文写入产物
unpacked/.../chiibaya/common/common.fhm  已被中文字形覆盖的正式字库
```

## 当前结论

```text
sub0 SFO 头部 / lookup / (A,B) 索引: 已确认（与 font16.sfo 同源）
B 单位 0x20 而非 0x10:                  已确认（maxB 撑满 sub0）
字形 288 字节 = 24x24 4bpp:            已确认（低 nibble=左像素）
汉字区映射（lookup+trail）:             已确认
中文字形回写:                           已确认并实测通过
对白渲染使用 sub0 而非 font16.sfo:      已确认（显示正常）
双字库兼容写入（sub0 + font16）:        已实现（--target both，实测通过）
```

## 遗留事项

- 半角区（索引 0~94）变宽字形格式未完全解析，本次汉化未涉及。
- `common.fhm` 中 sub1（`0xFC140` 起，count=84）为 ASCII 变宽小字库，sub2
  （`0xFD8E0`）为文本表——中文对白未用到，但回写文本子资源时须避开字库区。
- `font16.sfo` 变宽半角区（索引 0~96）同样未完全解析；汉化只使用其双字节区。
