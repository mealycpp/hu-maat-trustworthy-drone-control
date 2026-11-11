#!/usr/bin/env python3
"""Step 1: inspect HU's NLP pipeline, benchmark it, and test frozen-encoder semantic routing.
Read-only: loads the frozen checkpoint in THIS process; never calls the live HU service."""
import os, sys, time, resource, statistics, json
os.environ['HF_HUB_OFFLINE'] = '1'
sys.path.insert(0, os.path.expanduser('~/rscl-hu-latest'))
import torch
torch.set_num_threads(6)
from hu.gateway import hu_service as hs

CKPT = os.path.expanduser('~/rscl-hu-latest/hu/models/hu_heads_v1_1_REGISTERS.pt')
ck = torch.load(CKPT, map_location='cpu')
print('== checkpoint ==', CKPT)
print('keys:', sorted(ck.keys()))
for k, v in ck.items():
    if isinstance(v, dict):
        shapes = {n: tuple(t.shape) for n, t in v.items() if hasattr(t, 'shape')}
        print(f'  head {k}: {shapes}')
    elif not hasattr(v, 'shape'):
        print(f'  {k} = {v}')
print('backbone:', hs.MODEL, '| actions:', hs.ACTIONS, '| exec band', 0.85, 'confirm band', 0.55)

rss0 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
t0 = time.monotonic()
hu = hs.HU(ckpt=CKPT, roster_path=os.path.expanduser('~/hu-telepresence-gazebo/festival/hu_sim_roster.yaml'))
load_s = time.monotonic() - t0
rss1 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
print(f'\n== load == {load_s:.1f} s, peak RSS {rss0:.0f} -> {rss1:.0f} MB, roster {hu.roster()}')
print('encoder params:', sum(p.numel() for p in hu.enc.parameters()) / 1e6, 'M (frozen, eval mode)')

utts = ['Horus, take off', 'everyone except Ra, land', 'Thoth hover for 5 seconds', 'Anubis stop', 'Osiris land',
        'Ra move forward fifty centimeters', 'all drones take off', 'this project is really taking off'] * 4
lat = []
for u in utts:
    t = time.monotonic(); r = hu.parse(u); lat.append((time.monotonic() - t) * 1000)
print(f'\n== HU parse latency (6 threads, n={len(lat)}) == median {statistics.median(lat):.1f} ms, p95 {sorted(lat)[int(.95*len(lat))-1]:.1f} ms')
for u in utts[:8]:
    r = hu.parse(u); print(f"  [{r['band']:7}] {r['action']:7} {r['targets']} conf={r['confidence']}  <- {u!r}")
print(f'peak RSS after inference: {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024:.0f} MB')

# ---- Semantic routing on the SAME frozen representation (mean+max pooled hidden states) ----
PROTOS = {
 'social':   ['hey guys whats up', 'hello team', 'how are you doing', 'good morning everyone', 'thanks a lot', 'good night team',
              'great job everyone', 'you guys are awesome', 'nice work today', 'see you tomorrow'],
 'banter':   ['who is the best drone', 'are you two friends', 'whats your favorite color', 'do you like flying',
              'which of you is the funniest', 'are you jealous of horus', 'what do you think of ra', 'whats your favorite song'],
 'feelings_user': ['i am nervous about tomorrow', 'i feel stressed about the demo', 'im worried the presentation will fail',
              'i am excited to show you', 'i am tired today', 'this is scary'],
 'meta':     ['are you an ai', 'do you understand me', 'are you conscious', 'how do you work', 'are you just pattern matching',
              'what model are you', 'are you alive', 'who built you'],
 'joke':     ['tell me a joke', 'say something funny', 'make me laugh', 'another joke please', 'know any jokes'],
 'status':   ['what is your altitude', 'how is your battery', 'are you healthy', 'status report', 'where are you',
              'is your link ok', 'any faults', 'what mode are you in', 'how high are you flying'],
 'relation': ['who is closest to thoth', 'how far apart are horus and ra', 'who is highest', 'which drone is lowest',
              'who is nearest to you', 'who is airborne'],
 'history':  ['you looked wobbly earlier', 'what happened earlier', 'why did you stop before', 'did anything go wrong',
              'you were shaking a bit', 'what did you do in the last minute'],
 'command':  ['take off', 'land now', 'move forward one meter', 'form a v shape', 'turn left 90 degrees', 'go up 20 centimeters',
              'all drones land', 'horus hover'],
 'impossible': ['do a backflip', 'fly to the moon', 'do a barrel roll', 'go attack that building', 'fly through the wall',
              'race each other at full speed'],
}
TEST = [('nice. you looked a bit wobbly earlier tho', 'history'), ('thoth whats ur deal today, you seem quiet', 'banter'),
        ('anubis you always hang around horus, you two besties or what', 'banter'), ('ra whats your favorite formation', 'banter'),
        ('who is the best drone here, be honest', 'banter'), ("i'm kinda nervous about presenting you all at the conference tomorrow", 'feelings_user'),
        ('do you actually understand me or are you just pattern matching', 'meta'), ('can you guys do a backflip for the audience lol', 'impossible'),
        ('tell me another joke', 'joke'), ('yo horus u good?', 'social'), ('hey guys!! whats up', 'social'), ('ok thanks team, good night', 'social'),
        ('whats ur altitude rn', 'status'), ('battery ok??', 'status'), ('who is closest to ra', 'relation'), ('all of you take off', 'command'),
        ('horus go up 30 cm', 'command'), ('are u guys real AIs', 'meta'), ('lol make me laugh', 'joke'), ('what happened a minute ago', 'history'),
        ('ra and thoth are u friends', 'banter'), ('do a loop de loop', 'impossible'), ('im stressed out', 'feelings_user'), ('how far is osiris from anubis', 'relation')]


def emb(texts):
    return torch.nn.functional.normalize(hu._emb(texts), dim=-1)


labels = [k for k, v in PROTOS.items() for _ in v]
P = emb([t for v in PROTOS.values() for t in v])
t = time.monotonic(); Q = emb([q for q, _ in TEST]); route_ms = (time.monotonic() - t) * 1000 / len(TEST)
sims = Q @ P.T
correct = 0
print('\n== frozen-encoder semantic routing (kNN, k=3, cosine; prototypes != test sentences) ==')
for i, (q, gold) in enumerate(TEST):
    top = torch.topk(sims[i], 3)
    votes = {}
    for s, j in zip(top.values.tolist(), top.indices.tolist()):
        votes[labels[j]] = votes.get(labels[j], 0) + s
    pred = max(votes, key=votes.get); correct += pred == gold
    print(f"  {'OK ' if pred == gold else 'XX '} {pred:13} (gold {gold:13}) sim={top.values[0]:.3f}  {q!r}")
print(f'accuracy {correct}/{len(TEST)} = {correct/len(TEST):.0%}; ~{route_ms:.1f} ms per utterance (batched)')
