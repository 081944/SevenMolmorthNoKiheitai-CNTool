"""Bit-level helpers for the headerless MPEG-2 stream used by 7W ITE tiles."""
from __future__ import annotations

from dataclasses import dataclass


LUMA_DC = {
    0: "100", 1: "00", 2: "01", 3: "101", 4: "110", 5: "1110",
    6: "11110", 7: "111110", 8: "1111110", 9: "11111110",
    10: "111111110", 11: "111111111",
}
CHROMA_DC = {
    0: "00", 1: "01", 2: "10", 3: "110", 4: "1110", 5: "11110",
    6: "111110", 7: "1111110", 8: "11111110", 9: "111111110",
    10: "1111111110", 11: "1111111111",
}

# MPEG-2 Table B-15 (alternate intra VLC). Values are (run, level).
# run=64 is EOB and run=65 is escape.
DCT_VLC = {
    "10": (0, 1), "010": (1, 1), "110": (0, 2),
    "0110": (64, 0), "0111": (0, 3),
    "00101": (2, 1), "00110": (1, 2), "00111": (3, 1),
    "11100": (0, 4), "11101": (0, 5),
    "000001": (65, 0), "000100": (0, 7), "000101": (0, 6),
    "000110": (4, 1), "000111": (5, 1),
    "0000100": (7, 1), "0000101": (8, 1),
    "0000110": (6, 1), "0000111": (2, 2),
    "1111000": (9, 1), "1111001": (1, 3),
    "1111010": (10, 1), "1111011": (0, 8), "1111100": (0, 9),
    "00100000": (1, 5), "00100001": (11, 1),
    "00100010": (0, 11), "00100011": (0, 10),
    "00100100": (13, 1), "00100101": (12, 1),
    "00100110": (3, 2), "00100111": (1, 4),
    "11111010": (0, 12), "11111011": (0, 13),
    "11111100": (2, 3), "11111101": (4, 2),
    "11111110": (0, 14), "11111111": (0, 15),
    "000000100": (5, 2), "000000101": (14, 1),
    "000000111": (15, 1), "0000001100": (2, 4),
    "0000001101": (16, 1),
    "000000010000": (0, 11), "000000010001": (8, 2),
    "000000010010": (4, 3), "000000010011": (0, 10),
    "000000010100": (2, 4), "000000010101": (7, 2),
    "000000010110": (21, 1), "000000010111": (20, 1),
    "000000011000": (0, 9), "000000011001": (19, 1),
    "000000011010": (18, 1), "000000011011": (1, 5),
    "000000011100": (3, 3), "000000011101": (0, 8),
    "000000011110": (6, 2), "000000011111": (17, 1),
    "0000000010000": (10, 2), "0000000010001": (9, 2),
    "0000000010010": (5, 3), "0000000010011": (3, 4),
    "0000000010100": (2, 5), "0000000010101": (1, 7),
    "0000000010110": (1, 6), "0000000010111": (0, 15),
    "0000000011000": (0, 14), "0000000011001": (0, 13),
    "0000000011010": (0, 12), "0000000011011": (26, 1),
    "0000000011100": (25, 1), "0000000011101": (24, 1),
    "0000000011110": (23, 1), "0000000011111": (22, 1),
    "00000000010000": (0, 31), "00000000010001": (0, 30),
    "00000000010010": (0, 29), "00000000010011": (0, 28),
    "00000000010100": (0, 27), "00000000010101": (0, 26),
    "00000000010110": (0, 25), "00000000010111": (0, 24),
    "00000000011000": (0, 23), "00000000011001": (0, 22),
    "00000000011010": (0, 21), "00000000011011": (0, 20),
    "00000000011100": (0, 19), "00000000011101": (0, 18),
    "00000000011110": (0, 17), "00000000011111": (0, 16),
    "000000000010000": (0, 40), "000000000010001": (0, 39),
    "000000000010010": (0, 38), "000000000010011": (0, 37),
    "000000000010100": (0, 36), "000000000010101": (0, 35),
    "000000000010110": (0, 34), "000000000010111": (0, 33),
    "000000000011000": (0, 32), "000000000011001": (1, 14),
    "000000000011010": (1, 13), "000000000011011": (1, 12),
    "000000000011100": (1, 11), "000000000011101": (1, 10),
    "000000000011110": (1, 9), "000000000011111": (1, 8),
    "0000000000010000": (1, 18), "0000000000010001": (1, 17),
    "0000000000010010": (1, 16), "0000000000010011": (1, 15),
    "0000000000010100": (6, 3), "0000000000010101": (16, 2),
    "0000000000010110": (15, 2), "0000000000010111": (14, 2),
    "0000000000011000": (13, 2), "0000000000011001": (12, 2),
    "0000000000011010": (11, 2), "0000000000011011": (31, 1),
    "0000000000011100": (30, 1), "0000000000011101": (29, 1),
    "0000000000011110": (28, 1), "0000000000011111": (27, 1),
}

_DCT_PREFIXES = {code[:n] for code in DCT_VLC for n in range(1, len(code) + 1)}
_COMPONENTS = (0, 0, 0, 0, 1, 2)


class Mpeg2SyntaxError(ValueError):
    pass


class BitReader:
    def __init__(self, bits: str):
        self.bits = bits
        self.pos = 0

    def read(self, count: int) -> str:
        end = self.pos + count
        if end > len(self.bits):
            raise Mpeg2SyntaxError("unexpected end of IDEC bitstream")
        value = self.bits[self.pos:end]
        self.pos = end
        return value

    def uint(self, count: int) -> int:
        return int(self.read(count), 2)


@dataclass(frozen=True)
class Block:
    component: int
    dc_start: int
    ac_start: int
    end: int
    dc_value: int


@dataclass(frozen=True)
class Macroblock:
    index: int
    start: int
    mode_end: int
    end: int
    quant_scale: int
    blocks: tuple[Block, ...]


@dataclass(frozen=True)
class SourcedMacroblock:
    bits: str
    macroblock: Macroblock


def bytes_to_bits(data: bytes) -> str:
    return "".join(f"{value:08b}" for value in data)


def bits_to_bytes(bits: str) -> bytes:
    bits += "0" * ((-len(bits)) % 8)
    return bytes(int(bits[i:i + 8], 2) for i in range(0, len(bits), 8))


def _decode_dc(reader: BitReader, chroma: bool) -> int:
    table = CHROMA_DC if chroma else LUMA_DC
    reverse = {code: size for size, code in table.items()}
    code = ""
    while code not in reverse:
        code += reader.read(1)
        if len(code) > 10:
            raise Mpeg2SyntaxError(f"invalid DC VLC at bit {reader.pos - len(code)}")
    size = reverse[code]
    if size == 0:
        return 0
    value = reader.uint(size)
    if value < (1 << (size - 1)):
        value -= (1 << size) - 1
    return value


def encode_dc(diff: int, chroma: bool) -> str:
    table = CHROMA_DC if chroma else LUMA_DC
    if diff == 0:
        return table[0]
    size = abs(diff).bit_length()
    if size not in table:
        raise Mpeg2SyntaxError(f"DC differential {diff} is out of range")
    value = diff if diff > 0 else diff + (1 << size) - 1
    return table[size] + f"{value:0{size}b}"


def _skip_ac(reader: BitReader) -> None:
    coefficient = 1
    while True:
        code = ""
        while code not in DCT_VLC:
            code += reader.read(1)
            if code not in _DCT_PREFIXES:
                raise Mpeg2SyntaxError(
                    f"invalid alternate-intra AC VLC at bit {reader.pos - len(code)}"
                )
        run, _level = DCT_VLC[code]
        if run == 64:
            return
        if run == 65:
            run = reader.uint(6)
            coefficient += run
            if coefficient >= 64:
                raise Mpeg2SyntaxError("escaped AC coefficient exceeds block")
            reader.read(12)
        else:
            coefficient += run
            if coefficient >= 64:
                raise Mpeg2SyntaxError("AC coefficient exceeds block")
            reader.read(1)  # sign
        coefficient += 1


def _parse_macroblock(reader: BitReader, index: int, predictors: list[int],
                      quant_scale: int) -> tuple[Macroblock, int]:
    start = reader.pos
    first = reader.read(1)
    if first == "1":
        pass  # intra
    elif reader.read(1) == "1":
        quant_scale = reader.uint(5)  # intra + quant
    else:
        raise Mpeg2SyntaxError(f"invalid I macroblock mode at bit {start}")
    if quant_scale == 0:
        raise Mpeg2SyntaxError("first macroblock does not specify quantizer scale")
    mode_end = reader.pos

    blocks = []
    for component in _COMPONENTS:
        dc_start = reader.pos
        predictors[component] += _decode_dc(reader, component != 0)
        ac_start = reader.pos
        _skip_ac(reader)
        blocks.append(Block(component, dc_start, ac_start, reader.pos,
                            predictors[component]))
    return (Macroblock(index, start, mode_end, reader.pos, quant_scale,
                       tuple(blocks)), quant_scale)


def parse_idec(payload: bytes, macroblock_count: int = 8) -> tuple[str, tuple[Macroblock, ...]]:
    bits = bytes_to_bits(payload)
    reader = BitReader(bits)
    predictors = [128, 128, 128]
    quant_scale = 0
    macroblocks = []
    for index in range(macroblock_count):
        if index:
            # ITE tiles are dense: every following macroblock has increment 1.
            if reader.read(1) != "1":
                raise Mpeg2SyntaxError(
                    f"macroblock {index} is not contiguous at bit {reader.pos - 1}"
                )
        macroblock, quant_scale = _parse_macroblock(
            reader, index, predictors, quant_scale
        )
        macroblocks.append(macroblock)
    return bits, tuple(macroblocks)


def macroblock_with_reset_dc(bits: str, macroblock: Macroblock) -> str:
    """Return one macroblock whose DC predictors start at 128."""
    out = [bits[macroblock.start:macroblock.mode_end]]
    predictors = [128, 128, 128]
    for block in macroblock.blocks:
        diff = block.dc_value - predictors[block.component]
        out.append(encode_dc(diff, block.component != 0))
        out.append(bits[block.ac_start:block.end])
        predictors[block.component] = block.dc_value
    return "".join(out)


def parse_standard_mpeg2_slices(data: bytes) -> tuple[SourcedMacroblock, ...]:
    """Parse the two four-macroblock slices emitted for a 64x32 I-picture."""
    starts = []
    at = 0
    while True:
        at = data.find(b"\x00\x00\x01", at)
        if at < 0:
            break
        code = data[at + 3] if at + 3 < len(data) else -1
        if code in (1, 2):
            starts.append((at, code))
        at += 3
    if [code for _offset, code in starts] != [1, 2]:
        raise Mpeg2SyntaxError("expected exactly MPEG-2 slice rows 1 and 2")

    result = []
    for slice_index, (offset, code) in enumerate(starts):
        next_start = data.find(b"\x00\x00\x01", offset + 4)
        end = next_start if next_start >= 0 else len(data)
        bits = bytes_to_bits(data[offset + 4:end])
        reader = BitReader(bits)
        quant_scale = reader.uint(5)
        while reader.uint(1):
            reader.read(8)  # extra_information_slice
        predictors = [128, 128, 128]
        for column in range(4):
            if reader.read(1) != "1":
                raise Mpeg2SyntaxError(
                    f"non-contiguous FFmpeg macroblock in slice {code}"
                )
            index = slice_index * 4 + column
            macroblock, quant_scale = _parse_macroblock(
                reader, index, predictors, quant_scale
            )
            result.append(SourcedMacroblock(bits, macroblock))
    return tuple(result)


def build_idec_from_mpeg2(color: bytes, mask: bytes) -> bytes:
    """Merge FFmpeg color/mask I-pictures into one ITE IDEC tile stream."""
    color_mb = parse_standard_mpeg2_slices(color)
    mask_mb = parse_standard_mpeg2_slices(mask)
    order = (
        (color_mb, 0), (color_mb, 4), (color_mb, 1), (color_mb, 5),
        (mask_mb, 0), (mask_mb, 4), (mask_mb, 1), (mask_mb, 5),
        (color_mb, 2), (color_mb, 6), (color_mb, 3), (color_mb, 7),
        (mask_mb, 2), (mask_mb, 6), (mask_mb, 3), (mask_mb, 7),
    )

    predictors = [128, 128, 128]
    quant_scale = 0
    output = []
    for output_index, (source, index) in enumerate(order):
        sourced = source[index]
        macroblock = sourced.macroblock
        if output_index:
            output.append("1")  # macroblock_address_increment = 1
        if macroblock.quant_scale != quant_scale:
            output.append("01")  # intra + quant
            output.append(f"{macroblock.quant_scale:05b}")
            quant_scale = macroblock.quant_scale
        else:
            output.append("1")  # intra
        for block in macroblock.blocks:
            component = block.component
            diff = block.dc_value - predictors[component]
            output.append(encode_dc(diff, component != 0))
            output.append(sourced.bits[block.ac_start:block.end])
            predictors[component] = block.dc_value

    encoded = bits_to_bytes("".join(output)) + b"\x00\x00\x01\xb0"
    return encoded + b"\x00" * ((-len(encoded)) % 16)


def split_idec_into_slices(
        payload: bytes, plane: int = 0, macroblock_count: int = 16,
) -> list[tuple[int, int, int, str]]:
    """Return standard slices for the color or mask plane of an ITE tile."""
    if plane not in (0, 1):
        raise ValueError("ITE plane must be 0 or 1")
    bits, all_macroblocks = parse_idec(payload, macroblock_count)
    if macroblock_count == 8:
        if plane == 1:
            raise ValueError("this ITE tile has no mask plane")
        indices = range(8)
    else:
        # A 64x32 tile is encoded as two 32x32 strips.  Each strip stores
        # four color macroblocks followed by four mask macroblocks.
        indices = (0, 1, 2, 3, 8, 9, 10, 11) if plane == 0 else (
            4, 5, 6, 7, 12, 13, 14, 15
        )
    macroblocks = [all_macroblocks[index] for index in indices]
    mba_codes = ("1", "011", "010", "0011")
    slices = []
    for local_index, macroblock in enumerate(macroblocks):
        # Sony's encoder stores the two vertical macroblocks of each 16-pixel
        # column consecutively: (0,0), (0,1), (1,0), (1,1), ...
        row, column = local_index % 2, local_index // 2
        body = macroblock_with_reset_dc(bits, macroblock)
        # slice quantizer, extra_bit_slice=0, first macroblock address increment
        prefix = f"{macroblock.quant_scale:05b}" + "0" + mba_codes[column]
        slices.append((row, column, macroblock.quant_scale, prefix + body))
    return slices
