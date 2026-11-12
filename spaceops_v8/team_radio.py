"""Team radio: post drone-to-drone lines into SwarmChat (SpaceOps /api/ops/chat/agent).

Text only; this path can never command an aircraft.
"""
import json, time
from urllib import request

OPS = 'http://127.0.0.1:8766'


class Radio:
    def __init__(self, conversation='radio', pace=0.45):
        self.cid = f'{conversation}-' + time.strftime('%H%M%S')
        self.pace = pace
        try:
            with request.urlopen(OPS + '/api/ops/session', timeout=3) as r:
                self.tok = json.load(r)['token']
        except Exception:
            self.tok = None

    def say(self, speaker, text, evidence=None, to='TEAM', intent='team_strategy'):
        if not self.tok:
            return False
        body = json.dumps({'speaker': speaker, 'text': text, 'to': to, 'conversation_id': self.cid,
                           'evidence': evidence or {}, 'intent': intent}).encode()
        if len(body) > 1000:   # server limit: keep the text, trim bulky evidence
            body = json.dumps({'speaker': speaker, 'text': text, 'to': to, 'conversation_id': self.cid,
                               'evidence': {'trimmed': True}, 'intent': intent}).encode()
        req = request.Request(OPS + '/api/ops/chat/agent', data=body, method='POST',
                              headers={'Content-Type': 'application/json', 'X-RSCL-CSRF': self.tok})
        try:
            request.urlopen(req, timeout=3).read()
        except Exception:
            return False
        time.sleep(self.pace)
        return True


def post_lines(lines, conversation='radio'):
    r = Radio(conversation)
    for speaker, text, ev in lines:
        r.say(speaker, text, ev)
