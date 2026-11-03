# MOVIMAGE 影片字幕格式与解析说明

本文说明《セブン モールモースの騎兵隊》（Seven ～モールモースの騎兵隊～）影片字幕的磁盘格式、运行时解密/解析过程，以及如何提取和回写。格式部分尽量写成与本项目路径无关的形式；文末列出的地址和偏移是目前对本项目版本的实测值，移植到其他游戏或其他版本时必须重新确认。

## 1. 结论摘要

影片字幕不是烧录在视频画面里的图像，也不是 `mov.fhn` 菜单文本。它位于 `MOVIMAGE.BIN` 的字幕数据块中，具有以下特征：

- 每个字幕块固定 `0x7F0` 字节；
- 磁盘上的块使用一个字节 XOR 密钥保护；
- 解密后是以 `0x0D`（CR，回车）分隔的文本记录；
- 普通记录通常是 `HHHH:HHHH 文本`；当前版本另有 4 个影片块使用带空格的三位时间码（例如 ` 129: 206 文本`），两种形式都由扫描器识别并原样保留；
- 记录末尾为 `#####`，其后为零填充；
- 时间码和文本均使用游戏自己的字节编码。日文部分兼容 CP932，汉字通常通过汉化字库分配的自定义 CP932 双字节槽位编码。

因此，提取流程是“定位块 → XOR 解密 → 按 CR 分行 → 识别时间码和文本”；回写流程是“保留时间码 → 编码译文 → 重新写入 `#####` 和零填充 → XOR 加密 → 写入副本”。

## 2. 磁盘数据块布局

工具的 `--offset` 指向 **`MOVIMAGE.BIN` 中的加密 payload 起点**，不是运行时对象地址，也不是视频帧偏移。一个块的布局如下：

```text
file_offset + 0x000 .. +0x7EF   0x7F0 字节 XOR 加密 payload
```

解密后，payload 的逻辑布局为：

```text
HHHH:HHHH 文本<CR>
HHHH:HHHH 文本<CR>
...
#####<CR>
00 00 00 ...
```

`#####` 是解析终止标记。标记之后的内容不属于字幕文本，应保持为零填充；不要把整个 `0x7F0` 字节块都当成字符串。

### 2.1 运行时资源结构与磁盘块的区别

游戏加载资源后会建立一个运行时资源对象。根据当前 PCSX2 调试结果：

```text
对象 +0x08   资源指针 source
对象 +0x78   加载/解密状态字段（用于判断是否已准备好）
对象 +0x580  解密后的字幕文本缓冲区
```

`source` 指向的运行时资源结构中，已确认的字段为：

```text
source +0x08  一字节 XOR key（实际读取为该地址处的值）
source +0x10  加密字幕 payload，长度 0x7F0
```

运行时 `source + 0x10` 的内容与磁盘块内容相同（可能经过资源装载器定位或复制）。所以调试器中看到的地址不能直接当作文件偏移；应先取得 payload 的字节，再在 `MOVIMAGE.BIN` 中定位相同字节序列，或使用资源表/装载日志换算文件偏移。

### 2.2 文件开头的全局表

`MOVIMAGE.BIN` 开头 `0x00000000` 附近存在一段非零的全局表，随后是大段零填充。该表由多组小端序 `u32` 组成，其中一部分数值单调递增，另一部分数值像是指向文件内 MPEG/媒体数据的偏移（例如当前样本中出现 `0x0C0A2880`）。它更接近影片流的 seek/index 表，而不是“字幕条目表”；目前没有必要依赖它来提取字幕，且各字段的完整语义尚未在所有视频上验证。

已确认的字幕记录有一个更稳定的局部结构：块基址按 `0x800` 对齐，前 `0x10` 字节是头部，随后 `0x7F0` 字节是字幕 payload。头部的 `+0x08` 是 XOR key。全量扫描器正是利用这一结构逐块校验 `#####` 和时间码，而不是猜测全局表字段。

因此，当前工程采用两层信息：

```text
文件开头全局表    用于影片/媒体寻址（字段语义仍待完全确认）
每个 0x800 记录    0x10 字节头 + 0x7F0 字节 payload；可直接提取字幕
```

## 3. XOR 解密与加密

密钥是单字节整数 `key`，对 payload 的每一个字节执行 XOR：

```c
for (size_t i = 0; i < 0x7F0; i++) {
    plain[i] = encrypted[i] ^ key;
    encrypted2[i] = plain[i] ^ key;   // 回写时使用
}
```

XOR 的特点是同一操作可用于解密和加密。不要把 key 当作 ASCII 字符串；例如 key 为 `0x0A` 时，命令行写成 `--key 0x0A` 或 `--key 10`。

## 4. 明文字幕记录

解密后先查找第一个 ASCII 字符串 `#####`，只解析其前面的区域。每一行以 `0x0D` 分隔，第一处 ASCII 空格分割时间码和文本：

```text
0049:0139 セブン モールモースの騎兵隊\r
0161:0259 著作制作 株式会社\r
0289:0356 朗読 野宮真貴\r
#####\r
```

时间码由起始编号和结束编号组成，常见形式是 4 位数字（`HHHH:HHHH`），另有块使用带空格的 3 位数字（如 ` 129: 206`）。它们由影片字幕播放器解释，具体单位/帧率尚未在所有影片上验证。工具只把它当作不透明的 ASCII 字段并原样保留；除非已经通过运行时实验确认，否则不要重排、缩放或修改时间码。

文本部分按 CP932 解码时可以读取原版日文。汉化版本中的中文并不一定是标准 CP932：汉化工程会把字形分配到对白字库的空闲槽位，再在 `sub0_map.tsv` 中记录“Unicode 字符 → 两字节槽位编码”。因此回写中文时应优先使用该映射表；没有映射的字符不能凭空用普通 CP932 编码替代。

## 5. 运行时解密/解析伪代码

下面是根据 MIPS 反汇编整理的等价伪代码，适合移植到其他分析工具：

```c
void load_movie_subtitle(MovieObject *object) {
    uint8_t *source = *(uint8_t **)((uint8_t *)object + 0x08);
    uint8_t key = *(uint8_t *)(source + 0x08);
    uint8_t *encrypted = source + 0x10;
    uint8_t *plain = (uint8_t *)object + 0x580;

    for (size_t i = 0; i < 0x7F0; i++)
        plain[i] = encrypted[i] ^ key;

    parse_movie_subtitle(plain);
}

void parse_movie_subtitle(uint8_t *plain) {
    uint8_t *p = plain;
    while (memcmp(p, "#####", 5) != 0) {
        // 读取至 0x0D，解析 HHHH:HHHH、空格和文本
        p = next_cr_record(p);
    }
}
```

当前版本的关键函数地址：

| 地址 | 作用 |
| --- | --- |
| `0x0012B080` | 字幕对象/解析器初始化 |
| `0x0012B118` | 明文记录解析 |
| `0x0012B568` | 字幕加载、XOR 解密相关代码 |
| `0x0012B620` | 解析调用处，可作为调试断点 |
| `0x0012CB70` | 上层调用者 |

这些地址只适用于当前 ELF/游戏版本；换区域版、补丁版或重新链接的程序时，应用“字符串/内存访问特征”重新定位，而不要硬编码地址。

## 6. 用 PCSX2 调试定位新的字幕块

当已知字幕内容但不知道它在 `MOVIMAGE.BIN` 的哪个块时，可以按下面步骤操作：

1. 让 PCSX2 播放目标影片，并在 `0x0012B620` 下断点。
2. 断下后查看寄存器和内存：
   - `a2`：传入的原始/加密字幕缓冲区（具体是 `source` 还是 `source+0x10`，以当前指令对指针的加减为准）；
   - `a3`：通常指向对象的解密后文本缓冲区，当前版本约为 `0x01D40580`；
   - `t0`：当前 XOR key。
3. 从 `a2` 指向的位置导出 `0x7F0` 字节，或者从 `source+0x10` 导出；用 `t0` XOR 后应能看到 `HHHH:HHHH` 和 `#####`。
4. 在磁盘 `MOVIMAGE.BIN` 中搜索导出的 **加密** `0x7F0` 字节序列，命中位置就是 `--offset`。如果只导出了明文，则先用 key 重新 XOR 后再搜索。
5. 用该偏移和 key 执行提取命令，确认文本与画面一致。

注意：运行时地址是虚拟地址，不能直接减去某个固定基址当作文件偏移。资源装载可能包含对齐、索引和复制；可靠方法是搜索字节序列或读取资源索引。

## 7. 命令行提取与回写

工程内的通用脚本为 `tools/movie_subtitle_7w.py`。它只读输入，回写时先复制到输出文件，不会覆盖原始资源。

### 7.1 提取

```powershell
python tools\movie_subtitle_7w.py extract `
  unpak\MOVIMAGE.BIN `
  --offset 0xCA78010 `
  --key 0x0A `
  temp\movie_subtitle.txt
```

输出文件包含元数据注释和可编辑记录：

```text
# source=unpak\\MOVIMAGE.BIN
# offset=0xCA78010 length=0x7F0 key=0x0A

0049:0139|セブン モールモースの騎兵隊
0161:0259|著作制作 株式会社
0289:0356|朗読 野宮真貴
#####
```

编辑时只修改 `|` 右侧文本。`#` 开头的元数据行可保留或删除。

### 7.2 回写

```powershell
python tools\movie_subtitle_7w.py write `
  unpak\MOVIMAGE.BIN `
  temp\movie_subtitle.txt `
  work\build\MOVIMAGE.BIN `
  --offset 0xCA78010 `
  --key 0x0A `
  --map work\font\sub0_map.tsv
```

回写器会：

- 检查记录数量不变；
- 检查时间码未被修改；
- 对未修改的原文行复用原始 CP932 字节，保证原样 round-trip；
- 对修改行使用 CP932 或映射表编码；
- 重新写入 `#####<CR>`；
- 保留固定块长度和末尾零填充；
- 对新 payload 再做 XOR 后写入输出副本。

如果译文超过 `0x7F0` 字节，工具会拒绝写入。此时需要压缩译文、减少控制符，或确认游戏是否存在更大的字幕块格式；不能简单覆盖相邻资源。

### 7.3 全量扫描与提取

对当前版本的 `MOVIMAGE.BIN`，可以直接扫描所有 `0x800` 对齐记录。扫描器读取每个记录头部的 `+0x08` key，尝试解密 `0x7F0` 字节，并只保留同时满足“时间码记录 + `#####` 终止标记”的块：

```powershell
python tools\movie_subtitle_7w.py scan `
  unpak\MOVIMAGE.BIN `
  work\text\movie_subtitles
```

输出：

- `work\text\movie_subtitles\manifest.csv`：每个字幕块的块基址、payload 偏移、key、记录数和文本文件名；
- `subtitle_XX_<offset>.txt`：每个视频字幕块一个可编辑文本文件。

当前原始文件扫描出 15 个字幕块（其中 4 个使用带空格的三位时间码）。扫描不修改 `unpak\MOVIMAGE.BIN`，并且不把普通 MPEG 数据误判为字幕。

### 7.4 全量回写

翻译 `work\text\movie_subtitles\subtitle_*.txt` 后，可按扫描生成的 manifest 一次性回写所有字幕块：

```powershell
python tools\movie_subtitle_7w.py write-all `
  unpak\MOVIMAGE.BIN `
  work\text\movie_subtitles `
  work\build\MOVIMAGE.BIN `
  --map work\font\sub0_map.tsv
```

`write-all` 会先复制原始文件，再按 `manifest.csv` 中的 `payload_offset/length/key` 逐块处理。它不会改动文件开头的全局媒体表，也不会移动视频数据；每块仍保持固定长度。建议翻译完成后先做一次原样 round-trip，确认输出文件与原文件一致，再编辑译文并重新回写。

## 8. 独立实现示例（不依赖工程脚本）

下面代码展示最小的读取和解析逻辑。生产工具还应加入边界检查、编码错误处理和回写容量检查。

```python
from pathlib import Path

BLOCK_SIZE = 0x7F0

def read_subtitle(path: str, offset: int, key: int):
    encrypted = Path(path).read_bytes()[offset:offset + BLOCK_SIZE]
    if len(encrypted) != BLOCK_SIZE:
        raise ValueError("short subtitle block")
    plain = bytes(b ^ key for b in encrypted)
    end = plain.find(b"#####")
    if end < 0:
        raise ValueError("missing terminator")

    records = []
    for line in plain[:end].split(b"\\r"):
        if not line:
            continue
        timing, text = line.split(b" ", 1)
        records.append((timing.decode("ascii"), text.decode("cp932")))
    return records

records = read_subtitle("MOVIMAGE.BIN", 0x0CA78010, 0x0A)
for timing, text in records:
    print(timing, text)
```

回写的核心顺序必须是：

```python
plain = b"\\r".join(
    timing.encode("ascii") + b" " + encoded_text
    for timing, encoded_text in records
) + b"\\r#####\\r"
plain += b"\\0" * (BLOCK_SIZE - len(plain))
encrypted = bytes(b ^ key for b in plain)
```

## 9. 已确认样本

当前已在原始 `unpak\MOVIMAGE.BIN` 中确认一个开场旁白块：

```text
文件偏移  0x0CA78010
块长度    0x7F0
XOR key   0x0A
```

解密后可读到：

```text
0065:0136 伝えられているところによれば、
0149:0290 このお話は、まだ天と地が今ほど離れていなかった、
```

运行时还捕获到开场标题/署名记录：

```text
0049:0139 セブン モールモースの騎兵隊
0161:0259 著作制作 株式会社
0289:0356 朗読 野宮真貴
```

后者的运行时位置已经确认，但不要假定它与 `0x0CA78010` 是同一个磁盘块；应按第 6 节的方法重新从断点导出并定位。

## 10. 回写到 ISO 的注意事项

`tools/build_all.py` 的 CDIMAGE 回包流程不会自动处理 `MOVIMAGE.BIN`。完成字幕回写后：

1. 将生成的 `work\build\MOVIMAGE.BIN` 复制到 mkps2iso 的打包目录中，替换该目录下原有的 `MOVIMAGE.BIN`；
2. 用 mkps2iso 重新生成 ISO；
3. 在 PCSX2 中播放目标影片，检查时间码、换行、字形和字幕结束位置。

建议始终保留原始 `MOVIMAGE.BIN`，并把每个块的 `offset/key` 记录在翻译工程的清单中。不同影片、不同区域版可能使用不同 key 和不同块偏移，但块内算法和记录结构可以复用。

## 11. 移植到其他项目的建议

- 先验证块长度：不要默认所有游戏都使用 `0x7F0`；从解密循环的计数器或有效终止标记确认。
- 再验证密钥来源：可能是资源头字段，也可能由上层传入；确认运行时实际使用的字节。
- 保留原始字节：CP932 存在二义性字节序列，未改动行原样复制最安全。
- 把“时间码解析”和“文本编码”分开：时间码只是 ASCII 身份字段，文本编码可能是 CP932、定制字库码或混合格式。
- 回写前检查容量和对齐：固定块格式通常不能增长到相邻资源；若必须扩容，需要同时修改资源索引/装载器。
- 用原样 round-trip 做回归测试：提取后不编辑再回写，目标块应逐字节一致；再用一个短译文验证显示。
