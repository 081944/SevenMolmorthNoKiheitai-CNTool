#!/usr/bin/env python3
"""Unpack the Seven Warriors PS2 CDIMAGE.BIN.

This script is unpack-only. It reads the image and writes files to the
explicit output directory supplied by the user. Duplicate file-list paths
are preserved with .dupNNN suffixes and recorded in manifest.tsv.
"""
from __future__ import annotations

import argparse
import hashlib
import struct
from collections import Counter
from pathlib import Path

SECTOR = 0x800
CACHE_MARKER = ".source-image.sha256"


def u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def safe_rel(name: str) -> Path:
    path = Path(name.replace("/", "\\"))
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe path in file list: {name}")
    return path


def read_names(data: bytes) -> list[str]:
    """Read paths from the filelist.bin resource referenced by index 0."""
    filelist_sector, filelist_size = struct.unpack_from("<II", data, 0)
    filelist_start = filelist_sector * SECTOR
    filelist_end = filelist_start + filelist_size
    if filelist_start < 0 or filelist_end > len(data):
        raise ValueError("filelist.bin exceeds image")
    blob = data[filelist_start:filelist_end]
    names: list[str] = []
    pos = 0
    while pos < len(blob):
        end = blob.find(b"\0", pos)
        if end < 0:
            break
        raw = blob[pos:end]
        if not raw:
            break
        try:
            name = raw.decode("ascii")
        except UnicodeDecodeError:
            break
        if any(ord(ch) < 0x20 for ch in name):
            break
        names.append(name)
        pos = end + 1
    if not names:
        raise ValueError("filelist.bin contains no ASCII paths")
    return names


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _cache_is_valid(image: Path, output: Path) -> bool:
    marker = output / CACHE_MARKER
    manifest = output / "manifest.tsv"
    if not marker.is_file() or not manifest.is_file():
        return False

    fields = marker.read_text(encoding="utf-8").splitlines()
    if not fields or fields[0].strip() != _sha256(image):
        return False

    rows = []
    for line in manifest.read_text(encoding="utf-8").splitlines()[1:]:
        if line:
            rows.append(line.split("\t"))
    try:
        expected_entries = int(fields[1].split()[0])
    except (IndexError, ValueError):
        return False
    if len(rows) != expected_entries:
        return False
    return all(len(row) >= 3 and (output / row[2]).is_file() for row in rows)


def unpack(image: Path, output: Path, reuse_existing: bool = False) -> None:
    if reuse_existing and _cache_is_valid(image, output):
        print(f"reused cached unpacked tree {output}")
        print("source image cache: SHA-256 matched; files were not rewritten")
        return

    data = image.read_bytes()
    source_hash = hashlib.sha256(data).hexdigest()
    names = read_names(data)
    output.mkdir(parents=True, exist_ok=True)
    seen: Counter[str] = Counter()
    manifest: list[tuple[int, str, str, int, int]] = []

    for index, name in enumerate(names):
        if index * 8 + 8 > len(data):
            raise ValueError(f"index entry {index} is outside the image")
        sector, size = struct.unpack_from("<II", data, index * 8)
        start = sector * SECTOR
        end = start + size
        if end > len(data):
            raise ValueError(f"entry {index} exceeds image: 0x{start:X}+0x{size:X}")

        original_rel = safe_rel(name)
        key = original_rel.as_posix().lower()
        duplicate = seen[key]
        seen[key] += 1
        output_rel = original_rel if duplicate == 0 else original_rel.with_name(
            f"{original_rel.name}.dup{duplicate:03d}"
        )
        target = output / output_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data[start:end])
        manifest.append((index, name, output_rel.as_posix(), start, end))

    lines = ["index\toriginal_path\toutput_path\toffset\tend"]
    lines.extend("\t".join(map(str, row)) for row in manifest)
    (output / "manifest.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (output / CACHE_MARKER).write_text(
        source_hash + "\n" + str(len(names)) + " entries\n",
        encoding="utf-8",
    )
    print(f"unpacked {len(names)} entries to {output}")
    print("file list source: CDIMAGE index 0 (filelist.bin)")
    print(f"unique output files: {len(manifest)}; manifest: {output / 'manifest.tsv'}")


def repack(image: Path, unpacked: Path, output: Path, quiet: bool = False) -> None:
    """Rebuild CDIMAGE.BIN from an unpacked tree + its manifest.tsv.

    Strategy (keeps the image bootable):
      * A file whose new content is NO LARGER than its original slot is written
        back in place at its recorded offset; a shortened file updates its index size.
      * A file that GREW is appended to the tail (0x800-aligned) and that
        entry's (sector, size) in the index table is updated. filelist.bin
        (entry 0) is expected to stay the same byte-for-byte.
    """
    root = unpacked
    manifest = root / "manifest.tsv"
    if not manifest.exists():
        raise FileNotFoundError(f"missing {manifest}")
    data = bytearray(image.read_bytes())
    rows = []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("index"):
            continue
        idx, orig, out, off, end = line.split("\t")
        rows.append((int(idx), out, int(off), int(end)))
    n = len(rows)
    tail = _image_tail(data, n)
    moved: list[int] = []
    resized: list[tuple[int, int, int]] = []
    changed = 0
    unchanged = 0
    for idx, out, off, end in rows:
        if not quiet and idx and idx % 200 == 0:
            print(f"  repack {idx}/{n} entries ...", flush=True)
        src = root / out
        if not src.is_file():
            continue  # leave original bytes as-is
        new = src.read_bytes()
        cap = end - off
        if len(new) <= cap:
            padded = new + b"\0" * (cap - len(new))
            if padded == bytes(data[off:end]):
                unchanged += 1
                continue
            data[off:end] = padded
            # The index table is the file-system boundary seen by the game.
            # Leaving the old size here makes a shortened SPL/NHT/FHM look
            # like valid data followed by zero padding; parsers that expect
            # the container footer at EOF can then hang or render a black UI.
            if idx != 0 and len(new) != cap:
                struct.pack_into("<II", data, idx * 8, off // SECTOR, len(new))
                resized.append((idx, cap, len(new)))
            changed += 1
        else:
            # grows: append at tail, update index entry
            sect = tail // SECTOR
            data.extend(new)
            # zero-pad to sector boundary
            pad = (-len(new)) % SECTOR
            data.extend(b"\0" * pad)
            tail += len(new) + pad
            off2 = sect * SECTOR
            struct.pack_into("<II", data, idx * 8, sect, len(new))
            moved.append(idx)
            changed += 1
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(data)
    if not quiet:
        missing = n - changed - unchanged
        print(f"repacked {changed}/{n} entries changed "
              f"({unchanged} identical, {missing} absent) "
              f"to {output} size={len(data)}")
    if moved:
        print("moved (grew, appended to tail):", moved)

    if resized and not quiet:
        print(f"resized (shrunk, index size updated): {len(resized)} entries")

def _image_tail(data: bytes, count: int) -> int:
    """Highest used byte end as a 0x800-aligned tail position.

    ``count`` is the number of index entries (== manifest rows).  Iterating
    the whole image as if every 8 bytes were an index entry would read file
    payload as (sector, size) pairs and produce a garbage tail; only walk the
    real index table.
    """
    end_max = 0x800 * 2  # index table region not reclaimed
    for i in range(count):
        sector, size = struct.unpack_from("<II", data, i * 8)
        e = sector * SECTOR + size
        if e > end_max:
            end_max = e
    return ((end_max + SECTOR - 1) // SECTOR) * SECTOR


def main() -> None:
    parser = argparse.ArgumentParser(description="Unpack / repack a PS2 CDIMAGE.BIN")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("unpack", help="unpack an image to a directory")
    p.add_argument("image", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--reuse-existing", action="store_true",
                   help="reuse an existing tree when it matches the source image")
    p.set_defaults(fn=lambda a: unpack(a.image, a.output, a.reuse_existing))
    p = sub.add_parser("repack", help="rebuild an image from unpacked dir + manifest")
    p.add_argument("image", type=Path, help="original CDIMAGE.BIN (skeleton)")
    p.add_argument("unpacked", type=Path, help="dir containing manifest.tsv + files")
    p.add_argument("output", type=Path, help="output CDIMAGE.BIN")
    p.add_argument("--quiet", action="store_true",
                   help="suppress the success summary (only failures print)")
    p.set_defaults(fn=lambda a: repack(a.image, a.unpacked, a.output, quiet=getattr(a, "quiet", False)))
    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
