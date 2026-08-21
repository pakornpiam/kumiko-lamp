import math
from pathlib import Path
import sys
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kumiko_lamp import (
    MODERN_DIFFUSER_CLEARANCE,
    MODERN_DIFFUSER_MAX_T,
    MODERN_DIFFUSER_MIN_T,
    MODERN_RING_H,
    MODERN_THREAD_ENGAGEMENT,
    Params,
    build_modern_diffuser,
    check_fits,
    modern_diffuser_for_print,
    place_modern_parts,
)


class ModernDiffuserTests(unittest.TestCase):
    def setUp(self):
        self.params = Params(
            lantern_style="modern", size=100.0, height=218.0, plate_t=1.2)

    def test_derived_fit_and_guards(self):
        p = self.params
        self.assertAlmostEqual(MODERN_DIFFUSER_CLEARANCE, 0.4)
        self.assertAlmostEqual(MODERN_DIFFUSER_MIN_T, 1.0)
        self.assertAlmostEqual(MODERN_DIFFUSER_MAX_T, 4.0)
        self.assertAlmostEqual(p.modern_diffuser_outer_r, 45.6)
        self.assertAlmostEqual(p.modern_diffuser_inner_r, 44.4)
        self.assertEqual(check_fits(p, "asanoha"), [])
        self.assertTrue(any("1.0 mm minimum" in issue for issue in check_fits(
            Params(lantern_style="modern", size=100, height=218, plate_t=0.8),
            "asanoha")))
        self.assertEqual(check_fits(
            Params(lantern_style="modern", size=100, height=218, plate_t=1.0),
            "asanoha"), [])
        self.assertEqual(check_fits(
            Params(lantern_style="modern", size=100, height=218, plate_t=4.0),
            "asanoha"), [])
        self.assertTrue(any("4.0 mm maximum" in issue for issue in check_fits(
            Params(lantern_style="modern", size=100, height=218, plate_t=4.2),
            "asanoha")))

    @staticmethod
    def _flat_face_min_radius(mesh, z):
        flat = mesh.triangles[np.all(np.isclose(
            mesh.triangles[:, :, 2], z, atol=1e-6), axis=1)]
        return np.min(np.linalg.norm(flat[:, :, :2], axis=2))

    def test_cup_is_closed_top_and_support_free_lid_down(self):
        for thickness in (1.0, 1.2, 4.0):
            p = Params(lantern_style="modern", size=100.0, height=218.0,
                       plate_t=thickness)
            mesh = build_modern_diffuser(p)
            self.assertTrue(mesh.is_watertight)
            self.assertTrue(mesh.is_winding_consistent)
            self.assertEqual(mesh.body_count, 1)
            np.testing.assert_allclose(
                mesh.extents, [91.2, 91.2, 208.0], atol=1e-6)
            analytic = math.pi * (
                p.modern_diffuser_outer_r ** 2 * p.modern_diffuser_h -
                p.modern_diffuser_inner_r ** 2 *
                (p.modern_diffuser_h - p.plate_t))
            self.assertLess(abs(mesh.volume - analytic) / analytic, 0.001)
            # Assembly geometry is open through the lower centre, then closed
            # by a full disk and matching inside ceiling at the upper end.
            self.assertGreater(self._flat_face_min_radius(mesh, 0.0),
                               p.modern_diffuser_inner_r * 0.99)
            self.assertAlmostEqual(self._flat_face_min_radius(
                mesh, p.modern_diffuser_h - p.plate_t), 0.0, places=5)
            self.assertAlmostEqual(self._flat_face_min_radius(
                mesh, p.modern_diffuser_h), 0.0, places=5)

            printed = modern_diffuser_for_print(p, mesh)
            self.assertAlmostEqual(self._flat_face_min_radius(printed, 0.0),
                                   0.0, places=5)
            self.assertAlmostEqual(self._flat_face_min_radius(
                printed, p.plate_t), 0.0, places=5)
            self.assertGreater(self._flat_face_min_radius(
                printed, p.modern_diffuser_h),
                p.modern_diffuser_inner_r * 0.99)

    def test_assembly_places_cup_lid_level_with_shade_top(self):
        p = self.params
        diffuser = build_modern_diffuser(p)
        # Placement only copies its inputs, so the same valid cup can stand
        # in for unrelated parts without paying for the full CSG shade/base.
        parts = place_modern_parts(p, diffuser, diffuser, diffuser, diffuser)
        placed = parts["diffuser_plate"]
        z0 = p.modern_base_h - MODERN_THREAD_ENGAGEMENT + MODERN_RING_H
        self.assertAlmostEqual(placed.bounds[0, 2], z0)
        shade_top = p.modern_base_h - MODERN_THREAD_ENGAGEMENT + p.height
        self.assertAlmostEqual(placed.bounds[1, 2], shade_top)
        self.assertAlmostEqual(self._flat_face_min_radius(
            placed, shade_top), 0.0, places=5)


if __name__ == "__main__":
    unittest.main()
