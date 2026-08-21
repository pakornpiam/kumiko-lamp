"""Focused parity and printability tests for the Modern filled Seigaiha tile."""

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import kumiko_lamp as lamp  # noqa: E402
from tools import svg2pattern  # noqa: E402


class ModernSeigaihaTests(unittest.TestCase):
    def test_source_and_baked_tile_contract(self):
        loops = svg2pattern.convert(
            ROOT / "reference" / "seigaiha-blue.svg",
            first_path=True, preserve_svg_winding=True)
        self.assertEqual(len(loops), 4)
        self.assertEqual([len(loop) for loop in loops], [252, 30, 52, 29])
        self.assertEqual(len(lamp._SEIGAIHA_TILE_LOOPS), 4)
        self.assertEqual(sum(len(loop) // 2
                             for loop in lamp._SEIGAIHA_TILE_LOOPS), 363)
        areas = [lamp._flat_loop_area(loop)
                 for loop in lamp._SEIGAIHA_TILE_LOOPS]
        self.assertEqual([area > 0 for area in areas],
                         [True, False, False, True])
        self.assertAlmostEqual(lamp._SEIGAIHA_TILE_LOOPS[0][0], -0.4171)
        self.assertIn(0.08111, lamp._SEIGAIHA_TILE_LOOPS[1])
        self.assertIn(-0.25, lamp._SEIGAIHA_TILE_LOOPS[3])
        # At the largest supported test pitch, the converter's 0.001 source
        # tolerance is 0.09 mm physically: below the 0.1 mm artwork budget.
        self.assertLessEqual(0.001 * (2 * 45), 0.1)

    def test_repeat_and_signed_region(self):
        contours = lamp.modern_seigaiha_contours(56, 28, 28)
        self.assertEqual(len(contours), 100)
        for a, b in zip(contours[0], contours[4]):
            self.assertAlmostEqual(b[0] - a[0], 56)
            self.assertAlmostEqual(b[1] - a[1], 0)
        expected = [0.056, -1.4770538373, -0.3586158958, -0.5541290057]
        samples = [(0, 0), (3.2, 5.1), (-17.9, 11.7), (9, -6)]
        for (u, z), want in zip(samples, expected):
            got = lamp.modern_seigaiha_signed_distance(u, z, 28)
            self.assertAlmostEqual(got, want, places=8)
            self.assertAlmostEqual(
                lamp.modern_seigaiha_signed_distance(u + 56, z + 28, 28),
                got, places=8)
        self.assertFalse(lamp.modern_point_in_seigaiha(0, 0, 28, 1.6))
        self.assertTrue(lamp.modern_point_in_seigaiha(0, 0, 28, 3.2))
        self.assertFalse(lamp.modern_point_in_seigaiha(-17.9, 11.7, 28, 0.8))

    def test_support_free_guard_over_parameter_matrix(self):
        for grid in (12, 20, 28, 36, 45):
            for slat_w in (0.8, 1.6, 2.4, 3.2):
                with self.subTest(grid=grid, slat_w=slat_w):
                    self.assertLessEqual(
                        lamp.modern_seigaiha_bridge_span(grid, slat_w), 9.0)
                    params = lamp.Params(lantern_style="modern", grid=grid,
                                         slat_w=slat_w)
                    self.assertFalse(lamp.check_modern_fits(params, "seigaiha"))
        unsafe = lamp.Params(lantern_style="modern", grid=140, slat_w=0.8)
        self.assertGreater(lamp.modern_seigaiha_bridge_span(140, 0.8), 9.0)
        self.assertTrue(any("support-free limit" in issue for issue in
                            lamp.check_modern_fits(unsafe, "seigaiha")))


if __name__ == "__main__":
    unittest.main()
