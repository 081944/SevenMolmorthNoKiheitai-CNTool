"""Focused regression checks for reversible alpha editing and conflict guards."""
import argparse
import contextlib
import io
from pathlib import Path
import struct
import sys
import tempfile
import unittest

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gim_7w
import image_7w
import image_alpha_7w as alpha


def fixture(kind="8bpp"):
    header = bytearray(0x20)
    header[:4] = b"GIM\0"
    header[0x13] = {"8bpp": 0x13, "4bpp": 0x14, "32bpp": 0}[kind]
    struct.pack_into("<HH", header, 0x1C, 4, 1)
    if kind == "32bpp":
        return bytes(header) + bytes((100, 100, 100, 128)) * 4
    colors = [(255, 255, 255, 0), (255, 255, 255, 32),
              (255, 255, 255, 64), (255, 255, 255, 128)]
    count = 256 if kind == "8bpp" else 16
    palette = colors + [(0, 0, 0, 0)] * (count - 4)
    indices = bytes((0, 1, 2, 3)) if kind == "8bpp" else bytes((0x10, 0x32))
    return bytes(header) + indices + bytes(16) + bytes(c for p in palette for c in p)


class RestoreTests(unittest.TestCase):
    def test_untouched_alpha_and_transparent_rgb_are_exact(self):
        for kind in ("8bpp", "4bpp", "32bpp"):
            data = fixture(kind)
            parsed = gim_7w.parse(data)
            original = gim_7w.render(data, *parsed)
            peak = original.getchannel("A").getextrema()[1]
            restored, changed = alpha.restore(original, alpha.brighten(original, peak),
                                              peak, alpha.rendered_palette(data, parsed))
            self.assertEqual(changed, 0)
            self.assertEqual(restored.tobytes(), original.tobytes())
            self.assertEqual(gim_7w.rebuild(data, restored), data)

    def test_same_rgb_alpha_gradient_maps_to_correct_entry(self):
        for kind in ("8bpp", "4bpp"):
            data = fixture(kind)
            parsed = gim_7w.parse(data)
            original = gim_7w.render(data, *parsed)
            peak = original.getchannel("A").getextrema()[1]
            edited = alpha.brighten(original, peak)
            edited.putpixel((1, 0), (255, 255, 255, 128))
            restored, count = alpha.restore(original, edited, peak,
                                            alpha.rendered_palette(data, parsed))
            self.assertEqual(count, 1)
            self.assertEqual(restored.getpixel((1, 0)), original.getpixel((2, 0)))
            rebuilt = gim_7w.rebuild(data, restored)
            self.assertEqual(gim_7w.render(rebuilt, *parsed).tobytes(), restored.tobytes())
            self.assertEqual(len(rebuilt), len(data))

    def test_new_solid_pixel_and_erasure(self):
        data = fixture()
        parsed = gim_7w.parse(data)
        original = gim_7w.render(data, *parsed)
        edited = alpha.brighten(original, 128)
        edited.putpixel((0, 0), (250, 250, 250, 255))
        edited.putpixel((3, 0), (255, 255, 255, 0))
        restored, count = alpha.restore(original, edited, 128,
                                        alpha.rendered_palette(data, parsed))
        self.assertEqual(count, 2)
        self.assertEqual(restored.getpixel((0, 0)), (255, 255, 255, 128))
        self.assertEqual(restored.getpixel((3, 0))[3], 0)

    def test_wrong_dimensions_fail(self):
        with self.assertRaisesRegex(ValueError, "size mismatch"):
            alpha.restore(Image.new("RGBA", (2, 2)), Image.new("RGBA", (4, 1)), 128, None)


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "alpha"
        self.edits = self.root / "edit"
        self.exports = self.root / "export"
        self.sources = self.root / "sources"
        self.rel = "gim/atlas.png"
        for root in (self.edits, self.exports):
            (root / "gim").mkdir(parents=True)
        self.sources.mkdir()
        self.data = fixture()
        (self.sources / "atlas.arc").write_bytes(self.data)
        image = gim_7w.render(self.data, *gim_7w.parse(self.data))
        image.save(self.edits / self.rel)
        image.save(self.exports / self.rel)
        self.initial = (self.edits / self.rel).read_bytes()
        manifest = self.root / "manifest.csv"
        image_7w.write_manifest([image_7w.Source(
            "gim", "atlas.arc", 0, 4, 1, 0, self.rel)], manifest)
        self.prepare_args = argparse.Namespace(workspace=self.workspace,
            edit_root=self.edits, export_root=self.exports, unpacked=self.sources,
            manifest=manifest, filter=None, include_edited=False)
        self.apply_args = argparse.Namespace(workspace=self.workspace, filter=None, dry_run=False)

    def run_quiet(self, function, args):
        with contextlib.redirect_stdout(io.StringIO()):
            function(args)

    def edit_copy(self):
        image = alpha.load_image(self.workspace / self.rel)
        image.putpixel((1, 0), (255, 255, 255, 255))
        image.save(self.workspace / self.rel)

    def test_protect_existing_translation(self):
        image = alpha.load_image(self.edits / self.rel)
        image.putpixel((1, 0), (100, 100, 100, 128))
        image.save(self.edits / self.rel)
        saved = (self.edits / self.rel).read_bytes()
        self.run_quiet(alpha.prepare, self.prepare_args)
        self.assertFalse((self.workspace / self.rel).exists())
        self.assertEqual((self.edits / self.rel).read_bytes(), saved)

    def test_noop_dry_run_apply_repeat_and_revert(self):
        self.run_quiet(alpha.prepare, self.prepare_args)
        self.run_quiet(alpha.apply, self.apply_args)
        self.assertEqual((self.edits / self.rel).read_bytes(), self.initial)
        self.edit_copy()
        self.run_quiet(alpha.prepare, self.prepare_args)  # Never overwrite work.
        self.apply_args.dry_run = True
        self.run_quiet(alpha.apply, self.apply_args)
        self.assertEqual((self.edits / self.rel).read_bytes(), self.initial)
        self.apply_args.dry_run = False
        self.run_quiet(alpha.apply, self.apply_args)
        after = (self.edits / self.rel).read_bytes()
        self.assertNotEqual(after, self.initial)
        self.assertEqual((self.sources / "atlas.arc").read_bytes(), self.data)
        self.run_quiet(alpha.apply, self.apply_args)
        self.assertEqual((self.edits / self.rel).read_bytes(), after)
        original = alpha.load_image(self.workspace / ".state/original" / self.rel)
        alpha.brighten(original, 128).save(self.workspace / self.rel)
        self.run_quiet(alpha.apply, self.apply_args)
        self.assertEqual(alpha.load_image(self.edits / self.rel).tobytes(), original.tobytes())

    def test_source_target_and_snapshot_conflicts(self):
        self.run_quiet(alpha.prepare, self.prepare_args)
        self.edit_copy()
        for path, error in [
            (self.edits / self.rel, "imageedit changed"),
            (self.sources / "atlas.arc", "source GIM changed"),
            (self.workspace / ".state/original" / self.rel, "snapshot changed"),
        ]:
            before = path.read_bytes()
            path.write_bytes(before + b"changed")
            with self.assertRaisesRegex(ValueError, error):
                self.run_quiet(alpha.apply, self.apply_args)
            path.write_bytes(before)
        self.assertEqual((self.edits / self.rel).read_bytes(), self.initial)

    def test_nested_output_and_path_traversal_rejected(self):
        self.prepare_args.workspace = self.edits / "unsafe"
        with self.assertRaisesRegex(ValueError, "must be separate"):
            alpha.prepare(self.prepare_args)
        with self.assertRaisesRegex(ValueError, "outside"):
            alpha.safe_path(self.root, "../outside.png")

    def test_direct_edit_builds_without_apply_and_repeated_build_keeps_png(self):
        # Use the actual standard sibling directory layout used by the writer.
        direct_root = self.root / "direct"
        edits = direct_root / "imageedit"
        workspace = direct_root / "imagealpha"
        (edits / "gim").mkdir(parents=True)
        (edits / self.rel).write_bytes(self.initial)
        self.prepare_args.edit_root = edits
        self.prepare_args.workspace = workspace
        self.prepare_args.copies_only = False
        self.run_quiet(alpha.prepare, self.prepare_args)
        shown = alpha.load_image(edits / self.rel)
        self.assertEqual(shown.getchannel("A").getextrema()[1], 255)
        baseline = (edits / self.rel).read_bytes()
        image_7w._gim_rebuild(self.sources, "atlas.arc", 0, edits / self.rel, quiet=True)
        self.assertEqual((self.sources / "atlas.arc").read_bytes(), self.data)
        self.assertEqual((edits / self.rel).read_bytes(), baseline)
        shown.putpixel((1, 0), (255, 255, 255, 255))
        shown.save(edits / self.rel)
        saved = (edits / self.rel).read_bytes()
        for _ in range(2):
            image_7w._gim_rebuild(self.sources, "atlas.arc", 0, edits / self.rel, quiet=True)
            actual = gim_7w.render((self.sources / "atlas.arc").read_bytes(), 4, 1, "8bpp")
            self.assertEqual(actual.getpixel((1, 0)), (255, 255, 255, 128))
            self.assertEqual((edits / self.rel).read_bytes(), saved)
        # Continuing to edit after the source has been rebuilt is supported.
        shown.putpixel((2, 0), (255, 255, 255, 64))
        shown.save(edits / self.rel)
        image_7w._gim_rebuild(self.sources, "atlas.arc", 0, edits / self.rel, quiet=True)
        actual = gim_7w.render((self.sources / "atlas.arc").read_bytes(), 4, 1, "8bpp")
        self.assertEqual(actual.getpixel((2, 0)), (255, 255, 255, 32))


if __name__ == "__main__":
    unittest.main()
