# FHM 格式与可变长重建说明

本文记录《七人之勇者》PS2 版两个 `.fhm` 文件的容器结构、文本子资源结构、
PCSX2 运行时验证，以及 `tools/text_7w.py` 的可变长重建规则。结论以原版
`unpak/CDIMAGE.BIN` 解包到 `temp/CDIMAGE_ORIGINAL` 的文件为基准。

## 1. FHM 容器

文件头由小端整数构成：

```text
0x00  u32 sub_count
0x04  u32 sub_offset[sub_count]   # 文件绝对偏移
...   头部填充
      sub-resource 0
      sub-resource 1
      ...
```

`sub_offset[]` 通常递增，但按运行时语义它是子资源引用表，允许重复或乱序项。
解析时对有效偏移排序去重，子资源范围取下一个严格更大的偏移；重复表项只重建
一次，回写时所有引用该子资源的表项都会同步更新。

原版 `chiibaya/common/common.fhm`：

| 子资源 | 文件范围 | 长度 | 类型 |
| --- | ---: | ---: | --- |
| 0 | `0x20..0xFC140` | `0xFC120` | `SFO\0`，24x24 字库 |
| 1 | `0xFC140..0xFD8E0` | `0x17A0` | `SFO\0` |
| 2 | `0xFD8E0..0x100530` | `0x2C50` | UI/命名文本表 |

## 2. 文本子资源

`common.fhm` 的 sub 2 不是旧工具所假设的“`u32 count` 后直接跟字符串”。
实际布局是：

```text
相对子资源起点
0x0000  u32 count = 0x226 (550)
0x0004  u32 runtime_ptr[550]       # 磁盘上全部为 0
0x089C  char strings[550][]        # 逐项 NUL 结尾 CP932；空项也占一个 NUL
0x2B96  零填充
0x2BC4  5 条无索引 CP932 尾部字符串
...     零填充至子资源末尾 0x2C50
```

550 个主记录中有 479 个非空字符串，其余为空记录。空记录不能在提取/重建时
丢掉，否则后续记录号会错位。尾部另有 5 条未进入 550 项运行时指针表的旧字符串；
工具以 `[tail:n]` 单独表示，并在主字符串池未越界时保持其原相对地址。

### PCSX2 运行时证据

当前原版镜像运行时，`common.fhm` 整体位于 EE 内存 `0x01E80000`：

```text
文件 sub 2 偏移      0x000FD8E0
运行时 sub 2 地址    0x01F7D8E0
count                [0x01F7D8E0] = 0x226
runtime_ptr[0]        [0x01F7D8E4] = 0x01F7E17C
0x01F7E17C            "戦士\0"
runtime_ptr[1]        0x01F7E181 -> "騎士\0"
```

`0x01F7E17C - 0x01F7D8E0 = 0x89C = 4 + 550 * 4`。这直接证明磁盘上的
550 个零值是运行时指针缓存：加载器会把它们改写成绝对 EE 地址，而不是偏移表、
容量提示或无意义填充。

IDA Pro 9.3 使用 `work/elf/SLPS_250.25.i64` 做了静态交叉检查：EE ELF 中没有
对尾部 `0x2BC4..0x2C50` 的直接立即数引用，因此正常重建优先保持尾部原位置；
主池确实越过该位置时，尾部随子资源移动。运行时指针初始化的最终事实以上述
PCSX2 内存结果为准。

## 3. 重建算法

`rebuild_fhm_text()` 执行以下步骤：

1. 解析并校验 FHM 数量、绝对偏移和子资源边界。
2. 只接受满足“`count + count*4` 零指针缓存 + count 条 CP932 字符串”的文本块。
3. 按真实记录号替换文本，未编辑/不可编码记录保留原始字节。
4. 重新生成全部 550 条 NUL 结尾字符串，包括空记录。
5. 保留 5 条尾部字符串、间隔和尾部填充。
6. 新内容能放入原容量时维持原文件大小；超过时扩到 `0x10` 对齐。
7. 重新组装 FHM 并重算所有后续子资源的绝对偏移；SFO/GIM/未知块原样复制。

因此译文不再受原字符串槽长度限制。唯一编码约束仍是 CP932 或项目已经分配的
中文字形槽位。

## 4. 工具用法

```powershell
python tools\text_7w.py fhm extract `
  temp\CDIMAGE_ORIGINAL\seven_data_link\chiibaya\common\common.fhm `
  work\text\fhm\common.fhm.txt

python tools\text_7w.py fhm write `
  temp\CDIMAGE_ORIGINAL\seven_data_link\chiibaya\common\common.fhm `
  work\translation\fhm\common.fhm.txt `
  work\text\fhm\common.rebuilt.fhm
```

目录模式可一次审计解包树中的所有 `.fhm`。输出目录保留相对路径，清单会列出每个
文件是否发现经过结构校验的文本表；`no-text` 项（例如 `rpgcommon/common.fhm`
的数值/图形块）会记录拒绝原因，便于确认没有静默漏文件：

```powershell
python tools\text_7w.py fhm extract `
  temp\original_build_tree\seven_data_link `
  work\text\fhm --manifest work\report\fhm_manifest.csv
```

提取和回写的输入应是日文 pristine 树（例如 `temp\original_build_tree` 或由
原始镜像重新解包的目录）。若直接使用已经写入中文字库的 `unpacked` 文件，字节
会按字库槽位解码成映射字符，不能作为日文原文校验基准。

新提取格式使用真实主记录号和独立尾部编号：

```text
[text:0] len=4 | 戦士 | 战士
[text:8] len=10 | アーチャー | 弓手
[tail:0] len=2 | 戦 | 战
```

旧版候选文件使用压缩后的连续 `[text:n]` 编号。写入器会识别旧版头部并把前
479 项映射到非空主记录、后 5 项映射到尾部文本，现有翻译稿无需立即重排。

## 5. 验证结果

- 原版 `common.fhm` 空编辑重建：文件大小 `0x100530`，SHA256 与原版完全一致：
  `26A422683F6522E24A785669A7CC667C8E98B4076C18849AACE9CC9080D8C32A`。
- 旧版 484 行翻译稿：477 项发生字节变化，0 项溢出，0 项不可编码，仍可解析。
- 将第 0 项由 4 字节扩为 400 字节：文件增加 400 字节，回读长度为 400。
- 合成“文本块位于容器中间”的测试：后续两个 offset 均增加 `0x190`，两个
  非文本子资源逐字节不变。
- 目录级 `fhm extract` 会对所有 FHM 生成审计清单；当前 7W 解包树应报告
  `chiibaya/common/common.fhm` 为 550 条主记录（479 条非空）+ 5 条尾部文本，
  `chiibaya/rpgcommon/common.fhm` 为 `no-text`，而不是把数值块误报成文本。

## 6. rpgcommon 变体

`chiibaya/rpgcommon/common.fhm` 同样是 FHM 容器，但不是文本表：

```text
sub_count = 6
offsets = 0x20, 0x18050, 0x183C0, 0x193D0, 0x1D800, 0x1DD70
sub 0..3 = GIM
sub 4..5 = 数值/图形相关未知块（首 u32 均为 0x12）
```

sub 4/5 的 `count` 后不是零指针缓存，也不能按声明数量解析出 CP932 字符串池。
新工具会明确拒绝把它们当文本，避免旧式“扫描所有可解码字节”产生伪文本。
