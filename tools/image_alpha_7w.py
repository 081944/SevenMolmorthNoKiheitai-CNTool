#!/usr/bin/env python3
"""Prepare brighter GIMs for editing directly under work/imageedit.

Run from the project root. The normal image writer restores alpha automatically
using snapshots in work/imagealpha/.state. --copies-only retains the optional
separate editing-copy workflow; apply is only needed for those separate copies.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image

import gim_7w
import image_7w


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_image(path: Path) -> Image.Image:
    with Image.open(path) as image:
        return image.convert("RGBA")


def pixel_sha(image: Image.Image) -> str:
    return sha(str(image.size).encode("ascii") + image.tobytes())


def safe_path(root: Path, rel: str) -> Path:
    path = (root / rel).resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError(f"path is outside {root}: {rel}")
    return path


def brighten(image: Image.Image, peak: int) -> Image.Image:
    result = image.copy()
    result.putalpha(image.getchannel("A").point(
        [min(255, (a * 255 + peak // 2) // peak) for a in range(256)]))
    return result


def rendered_palette(data: bytes, parsed: tuple) -> list[tuple] | None:
    w, h, kind = parsed
    if kind == "32bpp":
        return None
    count = 256 if kind == "8bpp" else 16
    offset = 0x20 + (w * h if count == 256 else (w * h + 1) // 2) + 0x10
    colors = [tuple(data[offset + i * 4:offset + i * 4 + 4])
              for i in range(count)]
    if kind == "4bpp":
        keep = gim_7w._is_alpha_mask(colors)
        colors = [gim_7w._conv(c, keep) for c in colors]
    return colors


def restore(original: Image.Image, edited: Image.Image, peak: int,
            palette: list[tuple] | None) -> tuple[Image.Image, int]:
    """Retain original RGBA on unchanged pixels; quantize edits in preview space.

    Comparing alpha as well as RGB is essential for single-color alpha masks
    and soft edges. Output indexed colors match the existing GIM writer exactly.
    """
    if original.size != edited.size:
        raise ValueError(f"size mismatch: {edited.size}, expected {original.size}")
    before = list(original.getdata())
    preview = brighten(original, peak).getdata()
    colors = None
    if palette is not None:
        colors = [(c, (*c[:3], min(255, (c[3] * 255 + peak // 2) // peak)))
                  for c in palette]
    cache = {}
    changed = 0
    for i, (old, new) in enumerate(zip(preview, edited.getdata())):
        if old == new:
            continue
        changed += 1
        if new not in cache:
            if colors is None:
                cache[new] = (*new[:3], (new[3] * peak + 127) // 255)
            else:
                candidates = [(raw, shown) for raw, shown in colors
                              if (raw[3] == 0) == (new[3] == 0)]
                if not candidates:
                    raise ValueError("palette cannot represent the edited transparency")
                cache[new] = min(candidates, key=lambda pair: sum(
                    (a - b) ** 2 for a, b in zip(pair[1], new)))[0]
        before[i] = cache[new]
    result = Image.new("RGBA", original.size)
    result.putdata(before)
    return result, changed


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8")
    temporary.replace(path)


def prepare(args) -> None:
    workspace = args.workspace.resolve()
    edit_root = args.edit_root.resolve()
    export_root = args.export_root.resolve()
    unpacked = args.unpacked.resolve()
    for other in (edit_root, export_root, unpacked):
        if workspace.is_relative_to(other) or other.is_relative_to(workspace):
            raise ValueError(f"workspace must be separate from {other}")
    state_path = workspace / ".state/session.json"
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if (state["edit_root"], state["unpacked"]) != (str(edit_root), str(unpacked)):
            raise ValueError("workspace belongs to different input roots")
    else:
        state = {"version": 1, "edit_root": str(edit_root),
                 "unpacked": str(unpacked), "entries": {}}
    counts = {"prepared": 0, "existing": 0, "protected": 0, "unneeded": 0}
    for source in image_7w.read_manifest(args.manifest):
        rel = source.png_rel
        if source.kind != "gim" or (args.filter and args.filter not in rel):
            continue
        path = safe_path(edit_root, rel)
        if not path.is_file():
            continue
        if rel in state["entries"]:
            counts["existing"] += 1
            continue
        original = load_image(path)
        exported = safe_path(export_root, rel)
        if not args.include_edited and (not exported.is_file()
                or pixel_sha(original) != pixel_sha(load_image(exported))):
            print(f"[protected: differs from export / no export] {rel}")
            counts["protected"] += 1
            continue
        peak = original.getchannel("A").getextrema()[1]
        if peak == 0 or peak == 255:
            counts["unneeded"] += 1
            continue
        data = safe_path(unpacked, source.src_rel).read_bytes()[source.offset:]
        parsed = gim_7w.parse(data)
        if not gim_7w.is_gim(data) or parsed is None:
            raise ValueError(f"invalid source GIM: {rel}")
        if original.size != parsed[:2]:
            raise ValueError(f"source dimensions differ: {rel}")
        target = safe_path(workspace, rel)
        if target.exists():
            raise ValueError(f"untracked editing copy already exists: {target}")
        snapshot = safe_path(workspace / ".state/original", rel)
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        original_bytes = path.read_bytes()
        snapshot.write_bytes(original_bytes)
        shown = brighten(original, peak)
        target.parent.mkdir(parents=True, exist_ok=True)
        shown.save(target)
        state["entries"][rel] = {
            "peak": peak, "original_sha256": sha(original_bytes),
            "target_sha256": sha(original_bytes), "preview_sha256": pixel_sha(shown),
            "src_rel": source.src_rel, "offset": source.offset,
            "resource_sha256": sha(data),
        }
        save_state(state_path, state)
        counts["prepared"] += 1
        print(f"[prepared alpha 0..{peak} -> 0..255] {rel}")
    print("prepare:", counts)
    if not getattr(args, "copies_only", True):
        activate(workspace, args.filter)


def activate(workspace: Path, filter_text: str | None = None) -> None:
    """Install clear PNGs at their original paths, keeping original snapshots."""
    state_path = workspace / ".state/session.json"
    if not state_path.is_file():
        return
    state = json.loads(state_path.read_text(encoding="utf-8"))
    edit_root = Path(state["edit_root"]).resolve()
    plans = []
    for rel, entry in state["entries"].items():
        if filter_text and filter_text not in rel:
            continue
        if entry.get("inplace"):
            continue
        target = safe_path(edit_root, rel)
        if sha(target.read_bytes()) != entry["target_sha256"]:
            raise ValueError(f"imageedit changed since prepare: {rel}")
        snapshot = safe_path(workspace / ".state/original", rel)
        if sha(snapshot.read_bytes()) != entry["original_sha256"]:
            raise ValueError(f"original snapshot changed: {rel}")
        shown = load_image(safe_path(workspace, rel))
        if shown.size != load_image(snapshot).size:
            raise ValueError(f"editing copy dimensions changed: {rel}")
        plans.append((rel, target, shown))
    for rel, target, shown in plans:
        temporary = target.with_suffix(".alpha-tmp")
        shown.save(temporary, format="PNG")
        temporary.replace(target)
        state["entries"][rel]["inplace"] = True
        save_state(state_path, state)
        print(f"[edit directly; build restores alpha automatically] {rel}")


def load_for_writeback(png: Path, resource: bytes) -> Image.Image:
    """Decode an active clear PNG without rewriting the user's editing file."""
    image = load_image(png)
    # PNGs are under imageedit/gim/<resource path>; locate the associated root.
    root = next((p for p in png.resolve().parents if p.name == "imageedit"), None)
    if root is None:
        return image
    workspace = root.parent / "imagealpha"
    state_path = workspace / ".state/session.json"
    if not state_path.is_file():
        return image
    state = json.loads(state_path.read_text(encoding="utf-8"))
    rel = png.resolve().relative_to(root).as_posix()
    entry = state["entries"].get(rel)
    if entry is None or not entry.get("inplace"):
        return image
    snapshot = safe_path(workspace / ".state/original", rel)
    if sha(snapshot.read_bytes()) != entry["original_sha256"]:
        raise ValueError(f"original alpha snapshot changed: {rel}")
    parsed = gim_7w.parse(resource)
    if parsed is None:
        raise ValueError(f"invalid source GIM: {rel}")
    restored, _ = restore(load_image(snapshot), image, entry["peak"],
                          rendered_palette(resource, parsed))
    return restored


def apply(args) -> None:
    workspace = args.workspace.resolve()
    state_path = workspace / ".state/session.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    edit_root = Path(state["edit_root"]).resolve()
    unpacked = Path(state["unpacked"]).resolve()
    plans = []
    for rel, entry in state["entries"].items():
        if entry.get("inplace"):
            continue
        if args.filter and args.filter not in rel:
            continue
        edited_path = safe_path(workspace, rel)
        if not edited_path.is_file():
            continue
        edited = load_image(edited_path)
        digest = pixel_sha(edited)
        if digest == entry["preview_sha256"]:
            continue
        target = safe_path(edit_root, rel)
        if not target.is_file() or sha(target.read_bytes()) != entry["target_sha256"]:
            raise ValueError(f"imageedit changed since prepare/last apply: {rel}")
        snapshot = safe_path(workspace / ".state/original", rel)
        if sha(snapshot.read_bytes()) != entry["original_sha256"]:
            raise ValueError(f"original snapshot changed: {rel}")
        data = safe_path(unpacked, entry["src_rel"]).read_bytes()[entry["offset"]:]
        if sha(data) != entry["resource_sha256"]:
            raise ValueError(f"source GIM changed since prepare: {rel}")
        original = load_image(snapshot)
        restored, changed = restore(original, edited, entry["peak"],
                                    rendered_palette(data, gim_7w.parse(data)))
        # Exercise the same writer used by the build, without touching sources.
        rebuilt = gim_7w.rebuild(data, restored)
        if len(rebuilt) != len(data):
            raise ValueError(f"GIM size changed: {rel}")
        # An editor may rewrite PNG metadata without changing any pixels.
        plans.append((rel, target, restored, digest, changed))
    for rel, target, restored, digest, changed in plans:
        print(f"[{'would apply' if args.dry_run else 'apply'}] {rel}: {changed} pixels")
        if args.dry_run:
            continue
        entry = state["entries"][rel]
        if pixel_sha(restored) != pixel_sha(load_image(target)):
            temporary = target.with_suffix(".alpha-tmp")
            restored.save(temporary, format="PNG")
            temporary.replace(target)
        entry["target_sha256"] = sha(target.read_bytes())
        entry["preview_sha256"] = digest
        save_state(state_path, state)
    print(f"{'dry-run' if args.dry_run else 'apply'}: {len(plans)} changed image(s)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(required=True)
    command = commands.add_parser("prepare", help="create brighter editing copies")
    command.add_argument("--workspace", type=Path, default=Path("work/imagealpha"))
    command.add_argument("--edit-root", type=Path, default=Path("work/imageedit"))
    command.add_argument("--export-root", type=Path, default=Path("work/image"))
    command.add_argument("--manifest", type=Path, default=Path("work/imageedit/manifest.csv"))
    command.add_argument("--unpacked", type=Path, default=Path("unpacked/CDIMAGE_FULL"))
    command.add_argument("--filter", help="only process matching PNG paths")
    command.add_argument("--include-edited", action="store_true",
                         help="also prepare copies of images differing from the export")
    command.add_argument("--copies-only", action="store_true",
                         help="keep clear PNGs separate instead of editing in imageedit")
    command.set_defaults(func=prepare)
    command = commands.add_parser("apply", help="merge edited copies into imageedit")
    command.add_argument("--workspace", type=Path, default=Path("work/imagealpha"))
    command.add_argument("--filter", help="only process matching PNG paths")
    command.add_argument("--dry-run", action="store_true")
    command.set_defaults(func=apply)
    args = parser.parse_args()
    try:
        args.func(args)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(2, f"error: {error}\n")


if __name__ == "__main__":
    main()
