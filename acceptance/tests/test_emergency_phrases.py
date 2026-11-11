"""Emergency phrases must work with a fleet address / politeness; bare 'land' must not be an emergency."""
import unittest
from hu_live_server import emergency_key, EMERGENCY_HOVER, EMERGENCY_LAND


class TestEmergency(unittest.TestCase):
    def test_hover_variants(self):
        for s in ('khalas', 'All, khalas', 'all khalas!', 'everyone, stop', 'Everyone stop please', 'Everyone stop now',
                  'drones: abort please', 'khalas now',
                  'stop', 'all stop', 'STOP!', 'hey everyone, khalas'):
            with self.subTest(s=s):
                self.assertIn(emergency_key(s), EMERGENCY_HOVER)

    def test_land_variants(self):
        for s in ('land now', 'All, land now!', 'everyone down', 'everybody down please', 'emergency land', 'yalla land'):
            with self.subTest(s=s):
                self.assertIn(emergency_key(s), EMERGENCY_LAND)

    def test_not_emergency(self):
        for s in ('land', 'all land', 'Horus land', 'all take off', 'stop it Horus right there', 'khalas take off'):
            with self.subTest(s=s):
                self.assertIsNone(emergency_key(s))


if __name__ == '__main__':
    unittest.main()
