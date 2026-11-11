#!/usr/bin/env python3
"""Print an offline SwarmChat transcript on fake snapshots (run from a dir with swarmchat.py copied in)."""
import sys, tempfile
from pathlib import Path
sys.path.insert(0, '.')
import swarmchat as sc
import test_swarmchat as t

chat = sc.SwarmChat(Path(tempfile.mkdtemp()))
base = t.snapshot(fault=('Anubis',))
stale = t.snapshot(stale=('Anubis',))
Q = [('FLEET', 'status report everyone', base, None), ('Horus', 'Horus, what is your altitude and battery?', base, None),
     ('Anubis', 'Anubis, are you healthy?', base, None), ('FLEET', 'hello team, how is everyone feeling today?', base, None),
     ('Ra', 'Ra, tell me a joke', base, None), ('FLEET', 'who is closest to Thoth?', base, None),
     ('FLEET', 'can you all take off and form a V?', base, None), ('FLEET', 'who is highest?', base, None),
     ('FLEET', 'who is airborne?', stale, None), ('Thoth', 'Thoth, how has your altitude changed in the last 30 s?', base, t.health_climb()),
     ('Anubis', 'Anubis, what is your altitude?', stale, None), ('FLEET', 'how far apart are Horus and Ra?', base, None),
     ('Osiris', 'who are you?', base, None), ('FLEET', 'tell us a joke', base, None), ('Horus', 'are you an AI?', base, None)]
for to, q, st, h in Q:
    print(f'\nOPERATOR -> {to}: {q}')
    for m in chat.ask(q, to, st, [], True, health=h):
        print(f"   [{m['sender']}{' -> ' + m['to'] if m.get('to') not in (None, 'OPERATOR') else ''}] {m['text']}")
