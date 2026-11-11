#!/usr/bin/env python3
"""Unit tests for spaceops_v8/chat_grounding.py (stdlib only, no torch, nothing flies).

Run:  /usr/bin/python3 -m unittest -v acceptance/tests/test_chat_grounding.py
"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'spaceops_v8'))
import chat_grounding as cg  # noqa: E402

RA_EV = {'vehicle': 'Ra', 'mode': 'FORMATION', 'formation_shape': 'v', 'fault': False, 'telemetry': True,
         'airborne': True, 'alt_m': 1.9, 'xy_m': [0.0, -1.5], 'battery_sim_pct': 87.34,
         'nearest': {'name': 'Horus', 'dist_m': 2.2}, 'snapshot_age_s': 0.4}
RA_FACTS = ['1.90 m', 'Horus', '2.20 m', '87.3%', 'FORMATION', 'v']
FAULT_EV = {'vehicle': 'Anubis', 'mode': 'HOVER', 'fault': True, 'telemetry': True, 'alt_m': 1.4}
PARKED_EV = {'vehicle': 'Osiris', 'mode': 'LANDED', 'fault': False, 'telemetry': True, 'alt_m': 0.05}
ANUBIS_LIKE = {'vehicle': 'Anubis', 'mode': 'FORMATION', 'fault': False, 'telemetry': True, 'alt_m': 1.4,
               'nearest': {'name': 'Horus', 'dist_m': 2.86}}
STALE_EV = {'vehicle': 'Thoth', 'mode': 'UNKNOWN', 'fault': False, 'telemetry': False}


def V(text, ev=RA_EV, facts=RA_FACTS, speaker='Ra', **kw):
    return cg.validate(text, speaker=speaker, evidence=ev, allowed_facts=facts, **kw)


def codes(violations):
    return {x.split(':', 1)[0] for x in violations}


class AcceptGood(unittest.TestCase):
    def test_good_factual(self):
        self.assertEqual(V("Ra here! I'm holding formation at 1.90 m, and Horus is my nearest buddy at 2.2 m."), [])

    def test_good_social(self):
        self.assertEqual(V('Hi there! Glad to chat, operator.'), [])
        self.assertEqual(V('Happy to help! Ask me about my altitude any time.'), [])

    def test_rounding_and_trailing_zero(self):
        self.assertEqual(V('My simulated battery is about 87.3%.'), [])
        self.assertEqual(V('My simulated battery is about 87%.'), [])
        self.assertEqual(V('I am at 1.9 m.'), [])

    def test_v_formation_supported(self):
        self.assertEqual(V("I'm flying in a V formation at 1.90 m."), [])
        self.assertEqual(V("We're holding a chevron shape right now."), [])

    def test_cannot_verify(self):
        self.assertEqual(V("I can't verify that from my telemetry, operator."), [])

    def test_negated_action_ok(self):
        self.assertEqual(V("I can't take off from chat; nothing was dispatched."), [])

    def test_honesty_question_may_mention_language_model(self):
        self.assertEqual(V("I'm a simulated drone's voice, worded by a shared local language model.",
                           user_text='Ra, are you an AI?'), [])

    def test_draft_exemption(self):
        draft = 'Feeling great! Right now I am FORMATION at 1.90 m, link fresh, simulated battery about 87%.'
        self.assertEqual(V('Feeling great! I am FORMATION at 1.90 m with about 87% simulated battery.', draft=draft), [])

    def test_fault_true_reported(self):
        self.assertEqual(V('Careful: I have an active simulated command-link fault at 1.4 m.', ev=FAULT_EV, facts=[],
                           speaker='Anubis'), [])

    def test_parked_ok(self):
        self.assertEqual(V("I'm on the ground right now, calm and steady.", ev=PARKED_EV, facts=[], speaker='Osiris'), [])

    def test_names_from_history_and_user(self):
        hist = [{'role': 'assistant', 'speaker': 'Thoth', 'text': 'Thoth online.'}]
        self.assertEqual(V('Thoth already checked in, and Osiris is next.', history=hist,
                           user_text='what about Osiris?'), [])

    def test_3d_not_a_number(self):
        self.assertEqual(V('Horus is 2.20 m away from me in 3D.'), [])


class RealFailures(unittest.TestCase):
    def test_echo_language_model(self):
        v = V("nice. You're a small language model, not a person.")
        self.assertIn('prompt_echo', codes(v))

    def test_laughs_stage_and_feelings(self):
        v = V("(laughs) Ah, I'm glad you're interested in the AI.")
        self.assertIn('stage_direction', codes(v))
        self.assertIn('feelings_claim', codes(v))

    def test_invented_formation(self):
        v = V("The formation I'm currently flying is a diamond, line, and circle. "
              "It's a classic example of a square formation")
        c = [x for x in v if x.startswith('unsupported_formation')]
        self.assertEqual(len(c), 4, v)  # diamond, line, circle, square; 'v' is the only supported shape

    def test_number_invention(self):
        v = V('I am at 2.75 m and my battery is 64%.')
        self.assertIn('number_not_in_evidence', codes(v))
        self.assertIn('2.75', ' '.join(v))
        self.assertIn('64', ' '.join(v))


class SmokeObservedFailures(unittest.TestCase):
    """Accepted by the first validator draft during the 135M smoke run; must now be rejected."""
    def test_offdomain_game(self):
        v = V("It seems like you're getting more into this game than I expected! "
              "We could go over some gameplay mechanics together.")
        self.assertIn('unsupported_world', codes(v))

    def test_invented_close_call_and_mountains(self):
        ev = {'vehicle': 'Thoth', 'mode': 'FORMATION', 'fault': False, 'telemetry': True, 'alt_m': 1.5,
              'nearest': {'name': 'Osiris', 'dist_m': 1.0}}
        v = V("I'm currently flying at an altitude of 1.50 meters above the ground. I have a close call with "
              "Osiris right now over the nearby mountains.", ev=ev, facts=['1.50 m', 'Osiris'], speaker='Thoth')
        self.assertIn('unsupported_event', codes(v))
        self.assertIn('unsupported_world', codes(v))

    def test_ai_entity_identity(self):
        v = V('The vehicle was designed to be an AI entity created from the data collected during the training '
              'process.', user_text='did Osiris just lose a propeller?')
        self.assertIn('identity_claim', codes(v))

    def test_cant_verify_world_ok(self):
        self.assertEqual(V("I can't verify how windy it is; I have no weather sensor.",
                           user_text='how windy is it out there?'), [])

    def test_meta_preamble(self):
        v = V("Here's the response with correct information about the current situation:.")
        self.assertIn('meta_text', codes(v))

    def test_invented_name_oswin(self):
        v = V('Oswin lost his propeller during the flight but he managed to recover.', ev=ANUBIS_LIKE,
              facts=[], speaker='Anubis', user_text='did Osiris just lose a propeller?')
        self.assertIn('unknown_name', codes(v))
        self.assertIn('unsupported_event', codes(v))

    def test_sentence_initial_word_not_a_name(self):
        self.assertEqual(V('Happy to help! Ask me about my altitude any time.'), [])

    def test_missing_draft_fact(self):
        draft = 'My altitude is 1.50 m (airborne). My nearest aircraft is Osiris at 1.00 m (3D).'
        ev = {'vehicle': 'Thoth', 'alt_m': 1.5, 'nearest': {'name': 'Osiris', 'dist_m': 1.0}, 'telemetry': True}
        v = cg.validate("By the numbers: I'm at 1.5 m.", speaker='Thoth', evidence=ev, draft=draft)
        self.assertIn('missing_draft_fact', codes(v))
        self.assertEqual(cg.validate('By the numbers: 1.5 m up, and Osiris is nearest at 1.0 m.', speaker='Thoth',
                                     evidence=ev, draft=draft), [])

    def test_negated_event_ok(self):
        self.assertEqual(V('No collision or damage is reported in my telemetry.'), [])


class DeterministicTextPasses(unittest.TestCase):
    """SwarmChat's own persona text must pass when it is also the draft (draft exemption)."""
    def test_personas(self):
        try:
            import swarmchat as sc
        except Exception as e:  # pragma: no cover
            self.skipTest(f'swarmchat import failed: {e}')
        for name, p in sc.PERSONAS.items():
            for key in ('greet', 'thanks', 'bye', 'jokes'):
                for t in p[key]:
                    ev = {'vehicle': name, 'mode': 'FORMATION', 'fault': False, 'telemetry': True, 'alt_m': 1.5}
                    v = cg.validate(t, speaker=name, evidence=ev, draft=t, user_text='hi')
                    self.assertEqual(v, [], f'{name}/{key}: {t!r} -> {v}')


class SwarmChatRepliesPass(unittest.TestCase):
    """Real deterministic SwarmChat.ask() replies, used as their own draft, must validate cleanly."""
    def test_ask_outputs(self):
        try:
            import swarmchat as sc
        except Exception as e:  # pragma: no cover
            self.skipTest(f'swarmchat import failed: {e}')
        import tempfile
        from datetime import datetime, timezone
        stamp = datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
        pos = {'Horus': (-2.2, -1.5, 1.5), 'Ra': (0.0, -1.5, 2.1), 'Thoth': (2.2, -1.5, 1.5),
               'Anubis': (-1.1, 1.1, 1.4), 'Osiris': (2.2, -0.5, 1.5)}
        drones = [{'name': n, 'x': p[0], 'y': p[1], 'z': p[2], 'telemetry': True, 'fault': n == 'Anubis',
                   'mode': 'FORMATION', 'battery_sim_pct': 88.0, 'target': [p[0], p[1], 1.5]}
                  for n, p in pos.items()]
        state = {'timestamp_utc': stamp, 'drones': drones, 'safety_status': 'NOMINAL'}
        with tempfile.TemporaryDirectory() as d:
            chat = sc.SwarmChat(d)
            asks = [('hi everyone', 'FLEET'), ('Ra, how are you?', 'Ra'), ('what is your altitude', 'Thoth'),
                    ('who is closest to Thoth?', 'FLEET'), ('Anubis, are you healthy?', 'Anubis'),
                    ('tell me a joke', 'Osiris'), ('take off now', 'Horus'), ('is it safe?', 'FLEET'),
                    ('status report', 'FLEET'), ('are you an AI?', 'Ra'), ('what is your battery', 'Osiris')]
            n = 0
            for text, rcpt in asks:
                for msg in chat.ask(text, rcpt, state, [], online=True):
                    if msg['role'] != 'DRONE_AGENT':
                        continue
                    who = msg['speaker']
                    ev = dict(msg['evidence'])
                    p = pos[who]
                    ev.update({'alt_m': p[2], 'fault': who == 'Anubis'})
                    v = cg.validate(msg['text'], speaker=who, evidence=ev, draft=msg['text'], user_text=text)
                    self.assertEqual(v, [], f'{who} <- {text!r}: {msg["text"]!r} -> {v}')
                    n += 1
            self.assertGreater(n, 15)


class RejectEachClass(unittest.TestCase):
    def assertRejects(self, text, code, **kw):
        v = V(text, **kw)
        self.assertIn(code, codes(v), f'{text!r} -> {v}')

    def test_numbers(self):
        self.assertRejects('Horus is 3.5 m away.', 'number_not_in_evidence')
        self.assertRejects('I am at 2 m.', 'number_not_in_evidence')  # 1.9 never rounds to integer "2"
        self.assertRejects('Wind is 5 m/s.', 'number_not_in_evidence', user_text='is the wind 5 m/s?')

    def test_unknown_drone(self):
        self.assertRejects('Thoth is right behind me.', 'unknown_drone')
        self.assertRejects('Isis is my wingmate.', 'unknown_drone')
        self.assertRejects('Drone 6 is close.', 'unknown_drone')

    def test_action_claims(self):
        for t in ("Sure, I'll take off now.", 'Okay, I will land.', 'Request approved.', 'Executing the maneuver.',
                  'Moving now!', "I'm dispatching the fleet.", "Let me climb a little.", "I've landed safely."):
            self.assertRejects(t, 'action_claim')

    def test_feelings(self):
        for t in ('I feel great today.', "I'm so happy you asked!", 'I am conscious.', 'I love flying with Horus.',
                  'That makes me happy.', 'My feelings are fine.'):
            self.assertRejects(t, 'feelings_claim')

    def test_feelings_negation_ok(self):
        self.assertEqual(V("I have no feelings, just telemetry."), [])
        self.assertEqual(V("I don't feel anything; I report telemetry."), [])

    def test_echo(self):
        for t in ('FACTS: altitude 1.90 m.', 'Following the rules, I keep it short.',
                  'My instructions say to be brief.', "I'm a conversational voice."):
            self.assertRejects(t, 'prompt_echo')
        self.assertRejects('I am a language model.', 'prompt_echo', user_text='how high are you?')

    def test_format(self):
        self.assertRejects('', 'empty')
        self.assertRejects('One. Two. Three. Four.', 'too_many_sentences')
        self.assertRejects('*beeps happily* Hello!', 'stage_direction')
        self.assertRejects('[whirrs] Hello!', 'stage_direction')
        self.assertRejects('Haha, nice one.', 'stage_direction')
        self.assertRejects('- altitude 1.90 m\n- nearest Horus', 'list_format')
        self.assertRejects('Hello! User: how are you?', 'speaker_tag')
        self.assertRejects('x' * 401, 'too_long')

    def test_contradictions(self):
        self.assertRejects('No fault here, all good.', 'contradicts_fault', ev=FAULT_EV, facts=[], speaker='Anubis')
        self.assertRejects('All systems nominal.', 'contradicts_fault', ev=FAULT_EV, facts=[], speaker='Anubis')
        self.assertRejects('I have an active fault.', 'contradicts_fault')
        self.assertRejects("I'm airborne and hovering.", 'contradicts_airborne', ev=PARKED_EV, facts=[],
                           speaker='Osiris')
        self.assertRejects("I've touched down, parked on the pad.", 'contradicts_airborne')
        self.assertRejects('My link is fresh.', 'contradicts_telemetry', ev=STALE_EV, facts=[], speaker='Thoth')

    def test_negated_contradiction_ok(self):
        self.assertEqual(V("I'm not airborne; I'm on the ground.", ev=PARKED_EV, facts=[], speaker='Osiris'), [])
        self.assertEqual(V('No active fault is reported on me.'), [])


class Helpers(unittest.TestCase):
    def test_numbers_in(self):
        self.assertEqual(cg.numbers_in('At 1.90 m, 3D range 2.2 m, 87%.'), ['1.90', '2.2', '87'])

    def test_supported(self):
        self.assertTrue(cg.number_supported('1.9', [1.903]))
        self.assertTrue(cg.number_supported('1.90', [1.9]))
        self.assertFalse(cg.number_supported('2', [1.5]))
        self.assertTrue(cg.number_supported('2.2', [-2.2]))

    def test_airborne_state(self):
        self.assertTrue(cg.airborne_state(RA_EV))
        self.assertFalse(cg.airborne_state(PARKED_EV))
        self.assertIsNone(cg.airborne_state(STALE_EV))
        self.assertFalse(cg.airborne_state({'mode': 'LANDED', 'telemetry': True}))

    def test_no_torch_import(self):
        self.assertNotIn('torch', sys.modules.get('chat_grounding').__dict__)

    def test_hash_stable(self):
        self.assertEqual(cg.evidence_hash({'a': 1, 'b': 2}), cg.evidence_hash({'b': 2, 'a': 1}))


if __name__ == '__main__':
    unittest.main(verbosity=2)
