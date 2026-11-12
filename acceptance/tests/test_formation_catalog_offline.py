"""Offline checks for the extended formation catalogue (no Gazebo, nothing flies).

Catalogue (festival/formation_catalog.json) <-> planner geometry (formation_planner.slots)
<-> spoken phrases (strategist.PHRASE parsed by mission_intents.parse) <-> 3D height profiles.
Works both in the repo and in the temp copy made by acceptance/run_offline.sh (which copies
only festival/*.py and the tests, so the catalog and strategist are located in the repo).
"""
import itertools
import json
import math
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _find(rel):
    for root in (HERE.parents[1] if len(HERE.parents) > 1 else HERE, Path.home() / 'hu-telepresence-gazebo'):
        if (root / rel).exists():
            return root / rel
    raise FileNotFoundError(rel)


if not (HERE / 'formation_planner.py').exists():
    sys.path.insert(0, str(_find('festival')))
import formation_planner as fp  # noqa: E402
import mission_intents as mi  # noqa: E402
sys.path.insert(0, str(_find('acceptance/strategist.py').parent))
import strategist as st  # noqa: E402

CATALOG = json.loads(_find('festival/formation_catalog.json').read_text())
ORIGINAL = {'line', 'v', 'circle', 'grid', 'diamond', 'wedge', 'inverted_v', 'arrow', 'triangle', 'cross', 'x',
            'square', 'perimeter', 'two_rows', 'staggered', 'zigzag', 'left_echelon', 'right_echelon', 'column',
            'arc', 'fan', 't', 'l', 'pentagon', 'relay_chain'}


class CatalogueGeometry(unittest.TestCase):
    def test_catalogue_size(self):
        self.assertGreaterEqual(len(CATALOG), 95)
        self.assertGreaterEqual(len([k for k, v in CATALOG.items() if not v.get('alias_of')]), 95)

    def test_every_shape_builds_five_safe_slots(self):
        for shape in CATALOG:
            pts = fp.slots(shape, 5)
            self.assertEqual(len(pts), 5, shape)
            for x, y in pts:
                self.assertLessEqual(abs(x), fp.BOUND, shape)
                self.assertLessEqual(abs(y), fp.BOUND, shape)
            self.assertAlmostEqual(sum(x for x, _ in pts), 0.0, places=6, msg=shape)
            self.assertAlmostEqual(sum(y for _, y in pts), 0.0, places=6, msg=shape)
            for a, b in itertools.combinations(pts, 2):
                self.assertGreaterEqual(math.dist(a, b), fp.MIN_SEP, shape)

    def test_new_shapes_are_clear_and_work_for_subsets(self):
        for shape in set(CATALOG) - ORIGINAL:
            pts = fp.slots(shape, 5)
            self.assertGreaterEqual(min(math.dist(a, b) for a, b in itertools.combinations(pts, 2)), 1.0 - 1e-9, shape)
            for n in range(2, 5):
                self.assertEqual(len(fp.slots(shape, n)), n, shape)

    def test_status_is_honest(self):
        for shape, entry in CATALOG.items():
            if shape in ORIGINAL:
                self.assertEqual(entry['status'], 'VALIDATED', shape)
            else:
                # new shapes: unflown (OFFLINE_CHECKED, no evidence) or flown live (VALIDATED/EXPERIMENTAL with evidence)
                self.assertIn(entry['status'], ('OFFLINE_CHECKED', 'VALIDATED', 'EXPERIMENTAL'), shape)
                if entry['status'] == 'OFFLINE_CHECKED':
                    self.assertIsNone(entry.get('evidence'), shape)
                else:
                    self.assertTrue(str(entry.get('evidence') or '').startswith('formation_catalog_2026'), shape)
                self.assertIsNone(entry.get('alias_of'), shape)

    def test_unknown_shape_still_refused(self):
        with self.assertRaises(ValueError):
            fp.slots('hexagram', 5)
        with self.assertRaises(ValueError):
            fp.slots('letter_w', 6)


class SpokenPhrases(unittest.TestCase):
    def test_every_strategist_phrase_parses_to_its_shape(self):
        for shape, phrase in st.PHRASE.items():
            r = mi.parse('All form ' + phrase)
            self.assertEqual(r.get('action'), 'formation', phrase)
            self.assertEqual(r['params']['shape'], shape, phrase)

    def test_every_catalogued_shape_has_a_phrase(self):
        for shape, entry in CATALOG.items():
            if not entry.get('alias_of'):
                self.assertIn(shape, st.PHRASE, shape)

    def test_every_alias_targets_a_buildable_shape(self):
        for alias, kind in mi.SHAPES.items():
            self.assertEqual(len(fp.slots(kind, 5)), 5, alias)
            self.assertEqual(mi.parse('All form a ' + alias)['params']['shape'], kind, alias)

    def test_original_short_aliases_unchanged(self):
        for phrase, kind in (('a V', 'v'), ('an X', 'x'), ('a T', 't'), ('an L', 'l'), ('a line', 'line'),
                             ('a circle', 'circle'), ('a straight line', 'line'), ('a staggered line', 'staggered'),
                             ('a cross', 'cross'), ('a pentagon', 'pentagon'), ('a V formation', 'v'),
                             ('an inverted V', 'inverted_v'), ('a T shape', 't')):
            self.assertEqual(mi.parse('All form ' + phrase)['params']['shape'], kind, phrase)

    def test_new_letters_need_explicit_wording(self):
        self.assertEqual(mi.parse('All form the letter W')['params']['shape'], 'letter_w')
        self.assertEqual(mi.parse('All form a W shape')['params']['shape'], 'letter_w')
        self.assertIn('error', mi.parse('All form a W'))   # a bare new letter is not guessed


class HeightProfiles(unittest.TestCase):
    def test_profiles_reference_valid_bases_and_stay_in_band(self):
        self.assertGreaterEqual(len(fp.HEIGHT_PROFILES), 10)
        for name, (base, dz) in fp.HEIGHT_PROFILES.items():
            self.assertNotIn(name, CATALOG, name)        # 3D profiles are not 2D formations
            self.assertEqual(len(fp.slots(base, 5)), 5, name)
            for idx, d in dz.items():
                self.assertIn(idx, range(5), name)
                self.assertLessEqual(abs(d), 0.8, name)
                self.assertTrue(1.1 <= fp.ALT + d <= 2.7, name)


if __name__ == '__main__':
    unittest.main()
