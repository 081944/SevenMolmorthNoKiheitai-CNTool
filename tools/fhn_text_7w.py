#!/usr/bin/env python3
"""Extract and write fixed-field UI text embedded in screen FHN files.

These records are not ITE images.  A visible CP932 string starts at the
record offset, followed by formatting/control bytes and a fixed 0x40-byte
text field.  The remaining bytes in the record are preserved verbatim.
Image resources are excluded using the FHN offset table; mov/opt are handled
by the legacy UI writer.
"""
from __future__ import annotations

import argparse
import re
import struct
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from text_7w import encode_translation  # noqa: E402


TEXT_ROOT = "seven_data_link/takeshi/screen"
# mov/opt have an older fixed-offset writer in ui_write.py.  Keep them out of
# this candidate set so the build cannot apply two different writers to them.
EXTERNAL_UI_DIRS = {"mov", "opt"}
FIELD_SIZE = 0x40
RECORD_TAIL = 0x18
IMAGE_MAGICS = {b"ITE\x00", b"GIM\x00"}
LEADS = set(range(0x81, 0xA0)) | set(range(0xE0, 0xFD))
# These labels already have approved translations elsewhere in the project.
# Other extracted candidates intentionally keep original text as a review
# placeholder so this tool never invents translations for unknown strings.
KNOWN_TRANSLATIONS = {
    "あと": "剩余",
    "歳": "岁",
    "あと{G01}年{G02}日": "剩余{G01}年{G02}日",
    "とどめ回数　": "致命一击次数　",
    "コメント": "备注",
    "ピーク年齢": "巅峰年龄",
    "マスタークラス": "大师级",
    "上昇率{V10}{G01}～{G02}%": "提升率{V10}{G01}～{G02}%",
    "修行により{G06}%{H03}したパラメータ": "修行后{G06}%{H03}的参数",
    "修行仲間": "修行同伴",
    "修行年数": "修行年数",
    "修行戦績　{G07}戦（{G08}勝／{G09}負）":
        "修行战绩　{G07}战（{G08}胜／{G09}负）",
    "修行期間": "修行期间",
    "修行経験": "修行经验",
    "修行者引率中": "正在带队修行",
    "倒すと得られる貢献度": "击败后获得的贡献度",
    "入隊年数": "入队年数",
    "入隊日": "入队日",
    "初回攻撃力": "初次攻击力",
    "初回防御力": "初次防御力",
    "功労者": "功臣",
    "子供を教育中": "正在教育孩童",
    "安定性": "稳定性",
    "実施内容": "实施内容",
    "実施年齢": "实施年龄",
    "家宝リスト": "家宝列表",
    "年間の修行中": "正在修行",
    "年間の修行終了": "修行结束",
    "年齢": "年龄",
    "レベル": "等级",
    "引率者": "带队者",
    "引率者回数": "带队次数",
    "強さ": "强度",
    "思考": "思考模式",
    "戦績": "战绩",
    "戦（": "战（",
    "指導力": "指导力",
    "教育期間": "教育期间",
    "教育者回数": "教育次数",
    "期限まで残り　{G03}年{G04}日": "距期限剩余　{G03}年{G04}日",
    "歳（隠居{B06}": "岁（隐居{B06}",
    "特殊攻撃": "特殊攻击",
    "特記事項": "特别事项",
    "疲労度": "疲劳度",
    "発生場所": "发生地点",
    "発生日　{G01}年{G02}日": "发生日期　{G01}年{G02}日",
    "職種": "职业",
    "衰退年齢": "衰退年龄",
    "隠居年齢": "隐居年龄",
    "親密度": "亲密度",
    "退役日": "退役日",
    "ＨＰ": "HP",
    "攻撃力": "攻击力",
    "素早さ": "速度",
    "回復力": "回复力",
    "防御補助": "防御辅助",
    "攻撃補助": "攻击辅助",
    "間接攻撃": "间接攻击",
    "（１年で貢献度は{H06}減ります。）": "（1年后贡献度将{H06}减少。）",
    "～結果の出る能力～": "～产生效果的能力～",
}

# Full templates whose formatting commands split the visible label into more
# than one field fragment.  Keeping these translations keyed by the complete
# template avoids accidentally moving a value placeholder across a separator.
EXTENDED_TRANSLATIONS = {
    "元{B07}": "原{B07}",
    "第{G03}回遠征": "第{G03}次远征",
    "（{G05}年間）": "（{G05}年间）",
    "（{G0C}年間）": "（{G0C}年间）",
    "年{G01}日間": "{G01}天",
    "→{G01}": "→{G01}",
    "素早さ{T}{Z}{B10}": "速度{T}{Z}{B10}",
    "攻撃力{T}{Z}{B0E}{Z}{V0E}{B0F}":
        "攻击力{T}{Z}{B0E}{Z}{V0E}{B0F}",
    "防御補助{T}{Z}{B13}": "防御辅助{T}{Z}{B13}",
    "回復力{T}{Z}{B11}{Z}$W{C02}{V0E}{B12}":
        "回复力{T}{Z}{B11}{Z}$W{C02}{V0E}{B12}",
    "回復力{T}{Z}{B11}{Z}{V0E}$W{C02}{B12}":
        "回复力{T}{Z}{B11}{Z}{V0E}$W{C02}{B12}",
    "指導力{T}{Z}$.": "指导力{T}{Z}$.",
    "安定性{T}{Z}$/": "稳定性{T}{Z}$/",
    "教育者回数{T}{Z}{S1}{Z}回": "教育次数{T}{Z}{S1}{Z}回",
    "引率者回数{T}{Z}{S0}{Z}回": "带队次数{T}{Z}{S0}{Z}回",
    "歳（隠居{B06}{Z}歳）": "岁（隐居{B06}{Z}岁）",
    "ピーク年齢{T}{Z}{B08}{Z}歳": "巅峰年龄{T}{Z}{B08}{Z}岁",
    "マスタークラス{T}{Z}$R": "大师级{T}{Z}$R",
    "衰退年齢{T}{Z}{B09}{Z}歳": "衰退年龄{T}{Z}{B09}{Z}岁",
    "疲労度{T}{Z}$M{Z}　{B0A}": "疲劳度{T}{Z}$M{Z}　{B0A}",
    "レベル{T}{Z}$P": "等级{T}{Z}$P",
    "年齢{T}{Z}{S8}{Z}{B04}{Z}歳":
        "年龄{T}{Z}{S8}{Z}{B04}{Z}岁",
    "隠居年齢{T}{Z}{B06}{Z}歳": "隐居年龄{T}{Z}{B06}{Z}岁",
    "修行経験{T}{Z}$Q": "修行经验{T}{Z}$Q",
    "親密度{T}{Z}$N{Z}　{B0B}": "亲密度{T}{Z}$N{Z}　{B0B}",
    "入隊日{T}{Z}$({Z}年$){Z}日": "入队日{T}{Z}$({Z}年$){Z}日",
    "入隊年数{T}{Z}$,{Z}年$-{Z}日目":
        "入队年数{T}{Z}$,{Z}年$-{Z}日目",
    "戦（{S3}{Z}勝／{S4}{Z}敗）":
        "战（{S3}{Z}胜／{S4}{Z}败）",
    "とどめ回数　{S5}": "致命一击次数　{S5}",
    "攻撃力{T}{Z}$ {Z}{V10}{B0F}":
        "攻击力{T}{Z}$ {Z}{V10}{B0F}",
    "素早さ{T}{Z}$!": "速度{T}{Z}$!",
    "強さ{T}{Z}$%": "强度{T}{Z}$%",
    "防御補助{T}{Z}$#": "防御辅助{T}{Z}$#",
    "回復力{T}{Z}$\"{Z}{V10}{B12}":
        "回复力{T}{Z}$\"{Z}{V10}{B12}",
    "属性{T}{Z}$L{Z}{V10}{B0C}":
        "属性{T}{Z}$L{Z}{V10}{B0C}",
    "攻撃力{T}{Z}{B0E}{C01}{V10}{B0F}{C01}":
        "攻击力{T}{Z}{B0E}{C01}{V10}{B0F}{C01}",
    "素早さ{T}{Z}{B10}{C01}": "速度{T}{Z}{B10}{C01}",
    "強さ{T}{Z}{B18}{C01}": "强度{T}{Z}{B18}{C01}",
    "防御補助{T}{Z}{B13}{C01}": "防御辅助{T}{Z}{B13}{C01}",
    "回復力{T}{Z}{B11}{C01}{V10}{B12}{C01}":
        "回复力{T}{Z}{B11}{C01}{V10}{B12}{C01}",
    "創設から{G00}年{G01}日": "创建至今{G00}年{G01}日",
    "指導力　$.": "指导力　$.",
    "安定性　$/": "稳定性　$/",
    "倒した敵の数": "击败敌人数",
    "倒した放浪者の数": "击败流浪人数",
    "騎兵隊解散勧告まで": "距离骑兵队解散建议还有",
    "あと{G00}日": "剩余{G00}日",
    "貢献度{T}{Z}-{H00}/年": "贡献度{T}{Z}-{H00}/年",
    "半年後予測{T}{Z}{G01}": "半年后预测{T}{Z}{G01}",
    "１年後予測{T}{Z}{G02}": "1年后预测{T}{Z}{G02}",
    "街壊滅まで{G00}年{G0E}日": "距离城市毁灭还有{G00}年{G0E}日",
    "貢献度": "贡献度",
    "解決": "解决",
    "情報屋": "情报商",
    "回復の泉": "回复之泉",
    "放浪者出没中": "流浪者出没中",
    "倒した魔物{T}{Z}{G07}匹": "击败魔物{T}{Z}{G07}只",
    "城の仲間{T}{Z}{G08}人": "城中同伴{T}{Z}{G08}人",
    "帰城時の貢献度{T}{Z}{H09}": "返回城堡时的贡献度{T}{Z}{H09}",
    "ダメージ$H": "伤害$H",
    "攻撃者": "攻击者",
    "攻撃補助者": "攻击辅助者",
    "城内の出来事": "城内事件",
    "戦闘結果": "战斗结果",
    "家系内（全{G00}人）で今までに関わった{G01}人の構成":
        "家族中曾经关联过的{G01}人（全{G00}人）的构成",
    "家系内の主な強者ベスト３": "家族中主要强者前三名",
    "家宝": "家宝",
    "家系のうち、{G01}家系と関係を持っています。":
        "在家族中，与{G01}个家族有关系。",
    "城で待機中": "在城中待命",
    "（年）": "（年）",
    "属性{T}{Z}$L{Z}{B0C}": "属性{T}{Z}$L{Z}{B0C}",
    "退役日{T}{Z}$*{Z}年$+{Z}日": "退役日{T}{Z}$*{Z}年$+{Z}日",
    "属性{T}{Z}$L{Z}{V10}{B0C}": "属性{T}{Z}$L{Z}{V10}{B0C}",
    "最大{H00}部門": "最大{H00}项目",
    "修行年数": "修行年数",
    "上昇率{V10}{G01}%": "提升率{V10}{G01}%",
    "名前": "名称",
    "１位": "第1名",
    "２位": "第2名",
    "３位": "第3名",
    "記録ベスト３": "记录前三名",
    "結果": "结果",
    "記録": "记录",
    "～倒した魔物～": "～击败的魔物～",
    "獲得ポイント": "获得分数",
    "修行引率中": "正在带队修行",
    "評価$?": "评价$?",
    "創設から{G01}年{G02}日": "创建至今{G01}年{G02}日",
    "騎兵隊の強さ": "骑兵队实力",
    "残り{G00}年{G01}日": "剩余{G00}年{G01}日",
    "出現場所": "出现地点",
    "個人記録各部門": "个人记录各项目",
    "最大取得貢献度ベスト３": "获得贡献度最多前三名",
    "最大ダメージ部門ベスト３": "最大伤害项目前三名",
    "最短勝利時間部門ベスト３": "最短胜利时间项目前三名",
    "戻る": "返回",
}

LINE_RE = re.compile(
    r"^\[text:(\d+)\] @0x([0-9A-Fa-f]+) len=(\d+) \| (.*)$"
)
TOKEN_RE = re.compile(
    r"\{(?:[GHBV][0-9A-Fa-f]{2}|C[0-9A-Fa-f]{2}|T|S[0-9]|Z)\}"
)


def _field_token(raw: bytes, pos: int, end: int) -> tuple[str, int] | None:
    """Return one FHN inline token and its byte length."""
    if raw[pos] != 0 and raw[pos] < 0x20:
        return f"{{C{raw[pos]:02X}}}", 1
    if pos + 2 < end and raw[pos] == 0x24 and raw[pos + 1] in (
            ord("G"), ord("H"), ord("V")):
        return f"{{{chr(raw[pos + 1])}{raw[pos + 2]:02X}}}", 3
    if pos + 1 < end and raw[pos] == 0x24 and raw[pos + 1] == ord("T"):
        return "{T}", 2
    if pos + 1 < end and raw[pos] == 0x24 and 0x30 <= raw[pos + 1] <= 0x39:
        return f"{{S{chr(raw[pos + 1])}}}", 2
    if pos + 1 < end and raw[pos] == 0x24 and raw[pos + 1] < 0x20:
        return f"{{B{raw[pos + 1]:02X}}}", 2
    return None


def _field_text_end(field: bytes) -> int:
    """Find the end of text fragments before fixed field metadata.

    Text fragments use isolated NUL separators.  The fixed record metadata,
    when present, follows a longer zero-padding run near the end of the field.
    """
    for start in range(max(0, len(field) - 7)):
        if field[start:start + 8] == b"\0" * 8:
            return start
    return max((index for index, value in enumerate(field) if value), default=-1) + 1


def _format_field(raw: bytes) -> str:
    """Render a complete 0x40-byte field as editable text.

    NULs inside the used part of the field separate text/control fragments;
    they are not the end of the visible field.  ``{Z}`` preserves those
    separators while trailing padding remains omitted.
    """
    parts: list[str] = []
    used = _field_text_end(raw)
    pos = 0
    while pos < used:
        if raw[pos] == 0:
            parts.append("{Z}")
            pos += 1
            continue
        token = _field_token(raw, pos, used)
        if token is not None:
            value, token_len = token
            parts.append(value)
            pos += token_len
            continue
        start = pos
        pos += 1
        while pos < used and raw[pos] != 0 and _field_token(raw, pos, used) is None:
            pos += 1
        parts.append(raw[start:pos].decode("cp932", errors="replace"))
    return "".join(parts)


def _encode_template(text: str, translate: bool) -> bytes:
    """Encode editable text while restoring inline command token bytes."""
    out = bytearray()
    pos = 0
    for match in TOKEN_RE.finditer(text):
        if match.start() > pos:
            chunk = text[pos:match.start()]
            out += encode_translation(chunk) if translate else chunk.encode("cp932")
        token = match.group(0)[1:-1]
        if token == "Z":
            out.append(0)
        elif token.startswith("C"):
            out.append(int(token[1:], 16))
        elif token == "T":
            out += b"$T"
        elif token.startswith("S"):
            out += bytes((0x24, ord(token[1])))
        else:
            kind, value = token[0], int(token[1:], 16)
            if kind == "B":
                out += bytes((0x24, value))
            else:
                out += bytes((0x24, ord(kind), value))
        pos = match.end()
    if pos < len(text):
        chunk = text[pos:]
        out += encode_translation(chunk) if translate else chunk.encode("cp932")
    return bytes(out)


def _canonical_control_tokens(text: str) -> str:
    """Replace legacy literal field controls with stable editable tokens."""
    return "".join(
        f"{{C{ord(char):02X}}}" if 0 < ord(char) < 0x20 else char
        for char in text
    )


def _known_translation(text: str) -> str:
    translation = EXTENDED_TRANSLATIONS.get(text)
    if translation is not None:
        return translation
    translation = KNOWN_TRANSLATIONS.get(text)
    if translation is not None:
        return translation
    if "{Z}" in text:
        return "{Z}".join(_known_translation(part) for part in text.split("{Z}"))
    # Formatting suffixes such as {T} and {S5} are not part of the visible
    # label.  Reuse the reviewed base-label translation while preserving them.
    for suffix in ("{T}", "{S3}", "{S5}"):
        if text.endswith(suffix):
            base = text[:-len(suffix)]
            translated = KNOWN_TRANSLATIONS.get(base)
            if translated is not None:
                return translated + suffix
    return text


def _field_used_length(field: bytes) -> int:
    return _field_text_end(field)


def _rebuild_field(old_field: bytes, encoded: bytes, old_len: int,
                   full_field: bool = False) -> bytes | None:
    """Replace only the text prefix; preserve the fixed suffix byte-for-byte.

    The bytes after the text include inline commands and record sentinels.
    They are interpreted at fixed offsets by the UI code, so compacting them
    when a translation is shorter can corrupt the following state machine.
    """
    if len(old_field) != FIELD_SIZE or old_len > FIELD_SIZE:
        return None
    if full_field and old_len != _field_used_length(old_field):
        return None
    if old_len < FIELD_SIZE and old_field[old_len] != 0:
        return None
    suffix_start = next(
        (index for index in range(old_len + 1, FIELD_SIZE)
         if old_field[index] != 0),
        FIELD_SIZE,
    )
    if len(encoded) >= suffix_start:
        return None
    rebuilt = bytearray(old_field)
    rebuilt[:suffix_start] = b"\0" * suffix_start
    rebuilt[:len(encoded)] = encoded
    return bytes(rebuilt)


def _sjis_prefix(data: bytes, offset: int) -> tuple[int, int]:
    pos = offset
    chars = 0
    while pos + 1 < len(data) and data[pos] in LEADS:
        trail = data[pos + 1]
        if not (0x40 <= trail <= 0x7E or 0x80 <= trail <= 0xFC):
            break
        pos += 2
        chars += 1
    return pos, chars


def _fhn_resource_ranges(data: bytes) -> list[tuple[int, int]]:
    """Return absolute FHN resource ranges from the offset table."""
    if len(data) < 8:
        return []
    count = struct.unpack_from("<I", data, 0)[0]
    table_end = 4 + count * 4
    if not (1 <= count <= 0x10000) or table_end > len(data):
        return []
    offsets = [struct.unpack_from("<I", data, 4 + index * 4)[0]
               for index in range(count)]
    if offsets[0] < table_end or offsets[-1] >= len(data):
        return []
    if any(left >= right for left, right in zip(offsets, offsets[1:])):
        return []
    ends = offsets[1:] + [len(data)]
    return list(zip(offsets, ends))


def _is_record_candidate(data: bytes, offset: int,
                         resource_end: int | None = None) -> tuple[int, int] | None:
    if offset and data[offset - 1] != 0:
        return None
    limit = len(data) if resource_end is None else min(resource_end, len(data))
    if offset + FIELD_SIZE > limit:
        return None
    end, chars = _sjis_prefix(data, offset)
    if chars < 1 or end > offset + FIELD_SIZE:
        return None
    # The field may be followed immediately by formatting commands (0x24) or
    # by NUL padding.  Require the rest of the fixed field and record tail to
    # look like the known UI-record layout, which rejects compressed/image data.
    if end >= len(data) or data[end] not in (0, 0x24):
        return None
    if data[end:offset + FIELD_SIZE].count(0) < 16:
        return None
    field_end = offset + FIELD_SIZE
    tail = data[field_end:min(field_end + RECORD_TAIL, limit)]
    if len(tail) == RECORD_TAIL and tail.count(0) >= 4:
        return end, chars
    # The final text field may be followed immediately by the next ITE, so it
    # has no 0x18-byte record tail.  Accept it only when the remainder of its
    # non-image resource is pure padding.
    if field_end <= limit and not any(data[field_end:limit]):
        return end, chars
    return None


def target_sources(unpacked: Path) -> list[tuple[str, Path]]:
    root = unpacked / TEXT_ROOT
    out = []
    for path in sorted(root.rglob("*.fhn")):
        if path.parent.name not in EXTERNAL_UI_DIRS:
            out.append((path.relative_to(unpacked).as_posix(), path))
    return out


def extract_records(data: bytes) -> list[dict]:
    rows = []
    ranges = _fhn_resource_ranges(data)
    if not ranges:
        ranges = [(0, len(data))]
    for resource_start, resource_end in ranges:
        if data[resource_start:resource_start + 4] in IMAGE_MAGICS:
            continue
        for offset in range(resource_start, max(resource_start,
                                                 resource_end - FIELD_SIZE + 1)):
            candidate = _is_record_candidate(data, offset, resource_end)
            if candidate is None:
                continue
            if rows and offset < rows[-1]["offset"] + FIELD_SIZE:
                # A field can contain several NUL-separated visible fragments;
                # do not mistake a continuation fragment for a new record.
                continue
            end, chars = candidate
            field = data[offset:offset + FIELD_SIZE]
            used = _field_used_length(field)
            raw = field[:used]
            rows.append({
                "offset": offset,
                "byte_len": len(raw),
                "field_size": FIELD_SIZE,
                "record_size": FIELD_SIZE + RECORD_TAIL,
                "raw_hex": raw.hex(),
                "text": _format_field(field),
                "chars": chars,
            })
    return rows


def candidate_path(output_dir: Path, src_rel: str) -> Path:
    rel = Path(src_rel).relative_to(TEXT_ROOT)
    return output_dir / (str(rel).replace("\\", "_").replace("/", "_") + ".txt")


def _existing_translations(path: Path) -> dict[int, tuple[str, str]]:
    """Read old candidate rows so extraction can preserve reviewed edits."""
    if not path.is_file():
        return {}
    out: dict[int, tuple[str, str]] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        match = LINE_RE.match(line)
        if not match or "|" not in match.group(4):
            continue
        original, translation = match.group(4).split("|", 1)
        out[int(match.group(2), 16)] = (
            original.strip(" "), translation.strip(" "))
    return out


def _translation_for_row(text: str,
                         previous: tuple[str, str] | None) -> str:
    if previous is not None:
        original, translation = previous
        if text == original:
            known = _known_translation(text)
            if translation == original and known != text:
                return known
            return _canonical_control_tokens(translation)
        try:
            same_template = (_encode_template(text, translate=False) ==
                             _encode_template(original, translate=False))
        except UnicodeEncodeError:
            same_template = False
        if same_template:
            known = _known_translation(text)
            if translation == original and known != text:
                return known
            return _canonical_control_tokens(translation)
        # A previously extracted prefix may now have visible/control fragments
        # after an embedded NUL.  Keep the reviewed prefix translation and add
        # the newly exposed suffix as an untranslated review placeholder.
        if text.startswith(original):
            return (_canonical_control_tokens(translation) +
                    _canonical_control_tokens(text[len(original):]))
    return _known_translation(text)


def cmd_extract(args: argparse.Namespace) -> None:
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    total = 0
    for src_rel, source in target_sources(args.unpacked):
        rows = extract_records(source.read_bytes())
        path = candidate_path(output, src_rel)
        lines = [
            f"# source={src_rel} field_size=0x{FIELD_SIZE:X} "
            f"record_tail=0x{RECORD_TAIL:X}",
            "# Format: [text:n] @0xOFFSET len=BYTES | original|translation",
            "# Inline commands use tokens such as {G01}, {H06}, {B06}, {T}, {S5};",
            "# {Z} represents an embedded NUL separator and must be preserved.",
        ]
        previous = _existing_translations(path)
        for index, row in enumerate(rows):
            translation = _translation_for_row(
                row["text"], previous.get(row["offset"]))
            lines.append(
                f"[text:{index}] @0x{row['offset']:X} len={row['byte_len']} "
                f"| {row['text']}|{translation}"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        total += len(rows)
        print(f"extract: {src_rel} records={len(rows)} -> {path}")
    print(f"extract: files={len(target_sources(args.unpacked))} records={total}")


def parse_candidate(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        match = LINE_RE.match(line)
        if not match:
            continue
        content = match.group(4)
        if "|" not in content:
            continue
        original, translation = content.split("|", 1)
        rows.append({
            "index": int(match.group(1)),
            "offset": int(match.group(2), 16),
            "byte_len": int(match.group(3)),
            # Remove only the ASCII separator padding added by the file
            # format.  ``str.strip()`` would erase meaningful fullwidth
            # spaces used by several labels.
            "original": original.strip(" "),
            "translation": translation.strip(" "),
        })
    return rows


def _restore_original(unpacked: Path, original_tree: Path | None,
                      image: Path, src_rel: str) -> None:
    target = unpacked / src_rel
    pristine = original_tree / src_rel if original_tree else None
    if pristine is not None and pristine.is_file():
        target.write_bytes(pristine.read_bytes())
        return
    manifest = unpacked / "manifest.tsv"
    with manifest.open(encoding="utf-8") as fh:
        headers = fh.readline().rstrip("\n").split("\t")
        for line in fh:
            row = dict(zip(headers, line.rstrip("\n").split("\t")))
            if row.get("output_path") != src_rel:
                continue
            with image.open("rb") as source:
                source.seek(int(row["index"]) * 8)
                sector, size = struct.unpack("<II", source.read(8))
                source.seek(sector * 0x800)
                data = source.read(size)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            return
    raise FileNotFoundError(f"{src_rel} is missing from {manifest}")


def cmd_write(args: argparse.Namespace) -> None:
    total = changed = overflow = mismatch = unencodable = 0
    for candidate in sorted(args.translation.glob("*.txt")):
        header = candidate.read_text(encoding="utf-8-sig").splitlines()[:1]
        if not header or not header[0].startswith("# source="):
            continue
        src_rel = header[0].split("# source=", 1)[1].split(" field_size=", 1)[0]
        source = args.unpacked / src_rel
        if not source.is_file():
            print(f"[fhn] missing source: {src_rel}")
            mismatch += 1
            continue
        _restore_original(args.unpacked, args.original_tree, args.image, src_rel)
        data = bytearray(source.read_bytes())
        for row in parse_candidate(candidate):
            total += 1
            off = row["offset"]
            old_len = row["byte_len"]
            if off < 0 or off + FIELD_SIZE + RECORD_TAIL > len(data):
                mismatch += 1
                continue
            old_field = bytes(data[off:off + FIELD_SIZE])
            full_field = ("{Z}" in row["original"] or
                          old_len == _field_used_length(old_field))
            old = data[off:off + old_len]
            try:
                # Validation must use native CP932.  encode_translation maps
                # CJK characters through the freshly allocated Chinese glyph
                # slots, which is correct for output but not for source-byte
                # comparison.
                expected = _encode_template(row["original"], translate=False)
            except UnicodeEncodeError:
                mismatch += 1
                continue
            if old != expected:
                mismatch += 1
                continue
            try:
                # Extracted rows use the original text as a review placeholder.
                # Keep those bytes exactly as-is; passing Japanese kanji through
                # the Chinese slot map changes glyph indices without translating.
                if row["translation"] == row["original"]:
                    encoded = expected
                else:
                    encoded = _encode_template(row["translation"], translate=True)
            except UnicodeEncodeError:
                unencodable += 1
                continue
            field = _rebuild_field(old_field, encoded, old_len, full_field)
            if field is None:
                print(f"[fhn] overflow {src_rel} @0x{off:X}: "
                      f"translation bytes={len(encoded)}")
                overflow += 1
                continue
            if old_field != field:
                changed += 1
                data[off:off + FIELD_SIZE] = field
        source.write_bytes(data)
        print(f"[fhn] {src_rel}")
    print(f"fhn changed={changed} total={total} overflow={overflow} "
          f"unencodable={unencodable} mismatch={mismatch}")
    if overflow or unencodable or mismatch:
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    extract = sub.add_parser("extract")
    extract.add_argument("--unpacked", type=Path,
                         default=ROOT / "unpacked/CDIMAGE_FULL")
    extract.add_argument("--output", type=Path,
                         default=ROOT / "work/translation/fhn")
    extract.set_defaults(func=cmd_extract)
    write = sub.add_parser("write")
    write.add_argument("--unpacked", type=Path,
                       default=ROOT / "unpacked/CDIMAGE_FULL")
    write.add_argument("--translation", type=Path,
                       default=ROOT / "work/translation/fhn")
    write.add_argument("--image", type=Path,
                       default=ROOT / "unpak/CDIMAGE.BIN")
    write.add_argument("--original-tree", type=Path, default=None)
    write.set_defaults(func=cmd_write)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
