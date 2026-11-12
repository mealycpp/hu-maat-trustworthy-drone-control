"""Emergency-landing latch: once 'land now' starts, a later stop/hover/take-off cannot cancel it."""
import unittest

import test_missions  # noqa: F401  (installs the gz stubs before importing the controller)
from test_v9 import fleet
from control_server_hu import NAMES


class Latch(unittest.TestCase):
    def setUp(self):
        self.f = fleet('HOVER')

    def test_hover_after_emergency_land_is_ignored(self):
        self.f.command('emergency_land')
        self.assertTrue(all(self.f.dr[n].mode == 'LANDING' for n in NAMES))
        r = self.f.command('emergency_hover')
        self.assertEqual(r.get('targets'), [])
        self.assertIn('latched', r)
        self.assertTrue(all(self.f.dr[n].mode == 'LANDING' for n in NAMES))

    def test_takeoff_and_move_also_ignored_while_landing(self):
        self.f.command('emergency_land')
        r = self.f.command('takeoff', ['Horus'])
        self.assertEqual(r.get('targets'), [])
        self.assertEqual(self.f.dr['Horus'].mode, 'LANDING')

    def test_ordinary_land_is_not_latched(self):
        self.f.command('land', ['Ra'])
        self.f.command('hover', ['Ra'])
        self.assertNotEqual(self.f.dr['Ra'].mode, 'LANDING')     # a normal landing can still be stopped

    def test_latch_clears_after_touchdown(self):
        self.f.command('emergency_land')
        for n in NAMES:
            self.f.dr[n].mode = 'GROUNDED'; self.f.dr[n].z = 0.05
        self.f.command('hover', ['Horus'])                     # clears the latch (no longer LANDING)
        self.assertFalse(any(self.f.dr[n].land_latched for n in NAMES))


if __name__ == '__main__':
    unittest.main()
