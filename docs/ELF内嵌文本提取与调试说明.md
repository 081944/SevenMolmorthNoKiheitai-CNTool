# ELF 内嵌文本提取与调试说明

## 运行时确认

`SLPS-25025` 的可执行文件是单个 ELF `PT_LOAD`，代码和数据随 ELF 一起装入
EE 内存。可执行文件内的日文文本不是另一个 SPL/NHT 容器，不需要等待独立
文本加载器解压；游戏运行时直接从数据段取字符串指针。

PCSX2 DebugServer 和 IDA 交叉引用确认的对白排版入口是：

```text
0x00128608  文本排版/显示入口
0x0016A6E8  调用点
```

`0x0016A6E8` 从脚本状态对象读取 `0x198`、`0x19C` 字段，连同对象指针传入
`0x00128608`。因此要判断某条文本为什么仍显示日文，先用 ELF 文件偏移定位
字符串，再用其运行时地址或排版入口断点核对调用路径。

## 全量提取

```powershell
python tools\text_7w.py elf extract `
  work\elf\SLPS_250.25 `
  work\translation\elf\slps_250.25.txt `
  --manifest work\report\slps_250_elf_full.tsv
```

提取器默认扫描 ELF 的 `.data`、`.rodata`、`.sdata` 等非执行 section，并按完整 NUL 结尾字符串提取候选；内部换行会在同一行中保存为 `\\n`。`slps_250.25.txt` 是唯一允许回写的 ELF 翻译文件。manifest 同时记录原始字节
(`raw_hex`)、运行时地址、section、数据指针引用数量和置信度：

- `B`：在 ELF 中找到指向该运行时地址的数据指针；
- `C`：非执行数据段中的合法 CP932 候选，但尚未发现数据指针；
- `D`：执行段中的复合候选，仅供人工审阅，不能直接作为自动回写依据。

为了补充可能位于 `.text` 的字符串，可生成独立的扩展报告：

```powershell
python tools\text_7w.py elf extract `
  work\elf\SLPS_250.25 `
  work\build\slps_250_elf_broad.txt `
  --manifest work\report\slps_250_elf_broad.tsv `
  --include-exec
```

扩展报告不会替换现有翻译清单。候选行的 `@0x...` 是 ELF 文件偏移；使用带
`raw_hex` 的 manifest 回写时，工具会先核对原始字节，偏移内容不一致就跳过并
报告 `mismatch`，避免误把二进制区域写坏。`D` 级候选默认禁止回写；只有在
已通过人工或 PCSX2 动态确认后，显式追加 `--allow-low-confidence` 才会写入。

正式候选当前有 837 条双栏记录。回写前会校验每条记录的结束位置确为 NUL、译文可编码且不超过原始长度；任一项失败都会终止写入，不产生半成品 ELF。

## IDA 批量辅助

`tools/ida_extract_elf.py` 可在 IDA Pro 9.3 的 IDAPython 环境中导出 IDA 已识别
的 CP932 字符串、文件偏移、EE 地址和数据交叉引用：

```powershell
idat.exe -A -S"tools/ida_extract_elf.py" work\elf\SLPS_250.25.i64
```

当前机器的 IDA batch 环境提示尚未接受 batch license，因此本轮以已有 IDA
数据库/交叉引用和 PCSX2 DebugServer 完成运行时定位；section-aware 提取不依赖
IDA license，可以直接使用。
