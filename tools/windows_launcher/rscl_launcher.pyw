"""RSCL — Reconfigurable Space Computing Lab · HU–MA'AT Control (Windows launcher).

One window for the poster session: big buttons with notes, live status lights, an output panel and
emergency buttons. Every action runs ~/hu-telepresence-gazebo/poster_mode.sh (or HU's emergency channel)
inside WSL; flight safety stays with HU and MA'AT.
"""
import json, queue, re, subprocess, threading, time, tkinter as tk, urllib.request, webbrowser
from pathlib import Path
from tkinter import scrolledtext

HERE = Path(__file__).resolve().parent
NAVY, PANEL, GOLD, TEXT, MUTED = '#06121c', '#0c2130', '#e2b84a', '#e8f1f5', '#8fa9b6'
GREEN, AMBER, RED, GREY = '#3ddc84', '#f2b134', '#ff5c6c', '#4a5d69'
WSL = ['wsl.exe', '-d', 'ubuntu', '--', 'bash', '-lc']
PM = 'bash ~/hu-telepresence-gazebo/poster_mode.sh '
PROBE = '~/hu-telepresence-gazebo/hu-venv/bin/python ~/hu-telepresence-gazebo/acceptance/hu_band_probe.py '
ANSI = re.compile(r'\x1b\[[0-9;]*m')
NOFLASH = 0x08000000  # CREATE_NO_WINDOW

ACTIONS = [
    ('1', 'START EVERYTHING', PM + 'start', GOLD,
     'Starts Gazebo (3-D window), HU, MA\'AT, SpaceOps, LED lights and the automatic '
     'show, then opens both pages in Edge. 1–3 minutes. If Gazebo was closed, everything restarts cleanly.'),
    ('2', 'VISITOR MODE', PM + 'visitor', '#5fb8ff',
     'Pauses the show and lands the drones. Visitors type or speak commands in the HU console (port 8100).'),
    ('3', 'AUTOMATIC SHOW', PM + 'show', '#a98bff',
     'The drones choose every formation in team strategy rounds (proposals, safety veto, decision), fly, '
     'celebrate, land and repeat — nonstop.'),
    ('4', 'STATUS CHECK', PM + 'status', '#6fd6c4',
     'One-screen health check of every service, the drones, the show, the agents and the lights.'),
    ('5', 'STOP', PM + 'stop', '#c0c8cf',
     'Lands the drones and stops the show and lights. Services stay up for a quick restart.'),
    ('6', 'OPEN PAGES', 'PAGES', '#9fb4c0',
     'Re-opens the HU console (8100) and SpaceOps (8766). In SpaceOps: 🔊 DRONE VOICES.'),
]


class App:
    def __init__(self, root):
        self.root = root; self.q = queue.Queue(); self.busy = False
        root.title("RSCL · HU–MA'AT Control"); root.configure(bg=NAVY); root.geometry('1180x780'); root.minsize(1000, 680)
        try:
            self.logo_big = tk.PhotoImage(file=str(HERE / 'rscl_logo.png')).subsample(4)
            root.iconphoto(True, tk.PhotoImage(file=str(HERE / 'rscl_logo.png')).subsample(8))
        except Exception:
            self.logo_big = None
        head = tk.Frame(root, bg=NAVY); head.pack(fill='x', padx=18, pady=(14, 6))
        if self.logo_big:
            tk.Label(head, image=self.logo_big, bg=NAVY).pack(side='left')
        t = tk.Frame(head, bg=NAVY); t.pack(side='left', padx=16)
        tk.Label(t, text="HU–MA'AT  ·  Trustworthy Language Control of a Drone Fleet", fg=TEXT, bg=NAVY,
                 font=('Segoe UI', 21, 'bold')).pack(anchor='w')
        tk.Label(t, text='RSCL — Reconfigurable Space Computing Lab · Cal Poly Pomona · IEEE Telepresence 2026 · Demonstration 7',
                 fg=GOLD, bg=NAVY, font=('Segoe UI', 12)).pack(anchor='w', pady=(2, 0))
        tk.Label(t, text='Every flight command still passes HU (execute / confirm / refuse) and the MA\'AT safety supervisor.',
                 fg=MUTED, bg=NAVY, font=('Segoe UI', 11)).pack(anchor='w', pady=(4, 0))

        body = tk.Frame(root, bg=NAVY); body.pack(fill='both', expand=True, padx=18, pady=6)
        left = tk.Frame(body, bg=NAVY); left.pack(side='left', fill='both', expand=True)
        right = tk.Frame(body, bg=NAVY, width=330); right.pack(side='right', fill='y', padx=(14, 0))

        self.buttons = []
        grid = tk.Frame(left, bg=NAVY); grid.pack(fill='x')
        for i, (num, name, cmd, color, note) in enumerate(ACTIONS):
            card = tk.Frame(grid, bg=PANEL, highlightbackground='#1d3a4c', highlightthickness=1)
            card.grid(row=i // 2, column=i % 2, sticky='nsew', padx=6, pady=6)
            b = tk.Button(card, text=f'{num}   {name}', command=lambda c=cmd, n=name: self.run(c, n), bg=color, fg='#07131c',
                          activebackground=TEXT, font=('Segoe UI', 15, 'bold'), relief='flat', cursor='hand2', padx=10, pady=8)
            b.pack(fill='x', padx=10, pady=(10, 4))
            tk.Label(card, text=note, fg=TEXT, bg=PANEL, font=('Segoe UI', 11), wraplength=360, justify='left').pack(
                anchor='w', padx=12, pady=(0, 10))
            self.buttons.append(b)
        grid.columnconfigure(0, weight=1); grid.columnconfigure(1, weight=1)

        emer = tk.Frame(left, bg='#2a0d14', highlightbackground=RED, highlightthickness=2); emer.pack(fill='x', padx=6, pady=(8, 6))
        tk.Label(emer, text='EMERGENCY — works any time, through HU\'s symbolic channel (no AI in the path)', fg='#ffd5da',
                 bg='#2a0d14', font=('Segoe UI', 11, 'bold')).pack(anchor='w', padx=10, pady=(6, 2))
        row = tk.Frame(emer, bg='#2a0d14'); row.pack(fill='x', padx=10, pady=(0, 8))
        tk.Button(row, text='✋  HOLD  ("stop")', command=lambda: self.emergency('stop'), bg='#ffb02e', fg='#1a0b00',
                  font=('Segoe UI', 14, 'bold'), relief='flat', cursor='hand2', padx=12, pady=6).pack(side='left', expand=True, fill='x', padx=(0, 6))
        tk.Button(row, text='⬇  LAND NOW  (latched)', command=lambda: self.emergency('land now'), bg=RED, fg='white',
                  font=('Segoe UI', 14, 'bold'), relief='flat', cursor='hand2', padx=12, pady=6).pack(side='left', expand=True, fill='x')

        tk.Label(left, text='Output', fg=MUTED, bg=NAVY, font=('Segoe UI', 11, 'bold')).pack(anchor='w', padx=6)
        self.out = scrolledtext.ScrolledText(left, height=10, bg='#030b11', fg='#cfe6ee', insertbackground=TEXT,
                                             font=('Consolas', 11), relief='flat')
        self.out.pack(fill='both', expand=True, padx=6, pady=(2, 6))
        self.log('Ready. Press 1 to start everything. Emergency buttons are always active.')

        tk.Label(right, text='LIVE STATUS', fg=GOLD, bg=NAVY, font=('Segoe UI', 13, 'bold')).pack(anchor='w', pady=(4, 6))
        self.lights = {}
        for key, label in [('gz', 'Gazebo simulator'), ('sup', "MA'AT supervisor :8765"), ('hu', 'HU language model :8100'),
                           ('ops', 'SpaceOps :8766'), ('chat', 'Chat model :8767 (optional)'), ('drones', 'Drones'),
                           ('show', 'Automatic show'), ('lights', 'LED lights')]:
            f = tk.Frame(right, bg=PANEL); f.pack(fill='x', pady=3)
            dot = tk.Label(f, text='●', fg=GREY, bg=PANEL, font=('Segoe UI', 16)); dot.pack(side='left', padx=(8, 6))
            tk.Label(f, text=label, fg=TEXT, bg=PANEL, font=('Segoe UI', 11, 'bold')).pack(anchor='w')
            val = tk.Label(f, text='checking…', fg=MUTED, bg=PANEL, font=('Segoe UI', 10), wraplength=260, justify='left')
            val.pack(anchor='w', padx=(34, 4), pady=(0, 4))
            self.lights[key] = (dot, val)
        tips = ('Visitors can try:\n• Everyone, take off\n• All form a circle\n• All form Cassiopeia\n• Everyone except Anubis, hover\n'
                '• This project is really taking off  (refused)\n• khalas  (emergency hold)')
        tk.Label(right, text=tips, fg=TEXT, bg='#10283a', font=('Segoe UI', 11), justify='left', padx=10, pady=8).pack(fill='x', pady=(12, 0))

        root.after(200, self.pump); threading.Thread(target=self.poll, daemon=True).start()

    # ---- running commands -----------------------------------------------------------------------------
    def log(self, s):
        self.out.insert('end', s.rstrip() + '\n'); self.out.see('end')

    def pump(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == 'log':
                    self.log(payload)
                elif kind == 'done':
                    self.busy = False
                    for b in self.buttons:
                        b.config(state='normal')
                elif kind == 'status':
                    for key, (color, text) in payload.items():
                        dot, val = self.lights[key]; dot.config(fg=color); val.config(text=text)
        except queue.Empty:
            pass
        self.root.after(200, self.pump)

    def run(self, cmd, name):
        if cmd == 'PAGES':
            webbrowser.open('http://localhost:8100'); webbrowser.open('http://localhost:8766'); self.log('Opened HU console and SpaceOps.')
            return
        if self.busy:
            self.log('Busy — wait for the current step to finish (emergency buttons still work).'); return
        self.busy = True
        for b in self.buttons:
            b.config(state='disabled')
        self.log(f'\n▶ {name}')
        threading.Thread(target=self._exec, args=(cmd,), daemon=True).start()

    def _exec(self, cmd, done=True):
        try:
            p = subprocess.Popen(WSL + [cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                 encoding='utf-8', errors='replace', creationflags=NOFLASH)
            for line in p.stdout:
                self.q.put(('log', ANSI.sub('', line)))
            p.wait()
        except Exception as e:
            self.q.put(('log', f'error: {e}'))
        if done:
            self.q.put(('done', None))

    def emergency(self, phrase):
        self.log(f'\n⚠ EMERGENCY: "{phrase}" sent through HU')
        threading.Thread(target=self._exec, args=(f"pkill -f 'bash acceptance/formation_show.sh'; {PROBE} '{phrase}'", False),
                         daemon=True).start()

    # ---- live status ----------------------------------------------------------------------------------------
    def get(self, url):
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                return json.load(r)
        except Exception:
            return None

    def poll(self):
        while True:
            st = {}
            sup = self.get('http://127.0.0.1:8765/api/state')
            hu = self.get('http://127.0.0.1:8100/api/health')
            ops = self.get('http://127.0.0.1:8766/api/ops/session')
            chat = self.get('http://127.0.0.1:8767/health')
            if sup:
                ds = sup.get('drones', [])
                fresh = sum(1 for d in ds if d.get('telemetry'))
                st['sup'] = (GREEN, 'running')
                st['gz'] = (GREEN if fresh == 5 else AMBER, f'{fresh}/5 drones reporting')
                modes = sorted({d['mode'] for d in ds})
                st['drones'] = (GREEN if modes and modes[0] not in ('FAULT',) else AMBER,
                                ', '.join(modes) + ('  ·  altitude ' + ', '.join(f"{d['name'][0]} {d['z']:.1f}" for d in ds)))
            else:
                st['sup'] = (RED, 'not running — press 1'); st['gz'] = (GREY, 'unknown'); st['drones'] = (GREY, 'unknown')
            st['hu'] = (GREEN, 'model loaded') if hu and hu.get('checkpoint_loaded') else (RED, 'not running — press 1')
            st['ops'] = (GREEN, 'online') if ops else (RED, 'not running — press 1')
            st['chat'] = ((GREEN, 'voice model loaded') if chat and chat.get('loaded') else
                          (AMBER, 'loading / off — chat uses plain replies') if chat else (GREY, 'off (optional)'))
            try:
                # [x] bracket patterns: pgrep must not count this very status command line as a match
                out = subprocess.run(WSL + ["pgrep -f '[b]ash acceptance/formation_show.sh' >/dev/null && echo S; "
                                            "pgrep -f '[a]cceptance/light_show.py' >/dev/null && echo L; "
                                            "grep -E 'formed' /tmp/show.log 2>/dev/null | tail -1"],
                                     capture_output=True, text=True, timeout=8, creationflags=NOFLASH).stdout.splitlines()
                show = 'S' in out; lights = 'L' in out
                last = next((x for x in out if 'formed' in x), '')
                st['show'] = (GREEN, 'running · ' + ' '.join(last.split())) if show else (GREY, 'paused / stopped')
                st['lights'] = (GREEN, 'on') if lights else (GREY, 'off')
            except Exception:
                pass
            self.q.put(('status', st))
            time.sleep(3)


if __name__ == '__main__':
    root = tk.Tk(); App(root); root.mainloop()
