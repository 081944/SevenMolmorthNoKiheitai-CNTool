#!/usr/bin/env python3
"""Extract and write the fixed-slot text resource balloon.bhf.

Each record is 0x44 bytes.  The first 0x1c bytes are fixed metadata and the
remaining 0x28 bytes are a CP932/custom-font text slot.  Translation entries
are keyed by their original text bytes so repeated UI descriptions are
updated consistently without touching the metadata.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from text_7w import encode_translation

DEFAULT_SOURCE = ROOT / "unpacked" / "CDIMAGE_FULL" / "seven_data_link" / "balloon" / "balloon.bhf"
DEFAULT_CANDIDATE = ROOT / "work" / "translation" / "ui" / "balloon_bhf.txt"

FIXED_START = 0x4E0
SLOT_SIZE = 0x44
TEXT_OFFSET = 0x1C
TEXT_CAPACITY = SLOT_SIZE - TEXT_OFFSET
LINE_RE = re.compile(
    r"^\[text:(\d+)\] slot=(\d+) off=(0x[0-9A-Fa-f]+|auto) len=(\d+)"
    r"(?: src_hex=([0-9A-Fa-f ]+))? \| (.*)$"
)


def _decode_display(raw: bytes) -> str:
    return raw.decode("cp932", errors="replace")


def _slot_count(data: bytes) -> int:
    return (len(data) - FIXED_START) // SLOT_SIZE


def _slot_raw(data: bytes, slot: int) -> bytes:
    start = FIXED_START + slot * SLOT_SIZE + TEXT_OFFSET
    field = data[start:start + TEXT_CAPACITY]
    zero = field.find(b"\0")
    return field if zero < 0 else field[:zero]


def _conservative_encoded_length(text: str) -> int:
    """Measure capacity before the build has allocated custom glyph slots."""
    total = 0
    for char in text:
        if "\u4e00" <= char <= "\u9fff":
            total += 2
        else:
            total += len(char.encode("cp932"))
    return total


def _parse(path: Path) -> list[dict]:
    entries = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line or line.startswith("#"):
            continue
        match = LINE_RE.match(line)
        if match is None:
            raise ValueError(f"{path}:{line_no}: invalid balloon.bhf candidate row")
        seq, slot, off, cap, source_hex, content = match.groups()
        if "|" in content:
            source, target = content.split("|", 1)
        else:
            source, target = content, ""
        source = source.strip()
        target = target.strip()
        raw = bytes.fromhex(source_hex) if source_hex else source.encode("cp932")
        expected_off = FIXED_START + int(slot) * SLOT_SIZE + TEXT_OFFSET
        if off != "auto" and int(off, 16) != expected_off:
            raise ValueError(f"{path}:{line_no}: slot metadata does not match fixed layout")
        if int(cap) != TEXT_CAPACITY:
            raise ValueError(f"{path}:{line_no}: slot metadata does not match fixed layout")
        entries.append({
            "seq": int(seq),
            "slot": int(slot),
            "off": expected_off,
            "raw": raw,
            "source": source,
            "target": target,
        })
    return entries


def _entry_map(entries: list[dict]) -> dict[bytes, dict]:
    result: dict[bytes, dict] = {}
    for entry in entries:
        old = result.get(entry["raw"])
        if old is not None and old["target"] != entry["target"]:
            raise ValueError(f"conflicting translations for {entry['source']!r}")
        result[entry["raw"]] = entry
    return result


def extract(source: Path = DEFAULT_SOURCE, output: Path = DEFAULT_CANDIDATE) -> int:
    data = source.read_bytes()
    unique: OrderedDict[bytes, tuple[int, str]] = OrderedDict()
    for slot in range(_slot_count(data)):
        raw = _slot_raw(data, slot)
        if raw and raw not in unique:
            unique[raw] = (slot, _decode_display(raw))
    lines = [
        "# balloon.bhf 固定槽位说明文本（原文直接提取）",
        "# 每个槽位为 0x28 字节；只填写最后一列译文，不修改原文和元数据。",
    ]
    for seq, (raw, (slot, source_text)) in enumerate(unique.items()):
        off = FIXED_START + slot * SLOT_SIZE + TEXT_OFFSET
        lines.append(
            f"[text:{seq:03d}] slot={slot} off=0x{off:X} len={TEXT_CAPACITY} "
            f"src_hex={raw.hex(' ')} | {source_text}|"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"extracted {len(unique)} unique text byte sequences from {source}")
    print(output)
    return 0


def verify(source: Path = DEFAULT_SOURCE, candidate: Path = DEFAULT_CANDIDATE) -> int:
    data = source.read_bytes()
    entries = _parse(candidate)
    if not entries:
        raise ValueError("candidate contains no entries")
    seen_slots: set[int] = set()
    for entry in entries:
        slot = entry["slot"]
        if slot in seen_slots:
            raise ValueError(f"duplicate candidate slot {slot}")
        seen_slots.add(slot)
        if not (0 <= slot < _slot_count(data)):
            raise ValueError(f"candidate slot out of range: {slot}")
        actual = _slot_raw(data, slot)
        if actual != entry["raw"]:
            raise ValueError(
                f"source mismatch at slot {slot}: expected {entry['raw'].hex(' ')}, "
                f"found {actual.hex(' ')}"
            )
        if entry["target"]:
            try:
                encoded_length = len(encode_translation(entry["target"]))
            except UnicodeEncodeError:
                encoded_length = _conservative_encoded_length(entry["target"])
            if encoded_length > TEXT_CAPACITY:
                raise ValueError(
                    f"translation too long at slot {slot}: {encoded_length}>{TEXT_CAPACITY}"
                )
    print(f"verified {len(entries)} candidate entries against {source}")
    return 0


def write(source: Path = DEFAULT_SOURCE, candidate: Path = DEFAULT_CANDIDATE,
          output: Path | None = None) -> int:
    data = source.read_bytes()
    entries = _parse(candidate)
    if not entries:
        raise ValueError("candidate contains no entries")
    mapping = _entry_map(entries)
    # Validate every candidate and prepare all encoded bytes before mutating.
    for entry in entries:
        slot = entry["slot"]
        if not (0 <= slot < _slot_count(data)):
            raise ValueError(f"candidate slot out of range: {slot}")
        actual = _slot_raw(data, slot)
        if actual != entry["raw"]:
            raise ValueError(f"source mismatch at slot {slot}")
        if entry["target"]:
            encoded = encode_translation(entry["target"])
            if len(encoded) > TEXT_CAPACITY:
                raise ValueError(
                    f"translation too long at slot {slot}: {len(encoded)}>{TEXT_CAPACITY}"
                )

    out = bytearray(data)
    changed_slots = []
    matched_keys: set[bytes] = set()
    for slot in range(_slot_count(data)):
        raw = _slot_raw(data, slot)
        entry = mapping.get(raw)
        if entry is None or not entry["target"]:
            continue
        encoded = encode_translation(entry["target"])
        start = FIXED_START + slot * SLOT_SIZE + TEXT_OFFSET
        out[start:start + TEXT_CAPACITY] = encoded + b"\0" * (TEXT_CAPACITY - len(encoded))
        changed_slots.append(slot)
        matched_keys.add(raw)
    unused = [entry["source"] for entry in entries if entry["raw"] not in matched_keys]
    if unused:
        raise ValueError(f"candidate entries not found in resource: {unused[:5]}")
    destination = output or source
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(out)
    print(f"balloon.bhf: translated_slots={len(changed_slots)} output={destination}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    ex = sub.add_parser("extract")
    ex.add_argument("source", nargs="?", type=Path, default=DEFAULT_SOURCE)
    ex.add_argument("output", nargs="?", type=Path, default=DEFAULT_CANDIDATE)
    ve = sub.add_parser("verify")
    ve.add_argument("source", nargs="?", type=Path, default=DEFAULT_SOURCE)
    ve.add_argument("candidate", nargs="?", type=Path, default=DEFAULT_CANDIDATE)
    wr = sub.add_parser("write")
    wr.add_argument("source", type=Path)
    wr.add_argument("candidate", nargs="?", type=Path, default=DEFAULT_CANDIDATE)
    wr.add_argument("output", nargs="?", type=Path)
    args = parser.parse_args()
    if args.command == "extract":
        return extract(args.source, args.output)
    if args.command == "verify":
        return verify(args.source, args.candidate)
    return write(args.source, args.candidate, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
