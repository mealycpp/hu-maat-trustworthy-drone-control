"""HU named-address safety guard (hu_live_server.stated_targets): excluded aircraft can never be approved."""
import unittest

import hu_live_server as h

ALL = ['Horus', 'Ra', 'Thoth', 'Anubis', 'Osiris']


class Guard(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(h.stated_targets('Horus, take off'), ['Horus'])
        self.assertEqual(h.stated_targets('Ra and Thoth, take off'), ['Ra', 'Thoth'])
        self.assertEqual(h.stated_targets('Everyone, land'), ALL)
        self.assertEqual(h.stated_targets('All, take off'), ALL)
        self.assertIsNone(h.stated_targets('take off'))

    def test_single_exclusion(self):
        self.assertEqual(h.stated_targets('Everyone except Ra, land'), ['Horus', 'Thoth', 'Anubis', 'Osiris'])

    def test_multi_exclusion_never_expects_the_excluded(self):
        for s in ('All except Ra and Thoth, take off', 'Everyone except Ra and Thoth, land',
                  'All drones except Ra, Thoth, land', 'all except ra & thoth hover'):
            self.assertEqual(h.stated_targets(s), ['Horus', 'Anubis', 'Osiris'], s)

    def test_unresolvable_forms_force_refusal(self):
        self.assertEqual(h.stated_targets('All except the red one, land'), [])     # unparseable exclusion
        self.assertEqual(h.stated_targets('Horus except Ra, land'), [])            # exclusion without ALL
        self.assertEqual(h.stated_targets('Horus and all except Ra, take off'), [])  # name outside exclusion
        self.assertEqual(h.stated_targets('All and Ra, take off'), [])             # ambiguous
        self.assertEqual(h.stated_targets('All except Horus, Ra, Thoth, Anubis and Osiris, land'), [])

    def test_guard_rejects_hu_choosing_excluded(self):
        exp = h.stated_targets('All except Ra and Thoth, take off')
        self.assertNotEqual(set(exp), {'Ra', 'Thoth'})


if __name__ == '__main__':
    unittest.main()
