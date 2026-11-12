"""Exclusion targeting: every excluded name must really be excluded."""
import unittest
import mission_intents as mi


class Exclusions(unittest.TestCase):
    def t(self, s):
        return mi.addresses(' '.join(s.lower().split()))

    def test_single(self):
        self.assertEqual(self.t('everyone except ra, land')[0], ['Horus', 'Thoth', 'Anubis', 'Osiris'])

    def test_two_names(self):
        self.assertEqual(self.t('all except ra and thoth land')[0], ['Horus', 'Anubis', 'Osiris'])

    def test_list(self):
        self.assertEqual(self.t('everyone except ra, thoth and osiris hover')[0], ['Horus', 'Anubis'])

    def test_name_outside_exclusion_refused(self):
        tg, err = self.t('all except ra, horus take off')
        self.assertEqual(tg, ['Thoth', 'Anubis', 'Osiris'])   # 'ra, horus' is one exclusion list
        tg, err = self.t('horus and all except ra take off')
        self.assertEqual(tg, []); self.assertIn('Do not combine', err)

    def test_everyone_excluded_refused(self):
        tg, err = self.t('all except horus, ra, thoth, anubis and osiris land')
        self.assertEqual(tg, []); self.assertIn('no aircraft', err)

    def test_parse_formation_with_two_exclusions(self):
        r = mi.parse('All except Ra and Thoth, form a triangle')
        self.assertEqual(r['targets'], ['Horus', 'Anubis', 'Osiris'])


if __name__ == '__main__':
    unittest.main()
