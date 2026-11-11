"""Team-strategy policy (acceptance/strategist.py): planning numbers are real and vetoes are consistent."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # acceptance/
import strategist as st  # noqa: E402


def fleet_at(shape):
    pts = st.fp.slots(shape, 5)
    return {n: {'name': n, 'x': x, 'y': y, 'z': st.fp.ALT} for n, (x, y) in zip(st.NAMES, pts)}


class Strategy(unittest.TestCase):
    def test_same_shape_costs_nothing(self):
        p = st.plan('circle', fleet_at('circle'))
        self.assertAlmostEqual(p['longest'], 0.0, places=6)
        self.assertGreater(p['clear'], 1.5)

    def test_numbers_are_consistent(self):
        p = st.plan('wedge', fleet_at('v'))
        self.assertEqual(set(p['legs']), set(st.NAMES))
        self.assertAlmostEqual(p['longest'], max(p['legs'].values()))
        self.assertAlmostEqual(p['total'], sum(p['legs'].values()))
        self.assertIn(p['pair'][0], st.NAMES)

    def test_tight_transition_is_below_veto(self):
        # line -> diamond plans only ~0.96 m: the safety agent must veto it (< 1.0 m)
        p = st.plan('diamond', fleet_at('line'))
        self.assertLess(p['clear'], st.VETO_M)

    def test_every_phrase_parses_as_a_formation(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'festival'))
        import mission_intents as mi
        for shape, phrase in st.PHRASE.items():
            r = mi.parse('All form ' + phrase)
            self.assertEqual(r.get('action'), 'formation', phrase)
            self.assertEqual(r['params']['shape'], shape, phrase)


if __name__ == '__main__':
    unittest.main()
