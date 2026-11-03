#!/usr/bin/env python3
"""Extract the raw MPEG-2 video spans from MOVIMAGE.BIN.

The container has a small header, 0x800-byte subtitle records, and raw
headerless MPEG-2 spans.  This tool copies those spans to Test and converts
each one to an editing-friendly H.264 MP4.  It never modifies MOVIMAGE.BIN.
"""
from __future__ import annotations

import argparse
import csv
import mmap
import shutil
import subprocess
from pathlib import Path


SUBTITLE_BLOCKS = (
    0x0C0A4000,
    0x0CA78000,
    0x0F018800,
    0x10538800,
    0x119FA000,
    0x130B0800,
    0x14767800,
    0x15C30800,
    0x170F2000,
    0x18676800,
    0x19D2D000,
    0x1B67D000,
    0x1EDAD800,
    0x28BBF000,
    0x2E698800,
)

HEADER_SIZE_BEFORE_MPEG = 0x10
FIRST_MPEG_OFFSET = 0x1800
MPEG_AFTER_SUBTITLE_OFFSET = 0x810
CHUNK_SIZE = 8 * 1024 * 1024

MOVIE_NAMES = (
    "ed",
    "mk01",
    "mk02",
    "mk03",
    "mk04",
    "mk05",
    "mk06",
    "mk07",
    "mk08",
    "mk09",
    "mk10",
    "mk11",
    "mk12",
    "mv01",
    "mv02",
    "mv03",
    "mv04",
    "mv05",
    "namco",
    "op",
)


def segment_ranges(file_size: int) -> list[tuple[int, int, int | None]]:
    """Return (start, end, subtitle_index) for each raw video span."""
    starts = [FIRST_MPEG_OFFSET]
    starts.extend(block + MPEG_AFTER_SUBTITLE_OFFSET for block in SUBTITLE_BLOCKS)
    ends = list(SUBTITLE_BLOCKS) + [file_size]
    return [(start, end, index - 1 if index else None)
            for index, (start, end) in enumerate(zip(starts, ends))]


def copy_range(source: Path, destination: Path, start: int, end: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as src, destination.open("wb") as dst:
        src.seek(start)
        remaining = end - start
        while remaining:
            chunk = src.read(min(CHUNK_SIZE, remaining))
            if not chunk:
                raise OSError(f"short read at 0x{start:X}: {source}")
            dst.write(chunk)
            remaining -= len(chunk)


def find_chunk_headers(data: mmap.mmap) -> list[int]:
    """Find the 0x800-aligned movie chunk headers in MOVIMAGE.BIN."""
    headers: list[int] = []
    position = FIRST_MPEG_OFFSET
    marker = b"\x00\x00\x01\xB3"
    while True:
        sequence = data.find(marker, position)
        if sequence < 0:
            break
        header = sequence - HEADER_SIZE_BEFORE_MPEG
        if sequence >= FIRST_MPEG_OFFSET + HEADER_SIZE_BEFORE_MPEG and sequence % 0x800 == 0x10:
            headers.append(header)
        position = sequence + 1
    return headers


def logical_resource_ranges(source: Path) -> list[tuple[str, int, int, list[int]]]:
    """Return named logical movie resources and their chunk headers.

    A resource is a sequence of 0x800-aligned chunks.  A zero first field in
    the chunk header marks the final chunk of a resource.  Subtitle records
    may sit between two resources and are excluded when copying the MPEG data.
    """
    with source.open("rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as data:
        headers = find_chunk_headers(data)
        if not headers or headers[0] != FIRST_MPEG_OFFSET:
            raise ValueError("could not find the first MOVIMAGE movie chunk")

        final_headers = [header for header in headers if int.from_bytes(data[header:header + 4], "little") == 0]
        if len(final_headers) != len(MOVIE_NAMES):
            raise ValueError(
                f"expected {len(MOVIE_NAMES)} logical movies, found {len(final_headers)}"
            )

        starts = [headers[0]]
        for final_header in final_headers[:-1]:
            next_header = next((header for header in headers if header > final_header), None)
            if next_header is None:
                raise ValueError(f"missing resource start after 0x{final_header:X}")
            starts.append(next_header)

        ranges = []
        for index, (name, start) in enumerate(zip(MOVIE_NAMES, starts)):
            end = starts[index + 1] if index + 1 < len(starts) else source.stat().st_size
            chunks = [header for header in headers if start <= header < end]
            ranges.append((name, start, end, chunks))
        return ranges


def copy_logical_resource(
    source: Path,
    destination: Path,
    start: int,
    end: int,
    chunks: list[int],
) -> int:
    """Copy only MPEG payloads, dropping chunk headers and subtitle blocks."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    copied = 0
    with source.open("rb") as src, destination.open("wb") as dst:
        for index, header in enumerate(chunks):
            chunk_end = chunks[index + 1] if index + 1 < len(chunks) else end
            payload_start = header + HEADER_SIZE_BEFORE_MPEG
            cursor = payload_start
            for subtitle_block in SUBTITLE_BLOCKS:
                if subtitle_block >= chunk_end:
                    break
                if subtitle_block <= cursor:
                    continue
                src.seek(cursor)
                dst.write(src.read(subtitle_block - cursor))
                copied += subtitle_block - cursor
                cursor = subtitle_block + 0x800
            if cursor < chunk_end:
                src.seek(cursor)
                dst.write(src.read(chunk_end - cursor))
                copied += chunk_end - cursor
    return copied


def find_ffmpeg() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg was not found on PATH")
    return ffmpeg


def transcode(ffmpeg: str, raw: Path, output: Path, force: bool) -> None:
    if output.exists() and not force:
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-y",
        "-f",
        "mpegvideo",
        "-i",
        str(raw),
        "-map",
        "0:v:0",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-fps_mode",
        "passthrough",
        "-movflags",
        "+faststart",
        str(output),
    ]
    subprocess.run(command, check=True)


def extract(args: argparse.Namespace) -> None:
    source = args.input.resolve()
    output_root = args.output.resolve()
    raw_dir = output_root / "source_m2v"
    mp4_dir = output_root / "mp4"
    ffmpeg = find_ffmpeg()
    ranges = segment_ranges(source.stat().st_size)
    manifest_rows = []

    for index, (start, end, subtitle_index) in enumerate(ranges):
        stem = f"video_{index:02d}"
        raw = raw_dir / f"{stem}.m2v"
        mp4 = mp4_dir / f"{stem}.mp4"
        if not raw.exists() or args.force:
            copy_range(source, raw, start, end)
        transcode(ffmpeg, raw, mp4, args.force)
        manifest_rows.append({
            "video_index": index,
            "start": f"0x{start:X}",
            "end": f"0x{end:X}",
            "length": f"0x{end - start:X}",
            "subtitle_index": "" if subtitle_index is None else subtitle_index,
            "raw_m2v": str(raw),
            "mp4": str(mp4),
        })
        print(f"video_{index:02d}: 0x{start:X}..0x{end:X} -> {mp4}")

    manifest = output_root / "manifest.csv"
    with manifest.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=manifest_rows[0].keys())
        writer.writeheader()
        writer.writerows(manifest_rows)
    print(f"wrote {len(manifest_rows)} video span(s); manifest={manifest}")


def extract_logical(args: argparse.Namespace) -> None:
    source = args.input.resolve()
    output_root = args.output.resolve()
    raw_dir = output_root / "source_m2v"
    mp4_dir = output_root / "mp4"
    ffmpeg = find_ffmpeg()
    ranges = logical_resource_ranges(source)
    manifest_rows = []

    for index, (name, start, end, chunks) in enumerate(ranges):
        stem = f"{index:02d}_{name}"
        raw = raw_dir / f"{stem}.m2v"
        mp4 = mp4_dir / f"{stem}.mp4"
        if not raw.exists() or args.force:
            payload_length = copy_logical_resource(source, raw, start, end, chunks)
        else:
            payload_length = raw.stat().st_size
        transcode(ffmpeg, raw, mp4, args.force)
        manifest_rows.append({
            "video_index": index,
            "name": name,
            "resource_start": f"0x{start:X}",
            "resource_end": f"0x{end:X}",
            "resource_length": f"0x{end - start:X}",
            "chunk_count": len(chunks),
            "mpeg_payload_length": f"0x{payload_length:X}",
            "raw_m2v": str(raw),
            "mp4": str(mp4),
        })
        print(f"{index:02d}_{name}: 0x{start:X}..0x{end:X} ({len(chunks)} chunks) -> {mp4}")

    manifest = output_root / "manifest.csv"
    with manifest.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=manifest_rows[0].keys())
        writer.writeheader()
        writer.writerows(manifest_rows)
    print(f"wrote {len(manifest_rows)} logical video(s); manifest={manifest}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--logical",
        action="store_true",
        help="extract the 20 named logical movie resources instead of physical spans",
    )
    args = parser.parse_args()
    (extract_logical if args.logical else extract)(args)


if __name__ == "__main__":
    main()
