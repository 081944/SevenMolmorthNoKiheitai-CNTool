#!/usr/bin/env python3
"""Seven Warriors PS2 — GIM image extractor.

Format fully reverse-engineered from the game's own parser (EE .text
0x00101C20 in SLPS_250.25):

Header (0x30 bytes):
  0x00  "GIM\0" magic
  0x04  u32   (1 for disk files; 0 / 0xCDCDCDCD possible; not layout-critical)
  0x10  u32   variant id (0x13000000, 0x14000000, 0x0200 etc.)
  0x13  byte  format selector -> determines bytes-per-pixel
  0x1C  u16 LE width
  0x1E  u16 LE height

Format byte at 0x13 -> (bytes per pixel, palette?):
  0x13 / other  -> 1 Bpp  (8bpp  indexed, palette present)   [243 files]
  0x00          -> 4 Bpp  (32bpp direct RGBA, NO palette)    [50 files]
  0x14          -> 0.5Bpp (4bpp  indexed, palette present)   [5 files]

For indexed formats (8bpp / 4bpp) the layout is "palette-back":
  8bpp: pixel indices at 0x30, size w*h; palette at 0x30 + w*h, 256xRGBA.
  4bpp: two pixels per byte at 0x30 (LOW nibble first), size ceil(w*h/2);
        palette at 0x30 + ceil(w*h/2), 16xRGBA.
For direct 32bpp: pixel data at 0x20, size w*h*4, no palette.
Palette / pixel color bytes are RGBA (verified against in-game faces).

Alpha handling (matches in-game rendering): the game uses alpha testing --
pixels whose palette/pixel alpha == 0 are discarded (transparent); pixels with
alpha > 0 are shown fully opaque. So the exported PNG forces alpha=255 for any
non-zero alpha and keeps alpha=0 transparent. (Raw CLUT alphas are ~0x7d=125;
using them directly makes every image look faded/semi-transparent.)
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

from PIL import Image


def u16(b, o):
    return b[o] | (b[o + 1] << 8)


def u32(b, o):
    return int.from_bytes(b[o:o + 4], "little")


def is_gim(d: bytes) -> bool:
    return d[:4] == b"GIM\x00"


def fmt_byte(d: bytes) -> int:
    return d[0x13]


def parse(d: bytes):
    """Return (w, h, kind) or None if unusable.

    kind in {"8bpp", "4bpp", "32bpp"}.
    """
    w, h = u16(d, 0x1C), u16(d, 0x1E)
    if not (1 <= w <= 4096 and 1 <= h <= 4096):
        return None
    fb = fmt_byte(d)
    if fb == 0x00:
        kind = "32bpp"
        need = 0x20 + w * h * 4
        if len(d) < need:
            return None
        if abs(len(d) - need) > 8:
            # allow tiny drift only; 32bpp files are exactly header+pixels
            return None
    elif fb == 0x14:
        kind = "4bpp"
        px = (w * h + 1) // 2
        need = 0x20 + px + 0x10 + 64
        if len(d) < need:
            return None
    else:
        kind = "8bpp"
        need = 0x20 + w * h + 0x10 + 1024
        if len(d) < need:
            return None
    return w, h, kind


def _swap_bit34(i):
    """8bpp pixel index: swap bit3 and bit4.

    Verified against an in-game GS texture dump (ttr_cl): the correct color
    for pixel index i is palette[swap_bit34(i)]. Without this the image shows
    scattered wrong colors ('noise').
    """
    return (i & ~0x18) | ((i & 0x08) << 1) | ((i & 0x10) >> 1)


def _is_alpha_mask(colors) -> bool:
    """True if palette is single-color + alpha gradient (title/water/shock masks)."""
    rgbs = set()
    for r, g, b, a in colors:
        if a > 0:
            rgbs.add((r >> 4, g >> 4, b >> 4))
    return len(rgbs) <= 2


def _conv(rgba, keep_alpha):
    """Convert a palette/pixel color for output.

    keep_alpha=False (solid textures): alpha test -- a>0 -> 255, a==0 -> 0.
    keep_alpha=True  (alpha masks): alpha x2 -- a>0 -> min(255, a*2), a==0 -> 0.
    Verified against in-game logo: the mask body (raw alpha~128) must render
    opaque (->255) while edges keep a smooth gradient.
    """
    r, g, b, a = rgba
    if a == 0:
        return (r, g, b, 0)
    if not keep_alpha:
        return (r, g, b, 255)
    return (r, g, b, min(255, a * 2))


def build_indexed(px: bytes, pal: bytes, w: int, h: int, pal_entries: int):
    img = Image.new("RGBA", (w, h))
    colors = [tuple(pal[i * 4:i * 4 + 4]) for i in range(pal_entries)]
    keep = _is_alpha_mask(colors)
    img.putdata([_conv(colors[x], keep) if x < pal_entries
                 else (255, 0, 255, 255) for x in px])
    return img


def render(d: bytes, w: int, h: int, kind: str) -> Image.Image:
    if kind == "32bpp":
        px = [tuple(d[0x20 + i * 4:0x20 + i * 4 + 4]) for i in range(w * h)]
        keep = _is_alpha_mask(px)
        img = Image.new("RGBA", (w, h))
        img.putdata([_conv(c, keep) for c in px])
        return img
    if kind == "8bpp":
        px = d[0x20:0x20 + w * h]
        pal = d[0x20 + w * h + 0x10:0x20 + w * h + 0x10 + 1024]
        colors = [tuple(pal[i * 4:i * 4 + 4]) for i in range(256)]
        # Pixel index needs bit3<->bit4 swap (verified against GS texture dump).
        # Keep RAW alpha (dump shows semi-transparent alpha 50-84, NOT doubled).
        img = Image.new("RGBA", (w, h))
        img.putdata([(colors[_swap_bit34(x)][0], colors[_swap_bit34(x)][1],
                      colors[_swap_bit34(x)][2], colors[_swap_bit34(x)][3])
                     for x in px])
        return img
    # 4bpp: low nibble first, palette index used directly (NO bitrev).
    # alpha: x2 for masks. Verified against in-game logo (logo_n4_a2).
    raw = d[0x20:0x20 + (w * h + 1) // 2]
    pal = d[0x20 + (w * h + 1) // 2 + 0x10:0x20 + (w * h + 1) // 2 + 0x10 + 64]
    colors = [tuple(pal[i * 4:i * 4 + 4]) for i in range(16)]
    px = bytearray(w * h)
    for i, byte in enumerate(raw):
        lo = byte & 0x0F
        hi = byte >> 4
        px[i * 2] = lo
        if i * 2 + 1 < w * h:
            px[i * 2 + 1] = hi
    keep = _is_alpha_mask(colors)
    img = Image.new("RGBA", (w, h))
    img.putdata([_conv(colors[x], keep) for x in px])
    return img


def _opaque_indices(colors):
    """Indices of palette entries usable for opaque pixels (alpha > 0)."""
    return [i for i, c in enumerate(colors) if c[3] > 0]


def _nearest(colors, rgb):
    """Return palette index with the smallest RGB distance to rgb (non-alpha)."""
    tr, tg, tb = rgb
    best, best_d = 0, None
    for i in _opaque_indices(colors):
        r, g, b, _ = colors[i]
        dr, dg, db = r - tr, g - tg, b - tb
        dist = dr * dr + dg * dg + db * db
        if best_d is None or dist < best_d:
            best_d, best = dist, i
    return best


def _unique_pixel_map(colors, px_rgba, exact):
    """Map each unique pixel RGBA to a palette index.

    ``exact`` maps rendered-color -> palette index (built with the same
    conversion as render()); unedited pixels hit it precisely, so identity
    rebuild is lossless. Only genuinely new colors fall back to nearest-RGB.
    """
    transparent_idx = next((i for i, c in enumerate(colors) if c[3] == 0), 0)
    mapping = {}
    for c in px_rgba:
        idx = exact.get(c)
        if idx is None and c[3] != 0:
            idx = _nearest(colors, c[:3])
        if idx is None:
            idx = transparent_idx
        mapping.setdefault(c, idx)
    return mapping


def rebuild(d: bytes, img: Image.Image) -> bytes:
    """Encode an edited PNG back into a GIM, preserving the original format.

    - 8bpp / 4bpp: keeps the original 256/16-entry palette. Unedited pixels
      re-map losslessly to their exact source index (identical to render());
      pixels with colors outside the palette fall back to nearest-RGB, and
      transparent pixels go to the first alpha==0 entry. The bit3<->bit4 swap
      is applied back for 8bpp.
    - 32bpp: writes RGBA directly, keeping the original alpha byte for every
      pixel that stays opaque (PNG alpha 0 clears to transparent).
    Raises ValueError on size mismatch.
    """
    par = parse(d)
    if par is None:
        raise ValueError("not a usable GIM")
    w, h, kind = par
    img = img.convert("RGBA")
    if img.size != (w, h):
        raise ValueError(f"size mismatch: {img.width}x{img.height}, "
                         f"expected {w}x{h}")
    px = list(img.getdata())
    out = bytearray(d)

    if kind == "32bpp":
        for i, c in enumerate(px):
            off = 0x20 + i * 4
            out[off:off + 3] = c[:3]
            if c[3] == 0:
                out[off + 3] = 0  # keep transparent
            else:
                orig = d[off + 3]
                out[off + 3] = orig if orig > 0 else 125
        return bytes(out)

    if kind == "8bpp":
        colors = [tuple(d[0x20 + w * h + 0x10 + i * 4:
                          0x20 + w * h + 0x10 + i * 4 + 4]) for i in range(256)]
        # 8bpp render keeps raw palette colors (no alpha conversion)
        exact = {}
        for i, col in enumerate(colors):
            exact.setdefault(col, i)
        mapping = _unique_pixel_map(colors, px, exact)
        for i, c in enumerate(px):
            out[0x20 + i] = _swap_bit34(mapping[c])
        return bytes(out)

    # 4bpp: low nibble first; render applies the alpha x2 mask conversion
    colors = [tuple(d[0x20 + (w * h + 1) // 2 + 0x10 + i * 4:
                      0x20 + (w * h + 1) // 2 + 0x10 + i * 4 + 4])
              for i in range(16)]
    keep = _is_alpha_mask(colors)
    exact = {}
    for i, col in enumerate(colors):
        exact.setdefault(_conv(col, keep), i)
    mapping = _unique_pixel_map(colors, px, exact)
    raw = bytearray((w * h + 1) // 2)
    for i, c in enumerate(px):
        byte = mapping[c]
        if i % 2 == 0:
            raw[i // 2] = byte & 0x0F
        else:
            raw[i // 2] |= (byte & 0x0F) << 4
    out[0x20:0x20 + len(raw)] = bytes(raw)
    return bytes(out)


def cmd_extract(args):
    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    srcs = [args.input] if args.input.is_file() else sorted(
        p for p in args.input.rglob("*") if p.suffix.lower() == ".gim")
    ok = uns = 0
    for p in srcs:
        d = p.read_bytes()
        rel = str(p if args.input.is_file() else p.relative_to(args.input))
        if not is_gim(d):
            rows.append({"file": rel, "status": "not-gim", "w": "", "h": "",
                         "png": ""})
            uns += 1
            continue
        par = parse(d)
        if par is None:
            rows.append({"file": rel, "status": "unsupported", "w": "", "h": "",
                         "png": ""})
            uns += 1
            continue
        w, h, kind = par
        base = rel.replace("/", "__").replace("\\", "__")
        if base.lower().endswith(".gim"):
            base = base[:-4]
        try:
            img = render(d, w, h, kind)
        except Exception:
            rows.append({"file": rel, "status": "render-fail", "w": w, "h": h,
                         "png": ""})
            uns += 1
            continue
        name = f"{base}__{kind}.png"
        img.save(out / name)
        ok += 1
        rows.append({"file": rel, "status": "ok", "w": w, "h": h,
                     "png": name})
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with args.report.open("w", encoding="utf-8-sig", newline="") as fh:
            wr = csv.DictWriter(fh, fieldnames=["file", "status", "w", "h", "png"])
            wr.writeheader()
            wr.writerows(rows)
        print(f"report={args.report}")
    print(f"gim ok={ok} unsupported={uns} out={out}")


def cmd_write(args):
    d = args.input.read_bytes()
    if not is_gim(d):
        raise SystemExit(f"{args.input}: not a GIM file")
    img = Image.open(args.png)
    try:
        out = rebuild(d, img)
    except ValueError as e:
        raise SystemExit(f"refusing to write: {e}")
    args.output.write_bytes(out)
    par = parse(d)
    print(f"wrote {args.output} ({par[2]}, {par[0]}x{par[1]}, "
          f"{len(d)} -> {len(out)} bytes)")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    ex = sub.add_parser("extract", help="decode a .gim (or unpacked root) to PNGs")
    ex.add_argument("input", type=Path, help="a .gim file or unpacked root")
    ex.add_argument("output", type=Path, help="output directory for PNGs")
    ex.add_argument("--report", type=Path, help="optional CSV report path")
    ex.set_defaults(func=cmd_extract)

    wr = sub.add_parser("write", help="re-encode a PNG back into a GIM")
    wr.add_argument("input", type=Path, help="source .gim to modify")
    wr.add_argument("png", type=Path, help="edited PNG")
    wr.add_argument("output", type=Path, help="destination .gim")
    wr.set_defaults(func=cmd_write)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
