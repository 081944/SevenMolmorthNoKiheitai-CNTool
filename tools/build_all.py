#!/usr/bin/env python3
"""Seven Warriors PS2 — one-command build (font hack + text write-back + repack).

Pipeline:
  1. collect   distinct Chinese chars from translation drafts
  2. alloc     append-only slot mapping (char -> CP932 lead/trail via font16 index table)
  3. write-font write the Chinese glyphs into common.fhm sub0 (default) or
                both sub0 + font16.sfo (--font-target both), rendered by
                font_sub0_7w.py directly from a TTF
  4. write     spl / nht / fhm translation drafts back into the unpacked tree
  4.5 write-fhn write fixed-field UI text embedded in selected FHN files
  5. write-movie rewrite MOVIMAGE.BIN movie subtitles with the same fresh
                slot map (work/build/MOVIMAGE.BIN)
  5.5 (optional) write edited work/videoedit/19_op.mp4 back as MPEG-2 video
                (also work/build/MOVIMAGE.BIN)
  6. (optional) write-image re-encode surviving PNGs under work/imageedit back
                into the unpacked tree (ITE / FHN-embedded / GIM); this is
                opt-in because ITE MPEG re-encoding can introduce artifacts
  7. install   put the rebuilt font(s) into the unpacked tree
  8. repack    rebuild CDIMAGE.BIN from the updated unpacked tree + manifest
  9. pack      (optional) run mkps2iso through a temporary XML that points at
                the rebuilt CDIMAGE.BIN and MOVIMAGE.BIN to produce a bootable ISO

Default font target = common.fhm sub0 (the dialogue-rendering font). The default
allocator is append-only so existing save-compatible character codes stay
fixed. Pass --fresh-font-map only when old saves do not need to work. Pass
--font-target both to ALSO write font16.sfo (for UI/other text that renders
from the 16x16 font).

Usage:
  python tools/build_all.py \
      --image mkps2iso-1.1.1-win64/Seven/CDIMAGE.BIN.prev \
      --unpacked unpacked/CDIMAGE_FULL \
      --translation work/translation \
      [--font work/font/chinese.ttf] \
      [--font-target common|both] \
      [--out work/build/CDIMAGE.BIN] \
      [--pack-dir mkps2iso-1.1.1-win64/Seven] \
      [--mkps2iso mkps2iso-1.1.1-win64/mkps2iso.exe]

All paths are resolved against the project root, so the script works from any
working directory.  Fonts are licensed material: keep them under work/font/,
which is excluded from version control.
"""
from __future__ import annotations

import argparse
import csv
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import text_7w

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
FONT_HACK = TOOLS / "font_hack_7w.py"
FONT_ALLOC_STABLE = TOOLS / "font_alloc_stable_7w.py"
FONT_SUB0 = TOOLS / "font_sub0_7w.py"
TEXT = TOOLS / "text_7w.py"
IMAGE = TOOLS / "image_7w.py"
MOVIE = TOOLS / "movie_subtitle_7w.py"
MOVIE_VIDEO = TOOLS / "movimage_reinject.py"
FHN_TEXT = TOOLS / "fhn_text_7w.py"
UNPACK = TOOLS / "unpack_cdimage.py"
WORK = ROOT / "work"
# The CLI normally runs text_7w with cwd=ROOT. Keep its cached glyph map
# absolute when SPL rebuilding is performed in-process below.
text_7w._CSLOT_PATH = WORK / "font" / "char_to_slot.csv"
SPL_TRANSLATED_RE = re.compile(r"^\[\d+\] @0x[0-9A-Fa-f]+ len=\d+ \| .*\|", re.M)

# MOVIMAGE.BIN is a root-level ISO file (sibling of CDIMAGE.BIN), so it is NOT
# part of the unpacked CDIMAGE tree. It is edited in place: original bytes come
# from unpak/, the rewritten copy lands in work/build/.
MOVIE_IN = ROOT / "unpak" / "MOVIMAGE.BIN"
MOVIE_OUT = WORK / "build" / "MOVIMAGE.BIN"
MOVIE_TDIR = WORK / "translation" / "movie_subtitles"
MOVIE_VDIR = WORK / "videoedit"
MOVIE_VIDEO_INPUT = MOVIE_VDIR / "19_op.mp4"
MOVIE_VIDEO_TMP = ROOT / "temp" / "movie_video_build"

PACK_DIR_DEFAULT = ROOT / "mkps2iso-1.1.1-win64" / "Seven"
DEFAULT_IMAGE = PACK_DIR_DEFAULT / "CDIMAGE.BIN.prev"
DEFAULT_ORIGINAL_TREE = ROOT / "temp" / "CDIMAGE_ORIGINAL_CLEAN2"
SPL_FOOTER = bytes.fromhex("000000000000000010000000ffffffff")

COMMON_FHM = ROOT / "unpacked/CDIMAGE_FULL/seven_data_link/chiibaya/common/common.fhm"
FONT16_SFO = ROOT / "unpacked/CDIMAGE_FULL/seven_data_link/arita/font16.sfo"


@dataclass
class Ctx:
    image: Path
    original_tree: Path | None
    unpacked: Path
    translation: Path
    font: str | None
    font_target: str
    out: Path
    pack_dir: Path | None
    mkps2iso: Path | None


def trim_padded_spl(rel: str, data: bytes) -> bytes:
    """Remove CDIMAGE slot padding after a valid SPL footer."""
    if Path(rel).suffix.lower() != ".spl":
        return data
    footer = data.rfind(SPL_FOOTER)
    if footer >= 0 and not data[footer + len(SPL_FOOTER):].strip(b"\0"):
        return data[:footer + len(SPL_FOOTER)]
    return data


def project_path(p: Path | str | None) -> Path | None:
    """Resolve a CLI path against the project root instead of the CWD."""
    if p is None or p == "":
        return None
    path = Path(p)
    return path if path.is_absolute() else (ROOT / path)


def run(cmd: list):
    # Sub-tools resolve their own relative paths (work/, unpacked/, ...) against
    # the project root, regardless of the directory build_all was launched from.
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    # quiet sub-commands only emit on errors/skips; surface that, plus failures.
    if r.stdout:
        print(r.stdout, end="")
    if r.stderr:
        print(r.stderr, end="", file=sys.stderr)
    if r.returncode != 0:
        raise SystemExit(f"command failed: {' '.join(cmd)}")
    return r


def run_cwd(exe: str, args: list, cwd: str):
    r = subprocess.run([exe, *args], cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.stdout:
        print(r.stdout, end="")
    if r.stderr:
        print(r.stderr, end="", file=sys.stderr)
    if r.returncode != 0:
        raise SystemExit(f"command failed: {exe} {' '.join(args)}")
    return r


def spl_orig(unpacked: Path, stem: str) -> Path | None:
    """Locate the original .spl for a translation-draft stem."""
    cand = unpacked / "seven_data_link" / "rpgdata" / "output" / f"{stem}.spl"
    return cand if cand.is_file() else None


def restore_from_image(ctx: Ctx, rel: str) -> None:
    """Overwrite <rel> in the unpacked tree with the pristine bytes read from
    the original image (via the manifest offset table).

    write-back for .nht is content-driven (node boundaries shift when glyph
    bytes replace Japanese), so re-running build_all on an already-translated
    tree misaligns entries. Restoring the original file first makes every build
    idempotent without needing a hand-maintained backup copy.
    """
    manifest = ctx.unpacked / "manifest.tsv"
    target = ctx.unpacked / rel
    if not manifest.is_file():
        print(f"[restore] no manifest.tsv; cannot restore {rel}")
        return
    index = None
    with manifest.open(encoding="utf-8") as fh:
        r = csv.DictReader(fh, delimiter="\t")
        for row in r:
            if row.get("output_path") == rel:
                index = int(row["index"])
                break
    if index is None:
        print(f"[restore] {rel} not in manifest; skipping")
        return
    pristine = ctx.original_tree / rel if ctx.original_tree else None
    if pristine is not None and pristine.is_file():
        data = pristine.read_bytes()
    else:
        with ctx.image.open("rb") as f:
            f.seek(index * 8)
            sector, size = struct.unpack("<II", f.read(8))
            f.seek(sector * 0x800)
            data = f.read(size)
    # Original-tree files may retain the CDIMAGE slot padding too;
    data = trim_padded_spl(rel, data)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def write_spl(ctx: Ctx) -> int:
    tdir = ctx.translation / "spl"
    if not tdir.is_dir():
        return 0
    wrote = 0
    for cand in sorted(tdir.glob("*.txt")):
        rel = f"seven_data_link/rpgdata/output/{cand.stem}.spl"
        restore_from_image(ctx, rel)
        orig = spl_orig(ctx.unpacked, cand.stem)
        if orig is None:
            print(f"[skip] no original spl for {cand.name}")
            continue
        out = ctx.unpacked / "seven_data_link" / "rpgdata" / "output" / f"{cand.stem}.spl"
        out.parent.mkdir(parents=True, exist_ok=True)
        print(f"[spl] {cand.name}")
        rows = text_7w.parse_candidate(cand)
        rebuilt, result = text_7w.rebuild_spl(orig.read_bytes(), rows)
        translated = len(SPL_TRANSLATED_RE.findall(cand.read_text(encoding="utf-8-sig")))
        ignored = result.get("ignored", 0)
        if translated >= 5 and ignored >= max(5, translated // 2):
            raise SystemExit(
                f"[spl] {cand.name}: {ignored}/{translated} translated rows did not "
                "match the restore source. Use a pristine Japanese --image or "
                "--original-tree; refusing to mix old text bytes with a new font map.")
        if result.get("ignored") or result.get("relocated") or result.get("unencodable") or result.get("mismatch"):
            print(result)
        out.write_bytes(rebuilt)
        wrote += 1
    return wrote


def write_nht(ctx: Ctx) -> int:
    tdir = ctx.translation / "nht"
    if not tdir.is_dir():
        return 0
    wrote = 0
    for cand in sorted(tdir.glob("*.txt")):
        stem = cand.stem
        # candidate files are e.g. endless.nht.txt / rpg.nht.txt -> endless / rpg
        base = stem[:-4] if stem.endswith(".nht") else stem
        rel = f"seven_data_link/help/{base}.nht"
        restore_from_image(ctx, rel)
        orig = ctx.unpacked / "seven_data_link" / "help" / f"{base}.nht"
        if not orig.is_file():
            print(f"[skip] no original nht for {cand.name}")
            continue
        out = ctx.unpacked / "seven_data_link" / "help" / f"{base}.nht"
        run([sys.executable, str(TEXT), "nht", "write", str(orig), str(cand), str(out), "--quiet"])
        wrote += 1
    return wrote


def write_elf(ctx: Ctx) -> int:
    """Write translated ELF text (work/translation/elf/*.txt) back into the
    executable that mkps2iso embeds (pack_dir/SLPS_250.25).

    Restores the pristine executable from work/elf/SLPS_250.25 first so every
    build is idempotent (re-encoding glyph-slot bytes would otherwise shift
    node layout).
    """
    tdir = ctx.translation / "elf"
    if not tdir.is_dir():
        return 0
    if ctx.pack_dir is None:
        print("[skip] elf write needs --pack-dir")
        return 0
    pristine = ROOT / "work" / "elf" / "SLPS_250.25"
    target = ctx.pack_dir / "SLPS_250.25"
    if not pristine.is_file():
        print(f"[skip] no pristine ELF at {pristine}; "
              f"cannot restore before elf write")
        return 0
    # ELF candidates must preserve complete NUL-terminated records. The full
    # line-level scan splits embedded newlines and is unsafe for write-back.
    candidate = tdir / "slps_250.25.txt"
    if not candidate.is_file():
        print(f"[skip] missing record-level ELF candidate {candidate}")
        return 0
    wrote = 0
    for cand in [candidate]:
        if not target.is_file():
            print(f"[skip] no SLPS_250.25 under {ctx.pack_dir}")
            continue
        # restore pristine bytes, then write the translation over them
        target.write_bytes(pristine.read_bytes())
        elf_cmd = [sys.executable, str(TEXT), "elf", "write",
                   str(target), str(cand), str(target), "--quiet"]
        elf_manifest = ROOT / "work" / "report" / "slps_250_elf_full_refresh.tsv"
        if elf_manifest.is_file():
            elf_cmd += ["--manifest", str(elf_manifest)]
        run(elf_cmd)
        wrote += 1
    return wrote


def write_fhm(ctx: Ctx) -> int:
    tdir = ctx.translation / "fhm"
    if not tdir.is_dir():
        return 0
    wrote = 0
    for cand in sorted(tdir.glob("*.txt")):
        base = cand.stem[:-4] if cand.stem.endswith(".fhm") else cand.stem
        rel = f"seven_data_link/chiibaya/common/{base}.fhm"
        restore_from_image(ctx, rel)
        orig = ctx.unpacked / "seven_data_link" / "chiibaya" / "common" / f"{base}.fhm"
        if not orig.is_file():
            print(f"[skip] no original fhm for {cand.name}")
            continue
        out = ctx.unpacked / "seven_data_link" / "chiibaya" / "common" / f"{base}.fhm"
        run([sys.executable, str(TEXT), "fhm", "write", str(orig), str(cand), str(out), "--quiet"])
        wrote += 1
    return wrote


def write_fhn_text(ctx: Ctx) -> int:
    """Write fixed-field text candidates embedded in selected UI FHN files.

    ``fhn_text_7w.py`` restores each source file from ``original_tree`` (or
    the CDIMAGE manifest) before applying edits.  This keeps the operation
    deterministic when build_all is run repeatedly and preserves the FHN
    control/parameter bytes following each 0x40-byte text field.
    """
    tdir = ctx.translation / "fhn"
    if not tdir.is_dir():
        return 0
    cmd = [sys.executable, str(FHN_TEXT), "write",
           "--unpacked", str(ctx.unpacked),
           "--translation", str(tdir),
           "--image", str(ctx.image)]
    if ctx.original_tree:
        cmd += ["--original-tree", str(ctx.original_tree)]
    run(cmd)
    return len(list(tdir.glob("*.txt")))


def char_slot_to_sub0_map(csv_path: Path, out_tsv: Path) -> int:
    """Build the char->CP932 map (sub0_map.tsv) from alloc's char_to_slot.csv.

    alloc stores (char, slot_index, lead, trail); lead/trail is the CP932 byte
    the game actually looks up. The same byte resolves to the right glyph index
    in BOTH fonts via each font's own lead-lookup table.
    """
    rows = []
    with csv_path.open(encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            ch = r["char"]
            if len(ch) != 1:
                continue
            lead = int(r["lead"], 16); trail = int(r["trail"], 16)
            if not (0x81 <= lead <= 0x98):
                continue
            rows.append((ch, (lead << 8) | trail))
    out_tsv.parent.mkdir(parents=True, exist_ok=True)
    out_tsv.write_text("\n".join(f"{ch}\t{cp:04X}" for ch, cp in rows) + "\n",
                       encoding="utf-8")
    return len(rows)


def validate_char_slot_map(charset_path: Path, csv_path: Path,
                           allow_reserved: bool = False) -> None:
    """Reject a partial or duplicate allocation before writing resources.

    Stable maps intentionally retain characters no longer used by the current
    translation set, so those historical rows are accepted when requested.
    """
    expected = {c for c in charset_path.read_text(encoding="utf-8")
                if "\u4e00" <= c <= "\u9fff"}
    actual = []
    with csv_path.open(encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            ch = row.get("char", "")
            if len(ch) == 1 and "\u4e00" <= ch <= "\u9fff":
                actual.append(ch)
    duplicates = len(actual) - len(set(actual))
    missing = expected - set(actual)
    extra = set(actual) - expected
    if duplicates or missing or (extra and not allow_reserved):
        details = []
        if duplicates:
            details.append(f"duplicate={duplicates}")
        if missing:
            details.append(f"missing={len(missing)}")
        if extra and not allow_reserved:
            details.append(f"extra={len(extra)}")
        raise SystemExit(
            "font allocation is incomplete or inconsistent: "
            + ", ".join(details)
            + f" (charset={len(expected)} map={len(actual)})")
def pristine_resource_bytes(ctx: Ctx, rel: str) -> bytes:
    """Read one unmodified resource without changing the unpacked tree."""
    if ctx.original_tree is not None:
        pristine = ctx.original_tree / rel
        if pristine.is_file():
            return pristine.read_bytes()
    with (ctx.unpacked / "manifest.tsv").open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            if row.get("output_path") == rel:
                with ctx.image.open("rb") as source:
                    source.seek(int(row["index"]) * 8)
                    sector, size = struct.unpack("<II", source.read(8))
                    source.seek(sector * 0x800)
                    return source.read(size)
    raise FileNotFoundError(rel)

def write_fonts(ctx: Ctx, sub0_map: Path) -> dict[str, Path]:
    """Render+write Chinese glyphs. Returns {target: installed_output_file}.

    Font size / offset per library are configured in font_sub0_7w.py's
    FONT_PROFILES (top of file); --font, when given, overrides the TTF those
    profiles name.
    """
    fonts: dict[str, Path] = {}
    # --font overrides the per-library FONT_PROFILES; omit it to use them.
    font_args = ["--font", ctx.font] if ctx.font else []
    common_fhm = ctx.unpacked / "seven_data_link" / "chiibaya" / "common" / "common.fhm"
    font16_sfo = ctx.unpacked / "seven_data_link" / "arita" / "font16.sfo"
    pristine_common = pristine_resource_bytes(ctx, "seven_data_link/chiibaya/common/common.fhm")
    pristine_font16 = pristine_resource_bytes(ctx, "seven_data_link/arita/font16.sfo")
    # Always regenerate from the Japanese glyph region; do not reuse a prior map.
    current_common = bytearray(common_fhm.read_bytes())
    sub0_start, sub0_end = struct.unpack_from("<II", current_common, 4)
    if (sub0_start, sub0_end) != (0x20, 0xFC140):
        raise ValueError("unexpected common.fhm font subresource bounds")
    current_common[sub0_start:sub0_end] = pristine_common[sub0_start:sub0_end]
    common_base = WORK / "font" / "common_font_base.fhm"
    common_base.write_bytes(current_common)
    font16_base = WORK / "font" / "font16_base.sfo"
    font16_base.write_bytes(pristine_font16)
    # common sub0
    common_out = WORK / "font" / "common_sub0_ch.fhm"
    run([sys.executable, str(FONT_SUB0), "write",
         str(common_base), str(common_out), "--map", str(sub0_map),
         "--target", "common", "--quiet", *font_args])
    fonts["common"] = common_out
    # font16, only when requested
    if ctx.font_target == "both":
        f16_out = WORK / "font" / "font16_ch.sfo"
        run([sys.executable, str(FONT_SUB0), "write",
             str(font16_base), str(f16_out), "--map", str(sub0_map),
             "--target", "font16", "--quiet", *font_args])
        fonts["font16"] = f16_out
    return fonts


def install_fonts(ctx: Ctx, fonts: dict[str, Path]) -> None:
    if "common" in fonts:
        dst = ctx.unpacked / "seven_data_link" / "chiibaya" / "common" / "common.fhm"
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(fonts["common"].read_bytes())
    if "font16" in fonts:
        dst = ctx.unpacked / "seven_data_link" / "arita" / "font16.sfo"
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(fonts["font16"].read_bytes())


def write_images(ctx: Ctx) -> None:
    """Re-encode surviving work/imageedit PNGs back into the unpacked tree.

    Only runs when work/imageedit/manifest.csv exists (i.e. the export manifest
    and edited PNGs were prepared there). Missing manifest or no surviving PNGs
    = skipped, not an error.
    Size-mismatched images are skipped inside image_7w.py with a warning.
    """
    manifest = WORK / "imageedit" / "manifest.csv"
    if not manifest.is_file():
        print("[image] no work/imageedit/manifest.csv (run image_7w.py scan "
              "first); skipping image write-back")
        return
    run([sys.executable, str(IMAGE), "write-back",
         "--unpacked", str(ctx.unpacked), "--quiet"])


def write_ui(ctx: Ctx) -> None:
    """Write fixed-offset UI text resources back into the unpacked tree."""
    ui_cmd = [sys.executable, str(TOOLS / "ui_write.py"),
              "--unpacked", str(ctx.unpacked), "--image", str(ctx.image)]
    if ctx.original_tree:
        ui_cmd += ["--original-tree", str(ctx.original_tree)]
    run(ui_cmd)


def restore_image_resources(ctx: Ctx) -> int:
    """Remove stale image edits when image write-back is disabled.

    A previous build may have already rewritten an ITE/FHN/GIM in the
    unpacked tree. Merely skipping ``write-back`` does not undo that change,
    so the old lossy container would still be repacked. Restore image slots
    from the pristine CDIMAGE skeleton before every text-only build.
    """
    manifest = ctx.unpacked / "manifest.tsv"
    if not manifest.is_file():
        return 0
    image_exts = {".ite", ".fhn", ".gim"}
    restored = 0
    with manifest.open(encoding="utf-8") as fh, ctx.image.open("rb") as src:
        reader = csv.DictReader(fh, delimiter="\t")
        for row in reader:
            rel = row.get("output_path", "")
            if Path(rel).suffix.lower() not in image_exts:
                continue
            pristine = ctx.original_tree / rel if ctx.original_tree else None
            if pristine is not None and pristine.is_file():
                data = pristine.read_bytes()
            else:
                src.seek(int(row["index"]) * 8)
                sector, size = struct.unpack("<II", src.read(8))
                src.seek(sector * 0x800)
                data = src.read(size)
            target = ctx.unpacked / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            restored += 1
    return restored


def sync_manifest_slots(ctx: Ctx) -> int:
    """Synchronize manifest offsets/sizes with the selected CDIMAGE skeleton.

    Older builds allowed a last FHN resource to shrink.  The unpacked file and
    manifest then described that shortened slot, while a pristine skeleton
    still described the original sector and capacity.  Repack must use the
    skeleton's index table as the authority, otherwise later writes can land
    in the wrong file area.
    """
    manifest = ctx.unpacked / "manifest.tsv"
    if not manifest.is_file():
        return 0
    rows = list(csv.DictReader(manifest.open(encoding="utf-8"), delimiter="\t"))
    changed = 0
    with ctx.image.open("rb") as src:
        for row in rows:
            index = int(row["index"])
            src.seek(index * 8)
            sector, size = struct.unpack("<II", src.read(8))
            offset, end = sector * 0x800, sector * 0x800 + size
            if row["offset"] != str(offset) or row["end"] != str(end):
                row["offset"], row["end"] = str(offset), str(end)
                changed += 1
    if changed:
        with manifest.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=rows[0].keys(), delimiter="\t",
                                    lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        print(f"[manifest] synchronized {changed} slot(s) from CDIMAGE index")
    return changed


def write_movie(sub0_map: Path) -> bool:
    """Rewrite movie subtitles (MOVIMAGE.BIN) using the freshly built slot map.

    Returns True when a rewrite actually ran. MOVIMAGE.BIN is a root ISO file,
    so it is never restored from the CDIMAGE image; the original bytes always
    come from unpak/MOVIMAGE.BIN and the result goes to work/build/MOVIMAGE.BIN.
    """
    if not (MOVIE_TDIR / "manifest.csv").is_file():
        print("[movie] no work/translation/movie_subtitles/manifest.csv "
              "(translate movie subtitles first); skipping")
        return False
    if not MOVIE_IN.is_file():
        print(f"[movie] missing original {MOVIE_IN}; skipping")
        return False
    MOVIE_OUT.parent.mkdir(parents=True, exist_ok=True)
    run([sys.executable, str(MOVIE), "write-all",
         str(MOVIE_IN), str(MOVIE_TDIR), str(MOVIE_OUT),
         "--map", str(sub0_map)])
    return True


def write_movie_video(movie_was_rewritten: bool) -> bool:
    """Replace the edited opening video while preserving subtitle changes.

    The edited MP4 is an authoring source only.  The game needs MPEG-2
    payloads split at the original 60 fixed block boundaries, with a sequence
    end code in every block.  Staging starts from the current MOVIMAGE output
    so subtitle and video edits compose in either order.
    """
    if not MOVIE_VIDEO_INPUT.is_file():
        return False
    if not MOVIE_IN.is_file():
        print(f"[movie-video] missing original {MOVIE_IN}; skipping")
        return False

    base = MOVIE_OUT if movie_was_rewritten and MOVIE_OUT.is_file() else MOVIE_IN
    MOVIE_VIDEO_TMP.mkdir(parents=True, exist_ok=True)
    staged_source = MOVIE_VIDEO_TMP / "MOVIMAGE.base.BIN"
    # q8 keeps every encoded segment inside the original video slot.  q6/q7
    # make chunk 08 overlap the preserved PCM audio region.
    segments = MOVIE_VIDEO_TMP / "segments_q8_g18_b2_end"
    encoded = MOVIE_VIDEO_TMP / "19_op.q8.segmented.m2v"
    report = WORK / "report" / "movie_video_reinject.json"
    shutil.copyfile(base, staged_source)
    run([sys.executable, str(MOVIE_VIDEO), "encode-segments",
         str(MOVIE_VIDEO_INPUT), str(staged_source), str(segments), str(encoded),
         "--quality", "8", "--gop", "18", "--b-frames", "2"])
    MOVIE_OUT.parent.mkdir(parents=True, exist_ok=True)
    run([sys.executable, str(MOVIE_VIDEO), "reinject-segments",
         str(staged_source), str(segments), str(MOVIE_OUT), str(report)])
    print(f"[movie-video] wrote {MOVIE_OUT} from {MOVIE_VIDEO_INPUT}")
    return True


def find_xml(pack_dir: Path) -> Path | None:
    """Locate the mkps2iso XML project that uses pack_dir as its source."""
    for xml in sorted(pack_dir.parent.glob("*.xml")):
        text = xml.read_text(encoding="utf-8", errors="replace")
        if f'directory_tree source="{pack_dir.name}"' in text:
            return xml
    return None


def pack_iso(ctx: Ctx) -> None:
    """Build the ISO from the rebuilt files without staging large copies.

    Requires --pack-dir (mkps2iso source directory, the folder that holds the
    CDIMAGE.BIN to embed) and --mkps2iso (the mkps2iso.exe). The matching
    *.xml project is auto-detected from the directory_tree source name. ISO is
    written next to the XML (as configured by image_name in the project file).

    mkps2iso supports a per-file ``source`` attribute. A temporary copy of the
    project XML points CDIMAGE.BIN and MOVIMAGE.BIN at their rebuilt outputs,
    avoiding roughly 1.2 GB of staging I/O on every full build.
    """
    if ctx.pack_dir is None or ctx.mkps2iso is None:
        raise SystemExit(
            "--pack requires --pack-dir and --mkps2iso")
    if not ctx.out.is_file():
        raise SystemExit(f"missing rebuilt image {ctx.out} "
                         f"(run without --skip repack first)")
    xml = find_xml(ctx.pack_dir)
    if xml is None:
        raise SystemExit(
            f"no *.xml in {ctx.pack_dir.parent} whose directory_tree source "
            f"is {ctx.pack_dir.name!r}")
    project = ET.parse(xml)
    direct_sources = {"CDIMAGE.BIN": ctx.out.resolve()}
    if MOVIE_OUT.is_file():
        direct_sources["MOVIMAGE.BIN"] = MOVIE_OUT.resolve()
    found = set()
    for node in project.getroot().iter("file"):
        name = node.get("name")
        source = direct_sources.get(name)
        if source is None:
            continue
        node.set("source", source.as_posix())
        found.add(name)
    missing = set(direct_sources) - found
    if missing:
        raise SystemExit(
            "mkps2iso XML is missing file entries: " + ", ".join(sorted(missing)))
    print("[pack] using rebuilt files directly; large staging copies avoided")

    temporary_xml: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", suffix=".xml", prefix=f"{xml.stem}.build-",
            dir=xml.parent, delete=False
        ) as fh:
            temporary_xml = Path(fh.name)
        project.write(temporary_xml, encoding="utf-8", xml_declaration=False)
        run_cwd(str(ctx.mkps2iso), ["-y", str(temporary_xml)], cwd=str(xml.parent))
    finally:
        if temporary_xml is not None:
            temporary_xml.unlink(missing_ok=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--image", type=Path, default=DEFAULT_IMAGE,
                    help="pristine CDIMAGE.BIN skeleton (default Seven/CDIMAGE.BIN.prev)")
    ap.add_argument("--original-tree", type=Path,
                    help="optional extracted pristine files used before translation")
    ap.add_argument("--unpacked", type=Path, default=ROOT / "unpacked/CDIMAGE_FULL",
                    help="unpacked dir (must have manifest.tsv)")
    ap.add_argument("--translation", type=Path, default=ROOT / "work/translation")
    ap.add_argument("--font", default=None,
                    help="TTF for rendering Chinese, overriding the per-library "
                         "FONT_PROFILES in font_sub0_7w.py "
                         "(default: whatever those profiles name, normally "
                         "work/font/chinese.ttf)")
    ap.add_argument("--font-target", choices=["common", "both"], default="both",
                    help="which font(s) to write Chinese glyphs into (default both)")
    ap.add_argument("--fresh-font-map", action="store_true",
                    help="rebuild the character-slot map from scratch (breaks old-save compatibility)")
    ap.add_argument("--both", action="store_true",
                    help="shorthand for --font-target both (write glyphs into "
                         "common.fhm sub0 AND font16.sfo)")
    ap.add_argument("--out", type=Path, default=ROOT / "work/build/CDIMAGE.BIN")
    ap.add_argument("--pack-dir", type=Path,
                    default=PACK_DIR_DEFAULT,
                    help="mkps2iso source directory holding the CDIMAGE.BIN "
                         "to embed (default <root>/mkps2iso-1.1.1-win64/Seven)")
    ap.add_argument("--mkps2iso", type=Path,
                    default=None,
                    help="path to mkps2iso.exe "
                         "(default <pack-dir>/../mkps2iso.exe when present)")
    ap.add_argument("--no-iso", action="store_true",
                    help="skip the final mkps2iso ISO packing step")
    ap.add_argument("--write-images", action="store_true",
                    help="opt in to re-encoding PNGs under work/imageedit; "
                         "ITE images are lossy and may show artifacts")
    ap.add_argument("--skip", nargs="*", default=(), help="steps to skip")
    a = ap.parse_args()
    # Relative paths on the command line mean "relative to the project root",
    # matching the defaults, so the build does not depend on the CWD.
    a.image = project_path(a.image)
    a.original_tree = project_path(a.original_tree)
    a.unpacked = project_path(a.unpacked)
    a.translation = project_path(a.translation)
    a.out = project_path(a.out)
    a.pack_dir = project_path(a.pack_dir)
    a.mkps2iso = project_path(a.mkps2iso)
    a.font = str(project_path(a.font)) if a.font else None
    if a.both:
        a.font_target = "both"
    if a.no_iso:
        a.skip = tuple(set(a.skip) | {"pack"})
    if a.pack_dir and a.mkps2iso is None:
        cand = a.pack_dir.parent / "mkps2iso.exe"
        if cand.is_file():
            a.mkps2iso = cand
    original_tree = a.original_tree
    if original_tree is None and (DEFAULT_ORIGINAL_TREE / "manifest.tsv").is_file():
        original_tree = DEFAULT_ORIGINAL_TREE
    ctx = Ctx(image=a.image, original_tree=original_tree, unpacked=a.unpacked,
              translation=a.translation, font=a.font,
              font_target=a.font_target, out=a.out,
              pack_dir=a.pack_dir, mkps2iso=a.mkps2iso)
    FONT_DIR = WORK / "font"

    # The CDIMAGE index, rather than a previously mutated unpacked file, is
    # authoritative for every file's sector and capacity.
    sync_manifest_slots(ctx)

    # 1. Determine the character set + slot mapping FIRST so text write-back
    #    encodes with the SAME slot map that the glyph renderer will use.
    #    Stable allocation is append-only: adding a character never changes an
    #    existing save-compatible CP932 code. Use --fresh-font-map only for a
    #    deliberate incompatible rebuild.
    if "font" not in a.skip:
        run([sys.executable, str(FONT_HACK), "collect", "--quiet"])
        if a.fresh_font_map:
            alloc_cmd = [sys.executable, str(FONT_HACK), "alloc", "--quiet",
                         "--charset", str(FONT_DIR / "char_set.txt")]
        else:
            alloc_cmd = [sys.executable, str(FONT_ALLOC_STABLE), "--charset",
                         str(FONT_DIR / "char_set.txt")]
        run(alloc_cmd)
        validate_char_slot_map(FONT_DIR / "char_set.txt",
                               FONT_DIR / "char_to_slot.csv",
                               allow_reserved=not a.fresh_font_map)

    # 2. write translated text back (encodes with the freshly built slot map)
    if "write" not in a.skip:
        ns = write_spl(ctx)
        nn = write_nht(ctx)
        nf = write_fhm(ctx)
        ne = write_elf(ctx)

    # 3. build + install Chinese glyphs (into common.fhm sub0 / font16.sfo)
    if "font" not in a.skip:
        sub0_map = FONT_DIR / "sub0_map.tsv"
        n = char_slot_to_sub0_map(FONT_DIR / "char_to_slot.csv", sub0_map)
        fonts = write_fonts(ctx, sub0_map)
        install_fonts(ctx, fonts)

    # 3.2 rewrite MOVIMAGE.BIN movie subtitles with the same fresh slot map
    movie_was_rewritten = False
    if "movie" not in a.skip:
        movie_was_rewritten = write_movie(FONT_DIR / "sub0_map.tsv")
    if "movie-video" not in a.skip:
        write_movie_video(movie_was_rewritten)

    # When image write-back is enabled, all writers that restore complete FHN
    # containers must run before image_7w.py.  The image writer then preserves
    # the surrounding text while replacing the selected embedded ITEs.
    fhn_before_images = a.write_images and "image" not in a.skip
    if fhn_before_images and "fhn" not in a.skip:
        write_fhn_text(ctx)
    if fhn_before_images and "ui" not in a.skip:
        write_ui(ctx)

    # 3. re-encode edited images (ITE/FHN/GIM) back into the unpacked tree
    if "image" not in a.skip and a.write_images:
        write_images(ctx)
    else:
        restored = restore_image_resources(ctx)
        print(f"[image] skipped; restored {restored} original image resources "
              "(use --write-images to re-encode edited PNGs)")

    # In text-only mode restore_image_resources() above may restore complete
    # FHN containers, so UI text must be written after that restoration.  With
    # image write-back enabled it was intentionally written before the image
    # step above.
    if not fhn_before_images and "ui" not in a.skip:
        write_ui(ctx)

    # 3.6 write fixed-field text embedded in selected UI FHN resources when
    # image write-back was not active.  In that case this runs after image
    # restoration so restoring a pristine FHN cannot erase the text edits.
    if "fhn" not in a.skip and not fhn_before_images:
        write_fhn_text(ctx)

    # 4. repack CDIMAGE.BIN
    if "repack" not in a.skip:
        print(f"[repack] rebuilding CDIMAGE.BIN ({a.image.stat().st_size / 2**20:.0f} MB) "
              f"-> {a.out} ...", flush=True)
        a.out.parent.mkdir(parents=True, exist_ok=True)
        run([sys.executable, str(UNPACK), "repack",
             str(a.image), str(a.unpacked), str(a.out)])

    # 5. (optional) pack the bootable ISO
    if "pack" not in a.skip:
        print("[pack] running mkps2iso to build ISO (may take many minutes, "
              "no progress output until it finishes) ...", flush=True)
        pack_iso(ctx)


if __name__ == "__main__":
    print("starting build_all")
    main()
