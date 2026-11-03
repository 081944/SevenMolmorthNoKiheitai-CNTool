#!/usr/bin/env python3
"""Parse, export and rebuild the Seven Warriors font16.sfo glyph records."""
from __future__ import annotations
import argparse
import hashlib
import struct
from pathlib import Path
from PIL import Image

INDEX = 0x210
COUNT = 0xDBE
INDEX_END = INDEX + COUNT * 4
HEIGHT = 16
ASCII_COUNT = 97
DOUBLE_WIDTH = 16
DOUBLE_BLOCK_SIZE = 128

def load(path: Path) -> bytes:
    data = path.read_bytes()
    if data[:4] != b"SFO\0" or len(data) < INDEX_END:
        raise ValueError(f"not a 7W SFO: {path}")
    if struct.unpack_from("<I", data, 8)[0] != COUNT:
        raise ValueError("unexpected SFO index count")
    return data

def pairs(data: bytes):
    return [struct.unpack_from("<HH", data, INDEX + i * 4) for i in range(COUNT)]

def records(data: bytes):
    ps = pairs(data)
    maximum = max(b for _, b in ps if b)
    last_width = max(a >> 8 for a, b in ps if b == maximum)
    base = len(data) - maximum * 8 - HEIGHT * ((last_width + 3) // 4)
    if base < INDEX_END:
        raise ValueError("invalid payload base")
    for i, (a, b) in enumerate(ps):
        if not b:
            continue
        width = DOUBLE_WIDTH if i >= ASCII_COUNT else (a >> 8)
        if not width:
            continue
        next_b = next((candidate for _, candidate in ps[i + 1:] if candidate > b), None)
        start = b * 0x10 + 8
        end = (next_b * 0x10 + 8) if next_b is not None else len(data)
        # ASCII entries are variable-width packed rows. Every double-byte
        # index entry points at a complete 128-byte block containing two
        # 16x16 2bpp glyphs.
        expected = DOUBLE_BLOCK_SIZE if i >= ASCII_COUNT else end - start
        if i < ASCII_COUNT and (end - start) % 0x20:
            raise ValueError(f"entry {i}: invalid ASCII block size {end-start}")
        if i == len(ps) - 1 and end == len(data) and end - start == expected - 8:
            expected = end - start
        if end - start != expected:
            raise ValueError(f"entry {i}: width {width}, record {end-start}, expected {expected}")
        yield i, a, b, width, start, end

def decode(raw: bytes, width: int) -> list[int]:
    stride = (width + 3) // 4
    values = []
    for y in range(HEIGHT):
        row = raw[y * stride:(y + 1) * stride]
        row_values = [((byte >> shift) & 3) for byte in row for shift in (0, 2, 4, 6)]
        values.extend(row_values[:width])
    return values

def decode_double(raw: bytes) -> list[int]:
    """Decode one 128-byte glyph: two stacked 16x8 4bpp halves."""
    raw = raw.ljust(DOUBLE_BLOCK_SIZE, b"\0")
    values = []
    for half in range(2):
        part = raw[half * 64:(half + 1) * 64]
        values.extend(n for byte in part for n in (byte & 0x0F, byte >> 4))
    return [min(15, n) * 17 for n in values[:DOUBLE_WIDTH * HEIGHT]]

def encode_double(values: list[int]) -> bytes:
    values = [min(15, max(0, v)) for v in values]
    out = bytearray()
    for y in range(HEIGHT):
        row = values[y * DOUBLE_WIDTH:(y + 1) * DOUBLE_WIDTH]
        out.extend((row[x] | (row[x + 1] << 4)) for x in range(0, DOUBLE_WIDTH, 2))
    return bytes(out)

def encode(values: list[int], width: int) -> bytes:
    if width == DOUBLE_WIDTH:
        raise ValueError("double-byte glyphs require shared-block encoding")
    stride = (width + 3) // 4
    out = bytearray()
    for y in range(HEIGHT):
        row = values[y * width:(y + 1) * width] + [0] * (stride * 4 - width)
        out.extend(sum((row[x + j] & 3) << (2 * j) for j in range(4)) for x in range(0, len(row), 4))
    return bytes(out)

def image_values(path: Path, width: int) -> list[int]:
    image = Image.open(path).convert("L")
    if image.size != (width, HEIGHT):
        image = image.resize((width, HEIGHT), Image.Resampling.LANCZOS)
    return [min(3, max(0, (value + 42) // 85)) for value in image.getdata()]

def export(font: Path, output: Path, limit: int) -> None:
    data = load(font); output.mkdir(parents=True, exist_ok=True); count = 0
    map_lines = ["entry\tA\tB\toffset\tend\twidth\tkind\tfile"]
    for i, a, b, width, start, end in records(data):
        if i >= ASCII_COUNT:
            image = Image.new("L", (DOUBLE_WIDTH, HEIGHT))
            pixels = decode_double(data[start:end])
            image.putdata(pixels)
            kind = "double"
        else:
            image = Image.new("L", (width, HEIGHT))
            image.putdata([v * 85 for v in decode(data[start:end], width)])
            kind = "ascii"
        name = f"entry_{i:04d}.png"
        image.save(output / name)
        map_lines.append(f"{i}\t0x{a:04X}\t0x{b:04X}\t0x{start:X}\t0x{end:X}\t{width}\t{kind}\t{name}")
        count += 1
        if count >= limit:
            break
    (output / "font_map.tsv").write_text("\n".join(map_lines) + "\n", encoding="ascii")
    print(f"exported={count} output={output}")

def atlas(font: Path, output: Path, cols: int, scale: int) -> None:
    data = load(font)
    items = []
    for i, a, b, width, start, end in records(data):
        if i < ASCII_COUNT:
            continue
        image = Image.new("L", (DOUBLE_WIDTH, HEIGHT))
        image.putdata(decode_double(data[start:end]) if i >= ASCII_COUNT else
                      [v * 85 for v in decode(data[start:end], width)])
        items.append((i, a, b, image))
    cell_w, cell_h = 20 * scale, 20 * scale
    rows = (len(items) + cols - 1) // cols
    sheet = Image.new("L", (cols * cell_w, rows * cell_h), 0)
    map_lines = ["atlas_index\tindex_entry\tA\tB\twidth"]
    for n, (i, a, b, image) in enumerate(items):
        tile = Image.new("L", (20, 20), 0)
        tile.paste(image, (0, 0))
        sheet.paste(tile.resize((cell_w, cell_h), Image.Resampling.NEAREST),
                    ((n % cols) * cell_w, (n // cols) * cell_h))
        map_lines.append(f"{n}\t{i}\t0x{a:04X}\t0x{b:04X}\t{image.width}")
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, format="TIFF", compression="raw")
    output.with_suffix(".tsv").write_text("\n".join(map_lines) + "\n", encoding="ascii")
    print(f"atlas={output} size={sheet.width}x{sheet.height} glyphs={len(items)}")

def rebuild(font: Path, glyphs: Path, output: Path, allow_missing: bool) -> None:
    data = bytearray(load(font)); count = 0
    for i, a, b, width, start, end in records(data):
        source = glyphs / f"entry_{i:04d}.png"
        if not source.exists():
            if allow_missing:
                continue
            raise FileNotFoundError(source)
        if i >= ASCII_COUNT:
            image = Image.open(source).convert("L")
            if image.size != (DOUBLE_WIDTH, HEIGHT):
                image = image.resize((DOUBLE_WIDTH, HEIGHT), Image.Resampling.LANCZOS)
            values = [min(15, max(0, (v + 8) // 17)) for v in image.getdata()]
            packed = encode_double(values)
            if len(packed) < end - start:
                raise ValueError(f"entry {i}: encoded double glyph is too short")
            data[start:end] = packed[:end - start]; count += 1
        else:
            packed = encode(image_values(source, width), width)
            if len(packed) != end - start:
                raise ValueError(f"entry {i}: encoded size mismatch")
            data[start:end] = packed; count += 1
    output.parent.mkdir(parents=True, exist_ok=True); output.write_bytes(data)
    print(f"rebuilt={count} size={len(data)} sha256={hashlib.sha256(data).hexdigest()}")

def inspect(font: Path) -> None:
    data = load(font); rs = list(records(data))
    base = rs[0][4] - rs[0][2] * 8
    print(f"size=0x{len(data):X} records={len(rs)} payload=0x{base:X}")
    print(f"widths={sorted({r[3] for r in rs})}")

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("inspect"); p.add_argument("font", type=Path)
    p = sub.add_parser("export"); p.add_argument("font", type=Path); p.add_argument("output", type=Path); p.add_argument("--limit", type=int, default=10**9)
    p = sub.add_parser("atlas"); p.add_argument("font", type=Path); p.add_argument("output", type=Path); p.add_argument("--cols", type=int, default=32); p.add_argument("--scale", type=int, default=2)
    p = sub.add_parser("rebuild"); p.add_argument("font", type=Path); p.add_argument("glyphs", type=Path); p.add_argument("output", type=Path); p.add_argument("--allow-missing", action="store_true")
    args = parser.parse_args()
    if args.command == "inspect": inspect(args.font)
    elif args.command == "export": export(args.font, args.output, args.limit)
    elif args.command == "atlas": atlas(args.font, args.output, args.cols, args.scale)
    else: rebuild(args.font, args.glyphs, args.output, args.allow_missing)

if __name__ == "__main__":
    main()
