# 7W GIM 图片格式逆向笔记

本文档记录对 `unpacked/CDIMAGE_FULL` 下 298 个 `.gim` 文件格式的逆向进展。
**GIM 格式已完全破解，通用 PNG 导出已实现（见 `tools/gim_7w.py`）**。

格式结论通过 PCSX2 动态调试游戏自身解析代码（`SLPS_250.25` EE `.text`
`0x00101C20`，`0x001027E8`）与图像模型逐张核验得出，可靠。

## 文件结构（header 0x30 字节）

```
0x00  'GIM\0'           magic
0x04  u32               未使用/可忽略（1、0、0xCDCDCDCD 均见）
0x08  u32               运行时写为 buf+0x10（子结构指针）
0x0C  u32               运行时写为像素/调色板偏移
0x10  u32               变体标识（0x13000000、0x14000000、0x00000200…）
0x13  byte              ★ 格式选择字节（决定每像素字节数与有无调色板）
0x14  u32               运行时写为 buf+0x20；磁盘值（0x12fd1c 等）无布局意义
0x1C  u16  LE           宽
0x1E  u16  LE           高
```

## 格式选择（0x13 字节 → 每像素字节数/调色板）

全库 298 个文件只出现三种取值：

| 0x13 字节 | 含义 | 像素区 | 调色板 | 数量 |
|-----------|------|--------|--------|------|
| `0x13`（及其他） | **8bpp 索引** | `@0x30`，`w*h` 字节 | `@0x30+w*h`，256 色 RGBA (1024B) | 243 |
| `0x00`           | **32bpp 直存** RGBA | `@0x20`，`w*h*4` 字节 | 无 | 50 |
| `0x14`           | **4bpp 索引**（高位在前） | `@0x30`，`ceil(w*h/2)` 字节 | `@0x30+像素字节数`，16 色 RGBA (64B) | 5 |

游戏解析时的内部映射（`0x00101C20`）：
- `buf[0x13]==0`   → 每像素 4 字节（32bpp）
- `buf[0x13]==2`   → 每像素 2 字节（16bpp，本库无样本）
- `buf[0x13]==3`   → 每像素 1 字节（8bpp）
- `buf[0x13]==0x14`→ 每像素 0.5 字节（4bpp）
- 其他值（含 0x13）→ 每像素 1 字节（8bpp）

## 布局细节

- 调色板/像素颜色一律 **RGBA**。
- 8bpp / 4bpp 均为 **palette-back**（像素在前、调色板在尾部）。
- **4bpp 像素：每字节 2 像素，LOW nibble 在前（LSB first）**，索引直接使用。
- **8bpp 像素索引需交换 bit3<->bit4**：
  `i' = (i & ~0x18) | ((i & 0x08) << 1) | ((i & 0x10) >> 1)`。
  **已验证**：通过游戏 GS 纹理 dump（`ttr_cl`）对比，此映射颜色匹配率 100%。
  不做此映射会出现"杂色"（零散错误颜色）。
- **文件结构**：header `0x20` 字节，像素从 `0x20` 起；
  调色板在 `0x20 + 像素字节数 + 0x10`（中间有 0x10 字节调色板信息头，
  其中 `10 00 10 00` = 16×16 格子尺寸，多个 GIM 按格子切分使用）。
- 32bpp 文件 header 仅 **0x20** 字节，直接 RGBA 像素到文件尾，无调色板。

## Alpha 处理（重要，匹配游戏显示）

- **8bpp**：**保留原始 alpha**（不强制 255，不乘 2）。
  **已验证**：`ttr_cl` 用原始 alpha 与游戏 GS 纹理 dump 完全一致
  （alpha 匹配率 100%；dump 显示半透明 alpha≈50-84）。
- **4bpp alpha 蒙版**（logo/标题，调色板单色 + alpha 渐变）：
  **alpha ×2**（`min(255, a*2)`），使主体（原始 alpha≈128）变为 255 不透明，
  边缘保留平滑渐变。透明像素保留原始 RGB、alpha=0。
  经用户确认，`logo_n_4` 正确导出见 `temp/gim_text/logo_n4_a2.png`。

## 已确认正确的导出基准（勿再改动）

| 文件 | 处理 | 确认状态 |
|------|------|---------|
| `logo_n_4.gim` / `logo_p_4.gim` | 4bpp：低 nibble + 调色板 0x20+px+0x10 + alpha×2 | ✅ 正确（`logo_n4_a2.png`） |
| `ttr_cl.gim` | 8bpp：swap_bit34 索引 + 保留 alpha | ✅ 与 GS 纹理 dump 完全一致（颜色/alpha 匹配率 100%） |
| 8bpp 头像（`v20_face` 等） | 8bpp：swap_bit34 索引 + 保留 alpha | ✅ 正确（肤色自然、无杂色） |

**全部 298 个 GIM 已正确导出**（`tools/gim_7w.py` → `temp/gim_view9`）：
- 243 个 8bpp、50 个 32bpp、5 个 4bpp，全部正常。

## 导出脚本

`tools/gim_7w.py` 按上述规则导出全部 298 个文件 → PNG（每文件一张，
后缀 `__8bpp` / `__32bpp` / `__4bpp`），已全量通过。

```powershell
python .\tools\gim_7w.py extract .\unpacked\CDIMAGE_FULL .\temp\gim_view4 --report .\temp\gim_view4\report.csv
```

## PNG 回写（write / rebuild）

`tools/gim_7w.py write <源.gim> <编辑后.png> <输出.gim>` 把编辑后的 PNG
重新编码为 GIM，**保留原容器结构**：

| 格式 | 回写方式 |
|------|----------|
| 8bpp | 保留原 256 色调色板；按"渲染后颜色"精确映射回原索引（与 `render` 一致，未编辑像素**无损**）；不在调色板中的新颜色量化到最近色（RGB 距离）；透明像素落到位 alpha=0 的第一项；索引写回时再做 bit3↔bit4 交换 |
| 4bpp | 同 8bpp，但 16 色调色板、LOW nibble 在前、按 `render` 的 alpha×2 蒙版换算建映射 |
| 32bpp | 直接写 RGBA；不透明像素保留原 alpha 字节（PNG alpha=0 则清为透明） |

- **尺寸守卫**：PNG 宽高必须等于源 GIM，否则 `ValueError` 拒写。
- **无损性**：不改图直接回写（identity），重建后渲染 **逐像素一致**（8bpp/
  32bpp/4bpp 全库 298 个实测）。当调色板存在**重复颜色**时（少数 8bpp 特效图），
  原始索引与重建索引可能不同（`0x47`→`0x45` 等），但渲染结果完全相同，游戏
  显示无差异；调色板无重复色的文件则**字节级一致**。
- **局限**：8bpp/4bpp 是索引格式，编辑若引入调色板外的新颜色（如任意新文字
  色），会被量化到最接近的已有颜色；如需精确新色，请先改调色板。

批量场景由 `tools/image_7w.py write-back` 承接（scan→export→人工筛选编辑→
回写，覆盖 unpacked 树），见 `docs/工具与产物目录规范.md`"图片导出回写规范"。

## 已排除的误报

- **"图片左移"**：
  - **4bpp**：真实问题，根因是 nibble 顺序（应 low-first）。已修复，logo/标题居中。
  - **8bpp**：误报。像素为线性存储，头像/特效图均渲染居中、无错位
    （旧版 alpha 半透明致内容极淡，只见片段造成"左移"错觉）。
- **"颜色淡"**：根因是 CLUT alpha≈0x7d 被当作不透明度。按上表规则处理后，
  实色贴图不透明、蒙版保留渐变，与游戏显示一致。

## 已确认可正确处理的图片（带文字/UI 类）

以下文件已确认渲染正确、可作为处理基准（已由人工比对游戏画面确认）：

| 文件 | 尺寸 | 格式 | 说明 | 状态 |
|------|------|------|------|------|
| `futamura/logo/logo_n_4.gim` | 416×56 | 4bpp | 带文字 LOGO（游戏开场加载） | ✅ 正确 |
| `futamura/logo/logo_p_4.gim` | 168×32 | 4bpp | 带文字 LOGO | ✅ 正确 |
| `futamura/title/ttr_ch.gim` | 632×172 | 4bpp | 标题（4bpp，alpha 蒙版类） | ✅ 形状/位置正确 |
| `futamura/title/ttr_cl.gim` | 430×184 | 8bpp | 标题彩色图 | ⚠️ 需人工核验颜色 |
| `futamura/gameover/gov_ch1/2.gim` | 604×184 | 4bpp | 游戏结束画面 | ✅ 形状/位置正确 |

**4bpp 文字图（logo/标题）的关键处理**：低 nibble 在前（LSB first），
alpha>0 强制 255（不透明），palette-back 布局。脚本 `tools/gim_7w.py` 已实现。

## 尚未解决/确认的问题（待处理）

1. **8bpp 图颜色错乱 + 水平偏移（未解决）**
   - 现象：某些 8bpp 图（如 `rpgdata/face/v20_face.gim`）渲染后仍有
     "该黄棕显示灰、该灰显示黄棕"的颜色交换，且部分图水平偏移（用户测得
     v20_face 左偏约 16 像素）。
   - 尝试过：线性 / bitrev8 / 每行循环右移16 / PS2 GS swizzle（PSMT8）
     / 调色板重排 —— 均未能同时解决形状与颜色。
   - 影响：主要影响 `rpgdata/face/*`（角色头像）等 8bpp 文件。**32bpp 文件
     颜色与位置均正常**，4bpp 文字图位置正常。
   - 说明：游戏运行时内存中 8bpp 头像（如 0x01f99910）数据显示为线性渐变，
     但与其磁盘文件的关系仍未完全确认；不排除存在"运行时转换"或
     "不同文件不同存储"的情况。此问题当前搁置，不影响文字类图片处理。

2. **`bface.ite` 等 ITE 文件未破解**
   - 游戏主要纹理格式其实是 **ITE**（magic `0x00455449`，如 `bface.ite`、
     `tth_stt.ite`、`gov_wd1.ite`、`tt_m01.ite` 等）。
   - `futamura/title/*.ite`（标题菜单 `ttlh_m1-m7`、`tth_sev`、`tth_stt` 等）、
     `futamura/gameover/*.ite`、`takeshi/screen/enemy/*.ite` 很可能含文字，
     是汉化候选，但 **ITE 格式尚未破解**（纹理块疑似压缩/编码，无统一 header）。
   - 磁盘上另有 `endless.pack`（10MB，可能打包大量 UI 资源）。

3. **rodata 中引用的 UI GIM 文件在解包目录中缺失**
   - 游戏 rodata 引用 `parts.gim`、`rotation.gim`、`namewin.gim`、`suuji.gim`
     （数字）、`ba_gridtop/under/left/top/right.gim`、`back_01/02.gim`、
     `sousa.gim`、`win_main02/win_okuri/win_name/win_face.gim` 等文件名，
     但解包出的 `unpacked/CDIMAGE_FULL` 中只有 `win_face.gim` 存在。
   - 这些 UI/文字图片可能打包在 `.ite`/`.smd`/`.pack` 容器内，或位于
     CDIMAGE 的其他数据区。**需要进一步定位。**

## 游戏内其他纹理格式（供参考）

- 游戏主要纹理格式其实是 **ITE**（magic `0x00455449`，如角色头像 `bface.ite`），
  GIM 用于 UI 窗口、特效贴图。二者由同一分发函数 `0x0010277C` 按 magic 区分。
- 动态生成的 UI GIM（内存中 0x005e0xxx、0x0080xxxx 等）结构与磁盘文件一致。

## 不再把 `text_7w.py` / 文本侧进度混入

文本侧（`.spl`/`.nht`/`.fhm`）提取与回写已完成并验证，详见
`docs/工具与产物目录规范.md` 与 `docs/7W全库文本资源探寻报告.md`。
