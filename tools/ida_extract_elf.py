"""IDA Pro headless export of CP932 strings embedded in the game ELF.

Run from IDA's Python environment, for example:

  idat.exe -A -S"tools/ida_extract_elf.py temp/ida_elf_strings.tsv" work/elf/SLPS_250.25.i64

The output keeps both the ELF file offset and the IDA/EE address.  Function
cross-references are included so a candidate can be traced back to the code
that consumes it instead of relying on a raw byte scan alone.
"""
from __future__ import annotations

import csv
import os
import sys

import idaapi
import ida_bytes
import ida_funcs
import idautils
import idc


def decode_cp932(raw: bytes) -> str | None:
    try:
        text = raw.rstrip(b"\0").decode("cp932")
    except UnicodeDecodeError:
        return None
    if not text or any(ord(ch) < 0x20 and ch not in "\t\n\r" for ch in text):
        return None
    return text


def is_candidate(text: str) -> bool:
    # Keep Japanese/CP932 text and short UI labels, while excluding ordinary
    # compiler/ASCII noise from the main candidate list.
    japanese = sum(
        (0x3040 <= ord(ch) <= 0x30FF)
        or (0x3400 <= ord(ch) <= 0x9FFF)
        or ch in "、。！？「」『』・ー～"
        for ch in text
    )
    return japanese >= 1 and len(text.strip()) >= 1


def refs_for(ea: int) -> str:
    names = []
    for ref in idautils.XrefsTo(ea, 0):
        func = ida_funcs.get_func(ref.frm)
        name = ida_funcs.get_func_name(func.start_ea) if func else ""
        names.append(f"0x{ref.frm:X}:{name or '-'}")
    return ";".join(dict.fromkeys(names))


def file_offset(ea: int) -> int:
    try:
        return int(idaapi.get_fileregion_offset(ea))
    except Exception:
        return -1


def main() -> None:
    args = list(getattr(idc, "ARGV", []))
    output = args[1] if len(args) > 1 else os.path.join("temp", "ida_elf_strings.tsv")
    rows = []
    seen = set()
    for item in idautils.Strings():
        ea = int(item.ea)
        raw = ida_bytes.get_strlit_contents(ea, -1, item.strtype)
        if not raw:
            continue
        text = decode_cp932(bytes(raw))
        if text is None or not is_candidate(text) or ea in seen:
            continue
        seen.add(ea)
        rows.append({
            "file_offset": f"0x{file_offset(ea):X}",
            "address": f"0x{ea:X}",
            "byte_len": len(bytes(raw).rstrip(b"\0")),
            "text": text,
            "xrefs": refs_for(ea),
        })

    rows.sort(key=lambda row: int(row["address"], 16))
    with open(output, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["file_offset", "address", "byte_len", "text", "xrefs"],
            delimiter="\t",
        )
        writer.writeheader()
        writer.writerows(rows)
    print(f"ida_extract_elf: strings={len(rows)} output={output}")
    idc.qexit(0)


if __name__ == "__main__":
    main()
