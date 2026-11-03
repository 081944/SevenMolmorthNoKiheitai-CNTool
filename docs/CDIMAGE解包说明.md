# CDIMAGE.BIN 解包说明

本目录中的 `unpack_cdimage.py` 是一个仅解包脚本，适用于本游戏的
`CDIMAGE.BIN`。它不会执行重新打包，也不会自动写入 `unpak` 目录。

## 使用方法

在 PowerShell 中执行：

```powershell
python .\tools\unpack_cdimage.py unpack `
  .\unpak\CDIMAGE.BIN .\unpacked\CDIMAGE_FULL
```

完整构建脚本会使用带 `--reuse-existing` 的形式：

```powershell
python .\tools\unpack_cdimage.py unpack --reuse-existing `
  .\original\CDIMAGE.BIN .\temp\original_build_tree
```

该选项只在输出目录的源镜像 SHA-256、2718 条清单和所有文件均匹配时复用
已有原始树；首次运行或源镜像变化时仍会完整解包。复用的是只读语义上的
pristine 临时树，不会复用汉化后的 `unpacked` 工作树。

建议把输出目录指定为新的目录，例如 `unpacked\CDIMAGE_FULL`，不要指定为
包含原始镜像的目录或已有工程目录。

## 输出内容

脚本先读取第 0 个索引条目定位 `filelist.bin`，再从 `filelist.bin` 中读取
完整 ASCII 文件名表，按原路径创建文件。不能把镜像固定偏移 `0x6800` 当作
文件名表起点，因为不同游戏的布局不同。输出目录中还会生成：

```text
manifest.tsv
```

清单记录每个条目的原始路径、实际输出路径、镜像偏移和结束位置。

部分同类游戏的文件名表存在重复路径。遇到重复路径时，脚本会保留全部
条目，并使用以下形式命名：

```text
文件名
文件名.dup001
文件名.dup002
```

原始路径和这些输出路径都会记录在 `manifest.tsv` 中，因此不会因为同名
文件互相覆盖而丢失镜像条目。

## 注意事项

- 原始 `CDIMAGE.BIN` 只读取，不会被修改。
- 脚本只支持解包，不要把它当作重打包工具使用。
- 输出目录必须是专门的新目录。
- 当前已确认本游戏镜像包含 2718 个 CDIMAGE 条目。
- 旧的 `unpacked\CDIMAGE` 是使用错误文件名表位置生成的不完整结果；请使用
  新生成的 `unpacked\CDIMAGE_FULL`。
- 完整解包应包含 `seven_data_link/arita/font16.sfo`，这是本游戏的主要字库。
- `CDIMAGE.BIN` 的首个 32 位数是外层包数量，不是文件数量；文件数量应以
  `filelist.bin` 解析结果为准。
