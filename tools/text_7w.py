#!/usr/bin/env python3
"""Seven Warriors (7W) PS2 text/resource tool — unified.

Merges the former two scripts (`tools/text_tool_7w.py` and
`tools/extract_text_7w.py`) into one coherent interface:

  scan     classify every unpacked resource by magic.
  extract  structurally parse SPL type-3/type-10 string pools and emit lines.
  write    rebuild variable-length SPL string pools and relocate references.

SPL extraction and writing use the same parsed pool/line model, so the
candidate file that `write` consumes has exactly the format `extract` produces.
Input files are read-only; everything is written under a work directory.
"""
from __future__ import annotations

import argparse
import csv
import re
import struct
from pathlib import Path

# ---- shared constants -------------------------------------------------------

FULLWIDTH_SPACE = b"\x81\x40"
BREAK_BYTES = frozenset({0x00, 0x0A, 0x0D, 0x1A})
# Control bytes that should never be part of displayable text.
CTRL_LOW = frozenset(range(0x01, 0x20)) - {0x0A, 0x0D}

# A valid CP932/Shift-JIS double byte or half-width katakana (0xA1-0xDF).
SJIS_CHAR = re.compile(rb"(?:[\x81-\x9f\xe0-\xfc][\x40-\x7e\x80-\xfc]|[\xa1-\xdf])")
CJK_CHAR = re.compile(rb"(?:[\x88-\x9f\xe0-\xfc][\x40-\x7e\x80-\xfc])")
# Hiragana (lead 0x82) + katakana (lead 0x83) double-byte pairs.
KANA_CHAR = re.compile(rb"(?:[\x82][\x9f-\xf1]|[\x83][\x40-\x96])")
# Any Japanese text double-byte pair: punctuation/symbols (0x81), kana
# (0x82/0x83), half-width-kana-context and JIS kanji (0x88-0x9f, 0xe0-0xfc).
# Used to tell real script text apart from binary noise WITHOUT penalizing
# kana-heavy lines (the old KANA-only fallback dropped lines like
# "さあ、とっとと行った、行った！" because its 、！ punctuation pulled the
# kana/double ratio just under 0.70).
JP_TEXT_CHAR = re.compile(
    rb"(?:[\x81][\x40-\x7e\x80-\xfc]|[\x82][\x40-\xf1]|[\x83][\x40-\x96]|"
    rb"[\x88-\x9f][\x40-\x7e\x80-\xfc]|[\xe0-\xfc][\x40-\x7e\x80-\xfc])")

# Candidate file line: `[idx] @0xoffset len=length | text`
LINE_RE = re.compile(r"^\[(\d+)\] @0x([0-9A-Fa-f]+) len=(\d+) \| ?(.*)$")


def take_translation(raw: str) -> str:
    """Return the write-back text from a candidate line.

    Supports the dual-column edit format where the translator keeps the
    original Japanese before the last '|' for reference:

        [idx] @0xoffset len=36 | ほら…だろう？ | 你看，有各种草木的气味吧？

    Only the text after the LAST '|' is written back; the original slice is
    ignored. A trailing inline annotation introduced by '  #' (spl) or a
    lead-in '#' comment line (nht/fhm) is stripped as before. Control code
    escapes ('\\n'/'\\r') are preserved for the caller.
    """
    content = raw
    # Keep only the translation that follows the last '|'.
    marker = content.rfind("|")
    if marker != -1:
        content = content[marker + 1:].lstrip(" ")
    # Drop a trailing inline " # ..." annotation (used by extract).
    if "  #" in content:
        content = content.split("  #")[0].rstrip()
    return content

MAGICS = {
    b"GIM\0": "gim",
    b"ITE\0": "ite",
    b"KWSM": "kwsm",
    b"PS2D": "ps2d",
    b"\x01\0\0\0": "plain-01",
}


# ---- Chinese-glyph -> CP932 map (from font_hack_7w.py allocation) ----------

_CSLOT_PATH = Path("work/font/char_to_slot.csv")
_CSLOT: dict[str, tuple[int, int]] | None = None


def _load_char_slots() -> dict[str, tuple[int, int]]:
    """Load char->CP932 slot bytes. Empty if the map has not been built."""
    global _CSLOT
    if _CSLOT is not None:
        return _CSLOT
    m: dict[str, tuple[int, int]] = {}
    if _CSLOT_PATH.exists():
        with _CSLOT_PATH.open(encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                c = row["char"]
                if len(c) == 1:
                    m[c] = (int(row["lead"], 16), int(row["trail"], 16))
    _CSLOT = m
    return m


def encode_translation(text: str) -> bytes:
    """Encode a translation line to CP932 bytes.

    Chinese characters whose glyphs were assigned to a font slot (see
    char_to_slot.csv) are emitted as that file's exact lead/trail bytes so the
    game renders the glyph written by the same map. All other characters
    are encoded normally with CP932. Raises UnicodeEncodeError if a Chinese
    char has no mapped slot (i.e. its glyph is missing from the font hack).
    """
    slots = _load_char_slots()
    out = bytearray()
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff":
            slot = slots.get(ch)
            if slot is not None:
                lead, trail = slot
                out += bytes([lead, trail])
                continue
            # Japanese kanji already present in the game's CP932 font remain
            # valid without a custom Chinese slot.  Only truly unencodable
            # characters require a slot-map error.
            try:
                out += ch.encode("cp932")
            except UnicodeEncodeError:
                raise UnicodeEncodeError("cp932", text,
                                         out_len := -1, -1, f"no glyph slot for {ch!r}")
        else:
            out += ch.encode("cp932")
    return bytes(out)


_FONT = Path("unpacked/CDIMAGE_FULL/seven_data_link/arita/font16.sfo")
_LEAD_LOOKUP: dict[int, int] | None = None


def _lead_lookup_init() -> None:
    """Cache the game's lead->base u16 table at font offset 0x10.

    index = lead_lookup[lead] + trail  is the game's glyph addressing rule.
    """
    global _LEAD_LOOKUP
    if _LEAD_LOOKUP is not None:
        return
    tbl: dict[int, int] = {}
    if _FONT.exists():
        d = _FONT.read_bytes()
        if d[:4] == b"SFO\0":
            for lead in range(256):
                tbl[lead] = struct.unpack_from("<H", d, 0x10 + lead * 2)[0]
    _LEAD_LOOKUP = tbl


def _index_to_cp932(idx: int) -> tuple[int, int]:
    """Return (lead, trail) CP932 bytes for a font slot index.

    Prefer deriving it directly from the font via the exact inverse of the game's
    lookup (trail = idx - lookup_table[lead], lead = A-field low byte). This is
    authoritative and immune to a stale index_to_cp932.csv (whose 0x82 region
    used to carry a wrong 0x40 start-trail). Falls back to the CSV only if the
    font is unavailable.
    """
    _lead_lookup_init()
    if _FONT.exists() and _LEAD_LOOKUP is not None:
        d = _FONT.read_bytes()
        if len(d) >= 0x210 + idx * 4 + 4 and idx >= 97:
            A = struct.unpack_from("<HH", d, 0x210 + idx * 4)[0]
            if not (A == 0x00FF):
                lead = A & 0xFF
                base = _LEAD_LOOKUP.get(lead)
                if base is not None and base < 0x8000:
                    trail = idx - base
                    if 0 <= trail <= 0xFF:
                        return lead, trail
    from pathlib import Path as _P
    pmap = _P("work/font/index_to_cp932.csv")
    if pmap.exists():
        with pmap.open(encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                if int(row["index"]) == idx:
                    return int(row["lead"], 16), int(row["trail"], 16)
    return (0, 0)


def decode(raw: bytes) -> str:
    return raw.decode("cp932", errors="replace")


def classify(data: bytes) -> str:
    return MAGICS.get(data[:4], "unknown")


# ---- .spl structural parser / rebuilder -----------------------------------

SPL_FOOTER = bytes.fromhex("000000000000000010000000ffffffff")
SPL_BLOCK_ORDER = (3, 9, 14, 4, 5, 6, 7, 8, 11, 13, 15, 10, 12)


def _align16(value: int) -> int:
    return (value + 15) & ~15


def parse_spl(data: bytes) -> dict:
    """Parse the complete container layout of a plain-01 SPL.

    The first 16 bytes precede a fixed-width VM instruction stream.  The data
    area starts with a directory of code-entry pairs, followed by fourteen
    typed blocks and a fixed 16-byte sentinel footer.  Blocks are length
    delimited, so unknown block payloads can be copied without interpretation.
    """
    if len(data) < 32 or data[:4] != b"\x01\0\0\0":
        raise ValueError("not a plain-01 SPL")
    version, reserved0, data_off, reserved1 = struct.unpack_from("<IIII", data, 0)
    if version != 1 or reserved0 != 0 or reserved1 != 0:
        raise ValueError(
            f"invalid SPL header version={version} reserved={reserved0},{reserved1}"
        )
    if data_off < 0x10 or data_off > len(data) - 16 or (data_off - 0x10) % 8:
        raise ValueError(f"invalid SPL code/data offset 0x{data_off:X}")
    if data[-16:] != SPL_FOOTER:
        raise ValueError("invalid SPL footer sentinel")

    table_kind, entry_count, table_end, table_reserved = struct.unpack_from(
        "<IIII", data, data_off
    )
    expected_end = _align16(16 + entry_count * 8)
    if table_kind != 2 or table_end != expected_end:
        raise ValueError(
            f"invalid SPL entry table kind={table_kind} end=0x{table_end:X} "
            f"expected=0x{expected_end:X}"
        )
    entries = [
        struct.unpack_from("<II", data, data_off + 16 + i * 8)
        for i in range(entry_count)
    ]
    instructions = []
    for index, offset in enumerate(range(0x10, data_off, 8)):
        opcode, arg2, arg3, value = struct.unpack_from("<HBBI", data, offset)
        instructions.append({
            "index": index,
            "offset": offset,
            "opcode": opcode,
            "arg2": arg2,
            "arg3": arg3,
            "value": value,
        })

    blocks = []
    pos = data_off + table_end
    while pos < len(data) - 16:
        if pos + 16 > len(data) - 16:
            raise ValueError(f"truncated SPL block header at 0x{pos:X}")
        kind, count, size, reserved = struct.unpack_from("<IIII", data, pos)
        if size < 16 or size % 16 or pos + size > len(data) - 16:
            raise ValueError(
                f"invalid SPL block at 0x{pos:X}: kind={kind} size=0x{size:X}"
            )
        blocks.append({
            "offset": pos,
            "kind": kind,
            "count": count,
            "size": size,
            "reserved": reserved,
            "payload": data[pos + 16:pos + size],
        })
        pos += size
    if pos != len(data) - 16:
        raise ValueError(f"SPL blocks end at 0x{pos:X}, expected 0x{len(data)-16:X}")
    block_order = tuple(block["kind"] for block in blocks)
    if block_order != SPL_BLOCK_ORDER:
        raise ValueError(f"unexpected SPL block order: {block_order}")
    if not any(block["kind"] == 10 for block in blocks):
        raise ValueError("SPL has no type-10 string pool")
    return {
        "data_offset": data_off,
        "version": version,
        "instructions": instructions,
        "table_kind": table_kind,
        "table_count": entry_count,
        "table_end": table_end,
        "table_reserved": table_reserved,
        "entries": entries,
        "blocks": blocks,
        "footer": data[-16:],
    }


def _pool_starts(payload: bytes) -> list[int]:
    """Return non-empty NUL-delimited string starts in a pool payload."""
    starts = []
    pos = 0
    while pos < len(payload):
        end = payload.find(b"\0", pos)
        if end < 0:
            break
        if end > pos:
            starts.append(pos)
        pos = end + 1
    return starts


def _spl_pool_spans(data: bytes, parsed: dict, kind: int) -> list[dict]:
    """Return string spans for type-3 or type-10 pool blocks.

    Type 10 is referenced by type-9/type-11 records.  Those references are
    authoritative and avoid treating zero-filled alignment as extra strings.
    Type 3 is an inline auxiliary string pool and is scanned directly.
    """
    blocks = [block for block in parsed["blocks"] if block["kind"] == kind]
    if len(blocks) != 1:
        raise ValueError(f"expected one type-{kind} pool, found {len(blocks)}")
    block = blocks[0]
    payload = block["payload"]
    if kind == 10:
        starts = set()
        for ref_block in parsed["blocks"]:
            if ref_block["kind"] not in (9, 11):
                continue
            payload_len = ref_block["size"] - 16
            if payload_len != ref_block["count"] * 16:
                raise ValueError(
                    f"type-{ref_block['kind']} has unexpected record size"
                )
            raw = data[ref_block["offset"] + 16:ref_block["offset"] + ref_block["size"]]
            for i in range(ref_block["count"]):
                rel = struct.unpack_from("<I", raw, i * 16 + 8)[0]
                if rel >= len(payload) or (rel and payload[rel - 1] != 0):
                    raise ValueError(
                        f"type-{ref_block['kind']} string reference 0x{rel:X} "
                        "does not point to a pool string"
                    )
                starts.add(rel)
        if len(starts) != block["count"]:
            raise ValueError(
                f"type-10 count={block['count']} but references have {len(starts)} strings"
            )
        starts = sorted(starts)
    else:
        starts = _pool_starts(payload)

    spans = []
    for index, start in enumerate(starts):
        next_start = starts[index + 1] if index + 1 < len(starts) else len(payload)
        nul = payload.find(b"\0", start, next_start)
        if nul < 0:
            raise ValueError(f"unterminated type-{kind} string at pool offset 0x{start:X}")
        spans.append({
            "kind": kind,
            "block": block,
            "pool_start": block["offset"] + 16,
            "start": start,
            "end": next_start,
            "text_end": nul,
        })
    return spans


def extract_spl_lines(data: bytes, min_chars: int = 2) -> list[tuple[int, int, str]]:
    """Extract display lines only from the two SPL string pools."""
    parsed = parse_spl(data)
    lines = []
    for kind in (3, 10):
        for span in _spl_pool_spans(data, parsed, kind):
            base = span["pool_start"] + span["start"]
            raw = data[base:span["text_end"] + span["pool_start"]]
            for start, end, text in extract_lines_from(raw, min_chars):
                lines.append((base + start, base + end, text))
    return sorted(lines, key=lambda row: row[0])


def _rebuild_pool(spans: list[dict], replacements: dict[int, tuple[int, bytes]]) -> tuple[bytes, dict[int, int], int]:
    """Rebuild one pool and return (payload, old_rel->new_rel, changed_count)."""
    if not spans:
        block = None
        return b"", {}, 0
    block = spans[0]["block"]
    payload = block["payload"]
    out = bytearray(payload[:spans[0]["start"]])
    relocation = {}
    changed = 0
    for span in spans:
        old_start = span["start"]
        old_end = span["end"]
        old_text_end = span["text_end"]
        logical = payload[old_start:old_text_end]
        suffix = payload[old_text_end:old_end]  # NUL + original alignment zeros
        edits = []
        for off, (old_len, encoded) in replacements.items():
            if (span["pool_start"] + old_start <= off
                    and off + old_len <= span["pool_start"] + old_text_end):
                edits.append((off - span["pool_start"] - old_start,
                              old_len, encoded))
        edits.sort()
        # The caller validates lengths; this local guard keeps overlapping or
        # out-of-span rows from corrupting a neighboring string.
        cursor = 0
        rebuilt = bytearray()
        for rel, old_len, encoded in edits:
            if rel < cursor or rel > len(logical):
                continue
            if rel + old_len > len(logical):
                continue
            rebuilt.extend(logical[cursor:rel])
            rebuilt.extend(encoded)
            cursor = rel + old_len
            changed += 1
        rebuilt.extend(logical[cursor:])
        relocation[old_start] = len(out)
        out.extend(rebuilt)
        out.extend(suffix)
    return bytes(out), relocation, changed


def rebuild_spl(data: bytes, rows: list[dict]) -> tuple[bytes, dict[str, int]]:
    """Rebuild an SPL with translated lines allowed to grow.

    Rows are anchored by their original absolute offsets.  Only rows in type-3
    or type-10 pools are accepted; heuristic false positives in binary blocks
    are reported as ``ignored``.  Type-9/type-11 string references are
    relocated after the type-10 pool is rebuilt.
    """
    parsed = parse_spl(data)
    spans_by_kind = {kind: _spl_pool_spans(data, parsed, kind) for kind in (3, 10)}
    replacements = {3: {}, 10: {}}
    ignored = unencodable = mismatch = relocated = 0
    span_lookup = []
    line_lookup = []
    for kind, spans in spans_by_kind.items():
        for span in spans:
            abs_start = span["pool_start"] + span["start"]
            abs_end = span["pool_start"] + span["text_end"]
            span_lookup.append((abs_start, abs_end, kind))
            raw = data[abs_start:abs_end]
            for line_start, line_end, source_text in extract_lines_from(raw, 1):
                line_lookup.append((abs_start + line_start,
                                    abs_start + line_end, kind, source_text))

    for row in rows:
        if not row.get("translated", True):
            continue
        off = int(row["off"])
        length = int(row["length"])
        match = next((item for item in span_lookup if item[0] <= off and off + length <= item[1]), None)
        source = str(row.get("source", "")).replace("\\n", "\n").replace("\\r", "\r").strip()
        if source:
            direct_source = ""
            if match is not None and 0 <= off <= len(data) and off + length <= len(data):
                direct_source = clean_text(data[off:off + length])
            if direct_source != source:
                alternatives = [item for item in line_lookup if item[3] == source]
                if not alternatives:
                    ignored += 1
                    continue
                chosen = min(alternatives, key=lambda item: abs(item[0] - off))
                off, length, kind = chosen[0], chosen[1] - chosen[0], chosen[2]
                relocated += 1
            else:
                kind = match[2]
        else:
            if match is None:
                ignored += 1
                continue
            kind = match[2]
        try:
            text = str(row["text"]).replace("\\n", "\n").replace("\\r", "\r")
            encoded = encode_translation(text)
        except UnicodeEncodeError:
            unencodable += 1
            continue
        replacements[kind][off] = (length, encoded)

    rebuilt_pools = {}
    relocations = {}
    changed = 0
    for kind in (3, 10):
        spans = spans_by_kind[kind]
        if not spans:
            rebuilt_pools[kind] = b""
            relocations[kind] = {}
            continue
        pool, relocation, n = _rebuild_pool(spans, replacements[kind])
        rebuilt_pools[kind] = pool
        relocations[kind] = relocation
        changed += n

    # Re-emit blocks in their original order.  All block sizes are 16-byte
    # aligned; moving a grown pool therefore needs no absolute-pointer fixups.
    output = bytearray(data[:parsed["data_offset"]])
    output.extend(data[parsed["data_offset"]:parsed["data_offset"] + parsed["table_end"]])
    for block in parsed["blocks"]:
        kind = block["kind"]
        block_bytes = bytearray(data[block["offset"]:block["offset"] + block["size"]])
        if kind in (3, 10):
            pool = rebuilt_pools[kind]
            new_size = _align16(16 + len(pool))
            header = block_bytes[:16]
            struct.pack_into("<I", header, 8, new_size)
            block_bytes = bytearray(header) + bytearray(pool)
            block_bytes.extend(b"\0" * (new_size - len(block_bytes)))
        elif kind in (9, 11):
            # Type-9/type-11 records use payload word +8 as a type-10-relative
            # string offset.  The type-3 pool has no external references.
            for i in range(block["count"]):
                field = 16 + i * 16 + 8
                old_rel = struct.unpack_from("<I", block_bytes, field)[0]
                new_rel = relocations[10].get(old_rel)
                if new_rel is None:
                    mismatch += 1
                else:
                    struct.pack_into("<I", block_bytes, field, new_rel)
        output.extend(block_bytes)
    output.extend(SPL_FOOTER)
    return bytes(output), {
        "changed": changed,
        "ignored": ignored,
        "relocated": relocated,
        "unencodable": unencodable,
        "mismatch": mismatch,
    }


# ---- candidate parsing -----------------------------------------------------

def parse_candidate(path: Path) -> list[dict]:
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        match = LINE_RE.match(line)
        if not match:
            raise ValueError(f"{path}:{number}: invalid candidate line")
        # A row is "translated" only when the editor supplied a dual-column
        # form "orig | 译文". Single-column rows still hold the untranslated
        # original (Japanese) and must be left untouched — collecting/writing
        # them would mistake Japanese kanji for the Chinese translation.
        field = match.group(4)
        translated = "|" in field
        source_text = field[:field.rfind("|")].rstrip() if translated else field
        row_text = take_translation(field)
        rows.append({"idx": int(match.group(1)), "off": int(match.group(2), 16),
                     "length": int(match.group(3)), "text": row_text,
                     "source": source_text, "translated": translated,
                     "candidate_line": number})
    return rows


# ---- line-level clean extraction (from former extract_text_7w.py) ----------

def split_lines(data: bytes, min_chars: int = 2) -> list[tuple[int, int, bytes]]:
    """Return (start, end, raw) for each displayable text line.

    A line is a maximal run of bytes without a break byte that survives
    fixed-width padding trim. The raw slice keeps original length/offset.
    """
    lines: list[tuple[int, int, bytes]] = []
    start: int | None = None
    n = len(data)
    i = 0
    while i < n:
        b = data[i]
        if b in BREAK_BYTES:
            if start is not None and i > start:
                raw = data[start:i]
                if looks_text(raw, min_chars):
                    lines.append((start, i, raw))
            start = None
            i += 1
            continue
        if b in CTRL_LOW:
            if start is not None and i > start:
                raw = data[start:i]
                if looks_text(raw, min_chars):
                    lines.append((start, i, raw))
            start = None
            i += 1
            continue
        if start is None:
            start = i
        i += 1
    if start is not None and n > start:
        raw = data[start:n]
        if looks_text(raw, min_chars):
            lines.append((start, n, raw))
    return lines


def looks_text(raw: bytes, min_chars: int = 2) -> bool:
    if len(raw) < 2:
        return False
    cjk = len(CJK_CHAR.findall(raw))
    double = len(SJIS_CHAR.findall(raw))
    if double < min_chars:
        return False
    trimmed = strip_padding(raw)
    if not trimmed:
        return False
    ratio = len(CJK_CHAR.findall(trimmed)) / max(1, len(double_bytes(trimmed)))
    # Lines that are mostly kana + Japanese punctuation carry few CJK ideographs
    # (e.g. "さあ、とっとと行った、行った！"), so the 25% kanji gate would drop
    # them. Accept any line whose double-byte run is overwhelmingly made of
    # valid Japanese text characters (kana / kanji / 、。！？ symbols / 々ー〜 …).
    if ratio >= 0.25:
        return True
    jptext = len(JP_TEXT_CHAR.findall(trimmed))
    return jptext / max(1, len(double_bytes(trimmed))) >= 0.7


def double_bytes(raw: bytes) -> list[bytes]:
    return [m.group() for m in SJIS_CHAR.finditer(raw)]


def strip_padding(raw: bytes) -> bytes:
    """Trim full-width (0x8140) and half-width (0x20) space filler without ever
    dropping a double-byte lead byte."""
    out = bytearray()
    full = b"\x81\x40"
    i = 0
    n = len(raw)
    pending = False
    while i < n:
        if raw[i] == 0x20:
            pending = True
            i += 1
            continue
        if raw.startswith(full, i):
            pending = True
            i += 2
            continue
        if pending:
            out.append(0x20)
            pending = False
        out.append(raw[i])
        i += 1
    if pending:
        out.append(0x20)
    return bytes(out).strip(b"\x20")


def clean_text(raw: bytes) -> str:
    return decode(strip_padding(raw)).strip()


def extract_lines_from(data: bytes, min_chars: int) -> list[tuple[int, int, str]]:
    return [(start, end, clean_text(raw)) for start, end, raw
            in split_lines(data, min_chars)]


# ---- scan (from former text_tool_7w.py) ------------------------------------

def scan(root: Path, output: Path) -> None:
    rows = []
    for path in sorted(p for p in root.rglob("*") if p.is_file() and p.name != "manifest.tsv"):
        data = path.read_bytes()
        kind = classify(data)
        candidates = 0
        if kind == "plain-01":
            candidates = len(scan_sjis_counts(data))
        rows.append({"file": path.relative_to(root).as_posix(), "size": len(data),
                     "magic": data[:4].hex(" "), "kind": kind,
                     "candidate_count": candidates,
                     "status": "extractable" if candidates else "classified-only"})
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else
                                ["file", "size", "magic", "kind", "candidate_count", "status"])
        writer.writeheader()
        writer.writerows(rows)
    counts = {}
    for row in rows:
        counts[row["kind"]] = counts.get(row["kind"], 0) + 1
    print(f"scanned={len(rows)} report={output}")
    print(" ".join(f"{key}={value}" for key, value in sorted(counts.items())))


# A lightweight run counter for scan (avoids pulling in full line splitting).
SJIS_RUN = re.compile(rb"(?:[\x20-\x7e\x0a\x0d\x09]|[\x81-\x9f\xe0-\xfc][\x40-\x7e\x80-\xfc]|[\xa1-\xdf])+")


def scan_sjis_counts(data: bytes, minimum_jp: int = 2) -> list[tuple[int, int]]:
    found = []
    for match in SJIS_RUN.finditer(data):
        raw = match.group()
        if len(CJK_CHAR.findall(raw)) + len(SJIS_CHAR.findall(raw)) >= minimum_jp and len(raw) >= 4:
            found.append((match.start(), match.end()))
    return found


# ---- .nht help-text container ----------------------------------------------
#
# Layout (verified on help/endless.nht and help/rpg.nht):
#   u32                  image_count
#   0x004..0x3FF         zero-terminated text-entry offsets (absolute), then 0
#   0x400..0x7FF         image offsets; slot 0 is zero, slots 1..image_count,
#                        followed by zero-filled unused slots
#   text entries         entry 0 is the link directory; later entries have
#                        a 0x40-byte title slot followed by the body
#   ITE image resources  beginning at image_offsets[1]
# Text offsets and image offsets are independent tables. The text offset list
# ends at its first zero, so its count is not the u32 at file offset 0.
# Title slot is [offs[i], offs[i] + 0x40] (NUL-filled). Body starts at
# offs[i] + 0x40 and extends to the next text offset, or image_offsets[1] for
# the last text entry. Body lines split on 0x0A.
# -----------------------------------------------------------------------------

NHT_HEADER_SIZE = 0x800
NHT_TEXT_TABLE_MAX = 0xFF
NHT_BODY_PREFIX = 0x40

# Inline "hot-link" field. Structural analysis (verified on endless/rpg.nht):
#   byte 28 00 00 80 16  ...  (usually preceded by a \x14 escape byte)
#   <id:u16> <len:u8> ff ff <title:CP932> 17 <len2:u16> 13 16 14 00 00 00 80
# The leading \x14 (when present) belongs to the field. Title contains no 0x00
# and no 0x17, so we can locate the whole field and reuse its bytes verbatim on
# write while substituting only the surrounding visible text.
NHT_LINK_MARK = b"\x28\x00\x00\x80\x16"
NHT_LINK_TAIL = b"\x13\x16\x14\x00\x00\x00\x80"


def nht_find_links(data: bytes, start: int, end: int) -> list[tuple[int, int, str]]:
    """Return (field_start_abs, field_end_abs, decoded_title) for each inline
    link field in data[start:end]. Field bytes [field_start, field_end) include
    the optional leading \x14 escape and the full trailing marker."""
    out = []
    pos = start
    while True:
        s = data.find(NHT_LINK_MARK, pos, end)
        if s < 0:
            break
        fs = s - 1 if (s > start and data[s - 1] == 0x14) else s
        t0 = s + 5 + 2 + 1 + 2  # mark(5)+id(2)+len(1)+ff(2)
        m = data.find(b"\x17", t0, end)
        if m < 0:
            break
        title = data[t0:m].decode("cp932", errors="replace")
        fend = m + 3 + 7  # \x17 + len2(2) + tail(7)
        out.append((fs, fend, title))
        pos = fend
    return out


def nht_link_title_info(field: bytes) -> tuple[str, str, str]:
    """Return (full title, visible title, routing suffix) for one link field."""
    mark = field.find(NHT_LINK_MARK)
    if mark < 0:
        raise ValueError("nht link: missing marker")
    title_start = mark + 5 + 2 + 1 + 2
    title_end = field.find(b"\x17", title_start)
    if title_end < 0:
        raise ValueError("nht link: missing title terminator")
    full = field[title_start:title_end].decode("cp932")
    match = re.search(r"(_\d+)$", full)
    suffix = match.group(1) if match else ""
    visible = full[:-len(suffix)] if suffix else full
    return full, visible, suffix


def nht_replace_link_title(field: bytes, value: str) -> bytes:
    """Replace only a link's title while retaining its control/routing bytes."""
    mark = field.find(NHT_LINK_MARK)
    if mark < 0:
        raise ValueError("nht link: missing marker")
    title_start = mark + 5 + 2 + 1 + 2
    title_end = field.find(b"\x17", title_start)
    if title_end < 0:
        raise ValueError("nht link: missing title terminator")
    _full, _visible, suffix = nht_link_title_info(field)
    if suffix and not value.endswith(suffix):
        value += suffix
    encoded = encode_translation(value)
    # In the original format the u16 after ``0x17`` equals the title byte
    # length plus 6. It is parser length data, not routing data, so it must
    # follow a translated title's byte length or the game loses later links.
    link_length = len(encoded) + 6
    if link_length > 0xFFFF:
        raise ValueError("nht link: translated title is too long")
    length_bytes = struct.pack("<H", link_length)
    return (field[:title_start] + encoded + field[title_end:title_end + 1]
            + length_bytes + field[title_end + 3:])


def nht_body_nodes(body: bytes) -> list[tuple[str, int, int]]:
    """Split a body into (kind, start, end) nodes by byte type.

    kind:
      "link"  - inline hot-link field, structurally preserved on rewrite
      "text"  - a run of clean CP932 text (double-byte, half-width katakana,
                ASCII incl. space/punctuation); this is the translatable unit
      "keep"  - anything else (NUL filler, control bytes, lone high bytes),
                kept verbatim on rewrite

    Individual double-byte pairs are merged into one "text" node with their
    neighbors so that a line reads naturally.
    """
    links = []
    n = len(body)
    pos = 0
    while True:
        s = body.find(NHT_LINK_MARK, pos, n)
        if s < 0:
            break
        fs = s - 1 if (s > 0 and body[s - 1] == 0x14) else s
        t0 = s + 5 + 2 + 1 + 2
        m17 = body.find(b"\x17", t0, n)
        if m17 < 0:
            break
        fe = m17 + 3 + 7
        links.append((fs, fe))
        pos = fe

    def is_text_start(i: int) -> bool:
        c = body[i]
        if 0x81 <= c <= 0x9F or 0xE0 <= c <= 0xFC:
            return i + 1 < n and True
        return 0xA1 <= c <= 0xDF or 0x20 <= c < 0x7F

    nodes: list[tuple[str, int, int]] = []
    i = 0
    li = 0
    while i < n:
        if li < len(links) and i == links[li][0]:
            nodes.append(("link", links[li][0], links[li][1]))
            i = links[li][1]
            li += 1
            continue
        c = body[i]
        if 0x81 <= c <= 0x9F or 0xE0 <= c <= 0xFC:
            # double-byte run (merge adjacent)
            j = i
            while j < n:
                cc = body[j]
                if 0x81 <= cc <= 0x9F or 0xE0 <= cc <= 0xFC:
                    j += 2
                else:
                    # a single ASCII or half-width kana adjacent keeps run going
                    if j < n and (0x20 <= body[j] < 0x7F or 0xA1 <= body[j] <= 0xDF):
                        j += 1
                        continue
                    break
            nodes.append(("text", i, j))
            i = j
            continue
        if 0xA1 <= c <= 0xDF or 0x20 <= c < 0x7F:
            j = i
            while j < n:
                cc = body[j]
                if 0xA1 <= cc <= 0xDF or 0x20 <= cc < 0x7F:
                    j += 1
                elif 0x81 <= cc <= 0x9F or 0xE0 <= cc <= 0xFC:
                    break
                else:
                    break
            nodes.append(("text", i, j))
            i = j
            continue
        # keep run (NUL/control/lone high byte)
        j = i
        while j < n:
            cc = body[j]
            if is_text_start(j) or (li < len(links) and j == links[li][0]):
                break
            j += 1
        if j == i:
            j = i + 1
        nodes.append(("keep", i, j))
        i = j
    return nodes


def nht_parse_full(data: bytes) -> dict:
    """Parse the complete NHT header and text/image resource boundaries."""
    if len(data) < NHT_HEADER_SIZE:
        raise ValueError("not a .nht file (header too small)")
    image_count = struct.unpack_from("<I", data, 0)[0]
    if image_count == 0 or image_count > NHT_TEXT_TABLE_MAX:
        raise ValueError("nht: invalid image count")

    text_offsets: list[int] = []
    for index in range(1, NHT_TEXT_TABLE_MAX + 1):
        off = struct.unpack_from("<I", data, index * 4)[0]
        if off == 0:
            break
        text_offsets.append(off)
    if not text_offsets or text_offsets[0] < NHT_HEADER_SIZE:
        raise ValueError("nht: invalid text offset table")
    if text_offsets != sorted(text_offsets):
        raise ValueError("nht: text offsets are not sorted")
    if any(off >= len(data) for off in text_offsets):
        raise ValueError("nht: text offset out of range")

    image_offsets = [struct.unpack_from("<I", data, 0x400 + 4 * i)[0]
                     for i in range(image_count + 1)]
    if image_offsets[0] != 0 or any(not off for off in image_offsets[1:]):
        raise ValueError("nht: invalid image offset table")
    if image_offsets[1] <= text_offsets[-1] or image_offsets[1] >= len(data):
        raise ValueError("nht: invalid text/image boundary")
    if image_offsets[1:] != sorted(image_offsets[1:]):
        raise ValueError("nht: image offsets are not sorted")
    if any(data[off:off + 4] != b"ITE\x00" for off in image_offsets[1:]):
        raise ValueError("nht: image table does not point to ITE resources")
    return {"image_count": image_count, "text_offsets": text_offsets,
            "image_offsets": image_offsets, "text_start": text_offsets[0],
            "text_end": image_offsets[1], "image_start": image_offsets[1]}


def nht_parse(data: bytes) -> tuple[int, list[int]]:
    """Compatibility wrapper returning the text-entry count and offsets."""
    parsed = nht_parse_full(data)
    return len(parsed["text_offsets"]), parsed["text_offsets"]


def nht_title(data: bytes, off: int) -> tuple[str, int]:
    """(decoded title, byte length incl. NUL) at off."""
    end = data.find(b"\x00", off)
    if end < 0:
        end = len(data)
    raw = data[off:end]
    return raw.decode("cp932", errors="replace"), end - off + 1


def nht_body_slot(data: bytes, cnt: int, offs: list[int], i: int,
                  text_end: int | None = None) -> tuple[int, int, int]:
    """Return (body_start, body_len, body_end) for text entry i (>=1)."""
    body_start = offs[i] + NHT_BODY_PREFIX
    if body_start < 0 or body_start >= len(data):
        raise ValueError(f"entry {i}: invalid body start")
    body_end = (offs[i + 1] if i + 1 < cnt
                else (text_end if text_end is not None else len(data)))
    if body_end < body_start or body_end > len(data):
        raise ValueError(f"entry {i}: invalid body end")
    return body_start, body_end - body_start, body_end


# ---- .fhm UI/name text container -------------------------------------------
#
# FHM is a sub-resource container.  chiibaya/common/common.fhm contains two
# SFO fonts followed by a text-table sub-resource.  The text table is:
#
#   u32 count
#   u32 runtime_ptr[count]   # all zero on disk; absolute pointers after load
#   char strings[count][]    # packed NUL-terminated CP932, empty entries valid
#   zero padding
#
# PCSX2 confirms that the game overwrites runtime_ptr[] in-place when loading
# the resource.  Rebuilding therefore keeps that cache zeroed and regenerates
# the complete ordered string pool.  The FHM container is then reassembled and
# all following absolute sub-resource offsets are relocated.
# -----------------------------------------------------------------------------

FHM_SFO = b"SFO\x00"
FHM_GIM = b"GIM\x00"


def parse_fhm(data: bytes) -> dict:
    """Parse the FHM container and return unique absolute sub-resource spans.

    The shipped 7W files use a sorted table, but the loader treats the table
    as references: duplicate entries are legal and some tools emit them out
    of order.  Boundaries therefore come from the next strictly greater
    *unique* offset, matching the runtime container convention.
    """
    if len(data) < 8:
        raise ValueError("fhm: file too small")
    count = struct.unpack_from("<I", data, 0)[0]
    table_end = 4 + count * 4
    if count == 0 or count > 0x10000 or table_end > len(data):
        raise ValueError("fhm: invalid sub-resource count/table")
    offsets = [struct.unpack_from("<I", data, 4 + i * 4)[0]
               for i in range(count)]
    if any(o < table_end or o >= len(data) for o in offsets):
        raise ValueError("fhm: sub-resource offset out of range")
    unique_offsets = sorted(set(offsets))
    spans = [(o, unique_offsets[i + 1] if i + 1 < len(unique_offsets)
              else len(data))
             for i, o in enumerate(unique_offsets)]
    if any(start >= end for start, end in spans):
        raise ValueError("fhm: empty/overlapping sub-resource")
    return {"count": count, "offsets": offsets,
            "unique_offsets": unique_offsets, "spans": spans}


def parse_fhm_text_resource(sub: bytes) -> dict:
    """Parse the zero-cache + ordered-string-pool text sub-resource.

    This deliberately rejects the unrelated numeric blocks in
    rpgcommon/common.fhm instead of treating arbitrary binary as CP932 text.
    """
    if len(sub) < 8:
        raise ValueError("fhm text: sub-resource too small")
    count = struct.unpack_from("<I", sub, 0)[0]
    table_end = 4 + count * 4
    if count == 0 or count > 0x10000 or table_end > len(sub):
        raise ValueError("fhm text: invalid count/cache table")
    cache = sub[4:table_end]
    if any(cache):
        raise ValueError("fhm text: on-disk runtime pointer cache is not zero")

    rows: list[tuple[int, int, bytes]] = []
    pos = table_end
    nonempty = 0
    for index in range(count):
        end = sub.find(b"\x00", pos)
        if end < 0:
            raise ValueError(f"fhm text: entry {index} has no terminator")
        raw = sub[pos:end]
        try:
            raw.decode("cp932")
        except UnicodeDecodeError as exc:
            raise ValueError(f"fhm text: entry {index} is not CP932") from exc
        rows.append((pos, end, raw))
        nonempty += bool(raw)
        pos = end + 1

    # The known text block has a substantial populated pool.  A short
    # unindexed tail follows it (five legacy strings in the shipped common.fhm);
    # keep that tail as a separately addressable string list.
    if nonempty < max(1, count // 2):
        raise ValueError("fhm text: too few populated strings")
    tail_start = next((i for i in range(pos, len(sub)) if sub[i]), len(sub))
    tail_rows: list[tuple[int, int, bytes]] = []
    tail_gaps: list[bytes] = []
    tail_pos = tail_start
    tail_cursor = tail_start
    while tail_pos < len(sub):
        next_nonzero = next((i for i in range(tail_pos, len(sub)) if sub[i]), None)
        if next_nonzero is None:
            break
        tail_pos = next_nonzero
        tail_gaps.append(sub[tail_cursor:tail_pos])
        end = sub.find(b"\x00", tail_pos)
        if end < 0:
            raise ValueError("fhm text: unterminated tail string")
        raw = sub[tail_pos:end]
        try:
            raw.decode("cp932")
        except UnicodeDecodeError as exc:
            raise ValueError("fhm text: invalid CP932 tail") from exc
        tail_rows.append((tail_pos, end, raw))
        tail_pos = end + 1
        tail_cursor = tail_pos
    tail_suffix = sub[tail_cursor:]
    if any(tail_suffix):
        raise ValueError("fhm text: non-zero data follows tail strings")
    return {"count": count, "table_end": table_end, "rows": rows,
            "strings_end": pos, "tail_start": tail_start,
            "tail_rows": tail_rows, "tail_gaps": tail_gaps,
            "padding": tail_suffix}


def find_fhm_text_resource(data: bytes) -> tuple[int, int, int, dict]:
    """Return (sub-index, absolute start, absolute end, parsed text block)."""
    container = parse_fhm(data)
    matches = []
    for index, (start, end) in enumerate(container["spans"]):
        head = data[start:start + 4]
        if head in (FHM_SFO, FHM_GIM):
            continue
        try:
            parsed = parse_fhm_text_resource(data[start:end])
        except ValueError:
            continue
        matches.append((index, start, end, parsed))
    if not matches:
        raise ValueError("fhm: no validated text sub-resource found")
    if len(matches) != 1:
        raise ValueError(f"fhm: ambiguous text sub-resources ({len(matches)})")
    return matches[0]


def fhm_text_sub(data: bytes) -> tuple[int, int]:
    """Compatibility wrapper returning the validated text sub-resource span."""
    _index, start, end, _parsed = find_fhm_text_resource(data)
    return start, end


def rebuild_fhm_text(data: bytes, edits: dict[int, str]) -> tuple[bytes, dict]:
    """Rebuild the complete FHM text pool and relocate container offsets."""
    container = parse_fhm(data)
    text_index, text_start, text_end, text = find_fhm_text_resource(data)
    sub = data[text_start:text_end]

    encoded: list[bytes] = []
    changed = unencodable = 0
    valid_edits = {i: value for i, value in edits.items()
                   if 0 <= i < text["count"]}
    valid_tail_edits = {i: value for i, value in edits.items()
                        if -len(text["tail_rows"]) <= i < 0}
    ignored = len(edits) - len(valid_edits) - len(valid_tail_edits)
    for index, (_start, _end, raw) in enumerate(text["rows"]):
        if index not in valid_edits:
            encoded.append(raw)
            continue
        value = valid_edits[index].replace("\\n", "\n").replace("\\r", "\r")
        try:
            replacement = encode_translation(value)
        except UnicodeEncodeError:
            replacement = raw
            unencodable += 1
        if replacement != raw:
            changed += 1
        encoded.append(replacement)

    tail_encoded: list[bytes] = []
    # New candidates address the unindexed tail as [tail:n].  Older drafts
    # emitted these five rows as text:479..483; accept both spellings.
    for i, (_start, _end, raw) in enumerate(text["tail_rows"]):
        value = valid_tail_edits.get(-(i + 1))
        if value is None:
            tail_encoded.append(raw)
            continue
        value = value.replace("\\n", "\n").replace("\\r", "\r")
        try:
            replacement = encode_translation(value)
        except UnicodeEncodeError:
            replacement = raw
            unencodable += 1
        if replacement != raw:
            changed += 1
        tail_encoded.append(replacement)

    body = bytearray(sub[:text["table_end"]])
    for raw in encoded:
        body.extend(raw)
        body.append(0)
    # Keep the original relative location of the unindexed tail whenever the
    # rebuilt primary pool still fits before it.  This protects any code that
    # addresses these legacy strings by a fixed sub-resource offset.
    if text["tail_rows"]:
        target_tail = text["tail_start"]
        if len(body) < target_tail:
            body.extend(b"\x00" * (target_tail - len(body)))
        elif len(body) > target_tail:
            body.extend(b"\x00" * (text["tail_start"] - text["strings_end"]))
        for index, raw in enumerate(tail_encoded):
            body.extend(text["tail_gaps"][index])
            body.extend(raw)
            body.append(0)
        body.extend(text["padding"])
    else:
        body.extend(text["padding"])
    # Preserve original capacity when possible.  A grown resource is rounded
    # to the same 0x10-byte alignment used by every observed FHM boundary.
    new_sub_size = max(len(sub), (len(body) + 0xF) & ~0xF)
    body.extend(b"\x00" * (new_sub_size - len(body)))

    replacements = {text_index: bytes(body)}
    out = bytearray(data[:container["unique_offsets"][0]])
    relocated: dict[int, int] = {}
    for index, (start, end) in enumerate(container["spans"]):
        relocated[start] = len(out)
        out.extend(replacements.get(index, data[start:end]))
    for index, offset in enumerate(container["offsets"]):
        struct.pack_into("<I", out, 4 + index * 4, relocated[offset])

    result = {"changed": changed, "unencodable": unencodable,
              "ignored": ignored, "entries": text["count"],
              "text_sub": text_index, "old_size": len(data),
              "new_size": len(out), "extended": len(out) - len(data)}
    return bytes(out), result


def _fhm_candidate_text(parsed: dict, sub_index: int, ts: int, te: int,
                        min_chars: int) -> tuple[str, int]:
    out_lines: list[str] = [
        f"# fhm 文本子资源 sub={sub_index} @0x{ts:X}..0x{te:X}  "
        f"(长度 {te - ts}, 记录 {parsed['count']}, 尾部文本 {len(parsed['tail_rows'])})",
        "# 格式: [text:<记录号>] 或 [tail:<n>] len=<原字节数> | <可编辑文本>。空记录不输出。",
        "",
    ]
    emitted = 0
    for index, (_start, _end, raw) in enumerate(parsed["rows"]):
        if not raw:
            continue
        txt = raw.decode("cp932")
        note = "" if len(txt) >= min_chars else "  # 短于默认阈值，视需要翻译"
        out_lines.append(f"[text:{index}] len={len(raw)} | {txt}{note}")
        emitted += 1
    for index, (_start, _end, raw) in enumerate(parsed["tail_rows"]):
        txt = raw.decode("cp932")
        note = "" if len(txt) >= min_chars else "  # 短于默认阈值，视需要翻译"
        out_lines.append(f"[tail:{index}] len={len(raw)} | {txt}{note}")
        emitted += 1
    return "\n".join(out_lines) + "\n", emitted


def _fhm_manifest_row(path: Path, data: bytes, status: str,
                      parsed: dict | None = None, text_sub: int = -1,
                      error: str = "") -> dict:
    row = {"file": str(path), "status": status, "bytes": len(data),
           "sub_count": "", "text_sub": text_sub,
           "text_entries": "", "nonempty": "", "tail": "", "error": error}
    if parsed is not None:
        row["sub_count"] = parsed.get("count", "")
        if "rows" in parsed:
            row.update({"text_entries": parsed.get("count", ""),
                        "nonempty": sum(bool(raw) for _a, _b, raw in parsed["rows"]),
                        "tail": len(parsed.get("tail_rows", []))})
    return row


def cmd_fhm_extract(args) -> None:
    manifest = getattr(args, "manifest", None)
    # Directory mode is an audit pass: every FHM is accounted for in the
    # manifest, including numeric/image-only containers that contain no text.
    if args.input.is_dir():
        root = args.input
        files = sorted(root.rglob("*.fhm"))
        total = 0
        rows: list[dict] = []
        for path in files:
            data = path.read_bytes()
            rel = path.relative_to(root)
            out = args.output / rel.parent / (rel.name + ".txt")
            try:
                sub_index, ts, te, parsed = find_fhm_text_resource(data)
                body, emitted = _fhm_candidate_text(parsed, sub_index, ts, te,
                                                    args.min_chars)
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(body, encoding="utf-8")
                rows.append(_fhm_manifest_row(rel, data, "text", parsed,
                                              sub_index))
                total += emitted
            except (ValueError, UnicodeError) as exc:
                try:
                    container = parse_fhm(data)
                except ValueError:
                    container = None
                rows.append(_fhm_manifest_row(rel, data, "no-text", container,
                                              error=str(exc)))
        if manifest:
            manifest.parent.mkdir(parents=True, exist_ok=True)
            fields = ["file", "status", "bytes", "sub_count", "text_sub",
                      "text_entries", "nonempty", "tail", "error"]
            with manifest.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
        text_files = sum(row["status"] == "text" for row in rows)
        print(f"fhm files={len(files)} text_files={text_files} strings={total} "
              f"output={args.output}")
        if manifest:
            print(f"manifest={manifest}")
        return

    data = args.input.read_bytes()
    sub_index, ts, te, parsed = find_fhm_text_resource(data)
    body, emitted = _fhm_candidate_text(parsed, sub_index, ts, te, args.min_chars)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(body, encoding="utf-8")
    if manifest:
        manifest.parent.mkdir(parents=True, exist_ok=True)
        fields = ["file", "status", "bytes", "sub_count", "text_sub",
                  "text_entries", "nonempty", "tail", "error"]
        with manifest.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerow(_fhm_manifest_row(args.input, data, "text", parsed,
                                               sub_index))
    print(f"fhm entries={parsed['count']} strings={emitted} "
          f"text_sub={sub_index}@0x{ts:X} output={args.output}")


def cmd_fhm_write(args) -> None:
    import re as _re
    input_data = args.input.read_bytes()
    _sub_index, _ts, _te, parsed = find_fhm_text_resource(input_data)
    candidate = args.candidate.read_text(encoding="utf-8-sig")
    text_re = _re.compile(r"^\[(text|tail):(\d+)\] len=(\d+) \| (.*)$")
    edits: dict[int, str] = {}
    mismatch = 0
    legacy = "# fhm 文本子资源 @" in candidate
    nonempty_indices = [i for i, (_start, _end, raw)
                        in enumerate(parsed["rows"]) if raw]
    for line in candidate.splitlines():
        m = text_re.match(line)
        if m:
            # Only dual-column rows ("orig | 译文") carry a translation; single-
            # column rows still hold the untranslated original and are left as-is.
            if "|" not in m.group(4):
                continue
            kind, index = m.group(1), int(m.group(2))
            if kind == "tail":
                key = -(index + 1)
                target = parsed["tail_rows"]
                target_index = index
            elif legacy and index >= len(nonempty_indices):
                key = -(index - len(nonempty_indices) + 1)
                target = parsed["tail_rows"]
                target_index = index - len(nonempty_indices)
            elif legacy:
                key = nonempty_indices[index] if index < len(nonempty_indices) else -1
                target = parsed["rows"]
                target_index = key
            else:
                key = index
                target = parsed["rows"]
                target_index = index
            if not (0 <= target_index < len(target)):
                mismatch += 1
                continue
            raw = target[target_index][2]
            # The separator contributes one space before the translation;
            # preserve any intentional leading spaces in the source string.
            source = m.group(4).rsplit("|", 1)[0].rstrip()
            if "  #" in source:
                source = source.split("  #", 1)[0].rstrip()
            source = source.replace("\\n", "\n").replace("\\r", "\r")
            try:
                actual = raw.decode("cp932")
            except UnicodeDecodeError:
                actual = ""
            if int(m.group(3)) != len(raw) or source != actual:
                mismatch += 1
                continue
            edits[key] = take_translation(m.group(4))
    rebuilt, result = rebuild_fhm_text(input_data, edits)
    result["mismatch"] = mismatch
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(rebuilt)
    quiet = getattr(args, "quiet", False)
    if not quiet:
        print(f"fhm {result} output={args.output}")
    elif result["unencodable"] or result["ignored"] or result["mismatch"]:
        print(f"fhm {result} output={args.output}")


def rebuild_nht(data: bytes, edits: dict[int, dict[int, str]],
                title_edits: dict[int, str] | None = None,
                link_edits: dict[int, dict[int, str]] | None = None) -> tuple[bytes, dict]:
    """Rebuild NHT text, title slots, and structurally preserved link fields."""
    parsed = nht_parse_full(data)
    offs = parsed["text_offsets"]
    cnt = len(offs)
    title_edits = title_edits or {}
    link_edits = link_edits or {}
    rebuilt_entries: list[bytes] = []
    new_offsets: list[int] = []
    changed_entries = changed_nodes = changed_titles = changed_links = 0
    unencodable = overflow = ignored = 0
    cursor = parsed["text_start"]

    for i in range(cnt):
        start = offs[i]
        end = offs[i + 1] if i + 1 < cnt else parsed["text_end"]
        new_offsets.append(cursor)
        title_slot = data[start:start + NHT_BODY_PREFIX]
        entry_changed = False
        if i > 0 and i in title_edits:
            try:
                encoded_title = encode_translation(title_edits[i])
            except UnicodeEncodeError:
                encoded_title = title_slot.split(b"\x00", 1)[0]
                unencodable += 1
            if len(encoded_title) >= NHT_BODY_PREFIX:
                encoded_title = title_slot.split(b"\x00", 1)[0]
                overflow += 1
            elif encoded_title != title_slot.split(b"\x00", 1)[0]:
                changed_titles += 1
                entry_changed = True
            title_slot = encoded_title + b"\x00" * (NHT_BODY_PREFIX - len(encoded_title))

        body = data[start + NHT_BODY_PREFIX:end]
        nodes = nht_body_nodes(body)
        entry_edits = edits.get(i, {})
        entry_link_edits = link_edits.get(i, {})
        # Node ids are the ordinal among text nodes, not among all nodes.
        text_count = sum(kind == "text" for kind, _a, _b in nodes)
        ignored += sum(index < 0 or index >= text_count for index in entry_edits)
        link_count = sum(kind == "link" for kind, _a, _b in nodes)
        ignored += sum(index < 0 or index >= link_count for index in entry_link_edits)

        new_body = bytearray()
        text_index = 0
        link_index = 0
        for kind, a, b in nodes:
            raw = body[a:b]
            if kind == "link" and link_index in entry_link_edits:
                try:
                    replacement = nht_replace_link_title(
                        raw, link_edits[i][link_index])
                except UnicodeEncodeError:
                    replacement = raw
                    unencodable += 1
                if replacement != raw:
                    changed_links += 1
                    entry_changed = True
                new_body.extend(replacement)
                link_index += 1
                continue
            if kind != "text" or text_index not in entry_edits:
                new_body.extend(raw)
            else:
                value = entry_edits[text_index].replace("\\n", "\n").replace(
                    "\\r", "\r")
                try:
                    replacement = encode_translation(value)
                except UnicodeEncodeError:
                    replacement = raw
                    unencodable += 1
                if replacement != raw:
                    changed_nodes += 1
                    entry_changed = True
                new_body.extend(replacement)
            if kind == "text":
                text_index += 1
            elif kind == "link":
                link_index += 1

        original_size = end - start
        used_size = len(title_slot) + len(new_body)
        new_size = (original_size if used_size <= original_size
                    else (used_size + 0xF) & ~0xF)
        blob = title_slot + bytes(new_body) + b"\x00" * (new_size - used_size)
        rebuilt_entries.append(blob)
        cursor += len(blob)
        changed_entries += entry_changed

    out = bytearray(data[:parsed["text_start"]])
    for blob in rebuilt_entries:
        out.extend(blob)
    if len(out) < parsed["image_start"]:
        out.extend(b"\x00" * (parsed["image_start"] - len(out)))
    image_delta = len(out) - parsed["image_start"]
    out.extend(data[parsed["image_start"]:])

    for index, offset in enumerate(new_offsets):
        struct.pack_into("<I", out, 4 + 4 * index, offset)
    for index in range(1, parsed["image_count"] + 1):
        struct.pack_into("<I", out, 0x400 + 4 * index,
                         parsed["image_offsets"][index] + image_delta)

    result = {"entries": cnt - 1, "changed_entries": changed_entries,
              "changed_nodes": changed_nodes, "changed_titles": changed_titles,
              "changed_links": changed_links, "unencodable": unencodable,
              "overflow": overflow,
              "ignored": ignored, "old_size": len(data), "new_size": len(out),
              "extended": len(out) - len(data), "image_delta": image_delta}
    return bytes(out), result


def cmd_nht_extract(args) -> None:
    data = args.input.read_bytes()
    parsed = nht_parse_full(data)
    offs = parsed["text_offsets"]
    cnt = len(offs)
    out_lines: list[str] = []
    for i in range(cnt):
        title = "" if i == 0 else nht_title(data, offs[i])[0]
        body_start, body_len, body_end = nht_body_slot(
            data, cnt, offs, i, parsed["text_end"])
        body_abs = data[body_start:body_end]
        nodes = nht_body_nodes(body_abs)
        lines = [f"### 条目 {i:04d} 标题={title} body=0x{body_start:X} len={body_len}",
                 "# 格式: [title] / [text:<n>] / [link:<n>] 均可用双栏翻译；[keep] 为只读结构。",
                 ""]
        if i > 0:
            title_raw = data[offs[i]:offs[i] + NHT_BODY_PREFIX]
            title_len = title_raw.find(b"\x00")
            if title_len < 0:
                title_len = NHT_BODY_PREFIX
            lines.append(f"[title] len={title_len} | {title}")
        ti = 0
        li = 0
        for kind, a, b in nodes:
            if kind == "text":
                try:
                    s = body_abs[a:b].decode("cp932")
                except Exception:
                    s = body_abs[a:b].decode("cp932", errors="replace")
                lines.append(f"[text:{ti}] len={b - a} | {s.replace(chr(10), '\\\\n')}")
                ti += 1
            elif kind == "link":
                _full, visible, _suffix = nht_link_title_info(body_abs[a:b])
                lines.append(f"[link:{li}] len={b - a} | {visible}")
                li += 1
            else:
                lines.append(f"[keep] n={b - a}")
        out_lines.extend(lines)
        out_lines.append("")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    print(f"nht entries={cnt - 1} images={parsed['image_count']} output={args.output}")


def cmd_nht_write(args) -> None:
    import re as _re
    data = args.input.read_bytes()
    parsed = nht_parse_full(data)
    offs = parsed["text_offsets"]
    cnt = len(offs)
    candidate = args.candidate.read_text(encoding="utf-8-sig")

    head_re = _re.compile(r"^### 条目 (\d+) 标题=(.*?) body=0x([0-9A-Fa-f]+) len=(\d+)$")
    title_re = _re.compile(r"^\[title\] len=(\d+) \| (.*)$")
    text_re = _re.compile(r"^\[text:(\d+)\] len=(\d+) \| (.*)$")
    link_re = _re.compile(r"^\[link:(\d+)\] len=(\d+) \| (.*)$")
    entries: dict[int, dict[int, str]] = {}
    title_edits: dict[int, str] = {}
    link_edits: dict[int, dict[int, str]] = {}
    cur: int | None = None
    mismatch = 0
    for rawline in candidate.splitlines():
        m = head_re.match(rawline)
        if m:
            cur = int(m.group(1))
            if not (0 <= cur < cnt):
                raise ValueError(f"nht candidate: invalid entry {cur}")
            expected_body = offs[cur] + NHT_BODY_PREFIX
            candidate_body = int(m.group(3), 16)
            if candidate_body != expected_body:
                raise ValueError(
                    f"nht candidate entry {cur}: stale body offset 0x{candidate_body:X}; "
                    f"re-extract with the structural NHT parser (expected 0x{expected_body:X})")
            entries[cur] = {}
            continue
        tm = title_re.match(rawline)
        if cur is not None and tm and "|" in tm.group(2):
            title_raw = data[offs[cur]:offs[cur] + NHT_BODY_PREFIX]
            title_end = title_raw.find(b"\x00")
            if title_end < 0:
                title_end = NHT_BODY_PREFIX
            source = tm.group(2).rsplit("|", 1)[0].rstrip()
            if int(tm.group(1)) != title_end or source != title_raw[:title_end].decode("cp932"):
                mismatch += 1
            else:
                title_edits[cur] = take_translation(tm.group(2))
            continue
        tm = text_re.match(rawline)
        if cur is not None and tm and "|" in tm.group(3):
            entries[cur][int(tm.group(1))] = take_translation(tm.group(3))
            continue
        lm = link_re.match(rawline)
        if cur is not None and lm and "|" in lm.group(3):
            body_start, _body_len, body_end = nht_body_slot(
                data, cnt, offs, cur, parsed["text_end"])
            body = data[body_start:body_end]
            link_nodes = [(a, b) for kind, a, b in nht_body_nodes(body)
                          if kind == "link"]
            link_index = int(lm.group(1))
            source = lm.group(3).rsplit("|", 1)[0].rstrip()
            if (link_index < 0 or link_index >= len(link_nodes)
                    or int(lm.group(2)) != link_nodes[link_index][1] - link_nodes[link_index][0]):
                mismatch += 1
                continue
            a, b = link_nodes[link_index]
            _full, visible, _suffix = nht_link_title_info(body[a:b])
            if source != visible:
                mismatch += 1
                continue
            link_edits.setdefault(cur, {})[link_index] = take_translation(lm.group(3))

    rebuilt, result = rebuild_nht(data, entries, title_edits, link_edits)
    result["mismatch"] = mismatch
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(rebuilt)
    quiet = getattr(args, "quiet", False)
    if not quiet:
        print(f"nht {result} output={args.output}")
    elif (result["unencodable"] or result["ignored"] or result["mismatch"]
          or result["overflow"]):
        print(f"nht {result} output={args.output}")


# ---- commands ---------------------------------------------------------------

def cmd_scan(args) -> None:
    scan(args.root, args.report)


def cmd_extract(args) -> None:
    inp = args.input
    if inp.is_file():
        data = inp.read_bytes()
        lines = (extract_spl_lines(data, args.min_chars)
                 if classify(data) == "plain-01"
                 else extract_lines_from(data, args.min_chars))
        out = args.output
        out.parent.mkdir(parents=True, exist_ok=True)
        body = "\n".join(f"[{i:04d}] @0x{a:05X} len={b - a} | {text}"
                         for i, (a, b, text) in enumerate(lines))
        out.write_text(body + ("\n" if body else ""), encoding="utf-8")
        print(f"file={inp.name} lines={len(lines)} output={out}")
        return

    # Batch: input is a directory containing .spl scripts (optionally under spl_root).
    root = inp
    spl_dir = root / args.spl_root if (root / args.spl_root).is_dir() else root
    files = sorted(p for p in spl_dir.glob("*.spl") if p.is_file())

    all_rows: list[dict] = []
    total_lines = 0
    for path in files:
        data = path.read_bytes()
        lines = (extract_spl_lines(data, args.min_chars)
                 if classify(data) == "plain-01"
                 else extract_lines_from(data, args.min_chars))
        out_txt = args.output / (path.stem + ".txt")
        out_txt.parent.mkdir(parents=True, exist_ok=True)
        body = "\n".join(f"[{k:04d}] @0x{a:05X} len={b - a} | {text}"
                         for k, (a, b, text) in enumerate(lines))
        out_txt.write_text(body + ("\n" if body else ""), encoding="utf-8")
        for k, (a, b, text) in enumerate(lines):
            all_rows.append({"file": path.name, "line": k,
                             "offset": hex(a), "byte_len": b - a, "text": text})
        total_lines += len(lines)

    if args.manifest:
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        with args.manifest.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["file", "line", "offset", "byte_len", "text"])
            writer.writeheader()
            writer.writerows(all_rows)

    print(f"files={len(files)} lines={total_lines} text_out={args.output}")
    if args.manifest:
        print(f"manifest={args.manifest}")


def cmd_write(args) -> None:
    rows = parse_candidate(args.candidate)
    rebuilt, result = rebuild_spl(args.input.read_bytes(), rows)
    if args.reject_mostly_ignored:
        translated = sum(row.get("translated", True) for row in rows)
        ignored = result.get("ignored", 0)
        if translated >= 5 and ignored >= max(5, translated // 2):
            raise SystemExit(
                f"refusing write: {ignored}/{translated} translated rows did not match "
                "the source; use a pristine Japanese source")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(rebuilt)
    quiet = getattr(args, "quiet", False)
    if not quiet:
        print(result)
    elif (result.get("ignored") or result.get("relocated")
          or result.get("unencodable") or result.get("mismatch")):
        print(result)


def cmd_elf_write(args) -> None:
    """Write ELF-embedded NUL-terminated strings (candidate @0xoffset lines)
    back into an executable.

    The ELF has no container/string-pool abstraction like .spl, so each line
    is an absolute file offset into a NUL-terminated slot.  The translation is
    CP932-encoded and only written in place when it fits the original byte
    length (padded with NUL); longer translations are skipped (growing a slot
    in the middle of code/data would corrupt the executable).
    """
    data = bytearray(args.input.read_bytes())
    candidate = args.candidate.read_text(encoding="utf-8-sig")
    expected: dict[int, bytes] = {}
    confidence: dict[int, str] = {}
    manifest_rows: list[dict[str, str]] = []
    manifest_path = getattr(args, "manifest", None)
    if manifest_path is not None and manifest_path.is_file():
        with manifest_path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle, delimiter="\t"):
                try:
                    manifest_rows.append(row)
                    off = int(row.get("file_offset", ""), 0)
                    raw_hex = row.get("raw_hex", "")
                    if raw_hex:
                        expected[off] = bytes.fromhex(raw_hex)
                    level = row.get("confidence", "").strip().upper()
                    if level:
                        confidence[off] = level
                except (TypeError, ValueError):
                    continue
    text_re = re.compile(r"^\[(\d+)\] @0x([0-9A-Fa-f]+) len=(\d+) \| (.*)$")
    changed = propagated = overflow = unencodable = mismatch = placeholder = low_confidence = 0
    candidate_offsets: set[int] = set()
    translations_by_text: dict[str, str] = {}
    for rawline in candidate.splitlines():
        m = text_re.match(rawline)
        if not m:
            continue
        off = int(m.group(2), 16)
        length = int(m.group(3))
        content = m.group(4)
        if "|" not in content:
            continue  # untranslated row -> leave original bytes
        source_text, translated_text = content.rsplit("|", 1)
        source_text = source_text.replace("\\n", "\n").replace("\\r", "\r")
        translated_text = take_translation(translated_text).replace("\\n", "\n").replace("\\r", "\r")
        candidate_offsets.add(off)
        if (translated_text != source_text
                and translated_text not in {"?", "（未翻译文本）"}):
            translations_by_text.setdefault(source_text, translated_text)
        if (off < 0 or off + length >= len(data)
                or data[off + length] != 0):
            # Only complete NUL-terminated records are writable.
            mismatch += 1
            continue
        original = expected.get(off)
        if (original is not None
                and (len(original) != length
                     or data[off:off + len(original)] != original)):
            mismatch += 1
            continue
        if (confidence.get(off) == "D"
                and not getattr(args, "allow_low_confidence", False)):
            low_confidence += 1
            continue
        src = take_translation(content).replace("\\n", "\n").replace("\\r", "\r")
        # ``?`` is the extractor's untranslated/unknown marker.  It is not a
        # real translation: writing it into an ELF slot would replace the
        # original bytes with ``?`` + NUL padding, which is catastrophic when
        # the candidate came from a binary table rather than a text pool.
        if src == "?":
            placeholder += 1
            continue
        try:
            enc = encode_translation(src)
        except UnicodeEncodeError:
            unencodable += 1
            continue
        if len(enc) > length:
            overflow += 1
            continue
        old = data[off:off + length]
        if old.startswith(enc) and all(c in (0, 0x20) for c in old[len(enc):]):
            continue  # already written
        data[off:off + len(enc)] = enc
        if len(enc) < length:
            data[off + len(enc):off + length] = b"\0" * (length - len(enc))
        changed += 1
    # The translation draft keeps one row for repeated ELF strings.  Propagate
    # that approved translation to every matching manifest entry.
    for row in manifest_rows:
        try:
            off = int(row.get("file_offset", ""), 0)
            length = int(row.get("byte_len", ""), 0)
        except (TypeError, ValueError):
            continue
        if (off in candidate_offsets
                or row.get("confidence", "").strip().upper() == "D"):
            continue
        source_text = row.get("text", "").replace("\\n", "\n").replace("\\r", "\r")
        translated_text = translations_by_text.get(source_text)
        if (not translated_text or off + length >= len(data)
                or data[off + length] != 0):
            continue
        raw_hex = row.get("raw_hex", "")
        if raw_hex and data[off:off + length] != bytes.fromhex(raw_hex):
            continue
        try:
            enc = encode_translation(translated_text)
        except UnicodeEncodeError:
            unencodable += 1
            continue
        if len(enc) > length:
            overflow += 1
            continue
        data[off:off + len(enc)] = enc
        if len(enc) < length:
            data[off + len(enc):off + length] = b"\0" * (length - len(enc))
        propagated += 1
    if overflow or unencodable or mismatch:
        raise SystemExit(
            f"elf write refused: overflow={overflow} unencodable={unencodable} mismatch={mismatch}; "
            "every candidate must be a complete NUL-terminated record that fits")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(data)
    quiet = getattr(args, "quiet", False)
    if (not quiet or overflow or unencodable or mismatch or placeholder
            or low_confidence):
        print(f"elf changed={changed} propagated={propagated} overflow={overflow} "
              f"unencodable={unencodable} mismatch={mismatch} "
              f"placeholder={placeholder} low_confidence={low_confidence}")


def _elf_sections(data: bytes) -> list[dict]:
    """Read enough ELF32 section metadata to avoid scanning executable code."""
    if data[:4] != b"\x7fELF" or data[4] != 1 or data[5] != 1:
        raise ValueError("elf extract expects a little-endian ELF32 file")
    shoff = struct.unpack_from("<I", data, 0x20)[0]
    shentsize = struct.unpack_from("<H", data, 0x2E)[0]
    shnum = struct.unpack_from("<H", data, 0x30)[0]
    shstrndx = struct.unpack_from("<H", data, 0x32)[0]
    if shentsize < 40 or shstrndx >= shnum:
        raise ValueError("elf extract: invalid section table")
    headers = [struct.unpack_from("<IIIIIIIIII", data, shoff + i * shentsize)
               for i in range(shnum)]
    string_header = headers[shstrndx]
    names = data[string_header[4]:string_header[4] + string_header[5]]

    def section_name(offset: int) -> str:
        if offset >= len(names):
            return ""
        end = names.find(b"\0", offset)
        return names[offset:end if end >= 0 else len(names)].decode(
            "ascii", errors="replace")

    sections = []
    for header in headers:
        name, sh_type, flags, address, offset, size = header[:6]
        if sh_type == 8 or size == 0 or offset + size > len(data):
            continue
        sections.append({
            "name": section_name(name),
            "flags": flags,
            "address": address,
            "offset": offset,
            "size": size,
        })
    return sections


def extract_elf_records(data: bytes, min_chars: int) -> list[tuple[int, int, str]]:
    """Return complete NUL-terminated ELF strings without splitting newlines.

    ELF descriptions often contain hard line breaks. They remain one runtime
    string and must therefore be represented by one candidate record; splitting
    them would make write-back insert a NUL in the middle of the description.
    """
    records = []
    start = 0
    while start < len(data):
        end = data.find(b"\0", start)
        if end < 0:
            break
        raw = data[start:end]
        if raw and looks_text(raw, min_chars):
            text = clean_text(raw)
            if text:
                records.append((start, end, text))
        start = end + 1
    return records

def escape_elf_candidate_text(text: str) -> str:
    """Keep embedded line breaks inside one physical candidate-file row."""
    return text.replace("\r", "\\r").replace("\n", "\\n")

def cmd_elf_extract(args) -> None:
    """Extract CP932 strings from ELF data sections.

    Embedded executable text is already resident in the ELF's data/rodata
    sections at runtime; it is not an SPL/NHT-style external pool.  Scanning
    section boundaries avoids the large false-positive set produced by a raw
    whole-file scan over MIPS instructions.
    """
    data = args.input.read_bytes()
    rows = []
    manifest = []
    seen = set()

    def plausible_elf_text(text: str) -> bool:
        """Reject CP932-looking data tables while keeping short UI labels."""
        if any(0xE000 <= ord(ch) <= 0xF8FF for ch in text):
            return False
        # These characters are common in random binary data decoded as CP932,
        # but are not part of the game's normal text syntax.
        if any(ch in text for ch in "<>|\"\\"):
            return False
        japanese = [
            ch for ch in text
            if (0x3040 <= ord(ch) <= 0x30FF)
            or (0x3400 <= ord(ch) <= 0x9FFF)
            or ch in "、。！？「」『』・ー～"
        ]
        if len(japanese) < 2:
            return False
        return len(japanese) / max(1, len(text.strip())) >= 0.25

    include_exec = getattr(args, "include_exec", False)
    for section in _elf_sections(data):
        is_exec = bool(section["flags"] & 0x4)
        if is_exec and not include_exec:
            continue
        payload = data[section["offset"]:section["offset"] + section["size"]]
        scan_min = 1 if is_exec else args.min_chars
        for start, end, text in extract_elf_records(payload, scan_min):
            file_offset = section["offset"] + start
            raw = payload[start:end]
            plausible = plausible_elf_text(text)
            # Executable-section candidates are deliberately marked for review:
            # instruction bytes can decode as CP932 by accident. They are useful
            # for coverage, but are never an automatic write-back source.
            if is_exec:
                plausible = ("\ufffd" not in text and len(text.strip()) >= 2
                             and len(JP_TEXT_CHAR.findall(raw)) >= 2)
            if file_offset in seen or "\ufffd" in text or not plausible:
                continue
            # A text item must be backed by a real NUL/control-delimited run;
            # extract_lines_from already preserves this exact byte span.
            seen.add(file_offset)
            rows.append((file_offset, end - start, text))
            manifest.append({
                "section": section["name"],
                "file_offset": f"0x{file_offset:X}",
                "runtime_address": f"0x{section['address'] + start:X}",
                "byte_len": end - start,
                "raw_hex": raw.hex(),
                "confidence": "D" if is_exec else "C",
                "text": text,
            })

    rows.sort()
    # A data pointer to a candidate's EE runtime address is strong evidence
    # that the bytes are an actual string table entry.  Build this index once
    # so the report can prioritize referenced strings without disassembling
    # every MIPS instruction.
    wanted = {
        int(row["runtime_address"], 16): row
        for row in manifest
    }
    xrefs: dict[int, list[int]] = {address: [] for address in wanted}
    for pointer_offset in range(0, len(data) - 3, 4):
        value = struct.unpack_from("<I", data, pointer_offset)[0]
        if value in xrefs:
            xrefs[value].append(pointer_offset)
    for row in manifest:
        address = int(row["runtime_address"], 16)
        refs = xrefs[address]
        row["xref_count"] = str(len(refs))
        row["xref_offsets"] = ";".join(f"0x{off:X}" for off in refs)
        if refs and row["confidence"] == "C":
            row["confidence"] = "B"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "\n".join(f"[{i:04d}] @0x{off:X} len={length} | {escape_elf_candidate_text(text)}"
                  for i, (off, length, text) in enumerate(rows))
        + ("\n" if rows else ""),
        encoding="utf-8",
    )
    if args.manifest:
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        with args.manifest.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["section", "file_offset", "runtime_address", "byte_len",
                            "raw_hex", "confidence", "xref_count", "xref_offsets", "text"],
                delimiter="\t",
            )
            writer.writeheader()
            writer.writerows(manifest)
    print(f"elf strings={len(rows)} output={args.output}")
    if args.manifest:
        print(f"elf manifest={args.manifest}")


# ---- CLI --------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Unified Seven Warriors PS2 text/resource tool (scan/extract/write)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("scan", help="classify all unpacked resources")
    p.add_argument("root", type=Path)
    p.add_argument("report", type=Path)

    p = sub.add_parser("extract", help="structural SPL string-pool extraction (single file or batch)")
    p.add_argument("input", type=Path,
                   help="a single .spl file, or a directory of .spl scripts")
    p.add_argument("output", type=Path,
                   help="output .txt file (single) or output directory (batch)")
    p.add_argument("--spl-root", default="seven_data_link/rpgdata/output",
                   help="relative dir of .spl scripts inside a batch root")
    p.add_argument("--manifest", type=Path, help="aggregate CSV for batch mode")
    p.add_argument("--min-chars", type=int, default=2,
                   help="minimum double-byte chars per kept line (default 2)")

    p = sub.add_parser("write", help="rebuild SPL string pools and relocate references")
    p.add_argument("input", type=Path, help="original .spl file")
    p.add_argument("candidate", type=Path, help="edited candidate file in extract format")
    p.add_argument("output", type=Path, help="written .spl output")
    p.add_argument("--quiet", action="store_true",
                   help="only print ignored / unencodable / mismatched rows")

    p.add_argument("--reject-mostly-ignored", action="store_true",
                   help="fail before writing when most translated rows cannot match the source")
    p = sub.add_parser("elf", help="extract/write strings embedded in an executable")
    esub = p.add_subparsers(dest="elf_cmd", required=True)
    ee = esub.add_parser("extract", help="extract CP932 strings from ELF data sections")
    ee.add_argument("input", type=Path, help="original executable (e.g. SLPS_250.25)")
    ee.add_argument("output", type=Path, help="output candidate file")
    ee.add_argument("--min-chars", type=int, default=2,
                    help="minimum Japanese double-byte chars per item (default 2)")
    ee.add_argument("--include-exec", action="store_true",
                    help="also emit review-only candidates from executable sections")
    ee.add_argument("--manifest", type=Path,
                    help="optional TSV with section/file/runtime addresses and raw bytes")
    ew = esub.add_parser("write", help="write @0xoffset candidate lines back into an ELF")
    ew.add_argument("input", type=Path, help="original executable (e.g. SLPS_250.25)")
    ew.add_argument("candidate", type=Path, help="edited candidate in extract format")
    ew.add_argument("output", type=Path, help="written executable")
    ew.add_argument("--manifest", type=Path,
                    help="optional extraction TSV; verify raw bytes before writing")
    ew.add_argument("--allow-low-confidence", action="store_true",
                    help="allow writing D-level executable-section candidates")
    ew.add_argument("--quiet", action="store_true",
                    help="only print overflow / unencodable / mismatched rows")

    p = sub.add_parser("nht", help="extract/write .nht help-text containers")
    nsub = p.add_subparsers(dest="nht_cmd", required=True)
    ne = nsub.add_parser("extract", help="dump each .nht help entry with body lines")
    ne.add_argument("input", type=Path, help=".nht file")
    ne.add_argument("output", type=Path, help="output .txt")
    nw = nsub.add_parser("write", help="write edited .nht from a candidate file")
    nw.add_argument("input", type=Path, help="original .nht file")
    nw.add_argument("candidate", type=Path, help="edited candidate in nht-extract format")
    nw.add_argument("output", type=Path, help="written .nht output")
    nw.add_argument("--quiet", action="store_true",
                    help="only print entries that overflow / fail")

    p = sub.add_parser("fhm", help="extract/write .fhm UI/name text sub-resource")
    fsub = p.add_subparsers(dest="fhm_cmd", required=True)
    fe = fsub.add_parser("extract", help="dump FHM text; directory mode audits every .fhm")
    fe.add_argument("input", type=Path, help=".fhm file or unpacked directory")
    fe.add_argument("output", type=Path, help="output .txt, or directory in batch mode")
    fe.add_argument("--manifest", type=Path,
                    help="CSV audit manifest (recommended for directory mode)")
    fe.add_argument("--min-chars", type=int, default=4,
                    help="minimum decoded chars per kept string (default 4)")
    fw = fsub.add_parser("write", help="write edited .fhm from a candidate file")
    fw.add_argument("input", type=Path, help="original .fhm file")
    fw.add_argument("candidate", type=Path, help="edited candidate in fhm-extract format")
    fw.add_argument("output", type=Path, help="written .fhm output")
    fw.add_argument("--quiet", action="store_true",
                    help="only print strings that overflow / fail")

    args = parser.parse_args()
    if args.command == "scan":
        cmd_scan(args)
    elif args.command == "extract":
        cmd_extract(args)
    elif args.command == "write":
        cmd_write(args)
    elif args.command == "elf":
        if args.elf_cmd == "extract":
            cmd_elf_extract(args)
        elif args.elf_cmd == "write":
            cmd_elf_write(args)
    elif args.command == "nht":
        if args.nht_cmd == "extract":
            cmd_nht_extract(args)
        else:
            cmd_nht_write(args)
    elif args.command == "fhm":
        if args.fhm_cmd == "extract":
            cmd_fhm_extract(args)
        else:
            cmd_fhm_write(args)


if __name__ == "__main__":
    main()
