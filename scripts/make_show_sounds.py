#!/usr/bin/env python3
"""Synthesize the formation-show sounds (no downloads): hu_chime.wav, hu_goal.wav, hu_victory.wav.

Written to the Windows Music folder by default (formation_show.sh plays them with SOUND=1 under WSL):
  python3 scripts/make_show_sounds.py [output_dir]
"""
import math, os, struct, subprocess, sys, wave

SR = 44100


def tone(freqs, dur, vol=0.35):
    n = int(SR * dur)
    out = []
    for i in range(n):
        t = i / SR
        env = min(1, i / 400) * min(1, (n - i) / (SR * 0.05))
        s = sum(math.sin(2 * math.pi * f * t) + 0.3 * math.sin(4 * math.pi * f * t) for f in freqs) / len(freqs)
        out.append(vol * env * s)
    return out


def save(path, notes):
    data = []
    for f, d in notes:
        data += tone(f, d) if f else [0.0] * int(SR * d)
    with wave.open(path, 'w') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(b''.join(struct.pack('<h', int(max(-1, min(1, x)) * 32000)) for x in data))
    print('wrote', path)


def default_dir():
    try:  # WSL: the Windows user's Music folder
        prof = subprocess.run(['cmd.exe', '/c', 'echo %USERPROFILE%'], capture_output=True, text=True,
                              timeout=10).stdout.strip()
        return subprocess.run(['wslpath', prof], capture_output=True, text=True).stdout.strip() + '/Music'
    except Exception:
        return os.path.expanduser('~/Music')


if __name__ == '__main__':
    out = sys.argv[1] if len(sys.argv) > 1 else default_dir()
    os.makedirs(out, exist_ok=True)
    G, C2, E2, G2, C3 = 783.99, 1046.5, 1318.5, 1568, 2093
    save(os.path.join(out, 'hu_chime.wav'), [([E2], .08), ([G2], .16)])
    save(os.path.join(out, 'hu_goal.wav'), [([C2], .07), (None, .03), ([E2], .07), (None, .03), ([G2], .07),
                                            (None, .03), ([C3], .07), (None, .05), ([C3, G2], .28)])
    save(os.path.join(out, 'hu_victory.wav'), [([G], .12), (None, .03), ([G], .12), (None, .03), ([G], .12),
                                               (None, .03), ([C2, E2], .5), ([G], .18), (None, .04),
                                               ([C2, E2, G2], .9)])
