"""Topology checks for Sakura and the imported Sakura V2 artwork."""

import math
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import trimesh
import manifold3d

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import kumiko_lamp as lamp  # noqa: E402
from tools import svg2pattern  # noqa: E402


class SakuraPatternTests(unittest.TestCase):
    @staticmethod
    def _round_trip(mesh, directory, name):
        path = Path(directory) / f"{name}.stl"
        mesh.export(path)
        return trimesh.load(path)

    def assert_valid_part(self, mesh, params):
        self.assertTrue(mesh.is_watertight)
        self.assertTrue(mesh.is_winding_consistent)
        self.assertGreater(mesh.volume, 0)
        self.assertEqual(mesh.body_count, 1)
        self.assertTrue(np.all(np.sort(mesh.extents)[:2] <=
                               np.sort(params.bed[:2])))
        self.assertLessEqual(mesh.extents[2], params.bed[2])

    def test_registration_and_source_geometry(self):
        for pattern in ("sakura", "sakura_v2"):
            self.assertIn(pattern, lamp.pattern_names())
            self.assertIn(pattern, lamp.kumiko_pattern_names())
        self.assertEqual(len(lamp.pattern_names()), 13)
        self.assertEqual(len(lamp.kumiko_pattern_names()), 13)

        classic = lamp.Params()
        ow = classic.panel_w - 2 * classic.panel_border
        oh = classic.height - 2 * classic.panel_border
        clipped = lamp.clip_rect(
            lamp.pat_sakura(ow, oh, classic.grid),
            -ow / 2, -oh / 2, ow / 2, oh / 2)
        self.assertEqual(len(clipped), 350)
        self.assertTrue(lamp.is_region("sakura_v2"))
        self.assertEqual(len(lamp.SAKURA_V2_TILE_LOOPS), 79)
        self.assertEqual(sum(len(lp) for lp in lamp.SAKURA_V2_TILE_LOOPS), 870)
        self.assertTrue((ROOT / "reference" /
                         "vectorstock_20834246.svg").is_file())

        modern = lamp.Params(lantern_style="modern", size=100, height=218)
        circumference = 2 * math.pi * modern.modern_outer_r
        grow = lamp.MODERN_LATTICE_OVERLAP
        join = min(0.2, modern.slat_w / 4)
        field_w = circumference - modern.slat_w + 2 * join
        wrapped = lamp.clip_rect(
            lamp.pat_sakura(circumference, modern.modern_lattice_h,
                            modern.grid),
            -field_w / 2, -modern.modern_lattice_h / 2 - grow,
            field_w / 2, modern.modern_lattice_h / 2 + grow)
        self.assertEqual(len(wrapped), 924)
        contours = lamp.sakura_v2_contours(
            circumference, modern.modern_lattice_h, modern.grid)
        self.assertGreater(len(contours), 79)
        self.assertEqual(lamp.cap_pattern("sakura_v2"), "kikkou")

    def test_baked_tile_matches_supplied_svg(self):
        source = ROOT / "reference" / "vectorstock_20834246.svg"
        loops = svg2pattern.convert(source, preserve_svg_winding=True)
        source_w, source_h = 566.93, 490.98
        contours = [[(x * source_w, y * source_w) for x, y in loop]
                    for loop in loops]
        tile_w, tile_h = source_w / 4.0, source_h / 2.0
        artwork = manifold3d.CrossSection(
            contours, manifold3d.FillRule.Positive)
        tile = artwork ^ manifold3d.CrossSection.square(
            (tile_w, tile_h), center=True)
        extracted = [[float(v) / tile_w for point in loop for v in point]
                     for loop in tile.to_polygons()]
        self.assertEqual(len(extracted), len(lamp.SAKURA_V2_TILE_LOOPS))
        for actual, baked in zip(extracted, lamp.SAKURA_V2_TILE_LOOPS):
            np.testing.assert_allclose(actual, baked, atol=6e-7, rtol=0)

    def test_classic_panel_and_cap_survive_float32(self):
        params = lamp.Params()
        with tempfile.TemporaryDirectory(prefix="kumiko-sakura-test-") as tmp:
            for pattern in ("sakura", "sakura_v2"):
                with self.subTest(pattern=pattern):
                    panel = self._round_trip(
                        lamp.build_panel(params, pattern), tmp,
                        f"panel_{pattern}")
                    cap = self._round_trip(
                        lamp.build_cap(params, pattern), tmp,
                        f"top_cap_{pattern}")
                    self.assert_valid_part(panel, params)
                    self.assert_valid_part(cap, params)
                    np.testing.assert_allclose(
                        panel.extents, [155.7, 210.0, 4.0], atol=1e-5)
                    np.testing.assert_allclose(
                        cap.extents, [190.0, 190.0, 10.0], atol=1e-5)

    def test_modern_shade_survives_reachable_pitch_and_slat_extremes(self):
        variants = ((28, 1.6), (12, 0.8), (12, 3.2),
                    (45, 0.8), (45, 3.2))
        expected_volume = {"sakura": 95.61, "sakura_v2": 152.24}
        with tempfile.TemporaryDirectory(prefix="kumiko-sakura-test-") as tmp:
            for pattern in ("sakura", "sakura_v2"):
                for grid, slat_w in variants:
                    with self.subTest(pattern=pattern, grid=grid,
                                      slat_w=slat_w):
                        params = lamp.Params(
                            lantern_style="modern", size=100, height=218,
                            grid=grid, slat_w=slat_w)
                        self.assertEqual(lamp.check_fits(params, pattern), [])
                        shade = self._round_trip(
                            lamp.build_modern_shade(params, pattern), tmp,
                            f"modern_shade_{pattern}_{grid}_{slat_w}")
                        self.assert_valid_part(shade, params)
                        np.testing.assert_allclose(
                            shade.extents, [100.0, 100.0, 218.0], atol=1e-5)
                        if grid == 28 and slat_w == 1.6:
                            self.assertAlmostEqual(
                                shade.volume / 1000,
                                expected_volume[pattern], delta=0.02)


if __name__ == "__main__":
    unittest.main()
