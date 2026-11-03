#!/usr/bin/env python3
"""Seven Warriors PS2 — Chinese glyph font hacks.

Builds a Chinese-mapped custom glyph set on top of `font16.sfo`.

Pipeline (each step independent, run in order):

  collect    Scan translation drafts for the distinct Chinese character set.
             - dual-column lines ("orig | 译文") feed the translated text.
             - single-column lines (still original) are reported to stderr so
               translators know what is not yet done.
  alloc      Full-rebuild slot allocation: each Chinese char mapped to a
             double-byte slot index (lead 0x88..0x98 by default), output
             char_to_slot.csv.
  genfonts   Render every mapped char to a 16x16 grayscale PNG
             (entry_<index>.png) using the TTF passed via --font.
  merge      Write the rendered glyphs back into a copy of font16.sfo via
             font_sfo_7w.py rebuild.

The write-back (text_7w.py write) is integrated separately: when a candidate
line contains Chinese it is converted to the CP932 byte(s) of its mapped slot.

Everything reads read-only inputs and writes under work/font.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# shared paths / defaults
# ---------------------------------------------------------------------------

TRANSLATION_DIR = Path("work/translation")
FONT_DIR = Path("work/font")
ORIG_SFO = Path("unpacked/CDIMAGE_FULL/seven_data_link/arita/font16.sfo")
INDEX_MAP = FONT_DIR / "index_to_cp932.csv"
CHAR_SLOT = FONT_DIR / "char_to_slot.csv"
GLYPH_DIR = FONT_DIR / "glyphs"


# ---------------------------------------------------------------------------
# collect
# ---------------------------------------------------------------------------

def iter_candidate_lines(src: bytes) -> list[tuple[str, int, str]]:
    """Return (kind, seq, raw_line) for edit-able candidate lines.

    Splits translation drafts into their editable text span. A *translateable*
    line is one of:
      spl:   `[0000] @0x... len=.. | text`
      nht/fhm: `[text:N] len=.. | text`
      balloon.bhf: `[text:N] slot=.. off=.. len=.. | text`
      movie: `HHHH:HHHH|text`
    We return the raw suffix text after the first '|'.
    """
    import re
    txt = src.decode("utf-8", errors="replace")
    lines: list[tuple[str, int, str]] = []
    spl_re = re.compile(
        r"^\[\d+\]\s+@0x[0-9A-Fa-f]+\s+len=\d+\s\|\s?(.*?)\s*$", re.M)
    nht_re = re.compile(r"^\[text:(\d+)\]\s+(?:(?:@0x[0-9A-Fa-f]+|off=0x[0-9A-Fa-f]+)\s+)?len=\d+\s\|\s?(.*?)\s*$", re.M)
    member_re = re.compile(
        r"^\[text:\d+\]\s+pool=(?:pool1|pool2|tail)\s+"
        r"(?:(?:row|chunk)=\d+\s+)?off=0x[0-9A-Fa-f]+\s+len=\d+(?:\s+c=\d+\s+d=\d+)?"
        r"\s+\|\s?(.*?)\s*$", re.M)
    balloon_re = re.compile(
        r"^\[text:\d+\]\s+slot=\d+\s+off=(?:auto|0x[0-9A-Fa-f]+)\s+"
        r"len=\d+(?:\s+src_hex=[0-9A-Fa-f ]+)?\s+\|\s?(.*?)\s*$", re.M)
    movie_re = re.compile(r"^(\s*\d{1,4}\s*:\s*\d{1,4})\|(.*)$", re.M)
    for m in spl_re.finditer(txt):
        seq = m.group(0).split("]", 1)[0][1:].strip()  # index in brackets
        lines.append(("spl", int(seq), m.group(1)))
    for m in nht_re.finditer(txt):
        lines.append(("nht", int(m.group(1)), m.group(2)))
    for m in member_re.finditer(txt):
        lines.append(("member", 0, m.group(1)))
    for m in balloon_re.finditer(txt):
        lines.append(("balloon", 0, m.group(1)))
    # Movie subtitle lines are "HHHH:HHHH|text": the whole span after '|' is
    # already the final (translated) payload text, unlike spl where "orig|译"
    # keeps both.  Prefix with '|' so extract_translated() returns it verbatim.
    for m in movie_re.finditer(txt):
        lines.append(("movie", int(m.group(1).split(":", 1)[0]), "|" + m.group(2)))
    return lines


def extract_translated(text: str) -> str:
    """If a candidate span has "orig | 译文", return the translation (after the
    last '|'). Otherwise return '' meaning 'not translated yet'."""
    if "|" in text:
        return text.rsplit("|", 1)[1].strip()
    return ""


def zi(text: str) -> set[str]:
    """CJK unified ideographs used in a text span (the Chinese glyph need-set)."""
    return {c for c in text if "\u4e00" <= c <= "\u9fff"}


def cmd_collect(args) -> None:
    chars: set[str] = set()
    unresolved: int = 0
    files = sorted(TRANSLATION_DIR.rglob("*.txt"))
    for p in files:
        rel = p.relative_to(TRANSLATION_DIR).as_posix()
        for kind, seq, raw in iter_candidate_lines(p.read_bytes()):
            tr = extract_translated(raw)
            if tr:
                chars |= zi(tr)
            elif raw.strip():
                # not translated: tell the translator where it is
                unresolved += 1
                if not args.quiet:
                    print(f"[NOT-TRANSLATED] {rel} [{kind}:{seq}] {raw[:60]}",
                          file=sys.stderr)
    out = FONT_DIR / "char_set.txt"
    FONT_DIR.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(sorted(chars)), encoding="utf-8")
    if not args.quiet:
        print(f"collect: distinct_chars={len(chars)} unresolved_lines={unresolved} "
              f"char_set={out}")


# ---------------------------------------------------------------------------
# alloc
# ---------------------------------------------------------------------------

def cmd_alloc(args) -> None:
    """Full-rebuild allocation: every CJK char gets a slot; previous map discarded."""
    # Only ever allocate real CJK unified ideographs. Translation drafts or a
    # hand-edited char_set can otherwise carry stray non-CJK code points
    # (e.g. U+03F0 or U+FFFD replacement chars) that cannot round-trip through
    # CP932 and would poison the slot map / write-back.
    raw = Path(args.charset).read_text(encoding="utf-8")
    chars = sorted({c for c in raw if "\u4e00" <= c <= "\u9fff"})
    if not chars:
        print("alloc: char_set.txt contains no CJK ideographs; nothing to map",
              file=sys.stderr)
        sys.exit(2)
    # slot candidates: all non-placeholder double-byte index entries, prefer
    # kanji lead region 0x88..0x98, then the rest.
    import struct
    data = ORIG_SFO.read_bytes()
    cand_kanji: list[int] = []
    cand_other: list[int] = []
    # The game maps CP932 (lead,trail) -> glyph index as
    #     index = lookup_table[lead] + trail
    # where lookup_table is the 256 x u16 table at font offset 0x10.
    # Its exact inverse (index -> trail) is  trail = index - lookup_table[lead].
    lead_lookup = [struct.unpack_from("<H", data, 0x10 + lead * 2)[0]
                   for lead in range(256)]
    for i in range(97, 3518):
        A, B = struct.unpack_from("<HH", data, 0x210 + i * 4)
        if A == 0x00FF and B == 0:
            continue  # placeholder, skip
        lead = A & 0xFF
        (cand_kanji if 0x88 <= lead <= 0x98 else cand_other).append(i)
    slots = cand_kanji + cand_other
    if len(chars) > len(slots):
        print(f"alloc: OVERFLOW need {len(chars)} chars but only {len(slots)} "
              f"slots available", file=sys.stderr)
        sys.exit(2)
    FONT_DIR.mkdir(parents=True, exist_ok=True)
    # Build index -> (lead,trail) DIRECTLY from the font, never from a CSV that
    # may be stale (the old one hardcoded a wrong 0x40 start trail for the 0x82
    # region; the true start there is 0x4F -> exact-inverse formula fixes it).
    rev_cp932 = {}
    for i in range(97, 3518):
        if i not in set(slots):
            continue
        A, _ = struct.unpack_from("<HH", data, 0x210 + i * 4)
        lead = A & 0xFF
        trail = i - lead_lookup[lead]
        rev_cp932[i] = (lead, trail)
    with CHAR_SLOT.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["char", "index", "lead", "trail"])
        # We always build a FRESH mapping: char -> slot by order of chars list.
        for ch, idx in zip(chars, slots):
            lead, trail = rev_cp932.get(idx, (0, 0))
            w.writerow([ch, idx, f"{lead:02X}", f"{trail:02X}"])
    if not getattr(args, "quiet", False):
        print(f"alloc: mapped {len(chars)} chars -> {len(slots)} slots  {CHAR_SLOT}")


# ---------------------------------------------------------------------------
# genfonts
# ---------------------------------------------------------------------------

def cmd_genfonts(args) -> None:
    from PIL import Image, ImageDraw, ImageFont
    rows = list(csv.DictReader(CHAR_SLOT.open(encoding="utf-8-sig")))
    # pick font
    font_path = Path(args.font) if args.font else None
    font = _load_font(font_path)
    GLYPH_DIR.mkdir(parents=True, exist_ok=True)
    glyphs: dict[int, str] = {}
    for r in rows:
        ch = r["char"]; idx = int(r["index"])
        glyphs[idx] = _render_chinese(ch, font, int(args.size))
    # write only provided entries as entry_<index>.png; overwrite existing glyph
    n = 0
    for idx, img in glyphs.items():
        img.save(GLYPH_DIR / f"entry_{idx:04d}.png")
        n += 1
    print(f"genfonts: rendered {n} glyphs in {GLYPH_DIR} (font={font.path})")


def _load_font(path: Path | None):
    """Load the TTF given on the command line; no system-font fallback."""
    from PIL import ImageFont
    if path is None:
        raise FileNotFoundError(
            "genfonts needs an explicit font: pass --font <path.ttf> "
            "(the system font directory is not searched)")
    if not path.exists():
        raise FileNotFoundError(
            f"font not found: {path} "
            "(the system font directory is not searched)")
    return ImageFont.truetype(str(path), 24)


def _render_chinese(ch: str, font, size: int) -> "Image":
    from PIL import Image, ImageDraw
    img = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(img)
    bb = d.textbbox((0, 0), ch, font=font)
    w = bb[2] - bb[0]; h = bb[3] - bb[1]
    d.text(((size - w) / 2 - bb[0], (size - h) / 2 - bb[1]), ch, font=font, fill=255)
    return img


# ---------------------------------------------------------------------------
# merge (rebuild)
# ---------------------------------------------------------------------------

def cmd_merge(args) -> None:
    import subprocess, sys
    out = FONT_DIR / "font16_ch.sfo"
    cmd = [sys.executable, "tools/font_sfo_7w.py", "rebuild",
           str(ORIG_SFO), str(GLYPH_DIR), str(out), "--allow-missing"]
    print(" ".join(cmd))
    subprocess.run(cmd, check=True)
    print(f"merge: wrote {out}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("collect", help="scan translations for Chinese char set")
    c.add_argument("--quiet", action="store_true", help="suppress unresolved log")
    c.set_defaults(func=cmd_collect)

    a = sub.add_parser("alloc", help="full-rebuild slot allocation")
    a.add_argument("--charset", default=str(FONT_DIR / "char_set.txt"))
    a.add_argument("--quiet", action="store_true", help="suppress summary output")
    a.set_defaults(func=cmd_alloc)

    g = sub.add_parser("genfonts", help="render Chinese glyphs to 16x16 PNG")
    g.add_argument("--font", default=None, help="path to a TTF font")
    g.add_argument("--size", type=int, default=24, help="render canvas (px)")
    g.set_defaults(func=cmd_genfonts)

    m = sub.add_parser("merge", help="rebuild font16.sfo with Chinese glyphs")
    m.set_defaults(func=cmd_merge)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    try:
        main()
    except FileNotFoundError as exc:
        raise SystemExit(f"[font_hack] {exc}")
