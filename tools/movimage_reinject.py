#!/usr/bin/env python3
"""Reinject one edited MPEG-2 movie into MOVIMAGE.BIN without overwriting inputs.

The movie area is made of fixed 0x800-aligned blocks.  Each block has a
16-byte header, followed by an MPEG-2 video payload and the original audio
region.  The second little-endian u32 stores the actual video length and the
third stores the aligned video slot size.  This tool keeps all block positions
and audio bytes unchanged, replacing only the video payload and its length.
"""
from __future__ import annotations

import argparse
import json
import mmap
import shutil
import struct
import subprocess
from pathlib import Path


OP_START = 0x33150800
OP_END = 0x38461000
CHUNK_ALIGN = 0x800
HEADER_SIZE = 0x10
MOVIE_CHUNK_COUNT = 60
PICTURE_START = b"\x00\x00\x01\x00"
SEQUENCE_START = b"\x00\x00\x01\xB3"
SEQUENCE_END = b"\x00\x00\x01\xB7"


def u32(data: mmap.mmap, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def find_op_chunks(data: mmap.mmap) -> list[int]:
    """Find the 60 op chunk headers from their aligned MPEG sequence starts."""
    chunks: list[int] = []
    pos = OP_START + HEADER_SIZE
    while True:
        sequence = data.find(SEQUENCE_START, pos, OP_END)
        if sequence < 0:
            break
        header = sequence - HEADER_SIZE
        if sequence % CHUNK_ALIGN == HEADER_SIZE and header >= OP_START:
            if not chunks or header != chunks[-1]:
                chunks.append(header)
        pos = sequence + 1
    if len(chunks) != MOVIE_CHUNK_COUNT:
        raise ValueError(f"expected {MOVIE_CHUNK_COUNT} op chunks, found {len(chunks)}")
    return chunks


def chunk_table(source: Path) -> tuple[list[int], list[dict[str, int]]]:
    with source.open("rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as data:
        chunks = find_op_chunks(data)
        rows: list[dict[str, int]] = []
        for index, start in enumerate(chunks):
            end = chunks[index + 1] if index + 1 < len(chunks) else OP_END
            mpeg_length = u32(data, start + 4)
            video_capacity = u32(data, start + 8)
            audio_length = u32(data, start + 12)
            payload_start = start + HEADER_SIZE
            payload_end = payload_start + mpeg_length
            if video_capacity < mpeg_length:
                raise ValueError(
                    f"chunk {index} video capacity 0x{video_capacity:X} "
                    f"is smaller than MPEG length 0x{mpeg_length:X}"
                )
            rows.append({
                "index": index,
                "start": start,
                "end": end,
                "capacity": end - payload_start,
                "mpeg_length": mpeg_length,
                "video_capacity": video_capacity,
                "audio_length": audio_length,
                "frames": data[payload_start:payload_end].count(PICTURE_START),
            })
        return chunks, rows


def find_picture_ends(mpeg: bytes) -> list[int]:
    """Return byte offsets immediately after each MPEG picture start code.

    MPEG picture start codes are sufficient here because every picture is
    self-contained at the frame level and FFmpeg emits the stream in decode
    order.  The final end is added by the caller.
    """
    starts: list[int] = []
    position = 0
    while True:
        position = mpeg.find(PICTURE_START, position)
        if position < 0:
            break
        starts.append(position)
        position += 4
    return starts


def run_ffmpeg(
    ffmpeg: str,
    input_path: Path,
    output_path: Path,
    quality: int | None = None,
    bitrate: int | None = None,
    gop: int = 15,
    b_frames: int = 0,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "warning", "-y",
        "-i", str(input_path), "-map", "0:v:0", "-an",
        "-c:v", "mpeg2video",
        "-g", str(gop), "-bf", str(b_frames), "-pix_fmt", "yuv420p",
        "-r", "30", "-f", "mpeg2video", str(output_path),
    ]
    if bitrate is not None:
        command[command.index("-g"):command.index("-g")] = [
            "-b:v", f"{bitrate}k",
            "-minrate", f"{bitrate}k",
            "-maxrate", f"{bitrate}k",
            "-bufsize", "1835k",
        ]
    else:
        command[command.index("-g"):command.index("-g")] = ["-q:v", str(quality)]
    subprocess.run(command, check=True)


def encode_segment(
    ffmpeg: str,
    input_path: Path,
    output_path: Path,
    first_frame: int,
    frame_count: int,
    quality: int | None,
    bitrate: int | None,
    gop: int,
    b_frames: int,
) -> None:
    select = f"select=between(n\\,{first_frame}\\,{first_frame + frame_count - 1}),setpts=N/30/TB"
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "warning", "-y",
        "-i", str(input_path), "-vf", select, "-an",
        "-c:v", "mpeg2video", "-g", str(gop), "-bf", str(b_frames),
        "-pix_fmt", "yuv420p", "-r", "30", "-frames:v", str(frame_count),
        "-f", "mpeg2video", str(output_path),
    ]
    if bitrate is not None:
        command[command.index("-g"):command.index("-g")] = [
            "-b:v", f"{bitrate}k",
            "-minrate", f"{bitrate}k",
            "-maxrate", f"{bitrate}k",
            "-bufsize", "1835k",
        ]
    else:
        command[command.index("-g"):command.index("-g")] = ["-q:v", str(quality)]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(command, check=True)
    with output_path.open("ab") as fh:
        if output_path.stat().st_size < 4:
            raise ValueError(f"empty MPEG segment: {output_path}")
        with output_path.open("rb") as check:
            check.seek(-4, 2)
            if check.read(4) != SEQUENCE_END:
                fh.write(SEQUENCE_END)


def encode_segments(
    input_path: Path,
    source: Path,
    output_dir: Path,
    concat_output: Path,
    quality: int | None,
    bitrate: int | None,
    gop: int,
    b_frames: int,
) -> None:
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    _, rows = chunk_table(source)
    output_dir.mkdir(parents=True, exist_ok=True)
    frame_cursor = 0
    segment_paths: list[Path] = []
    for row in rows:
        segment = output_dir / f"chunk_{row['index']:02d}.m2v"
        encode_segment(
            ffmpeg, input_path, segment, frame_cursor, row["frames"], quality,
            bitrate, gop, b_frames
        )
        encoded = read_bytes(segment)
        actual_frames = len(find_picture_ends(encoded))
        if not encoded.startswith(SEQUENCE_START):
            raise ValueError(f"chunk {row['index']} has no MPEG sequence header")
        if actual_frames != row["frames"]:
            raise ValueError(
                f"chunk {row['index']} has {actual_frames} frames; expected {row['frames']}"
            )
        failures = validate_piece_sizes([row], [encoded])
        if failures:
            raise ValueError("encoded MPEG does not fit:\n" + "\n".join(failures))
        segment_paths.append(segment)
        frame_cursor += row["frames"]
    with concat_output.open("wb") as out:
        for segment in segment_paths:
            out.write(read_bytes(segment))
    print(f"encoded {len(segment_paths)} segments, {frame_cursor} frames -> {concat_output}")


def read_bytes(path: Path) -> bytes:
    with path.open("rb") as fh:
        return fh.read()


def validate_piece_sizes(rows: list[dict[str, int]], pieces: list[bytes]) -> list[str]:
    failures: list[str] = []
    for row, piece in zip(rows, pieces):
        if len(piece) > row["video_capacity"]:
            failures.append(
                f"chunk {row['index']}: encoded=0x{len(piece):X}, "
                f"video_slot=0x{row['video_capacity']:X}; "
                "would overwrite original audio"
            )
    return failures


def write_video_pieces(
    source: Path,
    output: Path,
    rows: list[dict[str, int]],
    pieces: list[bytes],
) -> None:
    """Replace only the video area and preserve each chunk's audio bytes."""
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, output)
    with output.open("r+b") as fh:
        for row, piece in zip(rows, pieces):
            payload_start = row["start"] + HEADER_SIZE
            fh.seek(payload_start)
            fh.write(piece)
            gap = row["video_capacity"] - len(piece)
            if gap:
                fh.write(b"\x00" * gap)
            fh.seek(row["start"] + 4)
            fh.write(struct.pack("<I", len(piece)))


def split_by_frame_counts(mpeg: bytes, rows: list[dict[str, int]]) -> list[bytes]:
    starts = find_picture_ends(mpeg)
    expected = sum(row["frames"] for row in rows)
    if len(starts) != expected:
        raise ValueError(f"encoded frame count {len(starts)} does not match source {expected}")
    pieces: list[bytes] = []
    frame_cursor = 0
    byte_cursor = 0
    for row in rows:
        frame_cursor += row["frames"]
        byte_end = starts[frame_cursor] if frame_cursor < len(starts) else len(mpeg)
        pieces.append(mpeg[byte_cursor:byte_end])
        byte_cursor = byte_end
    return pieces


def reinject(source: Path, encoded: Path, output: Path, report: Path) -> None:
    chunks, rows = chunk_table(source)
    encoded_bytes = read_bytes(encoded)
    pieces = split_by_frame_counts(encoded_bytes, rows)
    failures = validate_piece_sizes(rows, pieces)
    if failures:
        raise ValueError("encoded MPEG does not fit:\n" + "\n".join(failures))

    write_video_pieces(source, output, rows, pieces)

    result = {
        "source": str(source),
        "encoded": str(encoded),
        "output": str(output),
        "source_size": source.stat().st_size,
        "output_size": output.stat().st_size,
        "encoded_size": len(encoded_bytes),
        "chunks": [
            {
                **row,
                "encoded_length": len(piece),
                "video_remaining": row["video_capacity"] - len(piece),
                "block_remaining": row["capacity"] - len(piece),
                "audio_preserved": row["audio_length"],
            }
            for row, piece in zip(rows, pieces)
        ],
    }
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def reinject_segments(source: Path, segment_dir: Path, output: Path, report: Path) -> None:
    _, rows = chunk_table(source)
    pieces: list[bytes] = []
    failures: list[str] = []
    for row in rows:
        segment = segment_dir / f"chunk_{row['index']:02d}.m2v"
        if not segment.exists():
            raise FileNotFoundError(segment)
        piece = read_bytes(segment)
        actual_frames = len(find_picture_ends(piece))
        if not piece.startswith(SEQUENCE_START):
            failures.append(f"chunk {row['index']}: missing sequence header")
        if actual_frames != row["frames"]:
            failures.append(
                f"chunk {row['index']}: frames={actual_frames}, expected={row['frames']}"
            )
        failures.extend(validate_piece_sizes([row], [piece]))
        pieces.append(piece)
    if failures:
        raise ValueError("segmented MPEG validation failed:\n" + "\n".join(failures))

    write_video_pieces(source, output, rows, pieces)

    result = {
        "source": str(source),
        "segment_dir": str(segment_dir),
        "output": str(output),
        "source_size": source.stat().st_size,
        "output_size": output.stat().st_size,
        "chunks": [
            {
                **row,
                "encoded_length": len(piece),
                "video_remaining": row["video_capacity"] - len(piece),
                "block_remaining": row["capacity"] - len(piece),
                "audio_preserved": row["audio_length"],
            }
            for row, piece in zip(rows, pieces)
        ],
    }
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"reinfected {len(pieces)} chunks -> {output}")


def analyze(source: Path, report: Path) -> None:
    _, rows = chunk_table(source)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({"source": str(source), "chunks": rows}, indent=2), encoding="utf-8")
    print(json.dumps({
        "chunks": len(rows),
        "frames": sum(row["frames"] for row in rows),
        "source_mpeg_bytes": sum(row["mpeg_length"] for row in rows),
        "capacity_bytes": sum(row["capacity"] for row in rows),
        "min_capacity": min(row["capacity"] for row in rows),
        "max_capacity": max(row["capacity"] for row in rows),
        "report": str(report),
    }, indent=2))


def extract_op_payload(source: Path, output: Path) -> None:
    _, rows = chunk_table(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as src, output.open("wb") as dst:
        for row in rows:
            src.seek(row["start"] + HEADER_SIZE)
            payload = src.read(row["mpeg_length"])
            if len(payload) != row["mpeg_length"]:
                raise OSError(f"short read in op chunk {row['index']}")
            dst.write(payload)
    print(f"extracted {len(rows)} op payloads -> {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    analyze_parser = sub.add_parser("analyze")
    analyze_parser.add_argument("source", type=Path)
    analyze_parser.add_argument("report", type=Path)

    extract_parser = sub.add_parser("extract-op")
    extract_parser.add_argument("source", type=Path)
    extract_parser.add_argument("output", type=Path)

    encode_parser = sub.add_parser("encode")
    encode_parser.add_argument("input", type=Path)
    encode_parser.add_argument("output", type=Path)
    encode_parser.add_argument("--quality", type=int)
    encode_parser.add_argument("--bitrate", type=int, help="constant video bitrate in kbps")
    encode_parser.add_argument("--gop", type=int, default=15)
    encode_parser.add_argument("--b-frames", type=int, default=0)

    reinject_parser = sub.add_parser("reinject")
    reinject_parser.add_argument("source", type=Path)
    reinject_parser.add_argument("encoded", type=Path)
    reinject_parser.add_argument("output", type=Path)
    reinject_parser.add_argument("report", type=Path)

    segments_parser = sub.add_parser("encode-segments")
    segments_parser.add_argument("input", type=Path)
    segments_parser.add_argument("source", type=Path)
    segments_parser.add_argument("output_dir", type=Path)
    segments_parser.add_argument("concat_output", type=Path)
    segments_parser.add_argument("--quality", type=int)
    segments_parser.add_argument("--bitrate", type=int, help="constant video bitrate in kbps")
    segments_parser.add_argument("--gop", type=int, default=15)
    segments_parser.add_argument("--b-frames", type=int, default=0)

    reinject_segments_parser = sub.add_parser("reinject-segments")
    reinject_segments_parser.add_argument("source", type=Path)
    reinject_segments_parser.add_argument("segment_dir", type=Path)
    reinject_segments_parser.add_argument("output", type=Path)
    reinject_segments_parser.add_argument("report", type=Path)

    args = parser.parse_args()
    if args.command == "analyze":
        analyze(args.source.resolve(), args.report.resolve())
    elif args.command == "extract-op":
        extract_op_payload(args.source.resolve(), args.output.resolve())
    elif args.command == "encode":
        if args.quality is None and args.bitrate is None:
            args.quality = 4
        if args.quality is not None and args.bitrate is not None:
            raise SystemExit("choose either --quality or --bitrate")
        run_ffmpeg(
            shutil.which("ffmpeg") or "ffmpeg",
            args.input.resolve(),
            args.output.resolve(),
            args.quality,
            args.bitrate,
            args.gop,
            args.b_frames,
        )
    elif args.command == "encode-segments":
        if args.quality is None and args.bitrate is None:
            args.bitrate = 3000
        if args.quality is not None and args.bitrate is not None:
            raise SystemExit("choose either --quality or --bitrate")
        encode_segments(
            args.input.resolve(), args.source.resolve(), args.output_dir.resolve(),
            args.concat_output.resolve(), args.quality, args.bitrate,
            args.gop, args.b_frames,
        )
    elif args.command == "reinject-segments":
        reinject_segments(
            args.source.resolve(), args.segment_dir.resolve(), args.output.resolve(),
            args.report.resolve(),
        )
    else:
        reinject(args.source.resolve(), args.encoded.resolve(), args.output.resolve(), args.report.resolve())


if __name__ == "__main__":
    main()
