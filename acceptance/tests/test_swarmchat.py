#!/usr/bin/env python3
"""Offline tests for the deterministic SwarmChat conversational layer.

No Gazebo, no network, nothing flies. Fake snapshots mimic Recorder.state.
Run from a temp dir containing copies of spaceops_v8/*.py (see run_swarmchat_tests.sh).
"""
from datetime import datetime, timezone, timedelta
import math
from pathlib import Path
import re
import tempfile
import unittest

import swarmchat as sc

NAMES = sc.DRONES


def stamp(age_s=0.0):
    t = datetime.now(timezone.utc) - timedelta(seconds=age_s)
    return t.isoformat(timespec='milliseconds').replace('+00:00', 'Z')


# Thoth's nearest is Osiris (1.0 m); Horus-Ra are 2.2 m apart; Ra is highest.
POS = {'Horus': (-2.2, -1.5, 1.5), 'Ra': (0.0, -1.5, 2.1), 'Thoth': (2.2, -1.5, 1.5),
       'Anubis': (-1.1, 1.1, 1.4), 'Osiris': (2.2, -0.5, 1.5)}


def snapshot(fault=(), stale=(), grounded=(), age_s=0.0):
    drones = []
    for n in NAMES:
        x, y, z = POS[n]
        if n in grounded: z = 0.05
        drones.append({'name': n, 'x': x, 'y': y, 'z': z, 'target': [x, y + .1, z], 'yaw': 0.0,
                       'mode': 'FAULT' if n in fault else ('GROUNDED' if n in grounded else 'HOVER'),
                       'fault': n in fault, 'telemetry': n not in stale,
                       'battery_sim_pct': 91.4, 'ground_speed_est': 0.05})
    return {'world': 'test', 'drones': drones, 'timestamp_utc': stamp(age_s),
            'min_sep_m': 1.0, 'closest_pair': ['Thoth', 'Osiris'], 'safety_status': 'WATCH'}


def health_climb(name='Thoth', dz=1.5, seconds=30):
    # 4 Hz samples; climbs linearly by dz over the window.
    n = int(seconds * 4) + 1
    series = [{'t': round(i * .25, 2), 'fresh': True, 'mode': 'HOVER', 'fault': False,
               'z': round(.2 + dz * i / (n - 1), 3), 'speed': .1, 'track_err': .3 - .2 * i / (n - 1),
               'nearest': 1.0, 'nearest_peer': 'Osiris', 'battery_sim': 95.0} for i in range(n)]
    return {k: (series if k == name else []) for k in NAMES}


class SwarmChatTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.chat = sc.SwarmChat(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def ask(self, text, to='FLEET', state=None, online=True, events=(), health=None):
        return self.chat.ask(text, to, snapshot() if state is None else state, list(events), online, health=health)

    def drone_msgs(self, msgs):
        return [m for m in msgs if m['role'] == 'DRONE_AGENT']

    # -- relational ------------------------------------------------------------
    def test_closest_to_thoth_names_osiris_not_thoth_status(self):
        msgs = self.ask('who is closest to Thoth?')
        first = msgs[0]
        self.assertEqual(first['sender'], 'Thoth')
        self.assertIn('Osiris', first['text'])
        d = math.dist(POS['Thoth'], POS['Osiris'])
        self.assertIn(f'{d:.2f} m', first['text'])
        self.assertNotIn('I am HOVER', first['text'])  # not the old status template
        self.assertEqual(first['evidence']['peer'], 'Osiris')
        # the peer responds to Thoth (deterministic cross-talk)
        self.assertTrue(any(m['sender'] == 'Osiris' and m['to'] == 'Thoth' for m in msgs))

    def test_closest_asked_to_other_drone_is_third_person(self):
        msgs = self.ask('Horus, who is closest to Thoth?', to='Horus')
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]['sender'], 'Horus')
        self.assertIn("Thoth's nearest aircraft is Osiris", msgs[0]['text'])

    def test_highest_lowest_and_separation(self):
        hi = self.ask('who is highest?')
        self.assertEqual(hi[0]['sender'], 'Ra')
        self.assertIn('2.10 m', hi[0]['text'])
        lo = self.ask('who is lowest?')
        self.assertEqual(lo[0]['sender'], 'Anubis')
        sep = self.ask('what is the separation between Horus and Ra?')
        self.assertIn('2.28 m apart (3D)', sep[0]['text'])  # sqrt(2.2^2 + 0.6^2)
        far = self.ask('Horus, who is farthest from you?', to='Horus')
        self.assertIn('My farthest aircraft is', far[0]['text'])

    def test_airborne_and_fault_lists(self):
        st = snapshot(fault=('Anubis',), stale=('Ra',), grounded=('Osiris',))
        air = self.ask('who is airborne?', state=st)
        txt = air[-1]['text']
        self.assertEqual(air[-1]['sender'], "MA'AT")
        self.assertRegex(txt, r'Airborne .*: Horus, Thoth and Anubis\.')
        self.assertIn('On or near the ground: Osiris', txt)
        self.assertIn('UNKNOWN (stale or offline): Ra', txt)
        flt = self.ask('which drones have faults?', state=st)
        self.assertIn('Active simulated faults: Anubis', flt[-1]['text'])
        self.assertIn('state UNKNOWN): Ra', flt[-1]['text'])

    # -- social -----------------------------------------------------------------
    def test_joke_gets_joke_not_status(self):
        msgs = self.ask('Ra, tell me a joke', to='Ra')
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]['sender'], 'Ra')
        self.assertEqual(msgs[0]['evidence']['kind'], 'SOCIAL_JOKE')
        self.assertIn(msgs[0]['text'], sc.PERSONAS['Ra']['jokes'])
        again = self.ask('Ra, another joke please', to='Ra')
        self.assertNotEqual(msgs[0]['text'], again[0]['text'])
        fleet = self.ask('tell us a joke')
        self.assertEqual(len(self.drone_msgs(fleet)), 2)  # teller + one peer reaction
        self.assertEqual(fleet[1]['to'], fleet[0]['sender'])

    def test_greeting_and_how_are_you_one_reply_per_drone(self):
        msgs = self.ask('hello team, how is everyone feeling today?')
        drones = self.drone_msgs(msgs)
        self.assertEqual([m['sender'] for m in drones], list(NAMES))
        self.assertFalse(any(m['role'] == 'ASSURANCE' for m in msgs))
        self.assertEqual(len({m['text'] for m in drones}), 5)  # distinct voices
        for m in drones:
            self.assertEqual(m['evidence']['kind'], 'SOCIAL_GROUNDED')
            self.assertIn('HOVER at', m['text'])  # grounded in telemetry
        hi = self.ask('hi Osiris', to='Osiris')
        self.assertIn(hi[0]['text'], sc.PERSONAS['Osiris']['greet'])
        thanks = self.ask('thanks, Anubis', to='Anubis')
        self.assertIn(thanks[0]['text'], sc.PERSONAS['Anubis']['thanks'])

    def test_identity_and_honesty(self):
        who = self.ask('Thoth, who are you and what is your role?', to='Thoth')
        self.assertIn('analyst', who[0]['text'])
        self.assertIn('deterministic', who[0]['text'])
        ai = self.ask('are you an AI?', to='Horus')
        self.assertIn('not an AI model', ai[0]['text'])

    # -- status / health ----------------------------------------------------------
    def test_altitude_and_battery_specific(self):
        msgs = self.ask('Horus, what is your altitude and battery?', to='Horus')
        t = msgs[0]['text']
        self.assertIn('altitude is 1.50 m', t)
        self.assertIn('91.4%', t)
        self.assertIn('SIMULATED', t)

    def test_health_includes_fault_and_nearest(self):
        st = snapshot(fault=('Anubis',))
        t = self.ask('Anubis, are you healthy?', to='Anubis', state=st)[0]['text']
        self.assertIn('NOT fully healthy', t)
        self.assertIn('command-link fault', t)
        self.assertIn('nearest aircraft', t)

    def test_trend_from_health_history(self):
        msgs = self.ask('Thoth, how has your altitude changed in the last 30 s?', to='Thoth', health=health_climb())
        self.assertIn('climbed 1.50 m in the last 30 s', msgs[0]['text'])
        none = self.ask('Horus, what is your recent trend?', to='Horus', health=health_climb())
        self.assertIn('UNKNOWN', none[0]['text'])

    # -- unknowns -------------------------------------------------------------
    def test_stale_telemetry_says_unknown(self):
        st = snapshot(stale=('Anubis',))
        for q in ('Anubis, status.', 'Anubis, what is your altitude?', 'Anubis, how are you?'):
            t = self.ask(q, to='Anubis', state=st)[0]['text']
            self.assertIn('UNKNOWN', t, q)
            self.assertNotRegex(t, r'\d+\.\d+ m', q)
        near = self.ask('who is closest to Anubis?', state=st)
        self.assertIn('UNKNOWN', near[0]['text'])
        # stale aircraft never chosen as anyone's neighbour
        h = self.ask('who is closest to Horus?', state=st)
        self.assertNotIn('Anubis,', h[0]['text'].split('(')[0])

    def test_old_snapshot_is_stale(self):
        t = self.ask('Horus, altitude?', to='Horus', state=snapshot(age_s=60))[0]['text']
        self.assertIn('UNKNOWN', t)

    def test_offline_says_unknown(self):
        msgs = self.ask('Ra, how are you?', to='Ra', online=False)
        self.assertIn('UNKNOWN', msgs[0]['text'])
        self.assertTrue(any('offline' in m['text'] for m in msgs))

    # -- non-actuation ------------------------------------------------------------
    def test_flight_request_refused_no_dispatch(self):
        for to, q in (('Horus', 'Horus, take off now.'), ('FLEET', 'can you all take off and form a V?'),
                      ('Ra', 'Ra, land please')):
            msgs = self.ask(q, to=to)
            drone = self.drone_msgs(msgs)[0]
            self.assertEqual(drone['evidence']['kind'], 'FLIGHT_REQUEST_REFUSED')
            self.assertIs(drone['evidence']['dispatched'], False)
            self.assertIn('8100', drone['text'])
            self.assertTrue(any('DOES NOT execute' in m['text'] for m in msgs))
        self.assertFalse(hasattr(self.chat, 'dispatch') or hasattr(self.chat, 'action'))
        src = Path(sc.__file__).read_text()
        self.assertNotRegex(src, r'(?m)urlopen|^\s*(import|from)\s+(urllib|requests|socket|http)\b')

    def test_diagnostic_why_is_not_refusal(self):
        st = snapshot(fault=('Anubis',))
        msgs = self.ask("Anubis, why didn't you take off?", to='Anubis', state=st)
        self.assertIn('command-link fault', msgs[0]['text'])
        self.assertNotEqual(msgs[0]['evidence']['kind'], 'FLIGHT_REQUEST_REFUSED')

    # -- attribution / fleet room ------------------------------------------------
    def test_every_message_has_sender(self):
        qs = ['status report everyone', 'Ra, tell me a joke', 'who is closest to Thoth?', 'hi', 'blorp?',
              'Horus, take off', 'who is airborne?', 'is it safe to tighten up?']
        for q in qs:
            for m in self.ask(q):
                self.assertTrue(m.get('sender'), q)
                self.assertIn(m['sender'], (*NAMES, "MA'AT", 'SYSTEM', 'OPERATOR'))
        for m in self.chat.history():
            self.assertTrue(m.get('sender'))

    def test_fleet_discussion_one_reply_per_drone_plus_maat(self):
        msgs = self.ask('Fleet, discuss your current readiness and any faults.', state=snapshot(fault=('Anubis',)))
        self.assertEqual([m['sender'] for m in msgs], [*NAMES, "MA'AT"])
        thoth = next(m for m in msgs if m['sender'] == 'Thoth')
        osiris = next(m for m in msgs if m['sender'] == 'Osiris')
        self.assertIn('Osiris, you and I are the closest pair', thoth['text'])
        self.assertIn('Confirmed, Thoth', osiris['text'])
        anubis = next(m for m in msgs if m['sender'] == 'Anubis')
        self.assertIn('command-link fault', anubis['text'])

    def test_deterministic(self):
        a = [m['text'] for m in self.ask('hello team, how is everyone feeling today?')]
        other = sc.SwarmChat(Path(self.tmp.name) / 'b')
        b = [m['text'] for m in other.ask('hello team, how is everyone feeling today?', 'FLEET', snapshot(), [], True)]
        self.assertEqual(a, b)


if __name__ == '__main__':
    unittest.main()
