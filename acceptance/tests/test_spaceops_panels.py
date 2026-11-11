"""Offline tests for the SpaceOps V10 read-only panels (formations, latency, faults, ArduPilot export).

Run:  cd ~/hu-telepresence-gazebo/acceptance/tests && /usr/bin/python3 -m unittest -v test_spaceops_panels
Only temp files are used. The HTTP check binds an ephemeral 127.0.0.1 port (like test_v8.py);
nothing contacts 8100/8765/8766.
"""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import zipfile
from http.server import ThreadingHTTPServer
from urllib import request, error

HERE = Path(__file__).resolve().parent
SPACEOPS = Path(os.environ.get('SPACEOPS_DIR', HERE.parent.parent / 'spaceops_v8'))
sys.path.insert(0, str(SPACEOPS))
import spaceops_panels as panels  # noqa: E402

NAMES = panels.NAMES


def write_jsonl(path, rows):
    path.write_text(''.join(json.dumps(r) + '\n' for r in rows), encoding='utf8')


class FormationCatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); d = Path(self.tmp.name)
        self.results = d / 'results'; self.results.mkdir()
        old = {'generated_utc': '2026-11-11T05:36:52Z', 'world_env': 'military', 'criteria': 'OLD',
               'formations': [{'shape': 'line', 'status': 'REFUSED', 'why': 'No separation-safe assignment'},
                              {'shape': 'pentagon', 'alias_of': 'circle', 'status': 'VALIDATED', 'settle_s': 4.25,
                               'rms_error_m': .01, 'shape_error_m': .007, 'planned_min_sep_m': 1.11,
                               'observed_min_sep_m': .985, 'proximity_holds': 0}]}
        new = {'generated_utc': '2026-11-11T05:38:14Z', 'world_env': 'military', 'criteria': 'VALIDATED = ...',
               'formations': [{'shape': 'line', 'status': 'VALIDATED', 'settle_s': 3.8, 'rms_error_m': .011,
                               'shape_error_m': .011, 'planned_min_sep_m': 1.087, 'observed_min_sep_m': 1.107,
                               'proximity_holds': 0, 'closest_pair': ['Anubis', 'Osiris']}]}
        (self.results / 'formation_catalog_20261111T053652Z.json').write_text(json.dumps(old))
        (self.results / 'formation_catalog_20261111T053814Z.json').write_text(json.dumps(new))
        (self.results / 'unrelated.json').write_text('{}')
        self.catalog = d / 'formation_catalog.json'
        self.catalog.write_text(json.dumps({
            'line': {'status': 'VALIDATED', 'alias_of': None, 'evidence': 'formation_catalog_20261111T053814Z.json',
                     'rms_error_m': .011, 'observed_min_sep_m': 1.107},
            'pentagon': {'status': 'VALIDATED', 'alias_of': 'circle', 'evidence': 'formation_catalog_20261111T053652Z.json'},
            'spiral': {'status': 'EXPERIMENTAL', 'alias_of': None, 'evidence': '../../etc/passwd'},
        }))

    def tearDown(self):
        self.tmp.cleanup()

    def test_rows_join_catalog_with_cited_evidence(self):
        out = panels.load_formation_catalog(self.catalog, self.results)
        self.assertTrue(out['available']); self.assertEqual(out['source'], 'catalog')
        rows = {r['shape']: r for r in out['rows']}
        line = rows['line']
        self.assertEqual(line['status'], 'VALIDATED'); self.assertTrue(line['evidence_found'])
        self.assertEqual(line['settle_s'], 3.8); self.assertEqual(line['planned_min_sep_m'], 1.087)
        self.assertEqual(line['proximity_holds'], 0)
        # the earlier refused attempt is kept visible as history, not hidden
        self.assertIn({'file': 'formation_catalog_20261111T053652Z.json', 'status': 'REFUSED'}, line['history'])
        self.assertEqual(rows['pentagon']['alias_of'], 'circle'); self.assertEqual(rows['pentagon']['settle_s'], 4.25)
        self.assertEqual(out['counts'], {'VALIDATED': 2, 'EXPERIMENTAL': 1})
        self.assertEqual(out['criteria'], 'VALIDATED = ...')

    def test_bad_evidence_path_is_not_followed_and_values_stay_missing(self):
        rows = {r['shape']: r for r in panels.load_formation_catalog(self.catalog, self.results)['rows']}
        spiral = rows['spiral']
        self.assertFalse(spiral['evidence_found'])
        self.assertIsNone(spiral['settle_s']); self.assertIsNone(spiral['rms_error_m'])

    def test_missing_files_give_no_data(self):
        out = panels.load_formation_catalog(Path(self.tmp.name) / 'nope.json', Path(self.tmp.name) / 'nodir')
        self.assertFalse(out['available']); self.assertEqual(out['rows'], [])

    def test_fallback_to_latest_result_file_when_catalog_missing(self):
        out = panels.load_formation_catalog(Path(self.tmp.name) / 'nope.json', self.results)
        self.assertEqual(out['source'], 'latest_result_file')
        self.assertEqual([r['shape'] for r in out['rows']], ['line'])

    def test_live_formation_uses_formation_mode_and_fresh_only(self):
        state = {'timestamp_utc': 'x', 'drones': [
            {'name': 'Horus', 'mode': 'FORMATION', 'telemetry': True, 'x': 1.0, 'y': 0.0, 'z': 1.9, 'target': [1.0, 0.3, 1.9]},
            {'name': 'Ra', 'mode': 'FORMATION', 'telemetry': True, 'x': 0.0, 'y': 0.0, 'z': 1.9, 'target': [0.0, 0.4, 1.9]},
            {'name': 'Thoth', 'mode': 'FORMATION', 'telemetry': False, 'x': 9, 'y': 9, 'z': 9, 'target': [0, 0, 1.9]},
            {'name': 'Anubis', 'mode': 'HOVER', 'telemetry': True, 'x': 5, 'y': 5, 'z': 1, 'target': [0, 0, 1]}]}
        live = panels.live_formation(state)
        self.assertTrue(live['active']); self.assertEqual(live['count'], 3)
        self.assertEqual(live['stale'], ['Thoth'])
        self.assertAlmostEqual(live['rms_err_m'], round(((0.09 + 0.16) / 2) ** .5, 3))
        thoth = next(a for a in live['aircraft'] if a['name'] == 'Thoth')
        self.assertIsNone(thoth['x']); self.assertIsNone(thoth['err_m'])     # stale: no position shown
        self.assertFalse(panels.live_formation({'drones': [state['drones'][3]]})['active'])
        self.assertFalse(panels.live_formation(None)['active'])


class LatencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); d = Path(self.tmp.name)
        self.lat = d / 'hu_voice_to_motion.jsonl'; self.dec = d / 'hu_real_decisions.jsonl'
        e0 = 1794376303111
        write_jsonl(self.dec, [
            {'timestamp_utc': 'a', 'event_epoch_ms': e0, 'cmd_id': 'c1', 'utterance': 'horus, land', 'action': 'LAND',
             'targets': ['Horus'], 'band': 'CONFIRM', 'latency_ms': 688.7, 'decision_source': 'REAL HU MODEL',
             'dispatched': False},
            {'event_epoch_ms': e0 + 1224, 'cmd_id': 'c1', 'band': 'CONFIRMED', 'targets': ['Horus'],
             'decision_source': 'REAL HU MODEL', 'action': 'land', 'dispatched': True},
            {'event_epoch_ms': e0 + 20000, 'cmd_id': 'c2', 'utterance': 'all, khalas', 'band': 'REFUSE',
             'action': 'MISSION_UNSUPPORTED', 'targets': [], 'decision_source': 'DETERMINISTIC MISSION PLANNER',
             'dispatched': False, 'latency_ms': .05},
            {'event_epoch_ms': e0 + 30000, 'cmd_id': 'c3', 'utterance': 'all, hover', 'band': 'EMERGENCY',
             'action': 'HOVER', 'targets': list(NAMES), 'decision_source': 'SYMBOLIC SAFETY (NOT TRAINED HU)',
             'dispatched': True},
            'not a dict', ])
        self.dec.write_text(self.dec.read_text() + '{broken json\n')
        write_jsonl(self.lat, [
            {'timestamp_utc': '2026-11-11T05:51:45.223Z', 'source': 'typed', 'utterance': 'horus, land', 'action': 'LAND',
             'targets': ['Horus'], 'outcome': 'OBSERVED_ALL', 'asr_ms': None, 'hu_roundtrip_ms': 715.8,
             'hu_inference_ms': 688.7, 'operator_confirmation_wait_ms': 1203.4, 'transcript_clarification_wait_ms': 0,
             'dispatch_ack_to_first_observed_ms': 917.9, 'submitted_to_first_observed_ms': 2858.8,
             'submitted_to_all_observed_ms': 2858.8, 'per_drone_observed_ms': {'Horus': 2858.8, 'Evil': 1}},
            {'timestamp_utc': '2026-11-11T05:52:04.978Z', 'cmd_id': 'c2', 'utterance': 'all, khalas',
             'outcome': 'REFUSE', 'hu_roundtrip_ms': 21.8},
        ])

    def tearDown(self):
        self.tmp.cleanup()

    def test_milestones_copied_and_missing_kept_none(self):
        out = panels.latency_timeline(self.lat, self.dec)
        cmds = {c['utterance']: c for c in out['commands']}
        land = cmds['horus, land']
        self.assertEqual(land['kind'], 'LATENCY_RECORD'); self.assertTrue(land['link'].startswith('utterance'))
        self.assertEqual(land['cmd_id'], 'c1'); self.assertEqual(land['decision_source'], 'REAL HU MODEL')
        seg = {s['key']: s['ms'] for s in land['segments']}
        self.assertIsNone(seg['asr'])                      # not measured -> None, never estimated
        self.assertEqual(seg['clarify'], 0)                # measured zero stays zero
        self.assertEqual(seg['decision'], 715.8); self.assertEqual(seg['confirm'], 1203.4)
        self.assertEqual(seg['motion'], 917.9); self.assertEqual(seg['spread'], 0.0)
        self.assertAlmostEqual(land['unattributed_ms'], round(2858.8 - 715.8 - 1203.4 - 917.9, 1))
        self.assertNotIn('Evil', land['per_drone_observed_ms'])
        khalas = cmds['all, khalas']
        self.assertEqual(khalas['link'], 'cmd_id'); self.assertEqual(khalas['decision_source'], 'DETERMINISTIC MISSION PLANNER')
        kseg = {s['key']: s['ms'] for s in khalas['segments']}
        self.assertIsNone(kseg['motion']); self.assertIsNone(kseg['spread']); self.assertIsNone(khalas['unattributed_ms'])

    def test_decision_only_commands_do_not_claim_motion(self):
        out = panels.latency_timeline(self.lat, self.dec)
        hover = next(c for c in out['commands'] if c['utterance'] == 'all, hover')
        self.assertEqual(hover['kind'], 'DECISION_ONLY'); self.assertEqual(hover['decision_source'], 'SYMBOLIC SAFETY')
        self.assertEqual(hover['outcome'], 'DISPATCHED · MOTION NOT MEASURED')
        self.assertTrue(all(s['ms'] is None for s in hover['segments'] if s['key'] in ('motion', 'spread', 'asr')))
        # each decision appears once (no duplicates of linked commands)
        self.assertEqual(sorted(c['cmd_id'] for c in out['commands']), ['c1', 'c2', 'c3'])

    def test_timed_records_kept_when_decisions_flood(self):
        rows = [{'event_epoch_ms': 1794376400000 + i * 1000, 'cmd_id': f'x{i}', 'utterance': f'noise {i}',
                 'band': 'REFUSE', 'decision_source': 'REAL HU MODEL', 'dispatched': False} for i in range(80)]
        write_jsonl(self.dec, rows)
        out = panels.latency_timeline(self.lat, self.dec, maximum=10)
        self.assertEqual(len(out['commands']), 10)
        self.assertEqual(sum(c['kind'] == 'LATENCY_RECORD' for c in out['commands']), 2)

    def test_missing_files(self):
        out = panels.latency_timeline(Path(self.tmp.name) / 'x', Path(self.tmp.name) / 'y')
        self.assertFalse(out['available']); self.assertEqual(out['commands'], [])

    def test_labels(self):
        self.assertEqual(panels.decision_label(None), 'NOT RECORDED')
        self.assertEqual(panels.decision_label('SYMBOLIC SAFETY (NOT TRAINED HU)'), 'SYMBOLIC SAFETY')


def ev(utc, kind, msg='', targets=None):
    return {'utc': utc, 'kind': kind, 'message': msg, 'targets': targets or []}


class FaultTimelineTests(unittest.TestCase):
    def test_link_fault_pairing_and_durations(self):
        events = [ev('2026-11-11T04:30:25.024Z', 'SIMULATED_LINK_FAULT', 'x', ['Anubis']),
                  ev('2026-11-11T04:30:25.226Z', 'GZ_DASHBOARD', 'FAULT : Anubis', ['Anubis']),
                  ev('2026-11-11T04:30:26.028Z', 'REJECTION_VERIFIED', '', ['Anubis']),
                  ev('2026-11-11T04:30:26.031Z', 'CLEAR_LINK_FAULT', '', ['Anubis']),
                  ev('2026-11-11T04:30:26.226Z', 'GZ_DASHBOARD', 'CLEAR_FAULT : Anubis', ['Anubis']),
                  ev('2026-11-11T04:30:53.377Z', 'SIMULATED_LINK_FAULT', '', ['Anubis']),
                  ev('2026-11-11T04:31:29.018Z', 'SIMULATED_LINK_FAULT', '', ['Anubis']),
                  ev('2026-11-11T04:31:29.130Z', 'REJECTION_VERIFIED', '', ['Anubis'])]
        out = panels.fault_timeline(list(reversed(events)), now_ms=0)   # order-independent
        eps = out['lanes']['Anubis']
        self.assertEqual([e['status'] for e in eps], ['RECOVERED', 'SUPERSEDED', 'OPEN'])
        d = eps[0]['durations_s']
        self.assertEqual(d['onset_to_detection'], 0.202); self.assertEqual(d['detection_to_containment'], 0.802)
        self.assertEqual(d['containment_to_recovery'], 0.003); self.assertEqual(d['onset_to_recovery'], 1.007)
        # superseded episode: no detection/containment/recovery recorded -> None, not assumed
        self.assertIsNone(eps[1]['recovery_ms']); self.assertIsNone(eps[1]['durations_s']['onset_to_recovery'])
        self.assertIsNone(eps[2]['detection_ms']); self.assertIsNotNone(eps[2]['containment_ms'])
        self.assertEqual(out['lanes']['Horus'], [])

    def test_separation_fleet_recovery_and_hold_without_alert(self):
        events = [ev('2026-11-11T04:34:27.000Z', 'SAFETY_SEPARATION_ALERT', '0.825', ['Horus', 'Anubis']),
                  ev('2026-11-11T04:34:27.500Z', 'GZ_SEPARATION_HOLD', 'hold', ['Anubis']),
                  ev('2026-11-11T04:34:29.000Z', 'SAFETY_WATCH', '0.9', ['Ra', 'Thoth']),   # names a different pair
                  ev('2026-11-11T04:35:06.000Z', 'GZ_SEPARATION_HOLD', 'hold', ['Osiris'])]
        out = panels.fault_timeline(events, now_ms=0)
        a = out['lanes']['Anubis'][0]; h = out['lanes']['Horus'][0]; o = out['lanes']['Osiris'][0]
        self.assertEqual(a['status'], 'RECOVERED'); self.assertEqual(h['status'], 'RECOVERED')
        self.assertEqual(a['durations_s']['onset_to_detection'], 0.5); self.assertEqual(a['durations_s']['onset_to_recovery'], 2.0)
        self.assertIsNone(h['containment_ms'])                       # Horus never held: not recorded
        self.assertIsNone(o['onset_ms']); self.assertEqual(o['status'], 'OPEN')
        self.assertEqual(out['lanes']['Ra'], [])                     # recovery alone creates no episode

    def test_telemetry_supervisor_and_markers(self):
        events = [ev('2026-11-11T05:00:00.000Z', 'SUPERVISOR_ONLINE'),
                  ev('2026-11-11T05:00:10.000Z', 'LINK_STALE', 'stale', ['Ra']),
                  ev('2026-11-11T05:00:10.000Z', 'SAFETY_TELEMETRY_LOST', 'None'),
                  ev('2026-11-11T05:00:11.000Z', 'GZ_SAFETY_SKIPPED', 'LAND not applied', ['Ra']),
                  ev('2026-11-11T05:00:12.500Z', 'LINK_FRESH', 'fresh', ['Ra']),
                  ev('2026-11-11T05:00:12.500Z', 'SAFETY_CLEAR', '2.2', ['Horus', 'Ra']),
                  ev('2026-11-11T05:01:00.000Z', 'SUPERVISOR_LOST'),
                  ev('2026-11-11T05:01:03.250Z', 'SUPERVISOR_ONLINE'),
                  ev('2026-11-11T05:02:00.000Z', 'GZ_LANDED', 'Horus landed', ['Horus']),
                  ev('2026-11-11T05:02:01.000Z', 'GZ_STAGGERED_TAKEOFF', 'layered', ['Horus', 'Ra']),
                  ev('bad-time', 'SUPERVISOR_LOST'), {'kind': 'NO_UTC'}, 'junk']
        out = panels.fault_timeline(events, now_ms=0)
        ra = out['lanes']['Ra'][0]
        self.assertEqual(ra['type'], 'TELEMETRY'); self.assertEqual(ra['durations_s']['detection_to_containment'], 1.0)
        self.assertEqual(ra['durations_s']['onset_to_recovery'], 2.5)
        fleet = {e['type']: e for e in out['lanes']['FLEET']}
        self.assertEqual(fleet['TELEMETRY']['durations_s']['onset_to_recovery'], 2.5)
        self.assertEqual(fleet['SUPERVISOR']['durations_s']['onset_to_recovery'], 3.25)
        self.assertEqual(len(out['lanes']['FLEET']), 2)              # initial ONLINE pairs with nothing
        kinds = [(m['lane'], m['kind']) for m in out['markers']]
        self.assertIn(('Horus', 'GZ_LANDED'), kinds); self.assertIn(('Ra', 'GZ_STAGGERED_TAKEOFF', ), kinds)
        self.assertIn(('Ra', 'GZ_SAFETY_SKIPPED'), kinds)
        self.assertEqual(out['event_count'], 10)

    def test_empty(self):
        out = panels.fault_timeline([], now_ms=0)
        self.assertFalse(out['available']); self.assertIsNone(out['span_ms'])


class ArduPilotExportTests(unittest.TestCase):
    EXPORTER = [HERE.parent.parent / 'scripts' / 'export_ardupilot_logs.py',
                Path.home() / 'hu-telepresence-gazebo' / 'scripts' / 'export_ardupilot_logs.py']

    def make_session(self, root):
        s = root / 'SO-TEST-BIN'; s.mkdir()
        sys.path.insert(0, str(SPACEOPS))
        import csv
        fields = ('utc', 'epoch_ms', 'elapsed_s', 'vehicle', 'mode', 'fault', 'telemetry_fresh', 'x_m', 'y_m', 'z_m',
                  'vx_est_mps', 'vy_est_mps', 'vz_est_mps', 'ground_speed_est_mps', 'yaw_deg', 'target_x_m',
                  'target_y_m', 'target_z_m', 'battery_sim_pct', 'closest_aircraft', 'closest_3d_m', 'min_fleet_3d_m')
        t0 = 1794376303111
        for i, n in enumerate(NAMES):
            with (s / f'{n.lower()}_flight.csv').open('w', newline='') as f:
                w = csv.DictWriter(f, fieldnames=fields); w.writeheader()
                for k in range(8):
                    w.writerow({'utc': 'x', 'epoch_ms': t0 + 250 * k, 'elapsed_s': k / 4, 'vehicle': n,
                                'mode': 'TAKEOFF' if k < 4 else 'HOVER', 'fault': False,
                                'telemetry_fresh': k != 5, 'x_m': i, 'y_m': 0, 'z_m': .1 * k, 'vx_est_mps': 0,
                                'vy_est_mps': 0, 'vz_est_mps': .4, 'ground_speed_est_mps': 0, 'yaw_deg': 0,
                                'target_x_m': i, 'target_y_m': 0, 'target_z_m': 1.7, 'battery_sim_pct': 99.5,
                                'closest_aircraft': 'Ra', 'closest_3d_m': 1.0, 'min_fleet_3d_m': 1.0})
        write_jsonl(s / 'flight_events.jsonl', [ev('2026-11-11T05:51:43.500Z', 'GZ_HOVER', 'Horus reached', ['Horus'])])
        return s

    def test_zip_has_five_simulated_bins(self):
        with tempfile.TemporaryDirectory() as tmp:
            sess = self.make_session(Path(tmp))
            snap = panels.snapshot_session(sorted(sess.iterdir()), Path(tmp) / 'snap', sess.name)
            raw, summary = panels.ardupilot_zip(sess.name, snap, self.EXPORTER)
            self.assertFalse(summary['native_ardupilot_firmware_log'])
            self.assertFalse((sess / 'ardupilot_logs').exists())     # original session folder untouched
            out = Path(tmp) / 'unzipped'
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                names = z.namelist()
                self.assertEqual(sorted(n for n in names if n.endswith('.BIN')), sorted(f'{n}.BIN' for n in NAMES))
                self.assertIn('README.txt', names); self.assertIn('metadata.json', names)
                self.assertIn('SIMULATED', z.read('README.txt').decode())
                z.extractall(out)
            for n in NAMES:
                data = (out / f'{n}.BIN').read_bytes()
                self.assertIn(b'HU-MAAT SIMULATED GAZEBO TELEMETRY - NOT ARDUPILOT FIRMWARE', data)
            try:
                from pymavlink import mavutil
            except ImportError:
                self.skipTest('pymavlink not installed; byte-level check passed')
            m = mavutil.mavlink_connection(str(out / 'Horus.BIN'))
            first = m.recv_match(type='MSG')
            self.assertIn('SIMULATED', first.Message)
            parms = {}
            while True:
                p = m.recv_match(type=['PARM', 'GPS'])
                if p is None:
                    break
                if p.get_type() == 'PARM':
                    parms[p.Name] = p.Value
            self.assertEqual(parms.get('HU_SIMULATED'), 1.0)
            m.close()

    def test_no_data_yet(self):
        with tempfile.TemporaryDirectory() as tmp:
            snap = panels.snapshot_session([], Path(tmp), 'SO-EMPTY')
            with self.assertRaises(LookupError):
                panels.ardupilot_zip('SO-EMPTY', snap, self.EXPORTER)


class EndpointTests(unittest.TestCase):
    """New GET endpoints through the real Handler on an ephemeral localhost port (no live services)."""

    def test_endpoints_read_only_with_security_headers(self):
        import spaceops_server as srv
        with tempfile.TemporaryDirectory() as tmp:
            rec = srv.Recorder(Path(tmp) / 'SO-HTTP')
            rec.append_event('SIMULATED_LINK_FAULT', 'x', ['Anubis'])
            srv.Handler.rec = rec
            http = ThreadingHTTPServer(('127.0.0.1', 0), srv.Handler)
            t = threading.Thread(target=http.serve_forever, daemon=True); t.start()
            root = f'http://127.0.0.1:{http.server_address[1]}'
            try:
                for route in ('/api/ops/formations', '/api/ops/latency', '/api/ops/faults'):
                    r = request.urlopen(root + route)
                    self.assertEqual(r.headers['X-Content-Type-Options'], 'nosniff')
                    self.assertEqual(r.headers['Cache-Control'], 'no-store')
                    body = json.load(r); self.assertIsInstance(body, dict)
                faults = json.load(request.urlopen(root + '/api/ops/faults'))
                self.assertEqual(faults['lanes']['Anubis'][0]['status'], 'OPEN')
                with self.assertRaises(error.HTTPError) as cm:
                    request.urlopen(root + '/api/ops/export/ardupilot')     # no telemetry recorded yet
                self.assertEqual(cm.exception.code, 409)
                self.assertIn('No data yet', json.load(cm.exception)['error'])
                with self.assertRaises(error.HTTPError) as cm:
                    request.urlopen(request.Request(root + '/api/ops/faults', data=b'{}', method='POST'))
                self.assertEqual(cm.exception.code, 404)
                # with recorded samples the export is a zip of SIMULATED .BIN files
                old = srv.json_req
                srv.json_req = lambda *a, **k: {'world': 'w', 'events': [], 'drones': [
                    {'name': n, 'x': i * 1.5, 'y': 0, 'z': 1.2, 'target': [i * 1.5, 0, 1.2], 'mode': 'FORMATION',
                     'fault': False, 'yaw': 0, 'telemetry': True} for i, n in enumerate(NAMES)]}
                try:
                    rec.poll(); rec.poll()
                finally:
                    srv.json_req = old
                live = json.load(request.urlopen(root + '/api/ops/formations'))['live']
                self.assertTrue(live['active']); self.assertEqual(live['rms_err_m'], 0.0)
                r = request.urlopen(root + '/api/ops/export/ardupilot')
                self.assertEqual(r.headers['Content-Type'], 'application/zip')
                self.assertIn('SIMULATED', r.headers['Content-Disposition'])
                with zipfile.ZipFile(io.BytesIO(r.read())) as z:
                    self.assertEqual(len([n for n in z.namelist() if n.endswith('.BIN')]), 5)
            finally:
                http.shutdown(); http.server_close()

    def test_link_transitions_are_recorded_as_events(self):
        import spaceops_server as srv
        with tempfile.TemporaryDirectory() as tmp:
            rec = srv.Recorder(Path(tmp) / 'SO-LINK')
            old = srv.json_req

            def st(stale):
                return {'world': 'w', 'events': [], 'drones': [
                    {'name': n, 'x': i * 1.5, 'y': 0, 'z': 1.2, 'target': [i * 1.5, 0, 1.2], 'mode': 'HOVER',
                     'fault': False, 'yaw': 0, 'telemetry': not (stale and n == 'Ra')} for i, n in enumerate(NAMES)]}
            try:
                for s in (False, True, True, False):
                    srv.json_req = lambda *a, s=s, **k: st(s); rec.poll()
            finally:
                srv.json_req = old
            kinds = [e['kind'] for e in reversed(rec.events) if e['kind'].startswith('LINK_')]
            self.assertEqual(kinds, ['LINK_STALE', 'LINK_FRESH'])
            ep = rec.faults()['lanes']['Ra'][0]
            self.assertEqual(ep['type'], 'TELEMETRY'); self.assertEqual(ep['status'], 'RECOVERED')


class PageTests(unittest.TestCase):
    def test_page_has_new_panels_and_honest_labels(self):
        html = (SPACEOPS / 'index.html').read_text(encoding='utf8')
        for needle in ('id="formations"', 'id="latency"', 'id="faulttimeline"', 'id="export-bin"',
                       'EXPORT ARDUPILOT .BIN (SIMULATED TELEMETRY)', 'NOT MEASURED',
                       'ARDUPILOT DATAFLASH FORMAT OF SIMULATED GAZEBO TELEMETRY, NOT ARDUPILOT FIRMWARE LOGS'):
            self.assertIn(needle, html)
        self.assertNotIn('NOT AN ARDUPILOT DATAFLASH .BIN RECORDING', html)
        self.assertIn('id="swarmchat"', html)


if __name__ == '__main__':
    unittest.main()
