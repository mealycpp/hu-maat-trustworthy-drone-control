#!/usr/bin/env python3
"""SwarmChat conversation-intent router: HU's frozen SmolLM2-135M representation + a trained linear head.

Same recipe as HU's command heads (frozen backbone, mean+max pooled last hidden state, small trained
head), but a SEPARATE checkpoint (spaceops_v8/models/chat_intent_head.pt). The original HU checkpoint,
heads and thresholds are never loaded for training or modified.

  python3 chat_intent.py train     # fit on 'train', report on held-out 'test', save checkpoint
  python3 chat_intent.py "who is the best drone?"

Routing output never authorizes motion: 'command' only means "send to HU + MA'AT authorization".
"""
import json, os, sys, time
from pathlib import Path

os.environ.setdefault('HF_HUB_OFFLINE', '1')
import torch
from transformers import AutoTokenizer, AutoModel

HERE = Path(__file__).resolve().parent
DATA = HERE / 'models' / 'chat_intent_data.json'
CKPT = HERE / 'models' / 'chat_intent_head.pt'
BACKBONE = 'HuggingFaceTB/SmolLM2-135M'   # the same frozen backbone HU uses
MIN_CONF = 0.55                            # below this the router reports 'uncertain'


class IntentRouter:
    def __init__(self, ckpt=CKPT, threads=4, encoder=None, tokenizer=None):
        torch.set_num_threads(threads)
        self.tok = tokenizer or AutoTokenizer.from_pretrained(BACKBONE)
        if self.tok.pad_token is None: self.tok.pad_token = self.tok.eos_token
        self.enc = encoder or AutoModel.from_pretrained(BACKBONE).eval()
        for p in self.enc.parameters(): p.requires_grad_(False)
        self.head = None
        if Path(ckpt).exists():
            ck = torch.load(ckpt, map_location='cpu')
            self.labels = ck['labels']; self.mu = ck['mu']; self.sd = ck['sd']
            self.head = torch.nn.Linear(self.mu.shape[0], len(self.labels)); self.head.load_state_dict(ck['head']); self.head.eval()
            self.meta = ck.get('meta', {})

    @torch.no_grad()
    def features(self, texts):  # identical pooling to hu_service.HU._emb
        b = self.tok(list(texts), return_tensors='pt', padding=True, truncation=True, max_length=64)
        h = self.enc(**b).last_hidden_state; m = b['attention_mask'].unsqueeze(-1)
        return torch.cat([(h * m).sum(1) / m.sum(1), h.masked_fill(m == 0, -1e9).max(1).values], -1).float()

    @torch.no_grad()
    def route(self, text):
        if self.head is None: return {'intent': 'uncertain', 'confidence': 0.0, 'source': 'ROUTER_UNAVAILABLE'}
        t0 = time.monotonic()
        p = torch.softmax(self.head((self.features([text]) - self.mu) / self.sd), -1)[0]
        conf, i = float(p.max()), int(p.argmax())
        top = sorted(((float(v), self.labels[j]) for j, v in enumerate(p)), reverse=True)[:3]
        return {'intent': self.labels[i] if conf >= MIN_CONF else 'uncertain', 'best': self.labels[i], 'confidence': round(conf, 3),
                'top3': [(l, round(v, 3)) for v, l in top], 'latency_ms': round((time.monotonic() - t0) * 1000, 1),
                'source': 'HU_FROZEN_ENCODER+CHAT_INTENT_HEAD'}


def train(seed=0, epochs=500):
    torch.manual_seed(seed)
    data = json.loads(DATA.read_text())
    labels = list(data['train'])
    r = IntentRouter(ckpt='/nonexistent')
    texts = [t for k in labels for t in data['train'][k]]
    y = torch.tensor([labels.index(k) for k in labels for _ in data['train'][k]])
    X = r.features(texts); mu, sd = X.mean(0), X.std(0) + 1e-6; Z = (X - mu) / sd
    head = torch.nn.Linear(X.shape[1], len(labels))
    opt = torch.optim.AdamW(head.parameters(), lr=3e-3, weight_decay=2e-2)
    for _ in range(epochs):
        opt.zero_grad()
        loss = torch.nn.functional.cross_entropy(head(torch.nn.functional.dropout(Z, .25)), y, label_smoothing=.05)
        loss.backward(); opt.step()
    test = data['test']
    with torch.no_grad():
        P = torch.softmax(head((r.features([q for q, _ in test]) - mu) / sd), -1)
    pred = P.argmax(-1).tolist(); conf = P.max(-1).values.tolist()
    rows = [(q, g, labels[p], c) for (q, g), p, c in zip(test, pred, conf)]
    acc = sum(g == p for _, g, p, _ in rows) / len(rows)
    covered = [(g, p) for _, g, p, c in rows if c >= MIN_CONF]
    acc_conf = sum(g == p for g, p in covered) / max(1, len(covered))
    cmd_fp = sum(p == 'command' and g != 'command' for _, g, p, _ in rows)
    cmd_fn = sum(g == 'command' and p != 'command' for _, g, p, _ in rows)
    meta = {'backbone': BACKBONE, 'train_n': len(texts), 'test_n': len(rows), 'test_accuracy': round(acc, 3),
            'accuracy_when_confident': round(acc_conf, 3), 'coverage_at_min_conf': round(len(covered) / len(rows), 3),
            'min_conf': MIN_CONF, 'command_false_positives': cmd_fp, 'command_false_negatives': cmd_fn,
            'trained_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'seed': seed}
    torch.save({'labels': labels, 'mu': mu, 'sd': sd, 'head': head.state_dict(), 'meta': meta}, CKPT)
    for q, g, p, c in rows:
        print(f"  {'OK ' if g == p else 'XX '} {p:13} {c:.2f} (gold {g:13}) {q!r}")
    print(json.dumps(meta, indent=2)); print('saved', CKPT)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'train':
        train()
    else:
        r = IntentRouter()
        for t in sys.argv[1:] or ['who is the best drone?']:
            print(t, '->', r.route(t))
