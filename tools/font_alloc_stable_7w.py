#!/usr/bin/env python3
"""Append-only Chinese glyph-slot allocation for Seven Warriors PS2.

Unlike ``font_hack_7w.py alloc``, this allocator treats the existing CSV as
part of the save-file compatibility contract.  Existing character mappings
are never reordered, removed, or reassigned.  New characters are appended to
the first unused slot in the font's canonical candidate order.

The map may therefore contain reserved characters that are no longer present
in the current translation set.  Keep the CSV under version control or back
it up together with every release that must load old saves.
"""
from __future__ import annotations

import argparse
import csv
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FONT = ROOT / "unpacked/CDIMAGE_FULL/seven_data_link/arita/font16.sfo"
DEFAULT_CHARSET = ROOT / "work/font/char_set.txt"
DEFAULT_MAP = ROOT / "work/font/char_to_slot.csv"


def _candidate_slots(font: Path) -> list[tuple[int, int, int]]:
    data = font.read_bytes()
    if len(data) < 0x210 + 3518 * 4:
        raise ValueError(f"font is too small: {font}")
    lookup = [struct.unpack_from("<H", data, 0x10 + lead * 2)[0]
              for lead in range(256)]
    kanji: list[tuple[int, int, int]] = []
    other: list[tuple[int, int, int]] = []
    for index in range(97, 3518):
        a, b = struct.unpack_from("<HH", data, 0x210 + index * 4)
        if a == 0x00FF and b == 0:
            continue
        lead = a & 0xFF
        trail = index - lookup[lead]
        item = (index, lead, trail)
        (kanji if 0x88 <= lead <= 0x98 else other).append(item)
    return kanji + other


def _read_charset(path: Path) -> list[str]:
    chars = {c for c in path.read_text(encoding="utf-8")
             if "\u4e00" <= c <= "\u9fff"}
    return sorted(chars)


def _read_existing(path: Path, slots: dict[int, tuple[int, int]]) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    rows: list[dict[str, str]] = []
    chars: set[str] = set()
    used: set[int] = set()
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"char", "index", "lead", "trail"}
        if not required.issubset(reader.fieldnames or ()):
            raise ValueError(f"stable map missing columns {sorted(required)}: {path}")
        for line_no, row in enumerate(reader, 2):
            char = row.get("char", "")
            if len(char) != 1 or not ("\u4e00" <= char <= "\u9fff"):
                raise ValueError(f"{path}:{line_no}: invalid CJK char")
            try:
                index = int(row["index"])
                lead = int(row["lead"], 16)
                trail = int(row["trail"], 16)
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"{path}:{line_no}: invalid slot fields") from exc
            if char in chars:
                raise ValueError(f"{path}:{line_no}: duplicate character {char}")
            if index in used:
                raise ValueError(f"{path}:{line_no}: duplicate slot {index}")
            expected = slots.get(index)
            if expected is None:
                raise ValueError(f"{path}:{line_no}: slot {index} is not writable")
            if expected != (lead, trail):
                raise ValueError(
                    f"{path}:{line_no}: slot {index} CP932 mismatch "
                    f"({lead:02X},{trail:02X} != {expected[0]:02X},{expected[1]:02X})")
            rows.append({"char": char, "index": str(index),
                         "lead": f"{lead:02X}", "trail": f"{trail:02X}"})
            chars.add(char)
            used.add(index)
    return rows


def allocate(charset: Path, mapping: Path, font: Path, dry_run: bool = False) -> dict[str, int]:
    candidates = _candidate_slots(font)
    slot_map = {index: (lead, trail) for index, lead, trail in candidates}
    rows = _read_existing(mapping, slot_map)
    known = {row["char"] for row in rows}
    used = {int(row["index"]) for row in rows}
    requested = _read_charset(charset)
    new_chars = [char for char in requested if char not in known]
    free = [(index, lead, trail) for index, lead, trail in candidates
            if index not in used]
    if len(new_chars) > len(free):
        raise ValueError(f"stable allocation overflow: need {len(new_chars)} new slots, "
                         f"only {len(free)} available")
    additions: list[dict[str, str]] = []
    for char, (index, lead, trail) in zip(new_chars, free):
        additions.append({"char": char, "index": str(index),
                          "lead": f"{lead:02X}", "trail": f"{trail:02X}"})
    output = rows + additions
    if not dry_run:
        mapping.parent.mkdir(parents=True, exist_ok=True)
        with mapping.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["char", "index", "lead", "trail"])
            writer.writeheader()
            writer.writerows(output)
    return {"existing": len(rows), "added": len(additions),
            "reserved": len(rows) - sum(char in requested for char in known),
            "total": len(output), "available": len(free) - len(additions)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--charset", type=Path, default=DEFAULT_CHARSET,
                        help="current CJK character set (default: work/font/char_set.txt)")
    parser.add_argument("--map", type=Path, default=DEFAULT_MAP,
                        help="persistent char_to_slot.csv (default: work/font/char_to_slot.csv)")
    parser.add_argument("--font", type=Path, default=DEFAULT_FONT,
                        help="font16.sfo used to derive the CP932 slot table")
    parser.add_argument("--dry-run", action="store_true",
                        help="report the allocation without writing the map")
    args = parser.parse_args()
    result = allocate(args.charset, args.map, args.font, args.dry_run)
    action = "would allocate" if args.dry_run else "allocated"
    print(f"stable alloc: {action} existing={result['existing']} added={result['added']} "
          f"reserved={result['reserved']} total={result['total']} "
          f"available={result['available']} map={args.map}")


if __name__ == "__main__":
    main()
