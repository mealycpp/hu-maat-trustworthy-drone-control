#!/usr/bin/env python3
"""Export a SpaceOps session as ArduPilot DataFlash (.BIN) logs, one file per aircraft.

  python3 scripts/export_ardupilot_logs.py [session_dir] [--origin LAT,LON,ALT]

session_dir defaults to the newest ~/hu-telepresence-gazebo/logs/spaceops_sessions/SO-*.
Output: <session_dir>/ardupilot_logs/<Name>.BIN  (+ a README.txt)

HONESTY: these files use the ArduPilot DataFlash FORMAT so standard tools (Mission
Planner, UAV Log Viewer, MAVExplorer, pymavlink) can open them, but the data is
SIMULATED Gazebo telemetry sampled by SpaceOps at ~4 Hz - not produced by ArduPilot
firmware or real sensors. Every file says so in its first MSG lines and in PARM
HU_SIMULATED=1. Local arena metres are placed at a stated reference origin (default:
Cal Poly Pomona) with an ENU flat-earth conversion. Battery (BAT.RemPct) is the
SpaceOps SIMULATED ESTIMATE. Roll/pitch are not recorded, so ATT carries yaw only.

Message set (TimeUS = microseconds since session start):
  FMT, PARM, MSG, MODE, GPS (Lat/Lng/Alt/Spd/GCrs/VZ), POS, ATT (DesYaw/Yaw),
  CTUN-like HUCT (DAlt, Alt, TrkErr), BAT (RemPct, simulated), HUSP (nearest aircraft
  distance, fleet minimum separation), EV-like events as MSG text.
"""
import csv, json, math, struct, sys
from datetime import datetime, timezone
from pathlib import Path

HEAD = b'\xA3\x95'
GPS_EPOCH = datetime(1980, 1, 6, tzinfo=timezone.utc)
LEAP_S = 18
R_EARTH = 6378137.0
NAMES = ('Horus', 'Ra', 'Thoth', 'Anubis', 'Osiris')
# ArduCopter flight-mode numbers used for MODE/ModeNum
MODE_NUM = {'READY': 0, 'GROUNDED': 0, 'WAITING': 0, 'QUEUED': 0, 'TAKEOFF': 4, 'MOVE': 4, 'FORMATION': 4,
            'TURN': 4, 'SPIN': 4, 'HOVER': 5, 'LANDING': 9, 'FAULT': 5}

FORMATS = {  # id: (name, struct-chars, columns)
    129: ('PARM', 'QNf', 'TimeUS,Name,Value'),
    130: ('MSG', 'QZ', 'TimeUS,Message'),
    131: ('MODE', 'QMBB', 'TimeUS,Mode,ModeNum,Rsn'),
    132: ('GPS', 'QBBIHBcLLeffffB', 'TimeUS,I,Status,GMS,GWk,NSats,HDop,Lat,Lng,Alt,Spd,GCrs,VZ,Yaw,U'),
    133: ('POS', 'QLLfff', 'TimeUS,Lat,Lng,Alt,RelHomeAlt,RelOriginAlt'),
    134: ('ATT', 'Qcc', 'TimeUS,DesYaw,Yaw'),
    135: ('HUCT', 'Qfff', 'TimeUS,DAlt,Alt,TrkErr'),
    136: ('BAT', 'QBf', 'TimeUS,Inst,RemPct'),
    137: ('HUSP', 'Qff', 'TimeUS,Nearest,FleetMin'),
}
PACK = {'b': 'b', 'B': 'B', 'h': 'h', 'H': 'H', 'i': 'i', 'I': 'I', 'f': 'f', 'd': 'd', 'n': '4s', 'N': '16s',
        'Z': '64s', 'c': 'h', 'C': 'H', 'e': 'i', 'E': 'I', 'L': 'i', 'M': 'B', 'q': 'q', 'Q': 'Q'}


def struct_fmt(chars):
    return '<' + ''.join(PACK[c] for c in chars)


class DFWriter:
    def __init__(self, path):
        self.f = open(path, 'wb')
        fmt = struct_fmt('BBnNZ')
        self.f.write(HEAD + bytes([128]) + struct.pack(fmt, 128, 3 + struct.calcsize(fmt), b'FMT', b'BBnNZ',
                                                     b'Type,Length,Name,Format,Columns'))
        for mid, (name, chars, cols) in FORMATS.items():
            ln = 3 + struct.calcsize(struct_fmt(chars))
            self.f.write(HEAD + bytes([128]) + struct.pack(fmt, mid, ln, name.encode(), chars.encode(), cols.encode()))
        self.ids = {name: (mid, struct_fmt(chars)) for mid, (name, chars, _) in FORMATS.items()}

    def write(self, name, *vals):
        mid, fmt = self.ids[name]
        vals = [v.encode()[:64] if isinstance(v, str) else v for v in vals]
        self.f.write(HEAD + bytes([mid]) + struct.pack(fmt, *vals))

    def close(self):
        self.f.close()


def num(v, default=None):
    try:
        x = float(v); return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default


def gps_time(epoch_ms):
    t = datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc)
    secs = (t - GPS_EPOCH).total_seconds() + LEAP_S
    week = int(secs // 604800)
    return int(round((secs - week * 604800) * 1000)), week


def latlon(x, y, origin):
    lat0, lon0, _ = origin
    lat = lat0 + math.degrees(y / R_EARTH)
    lon = lon0 + math.degrees(x / (R_EARTH * math.cos(math.radians(lat0))))
    return int(round(lat * 1e7)), int(round(lon * 1e7))


def export(session: Path, origin=(34.0583, -117.8213, 213.0)):
    out = session / 'ardupilot_logs'; out.mkdir(exist_ok=True)
    events = []
    ev_file = session / 'flight_events.jsonl'
    if ev_file.exists():
        for line in ev_file.read_text(errors='replace').splitlines():
            try: events.append(json.loads(line))
            except ValueError: pass
    written = []
    for name in NAMES:
        csv_path = session / f'{name.lower()}_flight.csv'
        if not csv_path.exists(): continue
        with csv_path.open(encoding='utf8') as fh:
            rows = list(csv.DictReader(fh))
        if not rows: continue
        t0_ms = int(rows[0]['epoch_ms'])
        w = DFWriter(out / f'{name}.BIN')
        us = lambda epoch_ms: max(0, int((int(epoch_ms) - t0_ms) * 1000))
        for text in ('HU-MAAT SIMULATED GAZEBO TELEMETRY - NOT ARDUPILOT FIRMWARE',
                     f'Vehicle {name}; session {session.name}; sampled ~4 Hz by SpaceOps',
                     f'Origin {origin[0]:.6f},{origin[1]:.6f} alt {origin[2]:.1f} m (arena ENU, flat-earth)',
                     'BAT.RemPct is a SIMULATED estimate; ATT has yaw only (roll/pitch not recorded)'):
            w.write('MSG', 0, text)
        for pname, val in (('HU_SIMULATED', 1.0), ('HU_SAMPLE_HZ', 4.0), ('HU_ORIG_LAT', origin[0]),
                           ('HU_ORIG_LNG', origin[1]), ('HU_ORIG_ALT', origin[2])):
            w.write('PARM', 0, pname.encode().ljust(16, b'\0')[:16], float(val))
        last_mode = None; prev = None; n = 0
        for r in rows:
            t = us(r['epoch_ms']); mode = r.get('mode') or 'UNKNOWN'
            if mode != last_mode:
                w.write('MODE', t, MODE_NUM.get(mode, 0), MODE_NUM.get(mode, 0), 0)
                w.write('MSG', t, f'Supervisor mode {mode}' + (' (FAULT ACTIVE)' if r.get('fault') == 'True' else ''))
                last_mode = mode
            if r.get('telemetry_fresh') != 'True':
                if prev is None or prev.get('telemetry_fresh') == 'True':
                    w.write('MSG', t, 'TELEMETRY STALE - samples omitted until fresh')
                prev = r; continue
            x, y, z = num(r['x_m'], 0), num(r['y_m'], 0), num(r['z_m'], 0)
            lat, lng = latlon(x, y, origin)
            vx, vy, vz = num(r['vx_est_mps'], 0), num(r['vy_est_mps'], 0), num(r['vz_est_mps'], 0)
            spd = math.hypot(vx, vy); crs = (math.degrees(math.atan2(vx, vy)) + 360) % 360  # ENU -> course from north
            yaw_enu = num(r['yaw_deg'], 0); hdg = (90 - yaw_enu) % 360                     # ENU yaw -> compass heading
            gms, gwk = gps_time(int(r['epoch_ms']))
            w.write('GPS', t, 0, 3, gms, gwk, 0, 0, lat, lng, int(round((origin[2] + z) * 100)), spd, crs, vz, hdg, 1)
            w.write('POS', t, lat, lng, origin[2] + z, z, z)
            tz = num(r.get('target_z_m'))
            tx, ty = num(r.get('target_x_m')), num(r.get('target_y_m'))
            trk = math.dist((x, y, z), (tx, ty, tz)) if None not in (tx, ty, tz) else float('nan')
            w.write('ATT', t, int(round(hdg * 100)) if hdg < 327 else int(round((hdg - 360) * 100)),
                    int(round(hdg * 100)) if hdg < 327 else int(round((hdg - 360) * 100)))
            w.write('HUCT', t, tz if tz is not None else float('nan'), z, trk)
            w.write('BAT', t, 0, num(r.get('battery_sim_pct'), float('nan')))
            w.write('HUSP', t, num(r.get('closest_3d_m'), float('nan')), num(r.get('min_fleet_3d_m'), float('nan')))
            prev = r; n += 1
        t_end_ms = int(rows[-1]['epoch_ms'])
        for e in events:
            if name not in (e.get('targets') or []): continue
            try: ems = int(datetime.fromisoformat(str(e['utc']).replace('Z', '+00:00')).timestamp() * 1000)
            except (KeyError, ValueError): continue
            if t0_ms <= ems <= t_end_ms:
                w.write('MSG', us(ems), f"EV {e.get('kind', '')}: {e.get('message', '')}"[:64])
        w.close()
        written.append((name, out / f'{name}.BIN', n))
    (out / 'README.txt').write_text(
        'ArduPilot DataFlash-format logs exported from HU-MAAT SpaceOps session ' + session.name + '.\n'
        'SIMULATED Gazebo telemetry (~4 Hz), NOT recorded by ArduPilot firmware or real sensors.\n'
        f'Arena metres mapped to lat/lon around origin {origin} (ENU, flat-earth).\n'
        'BAT.RemPct = simulated estimate. ATT = yaw only. Custom messages: HUCT (DAlt/Alt/TrkErr), HUSP (Nearest/FleetMin).\n'
        'Open with UAV Log Viewer (plot.ardupilot.org), Mission Planner (Ctrl-F > Review a Log) or MAVExplorer.\n')
    return written


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    origin = (34.0583, -117.8213, 213.0)
    for a in sys.argv[1:]:
        if a.startswith('--origin='): origin = tuple(float(v) for v in a.split('=', 1)[1].split(','))
    root = Path.home() / 'hu-telepresence-gazebo/logs/spaceops_sessions'
    session = Path(args[0]) if args else sorted(root.glob('SO-*'))[-1]
    for name, path, n in export(session, origin):
        print(f'{name:7} {n:6} samples -> {path}')
