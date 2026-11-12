#!/usr/bin/env python3
"""Offline tests for the LED body-language rules in acceptance/light_show.py (no Gazebo needed)."""
import os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import light_show as ls  # noqa: E402


def drone(name, mode='READY', x=0.0, y=0.0, z=0.05, target=None, fault=False, telemetry=True):
    return {'name': name, 'mode': mode, 'x': x, 'y': y, 'z': z,
            'target': list(target) if target else [x, y, z], 'fault': fault, 'telemetry': telemetry}


def parked_fleet():
    return [drone('Horus', x=-2.2, y=-1.5), drone('Ra', x=0, y=-1.5), drone('Thoth', x=2.2, y=-1.5),
            drone('Anubis', x=-1.1, y=1.1), drone('Osiris', x=1.1, y=1.1)]


def rule(d, fleet, now=0.0, **flags):
    return ls.decide(d, fleet, now, flags)


class ModeColours(unittest.TestCase):
    def test_imports_without_gz(self):
        self.assertNotIn('gz.transport13', sys.modules)

    def test_parked_dim_white(self):
        f = parked_fleet()
        for d in f:
            self.assertEqual(rule(d, f), ('parked', ls.DIM_WHITE))
        d = drone('Ra', mode='GROUNDED', x=0, y=-1.5)
        self.assertEqual(rule(d, [d]).rule, 'parked')

    def test_queued_slow_amber_blink(self):
        d = drone('Ra', mode='QUEUED')
        on = rule(d, [d], now=0.1)
        off = rule(d, [d], now=ls.QUEUED_PERIOD / 2 + 0.1)
        self.assertEqual(on, ('queued', ls.AMBER))
        self.assertEqual(off.rule, 'queued')
        self.assertNotEqual(off.rgb, ls.AMBER)

    def test_takeoff_landing_cyan(self):
        for m in ('TAKEOFF', 'LANDING'):
            d = drone('Ra', mode=m)
            self.assertEqual(rule(d, [d]).rgb, ls.CYAN)

    def test_moving_vs_set_threshold(self):
        for m in ('MOVE', 'TURN', 'SPIN', 'FORMATION'):
            far = drone('Ra', mode=m, z=1.0, target=(0.5, 0, 1.0))
            near = drone('Ra', mode=m, z=1.0, target=(0.05, 0, 1.0))
            self.assertEqual(rule(far, [far]), ('moving', ls.AMBER), m)
            self.assertEqual(rule(near, [near]), ('set', ls.GREEN), m)

    def test_hover_is_set(self):
        d = drone('Ra', mode='HOVER', z=1.0)
        self.assertEqual(rule(d, [d]), ('set', ls.GREEN))

    def test_unknown_mode_safe_default(self):
        d = {'name': 'Ra', 'mode': None}
        self.assertEqual(rule(d, [d]).rule, 'parked')


class Priorities(unittest.TestCase):
    def test_fault_blinks_red(self):
        for d in (drone('Ra', mode='FAULT'), drone('Ra', fault=True), drone('Ra', telemetry=False)):
            self.assertEqual(rule(d, [d], now=0.1), ('fault', ls.RED))
            self.assertNotEqual(rule(d, [d], now=0.6).rgb, ls.RED)

    def test_tight_red(self):
        a = drone('Ra', mode='HOVER', x=0, z=1)
        b = drone('Thoth', mode='HOVER', x=0.8, z=1)
        self.assertEqual(rule(a, [a, b]), ('tight', ls.RED))
        b['x'] = 1.2
        self.assertEqual(rule(a, [a, b]).rule, 'set')

    def test_parked_spacing_is_not_tight(self):
        f = parked_fleet()
        self.assertTrue(all(rule(d, f).rule == 'parked' for d in f))

    def test_fault_and_tight_beat_talking_and_goal(self):
        a = drone('Ra', mode='HOVER', x=0, z=1)
        b = drone('Thoth', mode='HOVER', x=0.5, z=1)
        self.assertEqual(rule(a, [a, b], now=1, talk_until=2, goal_t0=1, index=0, n=5).rule, 'tight')
        a['fault'] = True
        self.assertEqual(rule(a, [a, b], now=1, talk_until=2, goal_t0=1).rule, 'fault')

    def test_talking_flash_then_back(self):
        d = drone('Ra', mode='MOVE', z=1, target=(1, 0, 1))
        self.assertEqual(rule(d, [d], now=10, talk_until=10.6), ('talking', ls.BLUE))
        self.assertEqual(rule(d, [d], now=10.7, talk_until=10.6).rule, 'moving')

    def test_talking_beats_goal(self):
        d = drone('Ra', mode='HOVER', z=1)
        self.assertEqual(rule(d, [d], now=1, talk_until=2, goal_t0=1, index=0, n=5).rule, 'talking')

    def test_goal_rainbow_chase(self):
        f = [drone(n, mode='HOVER', x=3 * i, z=1) for i, n in enumerate(ls.NAMES)]
        t0 = 100.0
        at = lambda i, t: ls.decide(f[i], f, t0 + t, {'goal_t0': t0, 'index': i, 'n': 5})
        self.assertEqual(at(0, 0.0).rule, 'goal')
        self.assertEqual(at(4, 0.1).rule, 'set')          # not reached by the chase yet
        self.assertEqual(at(4, 1.1).rule, 'goal')
        self.assertNotEqual(at(0, 0.0).rgb, at(4, 1.1).rgb)
        self.assertEqual(at(0, ls.GOAL_DURATION + 0.01).rule, 'set')


class Goal(unittest.TestCase):
    def fleet(self, mode, err):
        return [drone(n, mode=mode, x=3 * i, z=1, target=(3 * i + err, 0, 1)) for i, n in enumerate(ls.NAMES)]

    def test_fires_once_on_formation_complete(self):
        g = ls.GoalDetector()
        self.assertFalse(g.update(self.fleet('FORMATION', 0.5)))
        self.assertFalse(g.update(self.fleet('FORMATION', 0.3)))
        self.assertTrue(g.update(self.fleet('FORMATION', 0.05)))
        self.assertFalse(g.update(self.fleet('HOVER', 0.0)))       # stays set: no re-fire

    def test_no_fire_without_motion_or_while_parked(self):
        g = ls.GoalDetector()
        self.assertFalse(g.update(self.fleet('HOVER', 0.0)))
        self.assertFalse(g.update(parked_fleet()))

    def test_no_fire_while_someone_still_moving_or_landing(self):
        g = ls.GoalDetector()
        g.update(self.fleet('MOVE', 0.5))
        f = self.fleet('HOVER', 0.0); f[2] = drone('Thoth', mode='MOVE', x=6, z=1, target=(7, 0, 1))
        self.assertFalse(g.update(f))
        f[2] = drone('Thoth', mode='LANDING', x=6, z=0.5)
        self.assertFalse(g.update(f))

    def test_single_drone_move_does_not_fire(self):
        g = ls.GoalDetector()
        f = self.fleet('HOVER', 0.0); f[0] = drone('Horus', mode='MOVE', z=1, target=(1, 0, 1))
        g.update(f)
        self.assertFalse(g.update(self.fleet('HOVER', 0.0)))


class Helpers(unittest.TestCase):
    def test_chat_speakers_new_ids_only(self):
        seen = set()
        msgs = [{'id': 'a', 'speaker': 'SYSTEM'}, {'id': 'b', 'speaker': 'Horus', 'source': 'FIELD_CALLOUT'}]
        self.assertEqual(ls.chat_speakers(msgs, seen), ['Horus'])
        msgs.append({'id': 'c', 'speaker': 'Ra'})
        self.assertEqual(ls.chat_speakers(msgs, seen), ['Ra'])
        self.assertEqual(ls.chat_speakers(msgs, seen), [])

    def test_token_bucket_caps_rate(self):
        t = [0.0]
        b = ls.TokenBucket(10, clock=lambda: t[0])
        got = 0
        for _ in range(600):            # 60 s in 0.1 s steps, asking for 2 tokens each step (20/s)
            t[0] += 0.1
            got += 2 if b.try_take(2) else 0
        self.assertLessEqual(got, 10 * 60 + 10)

    def test_help_documents_language(self):
        doc = ls.__doc__
        for word in ('dim white', 'amber blink', 'cyan', 'green', 'blue flash', 'rainbow', 'red blink', '--mode party'):
            self.assertIn(word, doc)


if __name__ == '__main__':
    unittest.main()
