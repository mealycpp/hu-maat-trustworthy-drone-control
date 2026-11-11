#!/usr/bin/env python3
"""Can a small TRAINED head on HU's frozen SmolLM2-135M representation route conversation?
Same pattern as HU's command heads (frozen backbone + trained head); HU checkpoint untouched.
Compares: raw kNN, centred+whitened kNN, and a trained linear head. Train and test sentences are disjoint."""
import os, sys, random, time
os.environ['HF_HUB_OFFLINE'] = '1'
sys.path.insert(0, os.path.expanduser('~/rscl-hu-latest'))
import torch
torch.set_num_threads(6); torch.manual_seed(0); random.seed(0)
from transformers import AutoTokenizer, AutoModel

tok = AutoTokenizer.from_pretrained('HuggingFaceTB/SmolLM2-135M'); tok.pad_token = tok.pad_token or tok.eos_token
enc = AutoModel.from_pretrained('HuggingFaceTB/SmolLM2-135M').eval()


@torch.no_grad()
def feats(texts):  # identical pooling to hu_service.HU._emb
    b = tok(texts, return_tensors='pt', padding=True, truncation=True, max_length=64)
    h = enc(**b).last_hidden_state; m = b['attention_mask'].unsqueeze(-1)
    return torch.cat([(h * m).sum(1) / m.sum(1), h.masked_fill(m == 0, -1e9).max(1).values], -1).float()


TRAIN = {
 'social': ['hey guys whats up', 'hello team', 'how are you doing', 'good morning everyone', 'thanks a lot', 'good night team',
            'great job everyone', 'you guys are awesome', 'nice work today', 'see you tomorrow', 'hi there', 'yo whats good',
            'how is everybody', 'thank you so much', 'bye for now', 'cheers team', 'sup drones', 'hiya horus', 'morning ra', 'well done all'],
 'banter': ['who is the best drone', 'are you two friends', 'whats your favorite color', 'do you like flying', 'which of you is the funniest',
            'are you jealous of horus', 'what do you think of ra', 'whats your favorite song', 'who is the coolest', 'do you have a crush on anubis',
            'which formation do you like most', 'are you guys a team or rivals', 'who would win a race', 'whats your hobby', 'are you shy',
            'do you two get along', 'who is the leader', 'is thoth the nerd of the group', 'whats your favourite shape', 'who is the boss'],
 'user_feelings': ['i am nervous about tomorrow', 'i feel stressed about the demo', 'im worried the presentation will fail', 'i am excited to show you',
            'i am tired today', 'this is scary', 'i am anxious', 'honestly i am freaking out', 'i am so proud of you all', 'i feel overwhelmed',
            'im scared it will crash', 'i am happy with the results', 'i didnt sleep well', 'feeling a bit down'],
 'meta':   ['are you an ai', 'do you understand me', 'are you conscious', 'how do you work', 'are you just pattern matching', 'what model are you',
            'are you alive', 'who built you', 'do you have feelings', 'are you sentient', 'are you chatgpt', 'how do you understand language',
            'do you think', 'are you a real person', 'what is hu'],
 'joke':   ['tell me a joke', 'say something funny', 'make me laugh', 'another joke please', 'know any jokes', 'got a joke', 'joke time',
            'cheer me up with a pun', 'tell us something funny', 'one more joke'],
 'status': ['what is your altitude', 'how is your battery', 'are you healthy', 'status report', 'where are you', 'is your link ok', 'any faults',
            'what mode are you in', 'how high are you flying', 'whats your position', 'are you on the ground', 'how is your telemetry',
            'what is your heading', 'how fast are you going', 'is everything ok with you'],
 'relation': ['who is closest to thoth', 'how far apart are horus and ra', 'who is highest', 'which drone is lowest', 'who is nearest to you',
            'who is airborne', 'who is farthest away', 'which two are closest', 'distance between anubis and osiris', 'who is next to you'],
 'history': ['you looked wobbly earlier', 'what happened earlier', 'why did you stop before', 'did anything go wrong', 'you were shaking a bit',
            'what did you do in the last minute', 'how was the last flight', 'why did you hesitate before', 'what happened during the formation',
            'were you stable earlier'],
 'command': ['take off', 'land now', 'move forward one meter', 'form a v shape', 'turn left 90 degrees', 'go up 20 centimeters', 'all drones land',
            'horus hover', 'everyone take off', 'ra move back 50 cm', 'form a circle', 'spin around', 'thoth go down 30 centimeters', 'all stop'],
 'impossible': ['do a backflip', 'fly to the moon', 'do a barrel roll', 'go attack that building', 'fly through the wall', 'race each other at full speed',
            'fly to new york', 'carry me', 'do a flip', 'go faster than sound', 'crash into each other for fun'],
}
TEST = [('nice. you looked a bit wobbly earlier tho', 'history'), ('thoth whats ur deal today, you seem quiet', 'banter'),
        ('anubis you always hang around horus, you two besties or what', 'banter'), ('ra whats your favorite formation', 'banter'),
        ('who is the best drone here, be honest', 'banter'), ("i'm kinda nervous about presenting you all at the conference tomorrow", 'user_feelings'),
        ('do you actually understand me or are you just pattern matching', 'meta'), ('can you guys do a backflip for the audience lol', 'impossible'),
        ('tell me another joke', 'joke'), ('yo horus u good?', 'social'), ('hey guys!! whats up', 'social'), ('ok thanks team, good night', 'social'),
        ('whats ur altitude rn', 'status'), ('battery ok??', 'status'), ('who is closest to ra', 'relation'), ('all of you take off', 'command'),
        ('horus go up 30 cm', 'command'), ('are u guys real AIs', 'meta'), ('lol make me laugh', 'joke'), ('what happened a minute ago', 'history'),
        ('ra and thoth are u friends', 'banter'), ('do a loop de loop', 'impossible'), ('im stressed out', 'user_feelings'),
        ('how far is osiris from anubis', 'relation'), ('wassup osiris', 'social'), ('r u ok anubis', 'status'), ('whos the fastest of u', 'banter'),
        ('can u fly upside down', 'impossible'), ('what went wrong during the last test', 'history'), ('everyone land please', 'command'),
        ('i cant believe how well this works', 'user_feelings'), ('are you guys smart', 'meta')]
labels = list(TRAIN)
Xtr = feats([t for v in TRAIN.values() for t in v]); ytr = torch.tensor([labels.index(k) for k, v in TRAIN.items() for _ in v])
Xte = feats([q for q, _ in TEST]); yte = torch.tensor([labels.index(g) for _, g in TEST])
mu, sd = Xtr.mean(0), Xtr.std(0) + 1e-6
Ztr, Zte = (Xtr - mu) / sd, (Xte - mu) / sd


def knn(A, B, k=3):
    A, B = torch.nn.functional.normalize(A, dim=-1), torch.nn.functional.normalize(B, dim=-1)
    s = B @ A.T; out = []
    for row in s:
        top = torch.topk(row, k); v = torch.zeros(len(labels))
        for val, j in zip(top.values, top.indices): v[ytr[j]] += val
        out.append(int(v.argmax()))
    return torch.tensor(out)


print(f'train {len(ytr)} sentences / test {len(yte)} unseen sentences / {len(labels)} intents')
print(f'raw kNN            : {(knn(Xtr, Xte) == yte).float().mean():.0%}')
print(f'standardised kNN   : {(knn(Ztr, Zte) == yte).float().mean():.0%}')
head = torch.nn.Linear(Xtr.shape[1], len(labels)); opt = torch.optim.AdamW(head.parameters(), lr=3e-3, weight_decay=1e-2)
for epoch in range(400):
    opt.zero_grad(); loss = torch.nn.functional.cross_entropy(head(torch.nn.functional.dropout(Ztr, .2)), ytr); loss.backward(); opt.step()
with torch.no_grad():
    p = torch.softmax(head(Zte), -1); pred = p.argmax(-1)
print(f'trained linear head: {(pred == yte).float().mean():.0%}   (train loss {loss.item():.3f})')
for (q, g), pi, conf in zip(TEST, pred.tolist(), p.max(-1).values.tolist()):
    print(f"  {'OK ' if labels[pi] == g else 'XX '} {labels[pi]:13} {conf:.2f} (gold {g:13}) {q!r}")
