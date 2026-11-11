#!/usr/bin/env python3
"""Read-only data builders for the SpaceOps V10 analysis panels.

Every function here only READS recorded files (formation catalog results, HU
bridge logs, the current session's event log) and returns bounded JSON-safe
dicts. Nothing here dispatches, POSTs, or writes outside a temp directory.

Honesty rules implemented here:
  * A value that was not recorded is returned as None and labelled
    "not measured" / "not recorded" by the page - it is never estimated.
  * Durations are differences of recorded timestamps / recorded *_ms fields only.
  * "Dispatched" and "observed moving" are never reported as "completed".
  * The ArduPilot export is DataFlash-FORMAT SIMULATED Gazebo telemetry.
"""
from __future__ import annotations
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
import zipfile
from datetime import datetime, timezone

NAMES = ('Horus', 'Ra', 'Thoth', 'Anubis', 'Osiris')
CATALOG_FILE_RE = re.compile(r'^formation_catalog_[0-9A-Za-z_-]{1,40}\.json$')
MAX_RESULT_FILES = 12          # newest acceptance result files scanned for history
MAX_FILE_BYTES = 4_000_000     # never parse a larger catalog / result file


# ---------------------------------------------------------------- helpers
def _num(v):
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _load_json(path: Path):
    try:
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            return None
        return json.loads(path.read_text(encoding='utf8', errors='replace'))
    except (OSError, ValueError):
        return None


def tail_jsonl(path: Path, maximum=400, bytes_limit=1_500_000):
    """Last `maximum` JSON objects of a JSONL file, reading at most `bytes_limit` bytes."""
    try:
        with path.open('rb') as f:
            f.seek(0, os.SEEK_END); size = f.tell(); f.seek(max(0, size - bytes_limit))
            data = f.read().decode('utf-8', 'replace')
    except OSError:
        return []
    lines = data.splitlines()
    if size > bytes_limit and lines:
        lines = lines[1:]               # first line may be cut mid-record
    out = []
    for line in lines[-maximum:]:
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def parse_utc_ms(s):
    """ISO-8601 UTC string -> epoch ms (int) or None."""
    if not s or not isinstance(s, str):
        return None
    try:
        return int(datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp() * 1000)
    except ValueError:
        return None


# ------------------------------------------------- 03D formation performance
_EVIDENCE_FIELDS = ('phrase', 'interpreter', 'planned_min_sep_m', 'settle_s', 'rms_error_m', 'max_error_m',
                    'shape_error_m', 'observed_min_sep_m', 'closest_pair', 'proximity_holds', 'why')


def _result_files(results_dir: Path):
    try:
        files = [p for p in results_dir.iterdir() if CATALOG_FILE_RE.match(p.name)]
    except OSError:
        return []
    return sorted(files, key=lambda p: p.name)[-MAX_RESULT_FILES:]


def load_formation_catalog(catalog_path: Path, results_dir: Path):
    """Formation table: the published catalog joined to its cited evidence run.

    Rows come from the catalog (status/alias/evidence as published). Detailed metrics
    are read from the cited evidence file, never recomputed. `history` lists the status
    the same shape had in each recent result file (e.g. an earlier REFUSED attempt).
    """
    results = {}
    for p in _result_files(results_dir):
        data = _load_json(p)
        if isinstance(data, dict) and isinstance(data.get('formations'), list):
            results[p.name] = data
    catalog = _load_json(catalog_path)
    source = 'catalog'
    if not isinstance(catalog, dict) or not catalog:
        # No published catalog: fall back to the newest raw result file (labelled as such).
        source = 'latest_result_file'
        catalog = {}
        if results:
            name = sorted(results)[-1]
            for f in results[name]['formations']:
                if isinstance(f, dict) and f.get('shape'):
                    catalog[str(f['shape'])] = {'status': f.get('status'), 'alias_of': f.get('alias_of'),
                                                'evidence': name}
    rows = []
    for shape, entry in list(catalog.items())[:64]:
        if not isinstance(entry, dict):
            continue
        evidence = str(entry.get('evidence') or '')
        row = {'shape': str(shape), 'status': str(entry.get('status') or 'UNKNOWN').upper(),
               'alias_of': entry.get('alias_of'), 'evidence': evidence or None,
               'evidence_found': False, 'evidence_generated_utc': None, 'world_env': None}
        for k in _EVIDENCE_FIELDS:
            row[k] = None
        # Catalog-level numbers first (as published) ...
        for k in ('rms_error_m', 'observed_min_sep_m', 'settle_s', 'shape_error_m', 'planned_min_sep_m',
                  'proximity_holds'):
            if k in entry:
                row[k] = entry[k]
        # ... then the cited evidence record (basename only; path traversal impossible).
        ev = results.get(evidence) if CATALOG_FILE_RE.match(evidence) else None
        if ev is None and CATALOG_FILE_RE.match(evidence):
            ev = _load_json(results_dir / evidence)
        if isinstance(ev, dict):
            rec = next((f for f in ev.get('formations') or [] if isinstance(f, dict) and f.get('shape') == shape), None)
            if rec is not None:
                row['evidence_found'] = True
                row['evidence_generated_utc'] = ev.get('generated_utc')
                row['world_env'] = ev.get('world_env')
                row['evidence_status'] = rec.get('status')
                for k in _EVIDENCE_FIELDS:
                    if rec.get(k) is not None:
                        row[k] = rec.get(k)
        row['history'] = [{'file': name, 'status': next((f.get('status') for f in d['formations']
                                                           if isinstance(f, dict) and f.get('shape') == shape), None)}
                          for name, d in sorted(results.items())]
        row['history'] = [h for h in row['history'] if h['status'] is not None]
        rows.append(row)
    criteria = None
    for name in sorted(results, reverse=True):
        if results[name].get('criteria'):
            criteria = results[name]['criteria']; break
    counts = {}
    for r in rows:
        counts[r['status']] = counts.get(r['status'], 0) + 1
    return {'available': bool(rows), 'source': source, 'catalog_file': catalog_path.name,
            'rows': rows, 'counts': counts, 'criteria': criteria,
            'result_files': sorted(results), 'provenance':
            'LIVE-MEASURED in Gazebo by acceptance/formation_catalog_test.py; values copied from the cited '
            'evidence file, not recomputed. Simulation results, not flight-test results.'}


def live_formation(state):
    """Target-vs-actual for aircraft CURRENTLY in FORMATION mode (supervisor setpoint vs odometry)."""
    drones = (state or {}).get('drones') or []
    form = [d for d in drones if isinstance(d, dict) and str(d.get('mode')) == 'FORMATION']
    out = {'active': bool(form), 'count': len(form), 'aircraft': [], 'rms_err_m': None, 'rms_xy_m': None,
           'max_err_m': None, 'stale': [], 'timestamp_utc': (state or {}).get('timestamp_utc'),
           'basis': 'Supervisor setpoint (target) vs Gazebo odometry, fresh telemetry only; '
                    'RMS over aircraft in FORMATION mode at this sample'}
    errs, errs_xy = [], []
    for d in form:
        t = d.get('target') or []
        tgt = [_num(v) for v in t[:3]] if isinstance(t, (list, tuple)) and len(t) >= 3 else [None, None, None]
        pos = [_num(d.get(k)) for k in ('x', 'y', 'z')]
        fresh = bool(d.get('telemetry'))
        err = err_xy = None
        if fresh and None not in tgt and None not in pos:
            err = math.dist(pos, tgt); err_xy = math.hypot(pos[0] - tgt[0], pos[1] - tgt[1])
            errs.append(err); errs_xy.append(err_xy)
        if not fresh:
            out['stale'].append(d.get('name'))
        out['aircraft'].append({'name': d.get('name'), 'fresh': fresh,
                                'x': pos[0] if fresh else None, 'y': pos[1] if fresh else None,
                                'z': pos[2] if fresh else None,
                                'tx': tgt[0], 'ty': tgt[1], 'tz': tgt[2],
                                'err_m': None if err is None else round(err, 3),
                                'err_xy_m': None if err_xy is None else round(err_xy, 3)})
    if errs:
        out['rms_err_m'] = round(math.sqrt(sum(e * e for e in errs) / len(errs)), 3)
        out['rms_xy_m'] = round(math.sqrt(sum(e * e for e in errs_xy) / len(errs_xy)), 3)
        out['max_err_m'] = round(max(errs), 3)
    return out


# ------------------------------------------------ 03E command latency timeline
def decision_label(src):
    s = str(src or '').upper()
    if s.startswith('REAL HU'):
        return 'REAL HU MODEL'
    if s.startswith('DETERMINISTIC'):
        return 'DETERMINISTIC MISSION PLANNER'
    if s.startswith('SYMBOLIC'):
        return 'SYMBOLIC SAFETY'
    return 'NOT RECORDED' if not s else s[:40]


def _norm_utt(s):
    return re.sub(r'\s+', ' ', str(s or '').strip().lower())


def _group_decisions(decisions):
    groups = {}
    order = []
    for d in decisions:
        cid = d.get('cmd_id')
        if not cid:
            continue
        g = groups.get(cid)
        if g is None:
            g = groups[cid] = {'cmd_id': cid, 'records': []}; order.append(cid)
        g['records'].append(d)
    out = []
    for cid in order:
        recs = groups[cid]['records']
        first = next((r for r in recs if r.get('utterance') or r.get('normalized_utterance')), recs[0])
        confirmed = next((r for r in recs if str(r.get('band')) == 'CONFIRMED'), None)
        epoch = [r.get('event_epoch_ms') for r in recs if isinstance(r.get('event_epoch_ms'), (int, float))]
        conf_wait = None
        if confirmed is not None and str(first.get('band')) == 'CONFIRM' \
                and isinstance(first.get('event_epoch_ms'), (int, float)) \
                and isinstance(confirmed.get('event_epoch_ms'), (int, float)):
            conf_wait = round(confirmed['event_epoch_ms'] - first['event_epoch_ms'], 1)
        out.append({'cmd_id': cid, 'utterance': first.get('utterance') or first.get('normalized_utterance'),
                    'first_band': first.get('band'), 'final_band': recs[-1].get('band'),
                    'action': first.get('action'), 'targets': first.get('targets') or [],
                    'decision_source': next((r.get('decision_source') for r in recs if r.get('decision_source')), None),
                    'confidence': first.get('confidence'), 'decision_latency_ms': _num(first.get('latency_ms')),
                    'dispatched': any(r.get('dispatched') is True for r in recs),
                    'reason': first.get('reason'), 'input_source': first.get('source'),
                    'epoch_ms': min(epoch) if epoch else parse_utc_ms(first.get('timestamp_utc')),
                    'confirm_wait_from_log_ms': conf_wait})
    return out


SEGMENTS = (  # key, label, latency-record field
    ('asr', 'SPEECH / ASR', 'asr_ms'),
    ('clarify', 'TRANSCRIPT CLARIFICATION WAIT', 'transcript_clarification_wait_ms'),
    ('decision', 'DECISION ROUND-TRIP (HU / PLANNER)', 'hu_roundtrip_ms'),
    ('confirm', 'OPERATOR CONFIRMATION WAIT', 'operator_confirmation_wait_ms'),
    ('motion', 'DISPATCH ACK → FIRST OBSERVED MOTION', 'dispatch_ack_to_first_observed_ms'),
)


def _segments_from_latency(lat):
    segs = [{'key': k, 'label': lbl, 'ms': _num(lat.get(f)), 'field': f} for k, lbl, f in SEGMENTS]
    first = _num(lat.get('submitted_to_first_observed_ms'))
    allv = _num(lat.get('submitted_to_all_observed_ms'))
    segs.append({'key': 'spread', 'label': 'FIRST → ALL TARGETS OBSERVED', 'field':
                 'submitted_to_all_observed_ms - submitted_to_first_observed_ms',
                 'ms': round(allv - first, 1) if first is not None and allv is not None else None})
    return segs


def latency_timeline(latency_path: Path, decisions_path: Path, maximum=24):
    """Recent commands with separately measured milestones; missing ones stay None."""
    lat_rows = tail_jsonl(latency_path, maximum=60, bytes_limit=600_000)
    groups = _group_decisions(tail_jsonl(decisions_path, maximum=600, bytes_limit=1_500_000))
    by_id = {g['cmd_id']: g for g in groups}
    used = set()
    commands = []
    for lat in lat_rows:
        lat_ms = parse_utc_ms(lat.get('timestamp_utc'))
        g, link = None, 'UNLINKED'
        if lat.get('cmd_id') and lat['cmd_id'] in by_id:
            g, link = by_id[lat['cmd_id']], 'cmd_id'
        elif lat_ms is not None:
            utt = _norm_utt(lat.get('utterance'))
            cands = [x for x in groups if x['cmd_id'] not in used and _norm_utt(x['utterance']) == utt and utt
                     and x['epoch_ms'] is not None and -1000 <= lat_ms - x['epoch_ms'] <= 60_000]
            if cands:
                g, link = max(cands, key=lambda x: x['epoch_ms']), 'utterance + time (<=60 s)'
        if g is not None:
            used.add(g['cmd_id'])
        segs = _segments_from_latency(lat)
        first = _num(lat.get('submitted_to_first_observed_ms'))
        known = [s['ms'] for s in segs if s['key'] in ('clarify', 'decision', 'confirm', 'motion')]
        unattributed = None
        if first is not None and None not in known:
            rest = first - sum(known)
            unattributed = round(rest, 1) if rest >= 0 else None
        commands.append({
            'kind': 'LATENCY_RECORD', 'link': link, 'cmd_id': lat.get('cmd_id') or (g or {}).get('cmd_id'),
            'utc': lat.get('timestamp_utc'), 'epoch_ms': lat_ms,
            'utterance': lat.get('utterance'), 'action': lat.get('action'), 'targets': lat.get('targets') or [],
            'input_source': lat.get('source'), 'outcome': lat.get('outcome'), 'detail': lat.get('detail'),
            'decision_source': decision_label(lat.get('decision_source') or (g or {}).get('decision_source')),
            'band': (g or {}).get('first_band'), 'dispatched': (g or {}).get('dispatched'),
            'segments': segs, 'unattributed_ms': unattributed,
            'hu_inference_ms': _num(lat.get('hu_inference_ms')),
            'voice_onset_detected': lat.get('voice_onset_detected'),
            'totals': {k: _num(lat.get(k)) for k in ('submitted_to_first_observed_ms', 'submitted_to_all_observed_ms',
                                                     'voice_to_first_observed_ms', 'voice_to_all_observed_ms')},
            'per_drone_observed_ms': {k: _num(v) for k, v in (lat.get('per_drone_observed_ms') or {}).items()
                                      if k in NAMES},
            'sample_interval_ms': _num(lat.get('sample_interval_ms')),
            'measurement': str(lat.get('measurement') or '')[:240]})
    for g in groups:
        if g['cmd_id'] in used:
            continue
        segs = [{'key': k, 'label': lbl, 'ms': None, 'field': f} for k, lbl, f in SEGMENTS]
        segs.append({'key': 'spread', 'label': 'FIRST → ALL TARGETS OBSERVED', 'ms': None, 'field': None})
        segs[2].update(ms=g['decision_latency_ms'], field='hu_real_decisions.latency_ms')
        segs[3].update(ms=g['confirm_wait_from_log_ms'],
                       field='CONFIRMED.event_epoch_ms - CONFIRM.event_epoch_ms (bridge log)')
        commands.append({
            'kind': 'DECISION_ONLY', 'link': 'decision log only', 'cmd_id': g['cmd_id'],
            'utc': datetime.fromtimestamp(g['epoch_ms'] / 1000, timezone.utc).isoformat(timespec='milliseconds')
            .replace('+00:00', 'Z') if g['epoch_ms'] else None, 'epoch_ms': g['epoch_ms'],
            'utterance': g['utterance'], 'action': g['action'], 'targets': g['targets'],
            'input_source': g['input_source'],
            'outcome': ('DISPATCHED · MOTION NOT MEASURED' if g['dispatched']
                        else 'REFUSED' if g['first_band'] == 'REFUSE' or g['final_band'] == 'REFUSE'
                        else 'NOT DISPATCHED'),
            'detail': g['reason'], 'decision_source': decision_label(g['decision_source']),
            'band': g['first_band'], 'dispatched': g['dispatched'], 'segments': segs, 'unattributed_ms': None,
            'hu_inference_ms': None, 'voice_onset_detected': None, 'totals': {}, 'per_drone_observed_ms': {},
            'sample_interval_ms': None, 'measurement': 'HU bridge decision log only; no motion timing recorded'})
    # Keep the (rarer) records with motion timing visible next to the frequent decision-only rows.
    key = lambda c: c['epoch_ms'] or 0
    timed = sorted((c for c in commands if c['kind'] == 'LATENCY_RECORD'), key=key, reverse=True)[:maximum // 2]
    rest = sorted((c for c in commands if c['kind'] != 'LATENCY_RECORD'), key=key, reverse=True)[:maximum - len(timed)]
    commands = sorted(timed + rest, key=key, reverse=True)
    return {'available': bool(commands), 'commands': commands,
            'files': {'latency': latency_path.name, 'decisions': decisions_path.name},
            'provenance': 'Milestones copied from hu_voice_to_motion.jsonl (browser performance.now(), '
                          'telemetry-poll bound) and hu_real_decisions.jsonl. Missing = NOT MEASURED. '
                          'Observed motion is the first odometry displacement, not command completion.'}


# --------------------------------------------- 06B fault & recovery timeline
# Classification of recorded event kinds -> (episode type, stages filled).
EPISODE_TYPES = {
    'LINK_FAULT': 'SIMULATED COMMAND-LINK FAULT',
    'SEPARATION': 'SEPARATION / PROXIMITY',
    'TELEMETRY': 'AIRCRAFT TELEMETRY LINK',
    'SUPERVISOR': 'SUPERVISOR LINK (SPACEOPS ↔ 8765)',
}
STAGE_MEANING = {
    'LINK_FAULT': {'onset': 'operator injected simulated fault (SpaceOps, controller ack)',
                   'detection': 'supervisor logged FAULT state', 'containment': 'faulted aircraft refused HOVER',
                   'recovery': 'fault cleared (operator)'},
    'SEPARATION': {'onset': 'SpaceOps sample < 0.85 m (SEPARATION ALERT)',
                   'detection': 'supervisor proximity check < 0.82 m', 'containment': 'supervisor proximity hold',
                   'recovery': 'fleet minimum back >= 0.85 m (WATCH/CLEAR)'},
    'TELEMETRY': {'onset': 'aircraft telemetry flag went stale (FLEET lane: fleet status TELEMETRY LOST)',
                  'detection': 'same sample (stale flag)',
                  'containment': 'supervisor skipped dispatch (stale odometry)', 'recovery': 'telemetry fresh again'},
    'SUPERVISOR': {'onset': 'supervisor /api/state unreachable', 'detection': 'same poll',
                   'containment': 'not applicable / not recorded', 'recovery': 'supervisor reachable again'},
}
MARKER_KINDS = ('GZ_LANDED', 'GZ_STAGGERED_TAKEOFF', 'GZ_STAGGER_RELEASE', 'GZ_STAGGER_CANCELLED',
                'GZ_SAFETY_SKIPPED', 'HU_EMERGENCY', 'REJECTION_TEST_FAILED', 'GZ_ERROR')


def classify_event(e):
    """Map a recorded SpaceOps event to [(episode_type, [stages], scope)].

    scope: 'targets' (named aircraft, FLEET lane if none), 'fleet_lane' (always the FLEET lane),
    'all_lanes' (a fleet-wide recovery closing that type in every lane where it is open).
    """
    kind = str(e.get('kind') or '')
    msg = str(e.get('message') or '')
    if kind == 'SIMULATED_LINK_FAULT':
        return [('LINK_FAULT', ['onset'], 'targets')]
    if kind == 'GZ_DASHBOARD' and msg.startswith('FAULT'):
        return [('LINK_FAULT', ['detection'], 'targets')]
    if kind == 'REJECTION_VERIFIED':
        return [('LINK_FAULT', ['containment'], 'targets')]
    if kind == 'CLEAR_LINK_FAULT' or (kind == 'GZ_DASHBOARD' and msg.startswith('CLEAR_FAULT')):
        return [('LINK_FAULT', ['recovery'], 'targets')]
    if kind == 'SAFETY_SEPARATION_ALERT':
        # Leaving the TELEMETRY LOST fleet status also ends a fleet telemetry episode.
        return [('SEPARATION', ['onset'], 'targets'), ('TELEMETRY', ['recovery'], 'fleet_lane')]
    if kind == 'GZ_SEPARATION_HOLD':
        return [('SEPARATION', ['detection', 'containment'], 'targets')]
    if kind in ('SAFETY_WATCH', 'SAFETY_CLEAR'):
        return [('SEPARATION', ['recovery'], 'all_lanes'), ('TELEMETRY', ['recovery'], 'fleet_lane')]
    if kind == 'SAFETY_TELEMETRY_LOST':
        return [('TELEMETRY', ['onset', 'detection'], 'fleet_lane')]
    if kind == 'LINK_STALE':
        return [('TELEMETRY', ['onset', 'detection'], 'targets')]
    if kind == 'GZ_SAFETY_SKIPPED':
        return [('TELEMETRY', ['containment'], 'targets')]
    if kind == 'LINK_FRESH':
        return [('TELEMETRY', ['recovery'], 'targets')]
    if kind == 'SUPERVISOR_LOST':
        return [('SUPERVISOR', ['onset', 'detection'], 'fleet_lane')]
    if kind == 'SUPERVISOR_ONLINE':
        return [('SUPERVISOR', ['recovery'], 'fleet_lane')]
    return []


def _dur(a, b):
    return None if a is None or b is None else round((b - a) / 1000.0, 3)


def fault_timeline(events, now_ms=None, max_episodes=40, max_markers=120):
    """Pair recorded events into per-lane episodes: onset -> detection -> containment -> recovery.

    Lanes: the five aircraft and FLEET (events without targets). Fleet-wide recovery events
    (no targets, e.g. SAFETY_CLEAR, SUPERVISOR_ONLINE) close the matching open episode in
    every lane. A stage that was not recorded stays None. An episode superseded by a new onset
    without a recorded recovery is marked SUPERSEDED (never assumed recovered).
    """
    evs = []
    for e in events:
        if not isinstance(e, dict):
            continue
        t = parse_utc_ms(e.get('utc'))
        if t is not None:
            evs.append((t, e))
    evs.sort(key=lambda x: x[0])
    lanes = ('FLEET',) + NAMES
    open_ep = {}            # (lane, type) -> episode
    episodes = {l: [] for l in lanes}
    markers = []

    def new_episode(lane, typ, t):
        ep = {'type': typ, 'label': EPISODE_TYPES[typ], 'lane': lane, 'onset_ms': None, 'detection_ms': None,
              'containment_ms': None, 'recovery_ms': None, 'first_ms': t, 'last_ms': t, 'status': 'OPEN',
              'events': [], 'count': 0}
        episodes[lane].append(ep); open_ep[(lane, typ)] = ep
        return ep

    for t, e in evs:
        kind = str(e.get('kind') or '')
        targets = [x for x in (e.get('targets') or []) if x in NAMES]
        if kind in MARKER_KINDS:
            for lane in (targets or ['FLEET']):
                markers.append({'lane': lane, 't_ms': t, 'kind': kind, 'message': str(e.get('message') or '')[:120]})
        for typ, stages, scope in classify_event(e):
            if scope == 'fleet_lane':
                lane_list = ['FLEET']
            elif scope == 'all_lanes':
                lane_list = [l for l in lanes if (l, typ) in open_ep]
            else:
                lane_list = targets or ['FLEET']
            for lane in lane_list:
                ep = open_ep.get((lane, typ))
                if 'recovery' in stages:
                    if ep is None:
                        continue       # recovery without a recorded problem: nothing to pair
                    ep['recovery_ms'] = t; ep['status'] = 'RECOVERED'; ep['last_ms'] = t
                    ep['events'].append({'t_ms': t, 'kind': kind, 'stage': 'recovery'})
                    del open_ep[(lane, typ)]
                    continue
                if 'onset' in stages and ep is not None and ep['onset_ms'] is not None:
                    ep['status'] = 'SUPERSEDED'; del open_ep[(lane, typ)]; ep = None
                if ep is None:
                    ep = new_episode(lane, typ, t)
                for st in stages:
                    if ep[st + '_ms'] is None:
                        ep[st + '_ms'] = t
                ep['last_ms'] = t; ep['count'] += 1
                if len(ep['events']) < 12:
                    ep['events'].append({'t_ms': t, 'kind': kind, 'stage': '+'.join(stages)})
    out_lanes = {}
    for lane in lanes:
        eps = episodes[lane][-max_episodes:]
        for ep in eps:
            o, d, c, r = ep['onset_ms'], ep['detection_ms'], ep['containment_ms'], ep['recovery_ms']
            start = next((x for x in (o, d, c) if x is not None), ep['first_ms'])
            ep['start_ms'] = start
            ep['durations_s'] = {'onset_to_detection': _dur(o, d), 'detection_to_containment': _dur(d, c),
                                 'containment_to_recovery': _dur(c, r), 'onset_to_recovery': _dur(o, r),
                                 'first_to_recovery': _dur(start, r)}
        out_lanes[lane] = eps
    all_t = [t for t, _ in evs]
    return {'available': any(out_lanes[l] for l in lanes) or bool(markers), 'lanes': out_lanes,
            'markers': markers[-max_markers:], 'span_ms': [all_t[0], all_t[-1]] if all_t else None,
            'now_ms': now_ms if now_ms is not None else int(time.time() * 1000),
            'event_count': len(evs), 'types': EPISODE_TYPES, 'stage_meaning': STAGE_MEANING,
            'stage_definitions': {
                'onset': 'first recorded abnormal indicator (sampled; true physical onset may be earlier)',
                'detection': "the safety layer's own recorded flag",
                'containment': 'recorded protective action (refused command, proximity hold, skipped dispatch)',
                'recovery': 'recorded return to nominal (fault cleared, separation back above 0.85 m, link fresh)'},
            'provenance': 'Session flight_events.jsonl only. Timestamps are SpaceOps UTC at logging; supervisor '
                          '(GZ_*) events are stamped when SpaceOps observed them (4 Hz poll, <=~0.25 s later). '
                          'Durations are differences of recorded timestamps; nothing is interpolated.'}


# ------------------------------------------------------ ArduPilot .BIN export
def _load_exporter(candidates):
    for p in candidates:
        p = Path(p)
        if p.is_file():
            spec = importlib.util.spec_from_file_location('hu_export_ardupilot_logs', p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    raise FileNotFoundError('export_ardupilot_logs.py not found')


def ardupilot_zip(session_name: str, snapshot_dir: Path, exporter_candidates, max_input_bytes=400_000_000):
    """Run the DataFlash exporter on a SNAPSHOT copy of a session and return (zip bytes, summary).

    `snapshot_dir` must already contain copies of the session CSV/event files (the caller copies
    them under the recorder lock). Raises LookupError when there is no telemetry yet.
    """
    total = sum(p.stat().st_size for p in snapshot_dir.glob('*') if p.is_file())
    if total > max_input_bytes:
        raise ValueError('Session too large for in-memory export; run scripts/export_ardupilot_logs.py')
    mod = _load_exporter(exporter_candidates)
    written = mod.export(snapshot_dir)
    if not written:
        raise LookupError('No telemetry samples recorded in this session yet')
    out = snapshot_dir / 'ardupilot_logs'
    summary = {'format': 'ArduPilot DataFlash (.BIN) FORMAT', 'data': 'SIMULATED Gazebo telemetry sampled by SpaceOps',
               'native_ardupilot_firmware_log': False, 'session_id': session_name,
               'files': [{'vehicle': n, 'file': p.name, 'samples': k} for n, p, k in written],
               'marker': 'First MSG line of every file: HU-MAAT SIMULATED GAZEBO TELEMETRY - NOT ARDUPILOT FIRMWARE',
               'parm': 'HU_SIMULATED=1', 'exporter': 'scripts/export_ardupilot_logs.py'}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        for _, p, _ in written:
            z.write(p, arcname=p.name)
        if (out / 'README.txt').exists():
            z.write(out / 'README.txt', arcname='README.txt')
        z.writestr('metadata.json', json.dumps(summary, indent=2))
    return buf.getvalue(), summary


def snapshot_session(files, dest_root: Path, session_name: str):
    """Copy the given existing files into dest_root/session_name (caller holds the recorder lock)."""
    d = dest_root / session_name
    d.mkdir(parents=True, exist_ok=True)
    for p in files:
        p = Path(p)
        if p.is_file():
            shutil.copyfile(p, d / p.name)
    return d
