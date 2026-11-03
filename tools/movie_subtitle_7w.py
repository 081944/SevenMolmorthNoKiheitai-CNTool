#!/usr/bin/env python3
"""Extract, scan, and rewrite the XOR-protected movie subtitle tables used by 7W.

The game loads a 0x7f0-byte subtitle payload, XORs every byte with a one-byte
key, then parses CR-separated timing/text records (including spaced three-digit timings) ending in ``#####``.
This tool operates on the raw MOVIMAGE.BIN bytes and never modifies its input.

``scan`` assumes the observed MOVIMAGE record layout of 0x10-byte header plus
0x7f0-byte payload (0x800-byte stride), and writes every validated subtitle
block plus a CSV manifest.
"""
from __future__ import annotations

import argparse
import csv
import mmap
import re
import shutil
from pathlib import Path


DEFAULT_LENGTH = 0x7F0
DEFAULT_MAP = Path("work/font/sub0_map.tsv")
DEFAULT_STRIDE = 0x800
DEFAULT_HEADER_SIZE = 0x10
DEFAULT_KEY_OFFSET = 0x08
TIMING_RECORD_RE = re.compile(rb"^(\s*\d{1,4}\s*:\s*\d{1,4})(\s+)(.*)$")


def load_map(path: Path) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip() or "\t" not in line:
            continue
        ch, code = line.split("\t", 1)
        code = code.strip()
        if len(ch) == 1 and len(code) == 4:
            result[ch] = bytes.fromhex(code)
    return result


def encode_text(text: str, cmap: dict[str, bytes]) -> bytes:
    out = bytearray()
    for pos, ch in enumerate(text):
        mapped = cmap.get(ch) if "\u4e00" <= ch <= "\u9fff" else None
        if mapped is not None:
            out.extend(mapped)
            continue
        try:
            out.extend(ch.encode("cp932"))
        except UnicodeEncodeError as exc:
            raise ValueError(
                f"cannot encode {ch!r} at character {pos}; add it to {DEFAULT_MAP}"
            ) from exc
    return bytes(out)


def decode_payload(raw: bytes, key: int) -> bytes:
    return bytes(value ^ key for value in raw)


def read_payload(path: Path, offset: int, length: int, key: int) -> bytes:
    with path.open("rb") as fh:
        fh.seek(offset)
        raw = fh.read(length)
    if len(raw) != length:
        raise ValueError(f"short read at 0x{offset:X}: got 0x{len(raw):X}, expected 0x{length:X}")
    return decode_payload(raw, key)


def parse_raw_records(payload: bytes) -> tuple[list[tuple[str, str, bytes]], int]:
    marker = payload.find(b"#####")
    if marker < 0:
        raise ValueError("subtitle payload has no ##### terminator")
    body = payload[:marker]
    records: list[tuple[str, str, bytes]] = []
    for raw_line in body.split(b"\r"):
        if not raw_line:
            continue
        match = TIMING_RECORD_RE.match(raw_line)
        if match is None:
            raise ValueError(f"invalid subtitle record: {raw_line!r}")
        timing, _, text = match.groups()
        try:
            timing_text = timing.decode("ascii")
            text_text = text.decode("cp932")
        except UnicodeDecodeError as exc:
            raise ValueError(f"invalid CP932 subtitle record at {timing!r}") from exc
        records.append((timing_text, text_text, text))
    return records, marker


def parse_records(payload: bytes) -> tuple[list[tuple[str, str]], int]:
    raw_records, marker = parse_raw_records(payload)
    return [(timing, text) for timing, text, _ in raw_records], marker


def valid_timing(timing: str) -> bool:
    return re.fullmatch(r"\s*\d{1,4}\s*:\s*\d{1,4}\s*", timing) is not None


def format_extract_text(
    source: Path, offset: int, length: int, key: int, payload: bytes, block_base: int | None = None
) -> tuple[str, int, int]:
    records, marker = parse_records(payload)
    if not records or not all(valid_timing(timing) for timing, _ in records):
        raise ValueError("subtitle payload contains invalid timing records")
    lines = [
        f"# source={source}",
        f"# offset=0x{offset:X} length=0x{length:X} key=0x{key:02X}",
    ]
    if block_base is not None:
        lines.append(f"# block_base=0x{block_base:X}")
    lines.extend([f"# terminator_offset=0x{marker:X}", ""])
    lines.extend(f"{timing}|{text}" for timing, text in records)
    lines.append("#####")
    return "\n".join(lines) + "\n", len(records), marker


def extract(args: argparse.Namespace) -> None:
    payload = read_payload(args.input, args.offset, args.length, args.key)
    output, count, marker = format_extract_text(
        args.input, args.offset, args.length, args.key, payload
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(output, encoding="utf-8")
    print(f"extracted {count} record(s), terminator=0x{marker:X}")


def scan(args: argparse.Namespace) -> None:
    file_size = args.input.stat().st_size
    start = args.start
    end = file_size if args.end is None else min(args.end, file_size)
    if start < 0 or start >= end:
        raise ValueError(f"invalid scan range: 0x{start:X}..0x{end:X}")
    if args.key_offset < 0 or args.key_offset >= args.header_size:
        raise ValueError("key offset must point inside the block header")

    args.output.mkdir(parents=True, exist_ok=True)
    found: list[dict[str, str | int]] = []
    with args.input.open("rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as data:
        last_base = end - args.header_size - args.length
        for block_base in range(start, last_base + 1, args.stride):
            key = data[block_base + args.key_offset]
            payload_offset = block_base + args.header_size
            payload_end = payload_offset + args.length

            # The encrypted ##### marker is five copies of the same byte.
            # This cheap test avoids decrypting every 0x7F0-byte block in a
            # large MOVIMAGE.BIN file.
            encrypted_marker = bytes([ord("#") ^ key]) * 5
            if data.find(encrypted_marker, payload_offset, payload_end) < 0:
                continue

            encrypted = data[payload_offset:payload_end]
            payload = decode_payload(encrypted, key)
            try:
                output, count, marker = format_extract_text(
                    args.input,
                    payload_offset,
                    args.length,
                    key,
                    payload,
                    block_base=block_base,
                )
            except (UnicodeDecodeError, ValueError):
                continue

            name = f"subtitle_{len(found):02d}_{payload_offset:08X}.txt"
            (args.output / name).write_text(output, encoding="utf-8")
            found.append(
                {
                    "index": len(found),
                    "block_base": f"0x{block_base:X}",
                    "payload_offset": f"0x{payload_offset:X}",
                    "length": f"0x{args.length:X}",
                    "key": f"0x{key:02X}",
                    "records": count,
                    "terminator_offset": f"0x{marker:X}",
                    "text_file": name,
                }
            )

    manifest = args.output / "manifest.csv"
    fields = [
        "index",
        "block_base",
        "payload_offset",
        "length",
        "key",
        "records",
        "terminator_offset",
        "text_file",
    ]
    with manifest.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(found)
    print(f"found {len(found)} subtitle block(s); manifest={manifest}")


def parse_edit_file(path: Path) -> list[tuple[str, str]]:
    records: list[tuple[str, str]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip("\ufeff")
        if not line or line.startswith("#"):
            continue
        if line == "#####":
            break
        if "|" not in line:
            raise ValueError(f"{path}:{number}: expected TIMING|TEXT")
        timing, text = line.split("|", 1)
        timing = timing.rstrip()
        if not valid_timing(timing):
            raise ValueError(f"{path}:{number}: invalid timing {timing!r}")
        records.append((timing, text))
    return records


def rewrite_payload(original: bytes, edited: list[tuple[str, str]], cmap: dict[str, bytes]) -> bytes:
    old_records, marker = parse_raw_records(original)
    if len(edited) != len(old_records):
        raise ValueError(f"record count changed: original={len(old_records)}, edited={len(edited)}")
    chunks = []
    for (old_timing, old_text, old_raw), (timing, text) in zip(old_records, edited):
        if timing != old_timing:
            raise ValueError(f"timing changed: {old_timing} -> {timing}; timing is not editable")
        # CP932 has ambiguous byte sequences (notably 0x99 0x5C). Reuse the
        # original bytes for unchanged lines so an extract/write round-trip is
        # byte-identical; only edited lines go through the encoder.
        encoded = old_raw if text == old_text else encode_text(text, cmap)
        chunks.append(timing.encode("ascii") + b" " + encoded)
    # Preserve the exact bytes after ##### as well.  Some blocks contain two
    # CR bytes before the zero fill, while others contain one; keeping the
    # original terminator tail makes an extract/write round-trip byte exact.
    terminator_tail = original[marker + 5 :]
    decoded = b"\r".join(chunks) + b"\r#####" + terminator_tail
    if len(decoded) > len(original):
        raise ValueError(f"subtitle payload grew from 0x{len(original):X} to 0x{len(decoded):X}")
    # The original payload is zero-filled after the terminator. Preserve that
    # convention so the loader sees exactly the same fixed-size block.
    return decoded + b"\0" * (len(original) - len(decoded))


def write(args: argparse.Namespace) -> None:
    cmap = load_map(args.map)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.resolve() != args.input.resolve():
        shutil.copyfile(args.input, args.output)
    write_one_block(args.output, args.text, args.offset, args.length, args.key, cmap)
    edited = parse_edit_file(args.text)
    print(f"wrote {len(edited)} record(s) to {args.output} @0x{args.offset:X}")


def write_one_block(
    output: Path,
    text_path: Path,
    offset: int,
    length: int,
    key: int,
    cmap: dict[str, bytes],
) -> int:
    original = read_payload(output, offset, length, key)
    edited = parse_edit_file(text_path)
    updated = rewrite_payload(original, edited, cmap)
    with output.open("r+b") as fh:
        fh.seek(offset)
        fh.write(bytes(value ^ key for value in updated))
    return len(edited)


def write_all(args: argparse.Namespace) -> None:
    manifest = args.manifest or args.text_dir / "manifest.csv"
    if not manifest.exists():
        raise ValueError(f"manifest not found: {manifest}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.resolve() != args.input.resolve():
        shutil.copyfile(args.input, args.output)

    cmap = load_map(args.map)
    count = 0
    records = 0
    with manifest.open("r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            text_path = args.text_dir / row["text_file"]
            if not text_path.exists():
                raise ValueError(f"text file listed by manifest is missing: {text_path}")
            offset = int(row["payload_offset"], 0)
            length = int(row["length"], 0)
            key = int(row["key"], 0)
            records += write_one_block(args.output, text_path, offset, length, key, cmap)
            count += 1
    print(f"wrote {records} record(s) in {count} subtitle block(s) to {args.output}")


def parse_int(value: str) -> int:
    return int(value, 0)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("input", type=Path)
    common.add_argument("--offset", type=parse_int, required=True)
    common.add_argument("--length", type=parse_int, default=DEFAULT_LENGTH)
    common.add_argument("--key", type=parse_int, required=True)

    p_extract = sub.add_parser("extract", parents=[common])
    p_extract.add_argument("output", type=Path)
    p_extract.set_defaults(func=extract)

    p_scan = sub.add_parser("scan", help="find and extract every aligned subtitle block")
    p_scan.add_argument("input", type=Path)
    p_scan.add_argument("output", type=Path)
    p_scan.add_argument("--start", type=parse_int, default=0)
    p_scan.add_argument("--end", type=parse_int)
    p_scan.add_argument("--stride", type=parse_int, default=DEFAULT_STRIDE)
    p_scan.add_argument("--header-size", type=parse_int, default=DEFAULT_HEADER_SIZE)
    p_scan.add_argument("--key-offset", type=parse_int, default=DEFAULT_KEY_OFFSET)
    p_scan.add_argument("--length", type=parse_int, default=DEFAULT_LENGTH)
    p_scan.set_defaults(func=scan)

    p_write = sub.add_parser("write", parents=[common])
    p_write.add_argument("text", type=Path)
    p_write.add_argument("output", type=Path)
    p_write.add_argument("--map", type=Path, default=DEFAULT_MAP)
    p_write.set_defaults(func=write)

    p_write_all = sub.add_parser("write-all", help="rewrite all blocks listed in a scan manifest")
    p_write_all.add_argument("input", type=Path)
    p_write_all.add_argument("text_dir", type=Path)
    p_write_all.add_argument("output", type=Path)
    p_write_all.add_argument("--manifest", type=Path)
    p_write_all.add_argument("--map", type=Path, default=DEFAULT_MAP)
    p_write_all.set_defaults(func=write_all)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
