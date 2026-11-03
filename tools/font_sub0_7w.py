#!/usr/bin/env python3
"""Write Chinese glyphs into 7W SFO fonts (common.fhm sub0 24x24, font16.sfo 16x16).

Verified layouts:
  common.fhm sub0 (对白渲染字库):
    SFO header at file offset 0x20, count = 3822
    lead-byte lookup table at 0x20+0x10   (256 * u16)   index = lookup[lead] + trail
    (A,B) index table at 0x20+0x210       (count * 4)   glyph = file[B*0x20+0x20 : +288]
    24x24 4bpp, per row 12 bytes, low nibble = left pixel.
  font16.sfo (aria 16x16 字库):
    SFO header at file offset 0x00, count = 0xDBE = 3518
    lead-byte lookup table at 0x10        (256 * u16)   index = lookup[lead] + trail
    (A,B) index table at 0x210            (count * 4)   glyph = file[B*0x10+0x08 : +128]
    16x16 4bpp double-byte block, per row 8 bytes, low nibble = left pixel.
"""
from __future__ import annotations
import struct
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

TARGETS = {
    "common": dict(
        label="common.fhm sub0 (24x24)",
        magic_off=0x20,
        count_off=0x28,
        count=3822,
        lookup_off=0x30,          # 0x20 + 0x10
        index_off=0x230,          # 0x20 + 0x210
        glyph_off=lambda B: B * 0x20 + 0x20,
        glyph_size=288,
        dim=24,
    ),
    "font16": dict(
        label="font16.sfo (16x16)",
        magic_off=0x00,
        count_off=0x08,
        count=0xDBE,
        lookup_off=0x10,
        index_off=0x210,
        glyph_off=lambda B: B * 0x10 + 8,
        glyph_size=128,
        dim=16,
    ),
}

# ---- 字形渲染可调参数（按需修改后重新打包即可生效） ----
# 每个字库独立配置：common = 对白 24px 字库，font16 = UI 16px 字库。
# 字体：只填文件名（如 "chinese.ttf"），默认从 work/font/ 目录读取；
#       也可填相对/绝对路径。留空 None 则取 work/font/chinese.ttf。
#       不查找系统字体：指定的字体找不到会直接报错，不会悄悄换字形。
# 字号（px）：TTF 渲染字号，与字库像素可不同（>字库会裁切，<字库会缩小）。
# 偏移（px）：在自动居中后整体平移，正=右/下，负=左/上。

FONT_PATH_COMMON = "chinese.ttf"   # common.fhm sub0 字体文件（work/font/ 下）
FONT_PT_COMMON   = 24              # common.fhm sub0 字号
OFFSET_X_COMMON  = 0               # common.fhm sub0 左右偏移
OFFSET_Y_COMMON  = -1              # common.fhm sub0 上下偏移

FONT_PATH_FONT16 = None   # font16.sfo 字体文件（work/font/ 下）
FONT_PT_FONT16   = 16              # font16.sfo 字号
OFFSET_X_FONT16  = 0               # font16.sfo 左右偏移
OFFSET_Y_FONT16  = 0               # font16.sfo 上下偏移

# 每个字库的完整渲染配置
FONT_PROFILES = {
    "common": dict(font=FONT_PATH_COMMON, pt=FONT_PT_COMMON,
                   ox=OFFSET_X_COMMON, oy=OFFSET_Y_COMMON),
    "font16": dict(font=FONT_PATH_FONT16, pt=FONT_PT_FONT16,
                   ox=OFFSET_X_FONT16, oy=OFFSET_Y_FONT16),
}

def load(path: Path, target: str) -> bytes:
    t = TARGETS[target]
    data = path.read_bytes()
    if data[t["magic_off"]:t["magic_off"] + 4] != b"SFO\0":
        raise ValueError(f"not {t['label']}: {path}")
    cnt = struct.unpack_from("<I", data, t["count_off"])[0]
    if cnt != t["count"]:
        raise ValueError(f"{t['label']}: unexpected count {cnt} (expected {t['count']})")
    return data

def lookup_table(data: bytes, target: str) -> list[int]:
    t = TARGETS[target]
    return [struct.unpack_from("<H", data, t["lookup_off"] + k * 2)[0] for k in range(256)]

def index_table(data: bytes, target: str):
    t = TARGETS[target]
    return [struct.unpack_from("<HH", data, t["index_off"] + k * 4) for k in range(t["count"])]

def glyph_offset(B: int, target: str) -> int:
    return TARGETS[target]["glyph_off"](B)

def idx_of_cp(data: bytes, target: str, cp: int) -> int:
    lookup = lookup_table(data, target)
    return lookup[cp >> 8] + (cp & 0xFF)

def load_font(path: str | None, size: int):
    """Load the configured TTF, without any system-font fallback.

    A missing font raises immediately instead of silently picking a different
    typeface, so a build never changes glyph shapes behind your back.
    """
    tried: list[Path] = []
    if path:
        p = Path(path)
        candidates = [p]
        # 只填文件名（无目录分隔）时，默认从 work/font/ 下找
        if not p.is_absolute() and p.parent == Path("."):
            candidates.insert(0, Path("work/font") / p)
        for cand in candidates:
            tried.append(cand)
            if cand.exists():
                return ImageFont.truetype(str(cand), size)
    else:
        for name in ("chinese.ttf", "chinese.ttc", "font.ttf", "font.ttc"):
            cand = Path("work/font") / name
            tried.append(cand)
            if cand.exists():
                return ImageFont.truetype(str(cand), size)
    raise FileNotFoundError(
        "no Chinese font found; tried: "
        + ", ".join(str(t) for t in tried)
        + " (put chinese.ttf in work/font/ or pass --font <path.ttf>; "
          "the system font directory is not searched)")

def render_chinese(ch: str, font, size: int = 24,
                   offset_x: int = 0, offset_y: int = 0) -> Image.Image:
    """Render one Chinese char centered on a `size`x`size` canvas, then apply
    OFFSET_X / OFFSET_Y pixel shift (after centering)."""
    img = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(img)
    bb = d.textbbox((0, 0), ch, font=font)
    w = bb[2] - bb[0]; h = bb[3] - bb[1]
    # 先按字号缩放，再按画布尺寸等比缩放后居中
    d.text(((size - w) / 2 - bb[0] + offset_x,
            (size - h) / 2 - bb[1] + offset_y), ch, font=font, fill=255)
    return img

def encode_glyph(image: Image.Image, dim: int) -> bytes:
    """dim x dim grayscale -> dim^2/2 bytes 4bpp (low nibble = left pixel)."""
    image = image.convert("L")
    if image.size != (dim, dim):
        image = image.resize((dim, dim), Image.Resampling.LANCZOS)
    _gd = image.get_flattened_data() if hasattr(image, "get_flattened_data") \
        else image.getdata()
    vals = [min(15, max(0, (v + 8) // 17)) for v in _gd]
    out = bytearray()
    for y in range(dim):
        row = vals[y * dim:(y + 1) * dim]
        for x in range(0, dim, 2):
            out.append(row[x] | (row[x + 1] << 4))
    return bytes(out)

def decode_glyph(raw: bytes, dim: int) -> list[int]:
    stride = dim // 2
    vals = []
    for y in range(dim):
        row = raw[y * stride:(y + 1) * stride]
        for byte in row:
            vals.append(byte & 0x0F)
            vals.append(byte >> 4)
    return vals

def write_glyphs(fhm: Path, mapping: dict[str, int], output: Path, target: str,
                 font_path: str | None = None, quiet: bool = False):
    t = TARGETS[target]
    dim = t["dim"]
    data = bytearray(load(fhm, target))
    pairs = index_table(data, target)
    # 每个字库独立：字体 / 字号 / 偏移 都取自 FONT_PROFILES
    prof = FONT_PROFILES[target]
    fp = font_path or prof["font"]
    pt = prof["pt"]
    ox, oy = prof["ox"], prof["oy"]
    font = load_font(fp, pt)
    written = 0
    for ch, cp in mapping.items():
        idx = idx_of_cp(data, target, cp)
        A, B = pairs[idx]
        if (A & 0xFF) == 0xFF:
            raise ValueError(f"slot {idx} ({cp:#06x}) is a placeholder")
        off = glyph_offset(B, target)
        if off + t["glyph_size"] > len(data):
            raise ValueError(f"glyph {idx} offset out of range")
        img = render_chinese(ch, font, dim, ox, oy)
        data[off:off + t["glyph_size"]] = encode_glyph(img, dim)
        if not quiet:
            print(f"{ch} (cp932={cp:#06x}) -> idx {idx} B={B:#x} @0x{off:X} [{t['label']}]")
        written += 1
    output.write_bytes(data)
    if not quiet:
        print(f"wrote {output} ({written} glyphs, {len(data)} bytes) [{t['label']}]")

def inspect(fhm: Path, target: str) -> None:
    t = TARGETS[target]
    data = load(fhm, target)
    pairs = index_table(data, target)
    valid = [p for p in pairs if (p[0] & 0xFF) != 0xFF]
    max_b = max(p[1] for p in pairs)
    deltas = {}
    prev = None
    for p in pairs:
        if (p[0] & 0xFF) == 0xFF:
            prev = None
            continue
        if prev is not None and p[1] > prev:
            deltas[p[1] - prev] = deltas.get(p[1] - prev, 0) + 1
        prev = p[1]
    unit = 0x20 if target == "common" else 0x10
    print(f"file size=0x{len(data):X}  count={len(pairs)}  valid glyph slots={len(valid)}")
    print(f"glyph offset = B*{unit:#x} + {t['magic_off']:#x}  ({t['label']})")
    print(f"maxB=0x{max_b:X}  last glyph end=0x{glyph_offset(max_b, target) + t['glyph_size']:X}")
    print(f"B delta histogram (double-byte region mostly {t['glyph_size'] // unit}):")
    for d, n in sorted(deltas.items()):
        if n > 1 or d > 10:
            print(f"  delta {d} -> {n}")
    print("anchors:")
    for name, cp in (("亜", 0x889F), ("噛", 0x8A9A), ("、", 0x8141), ("０", 0x824F)):
        print(f"  {name} cp932={cp:#06X} -> idx {idx_of_cp(data, target, cp)}")

def export_glyph(fhm: Path, idx: int, output: Path, target: str) -> None:
    t = TARGETS[target]
    dim = t["dim"]
    data = load(fhm, target)
    pairs = index_table(data, target)
    A, B = pairs[idx]
    off = glyph_offset(B, target)
    raw = data[off:off + t["glyph_size"]]
    v = decode_glyph(raw, dim)
    img = Image.new("L", (dim, dim))
    img.putdata([min(15, p) * 17 for p in v])
    output.parent.mkdir(parents=True, exist_ok=True)
    img.save(output)
    print(f"exported idx {idx} (A={A:#06x} B={B:#06x}) -> {output} [{t['label']}]")

def read_map(path: Path) -> dict[str, int]:
    mapping = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        ch, _, cp = line.replace("\t", " ").partition(" ")
        mapping[ch] = int(cp, 16)
    return mapping

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="7W SFO font glyph writer (common sub0 + font16)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("inspect")
    p.add_argument("fhm", type=Path)
    p.add_argument("--target", choices=list(TARGETS), default="common")

    p = sub.add_parser("export")
    p.add_argument("fhm", type=Path)
    p.add_argument("--idx", type=int, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--target", choices=list(TARGETS), default="common")

    p = sub.add_parser("write")
    p.add_argument("fhm", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--map", type=Path, required=True, help="TSV: char<TAB>cp932-hex")
    p.add_argument("--font", default=None,
                   help="TTF path (default: the target's FONT_PROFILES entry)")
    p.add_argument("--target", choices=list(TARGETS) + ["both"], default="common",
                   help="common = write common.fhm sub0 only (default); "
                        "font16 = write font16.sfo only; "
                        "both  = write both (fhm=common.fhm, --font16=<font16.sfo>)")
    p.add_argument("--font16", type=Path, default=None,
                   help="font16.sfo path, required when --target=both")
    p.add_argument("--quiet", action="store_true",
                   help="suppress per-glyph and summary output")
    args = parser.parse_args()

    if args.command == "inspect":
        inspect(args.fhm, args.target)
    elif args.command == "export":
        export_glyph(args.fhm, args.idx, args.out, args.target)
    elif args.command == "write":
        mapping = read_map(args.map)
        if args.target == "both":
            if not args.font16:
                parser.error("--target=both requires --font16 <font16.sfo>")
            write_glyphs(args.fhm, mapping, args.output, "common", args.font, args.quiet)
            f16_out = args.output.with_name(args.output.stem + "_font16.sfo")
            write_glyphs(args.font16, mapping, f16_out, "font16", args.font, args.quiet)
        else:
            write_glyphs(args.fhm, mapping, args.output, args.target, args.font, args.quiet)

if __name__ == "__main__":
    try:
        main()
    except FileNotFoundError as exc:
        raise SystemExit(f"[font_sub0] {exc}")
