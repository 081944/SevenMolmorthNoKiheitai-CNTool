# 7W font16.sfo 字库解析结论

本文档记录 `Seven Warriors` PS2 版字库的实测结论。结论以原始文件和已完成的无损回写测试为准，不直接套用 Venus & Braves 的 `font.sfo` 结构，也不完全采信旧的分类说明文档。

> ⚠️ 注意：`font16.sfo`（aria，16x16）**不是对白渲染字库**。对白/剧情文本
> 渲染使用 `chiibaya/common/common.fhm` 内嵌的 sub0 字库（24x24 4bpp），
> 详见 `docs/common_fhm_sub0字库.md`。`font16.sfo` 为另一套 16x16 字库，
> 格式与本文件记录一致。
>
> 🔧 若某文本可能渲染自 font16.sfo，可对同一映射表**同时写入两个字库**
> （`tools/font_sub0_7w.py write --target both`），这样两套字库的字形都更新，
> 无需关心最终走哪条渲染路径。见 `docs/common_fhm_sub0字库.md` 的"双字库兼容
> 写入"一节。

## 文件

```text
unpacked/CDIMAGE_FULL/seven_data_link/arita/font16.sfo
```

基本信息：

```text
大小: 0x6FE10 = 458256 字节
文件头: 53 46 4F 00 (SFO\0)
索引数量: u32@0x08 = 0xDBE = 3518
```

## 索引结构

索引表从 `0x210` 开始，每项 4 字节：

```text
A: little-endian u16
B: little-endian u16
```

每项的实际数据偏移为：

```text
file_offset = B * 0x10 + 0x08
```

这条公式已经用 `B=0x1331` 验证：

```text
0x1331 * 0x10 + 0x08 = 0x13318
```

`0x13318` 正是 010 Editor 中可以观察到连续字形数据的位置。

`B=0` 的项是分页/分隔标记，不能当作正常字形记录。索引中存在少量特殊项和页间空洞，记录边界应通过后续更大的非零 `B` 推导。

## 字形分类

当前按索引位置分为两类：

### 变宽半角区

索引 `0-96`，共 97 项。

- `A >> 8` 表示半角字形的显示宽度；
- 字形记录大小由相邻 `B` 的差值决定；
- 记录通常为 `0x20` 的整数倍；
- 当前只确认了记录定位和部分数据排列；
- 变宽区域尚未得到可靠的位图导出与回写格式。

当前工具对这部分只能作为实验性数据处理，不能用于正式回写。

### 双字节区

从索引 `97` 附近开始，共 3400 个有效字形记录。

- 每条记录对应一个字形；
- 正常记录大小为 128 字节；
- `A` 的低字节不是字形宽度，双字节字形统一按 16 像素宽处理；
- 每条记录由两个连续的 64 字节区域组成；
- 前 64 字节是字形上半部，后 64 字节是字形下半部；
- 每个半区是 `16x8` 的 4bpp 数据；
- 将两个半区上下拼接后得到完整的 `16x16` 字形。

以 `B=0x1331` 为例：

```text
记录起点: 0x13318
上半部:   0x13318-0x13358
下半部:   0x13358-0x13398
下一记录: 0x13398
```

文件最后一条记录实际剩余 120 字节，末尾 8 字节属于文件尾部填充。提取时可以将该记录补零到 128 字节；回写时只能写回原有的 120 字节，不能扩大文件。

## 位图导出

当前导出工具：

```text
tools/font_sfo_7w.py
```

导出全部字形：

```powershell
python tools\font_sfo_7w.py export `
  unpacked\CDIMAGE_FULL\seven_data_link\arita\font16.sfo `
  work\font7w_all
```

输出内容：

- `entry_0000.png` 到 `entry_3517.png`；
- 双字节字形为 16x16 PNG；
- 半角字形按 `A >> 8` 输出对应宽度、16 高度的 PNG；
- `font_map.tsv` 保存索引、`A`、`B`、原始偏移、记录结束位置、宽度和文件名。

已验证的全量导出结果：

```text
PNG 数量: 3497
双字节字形: 3402
半角/变宽字形: 95
```

## 双字节字形回写

双字节字形支持 PNG 回写：

```powershell
python tools\font_sfo_7w.py rebuild `
  unpacked\CDIMAGE_FULL\seven_data_link\arita\font16.sfo `
  work\font7w_all `
  work\font7w_rebuilt.sfo `
  --allow-missing
```

正式使用时建议只提供需要修改的双字节 PNG，并使用 `--allow-missing`，这样未提供的字形保持原始数据不变。

双字节区已完成全量回写无损测试：

```text
重建记录: 3400
输出大小: 458256 字节
原文件与回写文件 SHA256 一致
```

验证文件：

```text
work/font7w_double_roundtrip.sfo
```

## 变宽区状态

变宽区目前不能认为已经完成。原因是：

- `A >> 8` 只能确定显示宽度，不能直接确定内部行 stride；
- 变宽记录存在 0x20、0x40、0x60 等不同大小；
- 当前线性 2bpp 解码不能稳定还原可辨认字形；
- 因此不能用当前实现对变宽 PNG 做可靠回写。

后续处理变宽区时，应使用 ASCII 已知字符作为校验样本，先确认真实行布局，再实现编码器。不能因为文件大小和索引边界可逆，就把变宽位图格式视为已经确认。

## 相关输出

```text
work/font7w_all/
work/font7w_all/font_map.tsv
work/font7w_all_atlas.tif
work/font7w_double_roundtrip.sfo
```

## 当前结论

```text
SFO 头部和索引: 已确认
B 到文件偏移公式: 已确认
双字节 128 字节字形: 已确认
双字节 PNG 导出: 已确认
双字节 PNG 回写: 已确认并通过 SHA256 无损测试
变宽字形导出: 尚未完全确认
变宽字形回写: 尚未实现
```
