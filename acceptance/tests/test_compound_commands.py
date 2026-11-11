"""Compound commands joined by "and" must be refused, never silently reduced to one action."""
import unittest

import mission_intents as mi


class CompoundCommands(unittest.TestCase):
    def test_refused(self):
        for s in ('Horus, take off and move forward 1 meter', 'Ra, take off and turn left 90 degrees',
                  'Everyone, take off and form a circle', 'Thoth, move up 1 meter and hover',
                  'Horus, take off then land'):
            r = mi.parse(s)
            self.assertIsNotNone(r, s)
            self.assertIn('Compound', r.get('error', ''), s)

    def test_single_actions_unaffected(self):
        self.assertIsNone(mi.parse('Horus, hold position'))          # HU primitive route
        self.assertIsNone(mi.parse('Everyone except Anubis, hover'))
        self.assertEqual(mi.parse('Horus, move forward 1 meter')['action'], 'move')
        self.assertEqual(mi.parse('Ra, turn left 90 degrees')['action'], 'turn')
        self.assertEqual(mi.parse('All form a circle')['action'], 'formation')


if __name__ == '__main__':
    unittest.main()
