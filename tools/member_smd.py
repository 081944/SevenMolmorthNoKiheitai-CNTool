# -*- coding: utf-8 -*-
"""Extract and write the random-name pools in battle_dat/commit/member.smd.

The SMD contains CP932 text packed before its record table.  The first pool
uses byte offsets, the second pool uses two-byte character offsets, and the
remaining bytes up to the record table are another continuous text pool.
Translation candidates keep the original source and the binary metadata while
the writer rebuilds the three regions and updates only the two offset columns.
"""
from __future__ import annotations

import argparse
import csv
import re
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "work" / "translation" / "ui"
DEFAULT_SOURCE = ROOT / "unpacked" / "CDIMAGE_FULL" / "seven_data_link" / "battle_dat" / "commit" / "member.smd"
DEFAULT_CANDIDATE = UI / "member_smd.txt"

HEADER = struct.Struct("<8I")
RECORD = struct.Struct("<4H")
ENTRY_RE = re.compile(
    r"^\[text:(\d+)\] pool=(pool1|pool2|tail) (?:(?:row|chunk)=(\d+) )?"
    r"off=0x([0-9A-Fa-f]+) len=(\d+)(?: c=(\d+) d=(\d+))? \| (.*)$"
)

KANA_DIGRAPHS = {
    "ディ": "迪", "ティ": "蒂", "デュ": "迪尤", "テュ": "蒂尤",
    "ヴァ": "瓦", "ヴィ": "维", "ヴェ": "维", "ヴォ": "沃",
    "ファ": "法", "フィ": "菲", "フェ": "费", "フォ": "佛",
    "ウァ": "瓦", "ウィ": "维", "ウェ": "维", "ウォ": "沃",
    "シャ": "夏", "シュ": "舒", "シェ": "谢", "ショ": "肖",
    "チャ": "查", "チュ": "丘", "チェ": "切", "チョ": "乔",
    "ジャ": "贾", "ジュ": "朱", "ジェ": "杰", "ジョ": "乔",
    "ツァ": "察", "ツィ": "齐", "ツェ": "策", "ツォ": "措",
}
KANA_SINGLE = {
    "ア": "阿", "イ": "伊", "ウ": "乌", "エ": "埃", "オ": "奥",
    "カ": "卡", "キ": "基", "ク": "库", "ケ": "凯", "コ": "科",
    "ガ": "加", "ギ": "吉", "グ": "古", "ゲ": "盖", "ゴ": "戈",
    "サ": "萨", "シ": "希", "ス": "斯", "セ": "塞", "ソ": "索",
    "ザ": "扎", "ジ": "吉", "ズ": "祖", "ゼ": "泽", "ゾ": "佐",
    "タ": "塔", "チ": "奇", "ツ": "津", "テ": "特", "ト": "托",
    "ダ": "达", "ヂ": "吉", "ヅ": "兹", "デ": "迪", "ド": "多",
    "ナ": "纳", "ニ": "尼", "ヌ": "努", "ネ": "内", "ノ": "诺",
    "ハ": "哈", "ヒ": "希", "フ": "弗", "ヘ": "赫", "ホ": "霍",
    "バ": "巴", "ビ": "比", "ブ": "布", "ベ": "贝", "ボ": "博",
    "パ": "帕", "ピ": "皮", "プ": "普", "ペ": "佩", "ポ": "波",
    "マ": "玛", "ミ": "米", "ム": "穆", "メ": "梅", "モ": "莫",
    "ヤ": "亚", "ユ": "尤", "ヨ": "约",
    "ラ": "拉", "リ": "里", "ル": "尔", "レ": "雷", "ロ": "罗",
    "ワ": "瓦", "ヲ": "沃", "ン": "恩", "ヴ": "维",
    "ァ": "阿", "ィ": "伊", "ゥ": "乌", "ェ": "埃", "ォ": "奥",
    "ャ": "亚", "ュ": "尤", "ョ": "约", "ッ": "特",
}


def _read(path: Path) -> bytes:
    data = path.read_bytes()
    if len(data) < 0x20 or data[:4] != b"SMD\0":
        raise ValueError(f"{path} is not an SMD container")
    return data


def _layout(data: bytes) -> tuple[tuple[int, ...], list[tuple[int, int, int, int]]]:
    header = HEADER.unpack_from(data, 0)
    data_start, table_start, table_end = header[4:7]
    if data_start != 0x20 or table_start >= table_end or table_end > len(data):
        raise ValueError("invalid member.smd header offsets")
    count = header[2]
    if table_end - table_start < (count + 2) * RECORD.size:
        raise ValueError("member.smd record table is shorter than expected")
    rows = [RECORD.unpack_from(data, table_start + i * RECORD.size)
            for i in range(count + 2)]
    return header, rows


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("cp932")
    except UnicodeDecodeError as exc:
        raise ValueError(f"member.smd text is not valid CP932 at byte {exc.start}") from exc


def _encode(text: str) -> bytes:
    # The normal project encoder substitutes allocated Chinese glyph slots.
    from text_7w import encode_translation
    return encode_translation(text)


def _existing_translations() -> dict[str, str]:
    """Load exact translations already present in project candidates.

    This only fills an identical source row.  It never guesses from a partial
    match, so extracting the SMD remains a lossless source operation.
    """
    result: dict[str, str] = {}
    for path in (ROOT / "work" / "translation").rglob("*.txt"):
        if path == DEFAULT_CANDIDATE or not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except UnicodeDecodeError:
            continue
        for line in lines:
            if "|" not in line or line.lstrip().startswith("#"):
                continue
            parts = line.split("|")
            if len(parts) < 3:
                continue
            source, target = parts[-2].strip(), parts[-1].strip()
            if source and target and source != target:
                result.setdefault(source, target)
    tsv = ROOT / "work" / "translation" / "专有名词表.tsv"
    if tsv.is_file():
        with tsv.open(encoding="utf-8-sig", newline="") as fh:
            for row in csv.reader(fh, delimiter="\t"):
                if len(row) >= 3 and row[1].strip() and row[2].strip():
                    result.setdefault(row[1].strip(), row[2].strip())
    return result


def _is_name_text(text: str) -> bool:
    return bool(text) and all("\u30a0" <= ch <= "\u30ff" or ch in "ー・" for ch in text)


def _phonetic_name(text: str) -> str:
    """Translate an unlisted fantasy name using the project's name style."""
    out: list[str] = []
    pos = 0
    while pos < len(text):
        if text[pos] == "ー":
            # Chinese name spellings in the existing table omit Japanese
            # long-vowel marks rather than leaving a Japanese glyph behind.
            pos += 1
            continue
        if text[pos] == "・":
            out.append("·")
            pos += 1
            continue
        pair = text[pos:pos + 2]
        if pair in KANA_DIGRAPHS:
            out.append(KANA_DIGRAPHS[pair])
            pos += 2
            continue
        out.append(KANA_SINGLE.get(text[pos], text[pos]))
        pos += 1
    return "".join(out)


def _translate_name_text(text: str, exact: dict[str, str]) -> str:
    if text in exact:
        return exact[text]
    tokens = sorted(
        [
            (source, target) for source, target in exact.items()
            if _is_name_text(source) and target and source != target
        ],
        key=lambda item: len(item[0]), reverse=True,
    )
    out: list[str] = []
    pos = 0
    while pos < len(text):
        match = next((item for item in tokens if text.startswith(item[0], pos)), None)
        if match is not None:
            out.append(match[1])
            pos += len(match[0])
            continue
        end = pos + 1
        if text[pos:pos + 2] in KANA_DIGRAPHS:
            end = pos + 2
        out.append(_phonetic_name(text[pos:end]))
        pos = end
    return "".join(out)


def extract(source: Path = DEFAULT_SOURCE, output: Path = DEFAULT_CANDIDATE) -> int:
    data = _read(source)
    header, rows = _layout(data)
    data_start, table_start = header[4], header[5]
    count = header[2]
    pool1_end = data_start + rows[count][0]
    pool2_start = pool1_end
    pool2_end = pool2_start + rows[count][1] * 2
    if not (data_start <= pool1_end <= pool2_end <= table_start):
        raise ValueError("member.smd text pool boundaries are invalid")

    known = _existing_translations()
    lines = [
        "# member.smd 随机角色名池（从原版直接提取）",
        "# pool1/pool2 为记录表索引的文本池；tail 为表后连续文本区。",
        "# 保留 pool、row/chunk、off、len、c、d 等元数据；只在最后一列填写译文。",
    ]
    seq = 0
    filled = 0
    for pool in ("pool1", "pool2"):
        for row in range(count):
            a, b, c, d = rows[row]
            if pool == "pool1":
                off = data_start + a
                end = data_start + rows[row + 1][0]
            else:
                off = pool2_start + b * 2
                end = pool2_start + rows[row + 1][1] * 2
            source_text = _decode(data[off:end])
            target = known.get(source_text, "")
            filled += bool(target)
            lines.append(
                f"[text:{seq}] pool={pool} row={row} off=0x{off:X} len={end-off} "
                f"c={c} d={d} | {source_text}|{target}"
            )
            seq += 1

    # The bytes after the indexed pool are still text and are changed by the
    # alternate translation.  Chunk them for editing without inventing record
    # boundaries; the writer concatenates the chunks again in order.
    tail = data[pool2_end:table_start]
    chunk_chars = 100
    for chunk, start in enumerate(range(0, len(tail), chunk_chars * 2)):
        raw = tail[start:start + chunk_chars * 2]
        if len(raw) % 2:
            raise ValueError("tail text chunk is not aligned to CP932 pairs")
        source_text = _decode(raw)
        target = known.get(source_text, "")
        filled += bool(target)
        lines.append(
            f"[text:{seq}] pool=tail chunk={chunk} off=0x{pool2_end+start:X} "
            f"len={len(raw)} | {source_text}|{target}"
        )
        seq += 1

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"extracted {count} pool1 + {count} pool2 + "
          f"{(len(tail) + chunk_chars * 2 - 1) // (chunk_chars * 2)} tail chunks")
    print(f"known exact translations filled: {filled}")
    print(output)
    return 0


def fill_translations(candidate: Path = DEFAULT_CANDIDATE) -> int:
    """Fill only empty translation columns in an extracted candidate."""
    exact = _existing_translations()
    lines = candidate.read_text(encoding="utf-8-sig").splitlines()
    filled = 0
    skipped = 0
    output: list[str] = []
    for line in lines:
        if not line.startswith("[text:"):
            output.append(line)
            continue
        match = ENTRY_RE.match(line)
        if match is None:
            raise ValueError(f"invalid member.smd candidate row: {line[:80]}")
        content = match.group(8)
        if "|" not in content:
            source_text, target = content, ""
        else:
            source_text, target = content.split("|", 1)
        if target.strip():
            skipped += 1
            output.append(line)
            continue
        translation = _translate_name_text(source_text, exact)
        if not translation:
            raise ValueError(f"could not translate member.smd source: {source_text!r}")
        output.append(line[:line.index("|", line.index("|") + 1) + 1] + translation)
        filled += 1
    candidate.write_text("\n".join(output) + "\n", encoding="utf-8", newline="\n")
    print(f"member.smd translations: filled={filled} skipped_existing={skipped}")
    return 0


def _parse_candidate(path: Path) -> dict[tuple[str, int], tuple[int, str, str]]:
    entries: dict[tuple[str, int], tuple[int, str, str]] = {}
    for line_no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line or line.startswith("#"):
            continue
        match = ENTRY_RE.match(line)
        if not match:
            raise ValueError(f"{path}:{line_no}: invalid member.smd candidate row")
        _seq, pool, item, off_hex, length, _c, _d, content = match.groups()
        if item is None:
            raise ValueError(f"{path}:{line_no}: missing row/chunk metadata")
        if "|" in content:
            source, target = content.split("|", 1)
        else:
            source, target = content, ""
        key = (pool, int(item))
        if key in entries:
            raise ValueError(f"{path}:{line_no}: duplicate {pool} {item}")
        entries[key] = (int(off_hex, 16), source, target.strip())
    return entries


def write(source: Path = DEFAULT_SOURCE, candidate: Path = DEFAULT_CANDIDATE,
          output: Path | None = None) -> int:
    data = _read(source)
    header, rows = _layout(data)
    data_start, table_start = header[4], header[5]
    count = header[2]
    entries = _parse_candidate(candidate)
    for pool in ("pool1", "pool2"):
        missing = [i for i in range(count) if (pool, i) not in entries]
        if missing:
            raise ValueError(f"candidate is missing {pool} rows: {missing[:5]}")

    old_pool1_end = data_start + rows[count][0]
    old_pool2_start = old_pool1_end
    old_pool2_end = old_pool2_start + rows[count][1] * 2
    old_tail = data[old_pool2_end:table_start]

    def translated(pool: str, item: int, off: int, raw: bytes) -> bytes:
        saved_off, source_text, target = entries[(pool, item)]
        if saved_off != off or source_text.encode("cp932") != raw:
            raise ValueError(f"candidate source mismatch: {pool} {item}")
        return raw if not target else _encode(target)

    pool1_parts = []
    for row in range(count):
        off = data_start + rows[row][0]
        end = data_start + rows[row + 1][0]
        pool1_parts.append(translated("pool1", row, off, data[off:end]))
    pool1 = b"".join(pool1_parts)

    pool2_parts = []
    for row in range(count):
        off = old_pool2_start + rows[row][1] * 2
        end = old_pool2_start + rows[row + 1][1] * 2
        part = translated("pool2", row, off, data[off:end])
        if len(part) % 2:
            raise ValueError(f"pool2 row {row} is not two-byte aligned")
        pool2_parts.append(part)
    pool2 = b"".join(pool2_parts)

    tail_entries = sorted((item, value) for (pool, item), value in entries.items()
                          if pool == "tail")
    if not tail_entries:
        raise ValueError("candidate has no tail chunks")
    tail_parts = []
    cursor = old_pool2_end
    for item, (off, source_text, target) in tail_entries:
        raw = data[cursor:cursor + len(source_text.encode("cp932"))]
        if off != cursor or source_text.encode("cp932") != raw:
            raise ValueError(f"candidate source mismatch: tail {item}")
        tail_parts.append(raw if not target else _encode(target))
        cursor += len(raw)
    if cursor != table_start:
        raise ValueError("tail chunks do not cover the complete text region")
    tail = b"".join(tail_parts)

    rebuilt_region = pool1 + pool2 + tail
    capacity = table_start - data_start
    if len(rebuilt_region) > capacity:
        raise ValueError(f"translated SMD text exceeds region capacity: "
                         f"{len(rebuilt_region)}>{capacity}")
    rebuilt_region += b"\0" * (capacity - len(rebuilt_region))
    out = bytearray(data)
    out[data_start:table_start] = rebuilt_region

    pos1 = 0
    pos2 = 0
    for row in range(count):
        old = list(rows[row])
        old[0] = pos1
        old[1] = pos2
        RECORD.pack_into(out, table_start + row * RECORD.size, *old)
        pos1 += len(pool1_parts[row])
        pos2 += len(pool2_parts[row]) // 2
    end_row = list(rows[count])
    end_row[0], end_row[1] = pos1, pos2
    RECORD.pack_into(out, table_start + count * RECORD.size, *end_row)
    # The final capacity/sentinel row repeats the end of pool1 while keeping
    # its pool2 upper bound (5000) intact.
    final_row = list(rows[count + 1])
    final_row[0] = pos1
    RECORD.pack_into(out, table_start + (count + 1) * RECORD.size, *final_row)

    destination = output or source
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(out)
    changed = sum(bool(value[2].strip()) for value in entries.values())
    print(f"member.smd: translated_entries={changed} size={len(out)} output={destination}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    ex = sub.add_parser("extract")
    ex.add_argument("source", nargs="?", type=Path, default=DEFAULT_SOURCE)
    ex.add_argument("output", nargs="?", type=Path, default=DEFAULT_CANDIDATE)
    wr = sub.add_parser("write")
    wr.add_argument("source", type=Path)
    wr.add_argument("candidate", nargs="?", type=Path, default=DEFAULT_CANDIDATE)
    wr.add_argument("output", nargs="?", type=Path)
    fill = sub.add_parser("fill")
    fill.add_argument("candidate", nargs="?", type=Path, default=DEFAULT_CANDIDATE)
    args = parser.parse_args()
    if args.command == "extract":
        return extract(args.source, args.output)
    if args.command == "fill":
        return fill_translations(args.candidate)
    return write(args.source, args.candidate, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
