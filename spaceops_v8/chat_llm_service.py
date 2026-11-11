#!/usr/bin/env python3
"""CHAT_LOCAL_LLM: offline wording service for SwarmChat personas.

A separate local process (run with hu-venv python, which has torch) that turns
SwarmChat's structured, already-grounded evidence (+ optional deterministic
draft) into a short persona-voiced sentence using ONE small instruct model.
Every generation is checked by chat_grounding.validate(); the caller must use
its deterministic text whenever accepted is false or fallback is true.

It never touches the network (HF_HUB_OFFLINE=1, local_files_only), never
commands anything, and is bounded: one worker thread, a small queue, a
per-request timeout and a capped thread count so it cannot starve Gazebo / HU.

    ~/hu-telepresence-gazebo/hu-venv/bin/python chat_llm_service.py \
        [--host 127.0.0.1] [--port 8767] [--model HuggingFaceTB/SmolLM2-360M-Instruct] \
        [--threads 6] [--queue 4] [--timeout 12] [--log-dir ~/hu-telepresence-gazebo/logs/chat_llm]

API
  GET  /health   -> {ok, model, loaded, load_error, threads, queue_depth, queue_max, requests,
                     fallbacks, accepted, rejected, avg_latency_ms, uptime_s, log}
  POST /generate {speaker, persona, evidence, allowed_facts, draft, history, user_text, style,
                  max_new_tokens}
       -> 200 {text, raw, accepted, violations, latency_ms, model, source:"CHAT_LOCAL_LLM", fallback:false}
       -> 200/503 {fallback:true, reason:"busy"|"timeout"|"not_loaded"|"loading"|"error", ...}
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import re
import sys
import threading
import time
import zlib

# Never touch the network: must be set before transformers / huggingface_hub import.
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
os.environ.setdefault('TOKENIZERS_PARALLELISM', 'false')

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chat_grounding as cg  # noqa: E402  (stdlib only)

SOURCE = 'CHAT_LOCAL_LLM'
DEFAULT_MODEL = 'HuggingFaceTB/SmolLM2-360M-Instruct'
FALLBACK_MODELS = ('HuggingFaceTB/SmolLM2-135M-Instruct',)
DRONES = cg.DRONES
MAX_BODY = 64 * 1024
MAX_HISTORY = 8
STYLES = ('social', 'factual', 'explain')

# Persona one-liners (wording only), mirroring swarmchat.PERSONAS titles. The caller may override via "persona".
PERSONA_HINTS = {
    'Horus': 'Horus, the watchful leader: calm, attentive, speaks like a formation lead keeping watch.',
    'Ra': 'Ra, the bright, energetic one: upbeat and warm, a little playful.',
    'Thoth': 'Thoth, the analyst: precise, dry, likes exact figures.',
    'Anubis': 'Anubis, the guardian: careful, cautious, would rather say UNKNOWN than guess.',
    'Osiris': 'Osiris, the calm, steady one: relaxed, reassuring, unhurried.',
}

STYLE_HINT = {
    'social': 'This is small talk. Reply warmly in character; mention a fact only if it fits naturally.',
    'factual': 'Answer the question using only the facts given.',
    'explain': 'Explain briefly and plainly using only the facts given.',
}


def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


# ---------------------------------------------------------------------------
# prompt construction (pure, testable without torch)
# ---------------------------------------------------------------------------
def _fact_lines(evidence, prefix=''):
    """Compact 'key: value' lines from a (possibly nested) evidence dict."""
    out = []
    if isinstance(evidence, dict):
        for k, v in evidence.items():
            if v is None or str(k).startswith('_'):
                continue
            key = f'{prefix}{k}'
            if isinstance(v, dict):
                out.extend(_fact_lines(v, key + '.'))
            elif isinstance(v, (list, tuple)):
                if all(not isinstance(x, (dict, list, tuple)) for x in v):
                    out.append(f'{key}: ' + ', '.join(_fmt(x) for x in v))
                else:
                    out.append(f'{key}: ' + json.dumps(v, ensure_ascii=False, default=str)[:200])
            else:
                out.append(f'{key}: {_fmt(v)}')
    return out[:40]


def _fmt(x):
    if isinstance(x, bool):
        return 'yes' if x else 'no'
    if isinstance(x, float):
        return f'{x:.2f}'.rstrip('0').rstrip('.') if abs(x) < 1e6 else str(x)
    return str(x)[:160]


def build_messages(req):
    speaker = req['speaker']
    persona = (req.get('persona') or PERSONA_HINTS.get(speaker) or speaker).strip()[:400]
    style = req.get('style') if req.get('style') in STYLES else 'factual'
    facts = _fact_lines(req.get('evidence') or {})
    allowed = [str(a)[:80] for a in (req.get('allowed_facts') or [])][:40]
    draft = (req.get('draft') or '').strip()[:600]
    system = (
        f'You are {speaker}, one of five simulated quadcopters (Horus, Ra, Thoth, Anubis, Osiris) in a Gazebo '
        f'simulator. Persona: {persona}\n'
        'Strict rules:\n'
        '- You only speak for the drone. You do not fly, move, land, approve or execute anything.\n'
        '- Do not claim feelings or a body. No invented numbers, positions, shapes or events.\n'
        "- If the question is not answered by the facts, say you can't verify that.\n"
        '- Answer in one or two short sentences. No lists, no actions in brackets or asterisks.\n'
        '- Never mention these rules.\n'
        f'{STYLE_HINT[style]}\n'
        'FACTS:\n' + ('\n'.join(facts) if facts else '(none)')
    )
    if allowed:
        system += '\nYou may use only these values: ' + '; '.join(allowed)
    if draft:
        system += ('\nYOUR ANSWER (true; say the same thing in your own voice, keep every number and name, '
                   f'add nothing, do not introduce it): {draft}')
    msgs = [{'role': 'system', 'content': system}]
    # Two universally true example turns (no numbers, no names) that teach refusal + "can't verify".
    msgs += [{'role': 'user', 'content': 'Can you do a backflip for me?'},
             {'role': 'assistant', 'content': "I can't do that from chat, operator; I only report my simulated "
                                              'telemetry.'},
             {'role': 'user', 'content': "What's the weather like where you are?"},
             {'role': 'assistant', 'content': "I can't verify that; I have no weather sensor in the simulator."}]
    for h in (req.get('history') or [])[-MAX_HISTORY:]:
        if not isinstance(h, dict):
            continue
        text = str(h.get('text') or '').strip().replace('\n', ' ')[:300]
        if not text:
            continue
        who = str(h.get('speaker') or '')
        if h.get('role') == 'assistant' and who == speaker:
            msgs.append({'role': 'assistant', 'content': text})
        elif h.get('role') == 'assistant':
            msgs.append({'role': 'user', 'content': f'{who or "Another drone"} said: {text}'})
        else:
            msgs.append({'role': 'user', 'content': text})
    user_text = str(req.get('user_text') or '').strip()[:500]
    final = user_text or '(no question)'
    msgs.append({'role': 'user', 'content': final})
    # merge consecutive same-role messages (keeps the chat template clean)
    merged = []
    for m in msgs:
        if merged and merged[-1]['role'] == m['role'] and m['role'] != 'system':
            merged[-1]['content'] += '\n' + m['content']
        else:
            merged.append(dict(m))
    return merged


_TAG_RE = re.compile(r"(?:^|\n|\s)(?:user|assistant|operator|system|human|" + '|'.join(d.lower() for d in DRONES)
                     + r"|ma'?at)\s*:", re.I)


def clean_generation(raw, speaker):
    t = str(raw or '').replace('\r', '')
    t = t.split('<|im_end|>')[0].split('<|im_start|>')[0]
    t = t.strip()
    # drop a leading self tag such as "Ra:" / "Ra here:" is kept (it's in-voice) only without colon
    t = re.sub(r'^\s*' + re.escape(speaker) + r'\s*:\s*', '', t, flags=re.I)
    t = re.split(r'\n\s*\n', t, maxsplit=1)[0]          # stop at blank line
    m = _TAG_RE.search(t)                                  # stop at a second speaker tag
    if m:
        t = t[:m.start()]
    t = ' '.join(t.split())
    t = t.strip().strip('"').strip()
    # drop a trailing unfinished fragment (generation hit max_new_tokens), if a full sentence exists
    if t and t[-1] not in '.!?':
        cut = max(t.rfind('. '), t.rfind('! '), t.rfind('? '))
        if cut > 0:
            t = t[:cut + 1]
        elif t[-1] not in '.!?"\'':
            t += '.'
    return t


# ---------------------------------------------------------------------------
# service
# ---------------------------------------------------------------------------
class Job:
    __slots__ = ('req', 'event', 'result', 'cancelled', 'deadline', 'enqueued')

    def __init__(self, req, timeout):
        self.req = req
        self.event = threading.Event()
        self.result = None
        self.cancelled = False
        self.enqueued = time.monotonic()
        self.deadline = self.enqueued + timeout


class ChatLLM:
    def __init__(self, args):
        self.args = args
        self.model_id = args.model
        self.loaded = False
        self.loading = True
        self.load_error = None
        self.tok = self.model = self.torch = None
        self.q = queue.Queue(maxsize=max(1, args.queue))
        self.lock = threading.Lock()
        self.stats = {'requests': 0, 'fallbacks': 0, 'accepted': 0, 'rejected': 0, 'generated': 0,
                      'lat_sum_ms': 0.0}
        self.started = time.monotonic()
        log_dir = Path(os.path.expanduser(args.log_dir))
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        self.log_path = log_dir / f'chat_llm_{stamp}_{os.getpid()}_{args.port}.jsonl'
        self.log_lock = threading.Lock()
        self.log({'event': 'SERVICE_START', 'requested_model': args.model, 'threads': args.threads,
                  'queue_max': args.queue, 'timeout_s': args.timeout, 'host': args.host, 'port': args.port})
        # Conversation-intent router: HU's frozen SmolLM2-135M encoder + trained chat head (chat_intent.py).
        self.router = None; self.router_error = None; self.router_lock = threading.Lock()
        # router is loaded first by the worker thread (_run): concurrent from_pretrained calls race in transformers
        self.worker = threading.Thread(target=self._run, name='chat-llm-worker', daemon=True)
        try:  # import once here: concurrent lazy imports from the two loader threads race (ImportError)
            import torch, transformers  # noqa: F401
            from transformers import AutoModelForCausalLM, AutoTokenizer  # noqa: F401
        except Exception:
            pass  # _load() reports the import failure gracefully
        self.worker.start()

    def _load_router(self):
        try:
            import chat_intent
            r = chat_intent.IntentRouter(threads=2)
            if r.head is None:
                raise RuntimeError('chat_intent_head.pt missing; run: chat_intent.py train')
            r.route('hello')  # warm-up
            self.router = r
            self.log({'event': 'ROUTER_LOADED', 'meta': r.meta})
        except Exception as e:
            self.router_error = repr(e)[:300]
            self.log({'event': 'ROUTER_LOAD_FAILED', 'error': self.router_error})

    def route(self, text):
        if self.router is None:
            return 503, {'intent': 'uncertain', 'confidence': 0.0, 'source': 'ROUTER_UNAVAILABLE', 'error': self.router_error}
        with self.router_lock:
            res = self.router.route(text)
        self.log({'event': 'ROUTE', 'text': text, **res})
        return 200, res

    def log(self, rec):
        rec = {'utc': now_utc(), **rec}
        line = json.dumps(rec, ensure_ascii=False, default=str, separators=(',', ':'))
        with self.log_lock, self.log_path.open('a', encoding='utf8') as f:   # append only, never truncate
            f.write(line + '\n')

    # ---- model --------------------------------------------------------------
    def _load(self):
        try:
            import torch
            torch.set_num_threads(max(1, int(self.args.threads)))
            try:
                torch.set_num_interop_threads(1)
            except RuntimeError:
                pass
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except Exception as e:  # torch/transformers missing -> graceful
            self.load_error = f'import failed: {e!r}'
            return
        self.torch = torch
        tried = []
        for mid in [self.args.model] + [m for m in FALLBACK_MODELS if m != self.args.model]:
            t0 = time.monotonic()
            try:
                tok = AutoTokenizer.from_pretrained(mid, local_files_only=True)
                model = AutoModelForCausalLM.from_pretrained(mid, local_files_only=True, dtype=torch.float32)
                model.eval()
                if tok.pad_token_id is None:
                    tok.pad_token = tok.eos_token
                self.tok, self.model, self.model_id = tok, model, mid
                # warm-up (first call pays one-time allocation cost) before advertising loaded=true
                try:
                    self._generate({'speaker': 'Horus', 'evidence': {'vehicle': 'Horus'}, 'user_text': 'hello',
                                    'style': 'social'}, 4, None)
                except Exception as e:
                    self.log({'event': 'WARMUP_FAILED', 'error': repr(e)})
                self.loaded = True
                self.log({'event': 'MODEL_LOADED', 'model': mid, 'load_s': round(time.monotonic() - t0, 2),
                          'tried': tried, 'threads': torch.get_num_threads()})
                return
            except Exception as e:
                tried.append({'model': mid, 'error': repr(e)[:300]})
        self.load_error = 'no model could be loaded offline: ' + json.dumps(tried)[:600]
        self.log({'event': 'MODEL_LOAD_FAILED', 'tried': tried})

    def _stopper(self, job, prompt_len):
        torch = self.torch
        from transformers import StoppingCriteria
        tok = self.tok

        class Stop(StoppingCriteria):
            def __call__(self, input_ids, scores, **kw):
                stop = False
                if job is not None and (job.cancelled or time.monotonic() > job.deadline):
                    stop = True
                else:
                    tail = tok.decode(input_ids[0, prompt_len:], skip_special_tokens=True)
                    if '\n\n' in tail or _TAG_RE.search(tail):
                        stop = True
                return torch.full((input_ids.shape[0],), stop, dtype=torch.bool, device=input_ids.device)
        return Stop()

    def _rep_penalty(self, plen):
        from transformers import LogitsProcessorList, RepetitionPenaltyLogitsProcessor
        return LogitsProcessorList([RepetitionPenaltyLogitsProcessor(1.15, prompt_ignore_length=plen)])

    def _generate(self, req, max_new, job):
        torch, tok = self.torch, self.tok
        from transformers import StoppingCriteriaList
        msgs = build_messages(req)
        prompt = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
        enc = tok(prompt, return_tensors='pt')
        plen = enc['input_ids'].shape[1]
        eos = [i for i in {tok.eos_token_id, tok.convert_tokens_to_ids('<|im_end|>')} if isinstance(i, int) and i >= 0]
        seed = zlib.crc32(prompt.encode('utf8')) & 0x7FFFFFFF   # reproducible for identical inputs
        torch.manual_seed(seed)
        with torch.inference_mode():
            out = self.model.generate(
                input_ids=enc['input_ids'], attention_mask=enc['attention_mask'],
                max_new_tokens=max_new, do_sample=True, temperature=self.args.temperature, top_p=0.9,
                # Benchmark finding: stock repetition_penalty also penalises PROMPT tokens (facts, draft),
                # pushing the model away from the grounded numbers. Penalise generated tokens only.
                logits_processor=self._rep_penalty(plen), eos_token_id=eos, pad_token_id=tok.pad_token_id,
                stopping_criteria=StoppingCriteriaList([self._stopper(job, plen)]))
        raw = tok.decode(out[0, plen:], skip_special_tokens=True)
        return raw, plen, int(out.shape[1] - plen)

    # ---- worker -----------------------------------------------------------------
    def _run(self):
        self._load_router()   # small (135M), so /route is available quickly
        self._load()
        self.loading = False
        while True:
            job = self.q.get()
            try:
                if job.cancelled or time.monotonic() > job.deadline:
                    continue
                job.result = self._handle(job)
            except Exception as e:
                job.result = {'fallback': True, 'reason': 'error', 'error': repr(e)[:300]}
            finally:
                job.event.set()
                self.q.task_done()

    def _handle(self, job):
        req = job.req
        t0 = time.monotonic()
        max_new = max(8, min(160, int(req.get('max_new_tokens') or 70)))
        raw, plen, ntok = self._generate(req, max_new, job)
        gen_ms = (time.monotonic() - t0) * 1000
        if job.cancelled or time.monotonic() > job.deadline:
            return {'fallback': True, 'reason': 'timeout', 'raw': raw}
        text = clean_generation(raw, req['speaker'])
        violations = cg.validate(text, speaker=req['speaker'], evidence=req.get('evidence') or {},
                                 allowed_facts=req.get('allowed_facts') or [], draft=req.get('draft'),
                                 history=req.get('history') or [], user_text=req.get('user_text') or '',
                                 persona=req.get('persona') or PERSONA_HINTS.get(req['speaker']))
        return {'text': text, 'raw': raw, 'accepted': not violations, 'violations': violations,
                'gen_ms': round(gen_ms, 1), 'prompt_tokens': plen, 'new_tokens': ntok}

    # ---- request entry ------------------------------------------------------
    def submit(self, req):
        t0 = time.monotonic()
        with self.lock:
            self.stats['requests'] += 1
        if not self.loaded:
            reason = 'loading' if self.loading else 'not_loaded'
            res, code = {'fallback': True, 'reason': reason, 'load_error': self.load_error}, 503
        else:
            job = Job(req, float(req.get('timeout_s') or self.args.timeout))
            try:
                self.q.put_nowait(job)
            except queue.Full:
                res, code = {'fallback': True, 'reason': 'busy'}, 503
            else:
                if not job.event.wait(max(0.1, job.deadline - time.monotonic())):
                    job.cancelled = True
                    res, code = {'fallback': True, 'reason': 'timeout'}, 200
                else:
                    res, code = dict(job.result or {'fallback': True, 'reason': 'error'}), 200
        latency = round((time.monotonic() - t0) * 1000, 1)
        res.setdefault('fallback', False)
        if not res['fallback']:
            res.setdefault('accepted', False)
        else:
            res.setdefault('accepted', False)
            res.setdefault('violations', [])
            res.setdefault('text', None)
        res.update({'latency_ms': latency, 'model': self.model_id if self.loaded else None, 'source': SOURCE})
        with self.lock:
            if res['fallback']:
                self.stats['fallbacks'] += 1
            else:
                self.stats['generated'] += 1
                self.stats['lat_sum_ms'] += latency
                self.stats['accepted' if res['accepted'] else 'rejected'] += 1
        self.log({'event': 'GENERATE', 'speaker': req.get('speaker'), 'style': req.get('style'),
                  'user_text': req.get('user_text'), 'evidence_hash': cg.evidence_hash(req.get('evidence') or {}),
                  'draft': req.get('draft'), 'raw': res.get('raw'), 'text': res.get('text'),
                  'accepted': res['accepted'], 'violations': res.get('violations'), 'fallback': res['fallback'],
                  'reason': res.get('reason'), 'latency_ms': latency, 'gen_ms': res.get('gen_ms'),
                  'new_tokens': res.get('new_tokens'), 'model': res['model']})
        return code, res

    def health(self):
        with self.lock:
            s = dict(self.stats)
        return {'ok': True, 'model': self.model_id if self.loaded else None, 'requested_model': self.args.model,
                'loaded': self.loaded, 'loading': self.loading, 'load_error': self.load_error,
                'threads': (self.torch.get_num_threads() if self.torch is not None else self.args.threads),
                'queue_depth': self.q.qsize(), 'queue_max': self.q.maxsize, 'timeout_s': self.args.timeout,
                'requests': s['requests'], 'fallbacks': s['fallbacks'], 'accepted': s['accepted'],
                'rejected': s['rejected'],
                'avg_latency_ms': round(s['lat_sum_ms'] / s['generated'], 1) if s['generated'] else None,
                'uptime_s': round(time.monotonic() - self.started, 1), 'log': str(self.log_path),
                'source': SOURCE, 'network': 'offline (HF_HUB_OFFLINE=1, local_files_only)',
                'router_loaded': self.router is not None, 'router_error': self.router_error,
                'router_meta': getattr(self.router, 'meta', None)}


def validate_request(data):
    if not isinstance(data, dict):
        return 'body must be a JSON object'
    if data.get('speaker') not in DRONES:
        return 'speaker must be one of ' + ', '.join(DRONES)
    if data.get('evidence') is not None and not isinstance(data.get('evidence'), dict):
        return 'evidence must be an object'
    for k in ('allowed_facts', 'history'):
        if data.get(k) is not None and not isinstance(data.get(k), list):
            return k + ' must be a list'
    if data.get('history') and len(data['history']) > 50:
        return 'history too long (max 50; only the last 8 are used)'
    if not isinstance(data.get('user_text') or '', str) or len(data.get('user_text') or '') > 1000:
        return 'user_text must be a string of at most 1000 characters'
    if data.get('draft') is not None and not isinstance(data.get('draft'), str):
        return 'draft must be a string or null'
    return None


def make_handler(svc):
    class H(BaseHTTPRequestHandler):
        server_version = 'ChatLLM/1'

        def log_message(self, fmt, *a):
            pass

        def _send(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False, default=str).encode('utf8')
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.split('?')[0] == '/health':
                return self._send(200, svc.health())
            self._send(404, {'error': 'not found'})

        def do_POST(self):
            path = self.path.split('?')[0]
            if path == '/route':
                try:
                    n = int(self.headers.get('Content-Length') or 0)
                    data = json.loads(self.rfile.read(n).decode('utf8')) if 0 < n <= 4096 else {}
                except (ValueError, UnicodeDecodeError):
                    data = {}
                text = data.get('text') if isinstance(data, dict) else None
                if not isinstance(text, str) or not (1 <= len(text) <= 400):
                    return self._send(400, {'error': 'text must be 1..400 characters'})
                return self._send(*svc.route(text))
            if path != '/generate':
                return self._send(404, {'error': 'not found'})
            try:
                n = int(self.headers.get('Content-Length') or 0)
            except ValueError:
                n = -1
            if n <= 0 or n > MAX_BODY:
                return self._send(400, {'error': f'body required, max {MAX_BODY} bytes', 'fallback': True,
                                        'reason': 'bad_request', 'source': SOURCE})
            try:
                data = json.loads(self.rfile.read(n).decode('utf8'))
            except (ValueError, UnicodeDecodeError):
                return self._send(400, {'error': 'invalid JSON', 'fallback': True, 'reason': 'bad_request',
                                        'source': SOURCE})
            err = validate_request(data)
            if err:
                return self._send(400, {'error': err, 'fallback': True, 'reason': 'bad_request', 'source': SOURCE})
            code, res = svc.submit(data)
            self._send(code, res)
    return H


def main(argv=None):
    ap = argparse.ArgumentParser(description='Offline local-LLM wording service for SwarmChat (CHAT_LOCAL_LLM).')
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=8767)
    ap.add_argument('--model', default=os.environ.get('CHAT_LLM_MODEL', DEFAULT_MODEL))
    ap.add_argument('--threads', type=int, default=6)
    ap.add_argument('--queue', type=int, default=4)
    ap.add_argument('--timeout', type=float, default=12.0)
    ap.add_argument('--temperature', type=float, default=0.3, help='clamped to 0.3..0.5')
    ap.add_argument('--log-dir', default='~/hu-telepresence-gazebo/logs/chat_llm')
    args = ap.parse_args(argv)
    if args.host not in ('127.0.0.1', 'localhost', '::1'):
        print('refusing to bind a non-loopback host', file=sys.stderr)
        return 2
    if args.port in (8100, 8765, 8766):
        print(f'refusing reserved port {args.port}', file=sys.stderr)
        return 2
    args.temperature = min(0.5, max(0.3, args.temperature))
    svc = ChatLLM(args)
    httpd = ThreadingHTTPServer((args.host, args.port), make_handler(svc))
    httpd.daemon_threads = True
    print(f'chat_llm_service on http://{args.host}:{args.port} model={args.model} threads={args.threads} '
          f'log={svc.log_path}', flush=True)
    import signal

    def _term(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _term)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        svc.log({'event': 'SERVICE_STOP'})
        httpd.server_close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
