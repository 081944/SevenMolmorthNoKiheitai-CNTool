#!/usr/bin/env python3
"""Seven Warriors PS2 — unified image export / write-back (ITE + FHN + NHT + GIM).

Workflow (mirrors the text/font pipeline):

  1. scan       list every image source in the unpacked tree into a manifest
  2. export     decode all sources to PNGs under work/image (mirror of the
                unpacked relative paths); re-print the manifest with the PNG
                paths filled in
  3. (user)     copy the manifest and PNGs to work/imageedit, delete those
                that carry no text, and translate the ones that do
  4. write-back re-encode the PNGs that still exist under work/imageedit back
     into the unpacked tree (overwrites the source files). PNGs whose
     dimensions don't match the source image are SKIPPED with a warning
     (never fatal, exit 0). Fixed-slot FHN images that are too large at the
     requested quantizer are retried at lower quality before being skipped.

Naming convention (path = identity, no extra state needed):
  export PNG      : work/image/ite/<rel>/<name>.png
  edited PNG      : work/imageedit/ite/<rel>/<name>.png
  FHN embedded    : work/image/ite/<rel>/<name>__ite<NNN>.png
                    (NNN = embedded-ITE index, same numbering as ite_7w.py)
  NHT embedded    : work/image/ite/<rel>/<name>__ite<NNN>.png
                    (NNN = NHT image index, zero-based)
  container ITE   : work/image/ite/<rel>/<name>__ite<NNN>.png
                    (NNN = valid ITE resource index in the container)
  GIM             : work/image/gim/<rel>/<name>.png
  embedded GIM    : work/image/gim/<rel>/<name>__gim<NNN>.png

The edited PNGs keep the same relative paths under work/imageedit.

write-back rule: only PNGs that still exist are re-encoded. Deleted PNGs mean
"leave that image untouched". So to keep an image original, just delete its
PNG. To translate one, keep and edit its PNG.

GIM write-back preserves the original container: 8bpp/4bpp keep the original
palette and re-map pixels to nearest palette colors; 32bpp writes RGBA pixels
directly keeping the original alpha bytes. Size guard: PNG dims must match the
source GIM.
"""
from __future__ import annotations

import argparse
import csv
import math
import struct
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

import ite_7w
import gim_7w


WORK_IMAGE_EXPORT = Path("work/image")
WORK_IMAGE_EDIT = Path("work/imageedit")
DEFAULT_UNPACKED = Path("unpacked/CDIMAGE_FULL")
ANIMATION_SUFFIXES = {".aal", ".fit"}


# ---------------------------------------------------------------------------
# manifest model
# ---------------------------------------------------------------------------

MANIFEST_FIELDS = [
    "kind",          # ite | fhn | nht | container_ite | gim
    "src_rel",       # path relative to the unpacked root (source file)
    "sub",           # embedded-ITE index (ite_7w.py numbering) for fhn; else ""
    "width",
    "height",
    "offset",        # byte offset for an embedded resource, else 0
    "png_rel",       # path relative to either image root (PNG); "" if not exported yet
]


@dataclass
class Source:
    kind: str
    src_rel: str
    sub: int | None
    width: int = 0
    height: int = 0
    offset: int = 0
    png_rel: str = ""
    skipped: str = ""


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------

def _u32(b: bytes, o: int) -> int:
    return int.from_bytes(b[o:o + 4], "little")


def _ite_dims(data: bytes) -> tuple[int, int] | None:
    try:
        ite = ite_7w.parse_ite(data)
        return ite.width, ite.height
    except ite_7w.IteError:
        return None


def _fhn_embedded(src: Path, rel: str) -> list[Source]:
    data = src.read_bytes()
    out: list[Source] = []
    stem = Path(rel).stem
    dir_rel = str(Path(rel).parent).replace("\\", "/")
    for sub, (offset, size) in enumerate(ite_7w.find_embedded_ites(data)):
        dims = _ite_dims(data[offset:offset + size])
        w, h = dims if dims else (0, 0)
        out.append(Source("fhn", rel, sub, w, h, offset,
                          f"ite/{dir_rel}/{stem}__ite{sub}.png"))
    return out


NHT_HEADER_SIZE = 0x800


def _nht_embedded(src: Path, rel: str) -> list[Source]:
    """Discover the ITE resources in an NHT help-text/image container."""
    data = src.read_bytes()
    if len(data) < NHT_HEADER_SIZE:
        return []
    image_count = _u32(data, 0)
    if image_count == 0 or 0x400 + 4 * (image_count + 1) > len(data):
        return []
    offsets = [_u32(data, 0x400 + 4 * i)
               for i in range(image_count + 1)]
    if offsets[0] != 0 or any(not off for off in offsets[1:]):
        return []
    if offsets[1:] != sorted(offsets[1:]):
        return []
    stem = Path(rel).stem
    dir_rel = str(Path(rel).parent).replace("\\", "/")
    out: list[Source] = []
    for sub in range(image_count):
        start = offsets[sub + 1]
        end = offsets[sub + 2] if sub + 2 < len(offsets) else len(data)
        image = data[start:end]
        if not image.startswith(b"ITE\0"):
            continue
        dims = _ite_dims(image)
        if dims is None:
            continue
        out.append(Source("nht", rel, sub, dims[0], dims[1], start,
                          f"ite/{dir_rel}/{stem}__ite{sub}.png"))
    return out


def _container_ites(src: Path, rel: str) -> list[Source]:
    """Find valid ITE resources in any non-NHT container file."""
    data = src.read_bytes()

    # A standalone .ite is itself the resource.  find_embedded_ites() also
    # reports offset 0 for it, but keeping it as a normal `ite` entry makes
    # export and write-back use the standalone code path and naming scheme.
    if Path(rel).suffix.lower() == ".ite" and data.startswith(b"ITE\0"):
        dims = _ite_dims(data)
        if dims is not None:
            return [Source(
                "ite", rel, None, dims[0], dims[1], 0,
                f"ite/{rel[:-4]}.png",
            )]

    found = ite_7w.find_embedded_ites(data)
    if not found:
        return []
    stem = Path(rel).name
    out: list[Source] = []
    for sub, (offset, size) in enumerate(found):
        dims = _ite_dims(data[offset:offset + size])
        if dims is None:
            continue
        out.append(Source("container_ite", rel, sub, dims[0], dims[1], offset,
                          f"ite/{rel}__ite{sub}.png"))
    return out


def _embedded_gims(src: Path, rel: str) -> list[Source]:
    """Find standalone or embedded GIM resources by their magic header."""
    data = src.read_bytes()
    magic = b"GIM\0"
    offsets: list[int] = []
    pos = 0
    while True:
        pos = data.find(magic, pos)
        if pos < 0:
            break
        if gim_7w.parse(data[pos:]) is not None:
            offsets.append(pos)
        pos += 4
    if not offsets:
        return []
    out: list[Source] = []
    is_root = len(offsets) == 1 and offsets[0] == 0 and Path(rel).suffix.lower() == ".gim"
    for sub, offset in enumerate(offsets):
        if is_root:
            png_rel = f"gim/{rel[:-4]}.png"
            index = None
        else:
            png_rel = f"gim/{rel}__gim{sub}.png"
            index = sub
        out.append(Source("gim", rel, index, 0, 0, offset, png_rel))
    return out


def scan(unpacked: Path, include_animation: bool = False) -> list[Source]:
    srcs: list[Source] = []
    for p in sorted(unpacked.rglob("*")):
        if not p.is_file() or p.name == "manifest.tsv":
            continue
        rel = p.relative_to(unpacked).as_posix()
        suffix = p.suffix.lower()
        if suffix in ANIMATION_SUFFIXES and not include_animation:
            continue
        if suffix == ".nht":
            srcs.extend(_nht_embedded(p, rel))
        else:
            srcs.extend(_container_ites(p, rel))
        srcs.extend(_embedded_gims(p, rel))
    return srcs


def write_manifest(srcs: list[Source], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=MANIFEST_FIELDS)
        w.writeheader()
        for s in srcs:
            w.writerow({
                "kind": s.kind,
                "src_rel": s.src_rel,
                "sub": "" if s.sub is None else s.sub,
                "width": s.width,
                "height": s.height,
                "offset": s.offset,
                "png_rel": s.png_rel,
            })


def read_manifest(path: Path) -> list[Source]:
    srcs: list[Source] = []
    with path.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            srcs.append(Source(
                kind=r["kind"], src_rel=r["src_rel"],
                sub=int(r["sub"]) if r["sub"] not in ("", "-") else None,
                width=int(r["width"]), height=int(r["height"]),
                offset=int(r.get("offset", "0") or 0),
                png_rel=r["png_rel"] or "",
            ))
    return srcs


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------

def _export_ite(src: Path, sub: int | None, png: Path, ffmpeg: Path,
                kind: str = "fhn", offset: int = 0) -> str:
    data = src.read_bytes()
    if kind == "container_ite":
        entries = ite_7w.find_embedded_ites(data)
        if sub is None or not (0 <= sub < len(entries)):
            raise ValueError(f"embedded ITE index {sub} out of range for {src}")
        entry_offset, size = entries[sub]
        if offset != entry_offset:
            raise ValueError(
                f"manifest offset 0x{offset:x} does not match embedded ITE "
                f"offset 0x{entry_offset:x} for {src}")
        ite = ite_7w.parse_ite(data[entry_offset:entry_offset + size])
    elif sub is None:
        ite = ite_7w.parse_ite(data)
    elif kind == "nht":
        image_count = _u32(data, 0)
        if sub < 0 or sub >= image_count:
            raise ValueError(f"NHT image index {sub} out of range")
        start = _u32(data, 0x400 + 4 * (sub + 1))
        end = (_u32(data, 0x400 + 4 * (sub + 2))
               if sub + 2 <= image_count else len(data))
        ite = ite_7w.parse_ite(data[start:end])
    else:
        entry_offset, size = ite_7w.find_embedded_ites(data)[sub]
        ite = ite_7w.parse_ite(data[entry_offset:entry_offset + size])
    img = ite_7w.render_ite(ite, ffmpeg)
    png.parent.mkdir(parents=True, exist_ok=True)
    img.save(png)
    return "ok"


def _export_gim(src: Path, png: Path, offset: int = 0) -> str:
    d = src.read_bytes()
    d = d[offset:]
    if not gim_7w.is_gim(d):
        return "not-gim"
    par = gim_7w.parse(d)
    if par is None:
        return "unsupported"
    w, h, kind = par
    img = gim_7w.render(d, w, h, kind)
    png.parent.mkdir(parents=True, exist_ok=True)
    img.save(png)
    return "ok"


def cmd_scan(args) -> None:
    srcs = scan(args.unpacked, args.include_animation)
    write_manifest(srcs, args.manifest)
    print(f"scan: sources={len(srcs)} manifest={args.manifest}")
    kinds = {}
    for s in srcs:
        kinds[s.kind] = kinds.get(s.kind, 0) + 1
    print("  " + "  ".join(f"{k}={v}" for k, v in sorted(kinds.items())))


def cmd_export(args) -> None:
    srcs = read_manifest(args.manifest)
    ffmpeg = ite_7w._find_ffmpeg(args.ffmpeg)
    output_root = args.output
    selected = []
    for s in srcs:
        if not s.png_rel:
            continue
        if args.filter and args.filter not in s.src_rel:
            continue
        selected.append(s)

    def export_one(s: Source) -> str | None:
        src = args.unpacked / s.src_rel
        png = output_root / s.png_rel
        try:
            if s.kind == "gim":
                status = _export_gim(src, png, s.offset)
                if status != "ok":
                    return f"{s.kind} {s.src_rel}: {status}"
            else:
                _export_ite(src, s.sub, png, ffmpeg, s.kind, s.offset)
        except Exception as exc:  # noqa: BLE001
            return f"{s.kind} {s.src_rel}: {exc}"
        return None

    fails = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        jobs = [pool.submit(export_one, s) for s in selected]
        for job in as_completed(jobs):
            failure = job.result()
            if failure:
                fails.append(failure)
    write_manifest(srcs, args.manifest)
    print(f"export: ok={len(selected) - len(fails)} fail={len(fails)} "
          f"pngs under {output_root} workers={max(1, args.workers)}")
    for line in fails:
        print("  FAIL " + line)


# ---------------------------------------------------------------------------
# write-back
# ---------------------------------------------------------------------------

def _fhn_rebuild(unpacked: Path, src_rel: str, subs: dict[int, Path],
                 args) -> int:
    """Rebuild one FHN, replacing every provided sub-index with its PNG.

    Returns the number of subs skipped (size mismatch / too large).
    """
    src = unpacked / src_rel
    data = bytearray(src.read_bytes())
    entries = ite_7w.find_embedded_ites(data)
    ffmpeg = ite_7w._find_ffmpeg(args.ffmpeg)
    skipped = 0
    for sub in sorted(subs):
        png = subs[sub]
        offset, old_size = entries[sub]
        orig = bytes(data[offset:offset + old_size])
        if not orig.startswith(b"ITE\0"):
            print(f"  [warn] fhn {src_rel} sub {sub} is not ITE, skipping", file=sys.stderr)
            skipped += 1
            continue
        img = Image.open(png).convert("RGBA")
        ite = ite_7w.parse_ite(orig)
        try:
            if img.size != (ite.width, ite.height):
                raise ValueError(
                    f"size mismatch: {png} is {img.width}x{img.height}, "
                    f"expected {ite.width}x{ite.height} ({src_rel} sub {sub})")
            can_resize = offset + old_size == len(data)
            rebuilt = ite_7w.rebuild_ite(orig, img, ffmpeg, args.quantizer)
            if not can_resize and len(rebuilt) > old_size:
                # Embedded resources before the last FHN entry cannot grow:
                # their extra bytes would shift the following offset table
                # entries. Match the VenusBraves writer by trying progressively
                # lower quality MPEG-2 quantizers before giving up.
                retry_sizes = []
                for quantizer in range(max(args.quantizer + 1, 5), 11):
                    retry = ite_7w.rebuild_ite(orig, img, ffmpeg, quantizer)
                    retry_sizes.append((quantizer, len(retry)))
                    if len(retry) <= old_size:
                        print(f"  [fhn] retry {png} at q={quantizer}: "
                              f"{len(rebuilt)}B -> {len(retry)}B")
                        rebuilt = retry
                        break
                else:
                    sizes = ", ".join(
                        f"q={q} {size}B"
                        for q, size in [(args.quantizer, len(rebuilt)), *retry_sizes]
                    )
                    raise ValueError(
                        f"rebuilt too large: {png} ({sizes}) exceeds FHN "
                        f"slot {old_size}B ({src_rel} sub {sub})")
            if not can_resize:
                rebuilt += b"\x00" * (old_size - len(rebuilt))
            data[offset:offset + old_size] = rebuilt
            if not getattr(args, "quiet", False):
                print(f"  [fhn] {src_rel} sub {sub} <- {png}")
        except ValueError as exc:
            print(f"  [skip] {exc}")
            skipped += 1
    src.write_bytes(bytes(data))
    return skipped


def _nht_rebuild(unpacked: Path, src_rel: str, subs: dict[int, Path],
                 args) -> int:
    """Rebuild selected ITE images in an NHT container.

    NHT stores absolute image offsets in its header. Unlike FHN resources,
    an NHT image may grow; when it does, later image offsets are shifted and
    the table is updated. Smaller replacements retain the original range.
    """
    src = unpacked / src_rel
    data = bytearray(src.read_bytes())
    if len(data) < NHT_HEADER_SIZE:
        raise ValueError(f"nht {src_rel} header is truncated")
    image_count = _u32(data, 0)
    if image_count == 0 or 0x400 + 4 * (image_count + 1) > len(data):
        raise ValueError(f"nht {src_rel} has an invalid image table")
    offsets = [_u32(data, 0x400 + 4 * i)
               for i in range(image_count + 1)]
    if (offsets[0] != 0 or any(not off for off in offsets[1:])
            or offsets[1:] != sorted(offsets[1:])):
        raise ValueError(f"nht {src_rel} has invalid image offsets")
    if any(offset < NHT_HEADER_SIZE or offset >= len(data)
           or data[offset:offset + 4] != b"ITE\0"
           for offset in offsets[1:]):
        raise ValueError(f"nht {src_rel} image table does not point to ITE resources")

    ffmpeg = ite_7w._find_ffmpeg(args.ffmpeg)
    skipped = 0
    for sub in sorted(subs):
        png = subs[sub]
        if sub < 0 or sub >= image_count:
            print(f"  [skip] nht {src_rel} sub {sub} is out of range")
            skipped += 1
            continue
        start = offsets[sub + 1]
        end = offsets[sub + 2] if sub + 1 < image_count else len(data)
        old_size = end - start
        orig = bytes(data[start:end])
        try:
            img = Image.open(png).convert("RGBA")
            ite = ite_7w.parse_ite(orig)
            if img.size != (ite.width, ite.height):
                raise ValueError(
                    f"size mismatch: {png} is {img.width}x{img.height}, "
                    f"expected {ite.width}x{ite.height} ({src_rel} sub {sub})")
            rebuilt = ite_7w.rebuild_ite(orig, img, ffmpeg, args.quantizer)
            if len(rebuilt) <= old_size:
                rebuilt += b"\0" * (old_size - len(rebuilt))
                data[start:end] = rebuilt
            else:
                delta = len(rebuilt) - old_size
                data[start:end] = rebuilt
                for index in range(sub + 2, image_count + 1):
                    offsets[index] += delta
                    struct.pack_into("<I", data, 0x400 + 4 * index,
                                     offsets[index])
            if not getattr(args, "quiet", False):
                print(f"  [nht] {src_rel} sub {sub} <- {png}")
        except (ValueError, ite_7w.IteError) as exc:
            print(f"  [skip] {exc}")
            skipped += 1
    src.write_bytes(bytes(data))
    return skipped


def _gim_rebuild(unpacked: Path, src_rel: str, offset: int, png: Path,
                 quiet: bool = False) -> None:
    """Rebuild one GIM resource in place, possibly inside a larger container.

    ``offset`` comes from the manifest: a standalone ``.gim`` file uses 0, while
    a GIM embedded in an ``.arc``/``.aap`` container points at the resource
    start. Only bytes from ``offset`` on are re-encoded; the container header
    and any data after the resource stay byte-identical. GIM rebuilds are
    size-preserving, which keeps the container layout valid.
    """
    src = unpacked / src_rel
    data = bytearray(src.read_bytes())
    if offset < 0 or offset + 4 > len(data):
        raise ValueError(f"GIM offset 0x{offset:x} is outside {src_rel}")
    resource = bytes(data[offset:])
    if not gim_7w.is_gim(resource):
        raise ValueError(f"no GIM magic at 0x{offset:x} in {src_rel}")
    import image_alpha_7w
    img = image_alpha_7w.load_for_writeback(png, resource)
    rebuilt = gim_7w.rebuild(resource, img)
    if len(rebuilt) != len(resource):
        raise ValueError(
            f"GIM rebuild changed size ({len(resource)}B -> {len(rebuilt)}B) "
            f"for the resource at 0x{offset:x} in {src_rel}")
    data[offset:] = rebuilt
    src.write_bytes(bytes(data))
    if not quiet:
        print(f"  [gim] {src_rel} +0x{offset:x} <- {png}")


def _ite_rebuild(unpacked: Path, src_rel: str, png: Path, args) -> None:
    src = unpacked / src_rel
    orig = src.read_bytes()
    img = Image.open(png).convert("RGBA")
    ite = ite_7w.parse_ite(orig)
    if img.size != (ite.width, ite.height):
        raise ValueError(
            f"size mismatch: {png} is {img.width}x{img.height}, "
            f"expected {ite.width}x{ite.height} ({src_rel})")
    ffmpeg = ite_7w._find_ffmpeg(args.ffmpeg)
    rebuilt = ite_7w.rebuild_ite(orig, img, ffmpeg, args.quantizer)
    src.write_bytes(rebuilt)
    if not getattr(args, "quiet", False):
        print(f"  [ite] {src_rel} <- {png}")


def _resolve_source(srcs: list[Source], png_rel: str) -> Source | None:
    for s in srcs:
        if s.png_rel == png_rel:
            return s
    return None


def cmd_write_back(args) -> None:
    srcs = read_manifest(args.manifest)
    by_rel: dict[str, list[Source]] = {}
    for s in srcs:
        by_rel.setdefault(s.src_rel, []).append(s)

    # collect surviving PNGs (only ones present on disk are re-encoded)
    survivors = [p for p in WORK_IMAGE_EDIT.rglob("*.png") if p.is_file()]
    if not survivors:
        if not getattr(args, "quiet", False):
            print("write-back: no PNGs under", WORK_IMAGE_EDIT)
        return

    # group by source file
    skipped = 0
    ite_pngs: dict[str, Path] = {}     # src_rel -> png
    fhn_pngs: dict[str, dict[int, Path]] = {}  # src_rel -> {sub: png}
    nht_pngs: dict[str, dict[int, Path]] = {}  # src_rel -> {sub: png}
    gim_pngs: list[tuple[Source, Path]] = []   # (source, png)
    for png in survivors:
        rel = png.relative_to(WORK_IMAGE_EDIT).as_posix()
        s = _resolve_source(srcs, rel)
        if s is None:
            print(f"  [skip] {rel} not in manifest "
                  f"(no source entry has this PNG path)", file=sys.stderr)
            skipped += 1
            continue
        if s.kind == "gim":
            gim_pngs.append((s, png))
            continue
        if s.kind == "ite":
            ite_pngs[s.src_rel] = png
        elif s.kind == "nht":
            nht_pngs.setdefault(s.src_rel, {})[s.sub] = png
        else:
            fhn_pngs.setdefault(s.src_rel, {})[s.sub] = png

    if not getattr(args, "quiet", False):
        print(f"write-back: ite={len(ite_pngs)} fhn={len(fhn_pngs)} "
              f"nht={len(nht_pngs)} gim={len(gim_pngs)}")
    for src_rel, png in sorted(ite_pngs.items()):
        try:
            _ite_rebuild(args.unpacked, src_rel, png, args)
        except ValueError as exc:
            print(f"  [skip] {exc}")
            skipped += 1
    for src_rel, subs in sorted(fhn_pngs.items()):
        skipped += _fhn_rebuild(args.unpacked, src_rel, subs, args)
    for src_rel, subs in sorted(nht_pngs.items()):
        skipped += _nht_rebuild(args.unpacked, src_rel, subs, args)
    for s, png in sorted(gim_pngs, key=lambda item: item[0].png_rel):
        try:
            _gim_rebuild(args.unpacked, s.src_rel, s.offset, png,
                         getattr(args, "quiet", False))
        except ValueError as exc:
            print(f"  [skip] {s.src_rel} (GIM at 0x{s.offset:x}): {exc}")
            skipped += 1
    if skipped:
        print(f"write-back: skipped {skipped} image(s) "
              f"(not in manifest / size mismatch / too large / not ITE), "
              f"see [skip] lines above")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("scan", help="discover image sources into the manifest")
    p.add_argument("--unpacked", type=Path, default=DEFAULT_UNPACKED)
    p.add_argument("--manifest", type=Path, default=WORK_IMAGE_EXPORT / "manifest.csv")
    p.add_argument("--include-animation", action="store_true",
                   help="include ITE/GIM resources from .aal/.fit animation containers")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("export", help="decode all manifest sources to PNGs")
    p.add_argument("--unpacked", type=Path, default=DEFAULT_UNPACKED)
    p.add_argument("--manifest", type=Path, default=WORK_IMAGE_EXPORT / "manifest.csv")
    p.add_argument("--filter", default=None,
                   help="only export sources whose src_rel contains this substring")
    p.add_argument("--ffmpeg", type=Path)
    p.add_argument("--output", type=Path, default=WORK_IMAGE_EXPORT,
                   help="PNG output root (default: work/image)")
    p.add_argument("--workers", type=int, default=8,
                   help="parallel image decoders (default: 8)")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("write-back",
                       help="re-encode surviving PNGs into the unpacked tree")
    p.add_argument("--unpacked", type=Path, default=DEFAULT_UNPACKED)
    p.add_argument("--manifest", type=Path, default=WORK_IMAGE_EDIT / "manifest.csv")
    p.add_argument("--quantizer", type=int, default=4, choices=range(1, 29))
    p.add_argument("--ffmpeg", type=Path)
    p.add_argument("--quiet", action="store_true",
                   help="only print skipped/error entries")
    p.set_defaults(func=cmd_write_back)
    return ap


def main() -> None:
    args = build_parser().parse_args()
    try:
        args.func(args)
    except (ite_7w.IteError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
