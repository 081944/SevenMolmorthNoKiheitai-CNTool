# -*- coding: utf-8 -*-
"""UI 文本写回：mov/opt/icon.sys/rpg_member/member.smd/balloon.bhf。
从候选文件读取 (off, len, 日文, 译文)，用槽位编码写回原文件（固定长度覆盖）。
用法: python ui_write.py  （在项目根目录运行，以便 text_7w 找到 char_to_slot.csv）
"""
import re
import sys
import argparse
import struct
from pathlib import Path

# Resolve paths from this checkout instead of the developer machine that
# originally generated this script.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
from text_7w import encode_translation
from member_smd import write as write_member_smd
from balloon_bhf import write as write_balloon_bhf

UI = ROOT / 'work' / 'translation' / 'ui'
UNP = ROOT / 'unpacked' / 'CDIMAGE_FULL' / 'seven_data_link'
ORIGINAL_IMAGE = ROOT / 'unpak' / 'CDIMAGE.BIN'
DEFAULT_ORIGINAL_TREE = ROOT / 'temp' / 'CDIMAGE_ORIGINAL_CLEAN2'
MANIFEST = ROOT / 'unpacked' / 'CDIMAGE_FULL' / 'manifest.tsv'

ENTRY_RE = re.compile(r'^\[text:\d+\] off=0x([0-9A-Fa-f]+) len=(\d+) \| (.*)$')

def parse_candidate(path):
    entries = []
    for line in path.read_text(encoding='utf-8').splitlines():
        m = ENTRY_RE.match(line)
        if not m:
            continue
        off = int(m.group(1), 16)
        cap = int(m.group(2))
        content = m.group(3)
        if '|' in content:
            jp, zh = content.split('|', 1)
            entries.append((off, cap, jp.strip(), zh.strip()))
        else:
            entries.append((off, cap, content.strip(), ''))
    return entries

def write_back(data, entries, label):
    changed = overflow = 0
    for off, cap, jp, zh in entries:
        if not zh:
            continue
        try:
            enc = encode_translation(zh)
        except UnicodeEncodeError as e:
            print(f'  [{label}] 无法编码 @0x{off:X} {zh!r}: {e}')
            continue
        if len(enc) > cap:
            print(f'  [{label}] 超长 @0x{off:X} {jp!r}->{zh!r}: {len(enc)}>{cap}')
            overflow += 1
            continue
        data[off:off+len(enc)] = enc
        if len(enc) < cap:
            data[off+len(enc):off+cap] = b'\x00' * (cap - len(enc))
        changed += 1
    return changed, overflow

def restore_original(unpacked, rel, image=ORIGINAL_IMAGE, original_tree=None):
    """Restore one packed file before applying fixed-offset UI edits.

    The unpacked tree is intentionally mutable during a build. Restoring from
    the pristine CDIMAGE slot makes repeated builds deterministic and avoids
    accumulating edits (or carrying a previously re-encoded FHN container).
    """
    container = Path(unpacked)
    root = container if (container / 'manifest.tsv').is_file() else container.parent
    manifest = root / 'manifest.tsv'
    row = None
    with manifest.open(encoding='utf-8') as fh:
        headers = fh.readline().rstrip('\n').split('\t')
        for line in fh:
            values = line.rstrip('\n').split('\t')
            candidate = dict(zip(headers, values))
            if candidate.get('output_path') == rel:
                row = candidate
                break
    if row is None:
        raise FileNotFoundError(f'{rel} is missing from {manifest}')
    pristine = Path(original_tree) / rel if original_tree else None
    if pristine is not None and pristine.is_file():
        data = pristine.read_bytes()
    else:
        with image.open('rb') as fh:
            fh.seek(int(row['index']) * 8)
            sector, size = struct.unpack('<II', fh.read(8))
            fh.seek(sector * 0x800)
            data = fh.read(size)
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)

def main(unpacked=UNP, image=ORIGINAL_IMAGE, original_tree=None):
    unpacked = Path(unpacked)
    unp = unpacked / 'seven_data_link'
    total_overflow = 0
    # mov.fhn
    restore_original(unpacked, 'seven_data_link/takeshi/screen/mov/mov.fhn', image, original_tree)
    d = bytearray((unp / 'takeshi/screen/mov/mov.fhn').read_bytes())
    entries = parse_candidate(UI / 'mov.txt')
    c, o = write_back(d, entries, 'mov.fhn')
    (unp / 'takeshi/screen/mov/mov.fhn').write_bytes(d)
    print(f'mov.fhn: changed={c} overflow={o}')
    total_overflow += o

    # opt.fhn
    restore_original(unpacked, 'seven_data_link/takeshi/screen/opt/opt.fhn', image, original_tree)
    d = bytearray((unp / 'takeshi/screen/opt/opt.fhn').read_bytes())
    entries = parse_candidate(UI / 'opt.txt')
    c, o = write_back(d, entries, 'opt.fhn')
    (unp / 'takeshi/screen/opt/opt.fhn').write_bytes(d)
    print(f'opt.fhn: changed={c} overflow={o}')
    total_overflow += o

    # icon.sys
    restore_original(unpacked, 'seven_data_link/niwata/icon/icon.sys', image, original_tree)
    d = bytearray((unp / 'niwata/icon/icon.sys').read_bytes())
    entries = parse_candidate(UI / 'icon_sys.txt')
    c, o = write_back(d, entries, 'icon.sys')
    (unp / 'niwata/icon/icon.sys').write_bytes(d)
    print(f'icon.sys: changed={c} overflow={o}')
    total_overflow += o

    # rpg_member (3 files)
    entries = parse_candidate(UI / 'rpg_member.txt')
    for fn in ['rpg_member1.bin', 'rpg_member2.bin', 'rpg_member3.bin']:
        restore_original(unpacked, f'seven_data_link/battle_dat/commit/{fn}', image, original_tree)
        p = unp / 'battle_dat/commit' / fn
        d = bytearray(p.read_bytes())
        c, o = write_back(d, entries, fn)
        p.write_bytes(d)
        print(f'{fn}: changed={c} overflow={o}')
        total_overflow += o

    # member.smd: packed random-name pools.  Its writer rebuilds the text
    # region and updates the two offset columns; unlike the fixed-slot files
    # above, it must not be handled as independent padded fields.
    member_candidate = UI / 'member_smd.txt'
    if member_candidate.is_file():
        member_rel = 'seven_data_link/battle_dat/commit/member.smd'
        restore_original(unpacked, member_rel, image, original_tree)
        member_src = unpacked / member_rel
        write_member_smd(member_src, member_candidate, member_src)
    else:
        print('member.smd: no work/translation/ui/member_smd.txt; skipped')

    # balloon.bhf: fixed-layout explanatory text slots.
    balloon_candidate = UI / 'balloon_bhf.txt'
    if balloon_candidate.is_file():
        balloon_rel = 'seven_data_link/balloon/balloon.bhf'
        restore_original(unpacked, balloon_rel, image, original_tree)
        balloon_src = unpacked / balloon_rel
        write_balloon_bhf(balloon_src, balloon_candidate, balloon_src)
    else:
        print('balloon.bhf: no work/translation/ui/balloon_bhf.txt; skipped')

    print(f'total overflow: {total_overflow}')
    return 0 if total_overflow == 0 else 1

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--unpacked', type=Path, default=UNP)
    ap.add_argument('--image', type=Path, default=ORIGINAL_IMAGE)
    ap.add_argument('--original-tree', type=Path,
                    default=DEFAULT_ORIGINAL_TREE if DEFAULT_ORIGINAL_TREE.is_dir() else None)
    args = ap.parse_args()
    sys.exit(main(args.unpacked, args.image, args.original_tree))
