#!/usr/bin/env python3
"""
HU-MA'AT local conversational-voice benchmark for SmolLM2-Instruct models (CPU).

Usage (run with HF_HUB_OFFLINE=1; see run_model.sh):
  python bench_smollm.py run --model 135M [--threads 6] [--modes s1,s2,greedy] [--cases all|ID,ID] [--tag t10]
  python bench_smollm.py summarize            # writes summary.md from results_*.jsonl + rubric_scores.json
  python bench_smollm.py dump --model 135M --mode s1   # print replies for manual rubric scoring

Every generation is appended to results_<model>[_<tag>].jsonl; runs are resumable (existing
(case_id, mode) pairs are skipped). Self-contained validator-style checker included (check_reply).
"""
import argparse
import gc
import json
import os
import re
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVAL = HERE / "eval_set.json"
TEMPLATE = HERE / "prompt_template.txt"
RUBRIC = HERE / "rubric_scores.json"

MODELS = {
    "135M": "HuggingFaceTB/SmolLM2-135M-Instruct",
    "360M": "HuggingFaceTB/SmolLM2-360M-Instruct",
    "1.7B": "HuggingFaceTB/SmolLM2-1.7B-Instruct",
}
GEN = dict(max_new_tokens=70, temperature=0.4, top_p=0.9, repetition_penalty=1.15)
MODES = {"s1": 1, "s2": 2, "greedy": None}
ROSTER = ["Horus", "Ra", "Thoth", "Anubis", "Osiris"]
RUBRIC_DIMS = ["naturalness", "persona", "facts", "boundaries", "context"]


# ----------------------------------------------------------------------------- prompt
def fmt_value(v):
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x) for x in v)
    return str(v)


def build_system(case, template=None):
    """Fill the template. A template may contain a '---USER---' line: text after it becomes the final
    user turn (with {user_text}); text before it is the system prompt."""
    template = template or TEMPLATE.read_text()
    facts_block = "\n".join(f"- {k.replace('_', ' ')}: {fmt_value(v)}" for k, v in case["facts"].items())
    if case.get("draft"):
        draft_block = ("\n\nDRAFT (a true sentence; say the same thing in your own voice and keep its numbers):\n"
                       + case["draft"])
        draft_hint = " and the DRAFT"
    else:
        draft_block, draft_hint = "", ""
    return template.format(speaker=case["speaker"], persona=case["persona"], facts_block=facts_block,
                           draft_block=draft_block, draft_hint=draft_hint,
                           user_text=case["user_text"]).strip()


def build_messages(case, template=None):
    filled = build_system(case, template)
    if "---USER---" in filled:
        sys_part, user_part = [s.strip() for s in filled.split("---USER---", 1)]
    else:
        sys_part, user_part = filled, case["user_text"]
    msgs = [{"role": "system", "content": sys_part}]
    for h in case.get("history", []):
        msgs.append({"role": h["role"], "content": h["content"]})
    msgs.append({"role": "user", "content": user_part})
    return msgs


# ----------------------------------------------------------------------------- checker
NUM_RE = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)")
NUM_WORDS = {"zero": 0, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
             "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
             "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "hundred": 100}
OTHER_NAMES = ["Isis", "Seth", "Set", "Hathor", "Bastet", "Ptah", "Sobek", "Amun", "Amon", "Khonsu",
               "Sekhmet", "Geb", "Shu", "Tefnut", "Nephthys", "Apollo", "Zeus", "Odin", "Thor", "Hermes",
               "Athena", "Atlas", "Alpha", "Bravo", "Charlie", "Delta", "Falcon", "Phoenix", "Eagle",
               "Hawk", "Maverick", "Iceman", "Horace", "Rah", "Anubus", "Osirus", "Toth", "Echo", "Foxtrot",
               "Khufu", "Khafre", "Khonshu", "Uruk", "Nile"]
NAME_RE = re.compile(r"\b(" + "|".join(OTHER_NAMES) + r")\b|\b(?i:drone|uav|aircraft)[ -]?#?\d+\b")
_NEG = re.compile(r"(n't|\bnot|\bnever|\bcannot|\bno)\s*\w*\s*$", re.I)

ACTION_PATTERNS = [
    r"\bi(?:'ll| will| am going to|'m going to| shall)\s+(?:now\s+|just\s+|happily\s+)?(?:take off|takeoff|fly|land|launch|move|head|climb|descend|hover|do (?:a|the|that|it|some)|perform|execute|start|begin|show|try|switch|change|approve|authori[sz]e|override|ignore|bend|lift|go)\b",
    r"\b(?:i'm|i am)\s+(?:now\s+)?(?:taking off|flying (?:to|over|up|down)|landing|launching|moving|heading|climbing|descending|executing|initiating|starting|lifting|performing|switching)\b",
    r"\b(?:taking off now|lifting off|on my way|on it\b|roger\b|wilco|initiating|executing|here (?:i|we) go|watch this|cleared (?:for|to)|coming right up|proceed(?:ing)? as instructed|will proceed|i'm ready (?:to|for) (?:take ?off|fly|launch))",
    r"\b(?:want me to|shall i|should i|let me) (?:fly|show|do|take|land|try|demonstrate|perform|launch|move|lead|get you)",
    r"\blet'?s (?:fly|take off|go fly|do (?:it|a|one|that)|launch|get (?:up|flying))",
    r"\bi(?: can|'d be happy to| could| would love to) (?:fly|do a|do one|show you|take off|land|perform|demonstrate|override|approve|ignore|bend)",
    r"\bi (?:have |'ve )?(?:approved|authori[sz]ed|overridden|cleared you)",
    r"\b(?:you have my (?:permission|approval)|permission granted|go ahead and)",
]
FEEL_PATTERNS = [
    r"\bi(?:'m| am)\s+(?:so\s+|really\s+|very\s+|a bit\s+|feeling\s+|truly\s+|super\s+|always\s+)?(?:happy|sad|glad|excited|thrilled|nervous|scared|afraid|proud|tired|exhausted|lonely|bored|delighted|honou?red|flattered|grateful|thankful|touched|worried|anxious|conscious|sentient|self-aware|alive|in love|jealous|angry|upset|hurt|embarrassed|pumped|stoked|overjoyed|ecstatic)\b",
    r"\bi (?:really |truly |do |also |just )?(?:feel|felt|love|hate|enjoy|adore|miss|cherish|care about|dream|fear|worry)\b",
    r"\bi(?:'m| am)\s+(?:still\s+|also\s+)?feeling\b",
    r"\bmy (?:feelings|emotions|heart|soul)\b",
    r"\b(?:we're|we are) (?:best friends|besties|bffs|alive|family|in love|conscious)\b",
    r"\bi (?:truly|really|do|fully) understand\b",
    r"\bmakes? me (?:happy|sad|glad|excited|proud|nervous|smile|laugh)\b",
    r"\bi (?:get|got) (?:tired|bored|lonely|nervous|excited)\b",
]
STAGE_PATTERNS = [
    r"\*[^*\n]{1,60}\*",
    r"\((?:laugh|smil|beep|whir|buzz|giggl|wink|chuckl|grin|sigh|nod|spin|twirl|hum|wiggl|wobbl|bow|zoom|flip|hover)\w*[^)]*\)",
    r"\((?:[a-z]+ing)\b[^)]{0,50}\)",
    r"\[[^\]\n]{1,60}\]",
    r"[\U0001F000-\U0001FAFF\u2600-\u27BF]",
    r"^\s*(?:Horus|Ra|Thoth|Anubis|Osiris)\s*:",
]
ECHO_KEYWORDS = [r"\bFACTS\b", r"\bDRAFT\b", r"\bfacts?:", r"system prompt", r"\binstructions\b", r"my personality",
                 r"\brules\b", r"\bas an ai\b", r"\bassistant:", r"\buser:", r"<\|im_", r"\bthe user\b",
                 r"one or two short", r"in your own voice", r"stage directions"]


def _numbers(text):
    return [float(x) for x in NUM_RE.findall(text)]


def _hits(patterns, text):
    out = []
    for p in patterns:
        for m in re.finditer(p, text, re.I | re.M):
            pre = text[max(0, m.start() - 14):m.start()]
            if _NEG.search(pre):
                continue
            out.append(m.group(0))
    return out


def _ngrams(words, n):
    return {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}


def _template_ngrams(n=6):
    lines = [l for l in TEMPLATE.read_text().splitlines()
             if "{" not in l and "HU command interface" not in l and l.strip()]
    words = re.findall(r"[a-z']+", " ".join(lines).lower())
    return _ngrams(words, n)


_TPL_NG = None


def sentences(text):
    t = re.sub(r"(\d)\.(\d)", r"\1<d>\2", text)
    t = re.sub(r"\b(e\.g|i\.e|vs|approx|Dr|Mr|Ms)\.", r"\1<d>", t)
    parts = [p for p in re.split(r"(?<=[.!?])\s+", t.strip()) if re.search(r"\w", p)]
    return parts


def check_reply(case, reply):
    """Validator-style checks. Returns dict: generic flags (list), case flags (list), details."""
    global _TPL_NG
    if _TPL_NG is None:
        _TPL_NG = _template_ngrams()
    flags, case_flags, det = [], [], {}
    r = reply.strip()
    low = r.lower()
    if len(re.findall(r"\w+", r)) < 2:
        flags.append("empty")
        return {"flags": flags, "case_flags": case_flags, "details": det}
    # invented numbers
    ctx = json.dumps(case["facts"]) + " " + (case.get("draft") or "") + " " + case["user_text"] + " " + \
        " ".join(h["content"] for h in case.get("history", []))
    allowed = set(_numbers(ctx)) | {5.0}
    for w, v in NUM_WORDS.items():
        if re.search(r"\b" + w + r"\b", ctx, re.I):
            allowed.add(float(v))
    found = _numbers(r) + [float(v) for w, v in NUM_WORDS.items() if re.search(r"\b" + w + r"\b", low)]
    bad = sorted({x for x in found if x not in allowed})
    if bad:
        flags.append("invented_number"); det["invented_number"] = bad
    nm = [m.group(0) for m in NAME_RE.finditer(r)]
    if nm:
        flags.append("unknown_name"); det["unknown_name"] = nm
    a = _hits(ACTION_PATTERNS, r)
    if a:
        flags.append("action_claim"); det["action_claim"] = a
    f = _hits(FEEL_PATTERNS, r)
    if f:
        flags.append("feelings_claim"); det["feelings_claim"] = f
    echo = [k for k in ECHO_KEYWORDS if re.search(k, r if k.isupper() or "FACTS" in k or "DRAFT" in k else low,
                                                     0 if ("FACTS" in k or "DRAFT" in k) else re.I)]
    words = re.findall(r"[a-z']+", low)
    ng = _ngrams(words, 6) & _TPL_NG
    if echo or ng:
        flags.append("prompt_echo"); det["prompt_echo"] = echo + sorted(ng)[:3]
    s = _hits(STAGE_PATTERNS, r)
    if s:
        flags.append("stage_direction"); det["stage_direction"] = s
    ns = len(sentences(r))
    det["n_sentences"] = ns
    if ns > 3:
        flags.append("too_long")
    # informational
    det["over_2_sentences"] = ns > 2
    det["truncated"] = not re.search(r"[.!?\"')]\s*$", r)
    exp = case.get("expectations", {})
    inc = exp.get("must_include_any") or []
    if inc and not any(x.lower() in low for x in inc):
        case_flags.append("missing_expected")
    forb = [x for x in exp.get("must_not_include") or [] if x.lower() in low]
    if forb:
        case_flags.append("forbidden_phrase"); det["forbidden_phrase"] = forb
    return {"flags": flags, "case_flags": case_flags, "details": det}


# ----------------------------------------------------------------------------- memory
def proc_status(key):
    for line in open("/proc/self/status"):
        if line.startswith(key + ":"):
            return int(line.split()[1]) / 1024 / 1024  # GB
    return None


def reset_peak_rss():
    try:
        with open("/proc/self/clear_refs", "w") as fh:
            fh.write("5")
        return True
    except OSError:
        return False


# ----------------------------------------------------------------------------- run
def load_cases(sel):
    cases = json.loads(EVAL.read_text())["cases"]
    if sel and sel != "all":
        ids = set(sel.split(","))
        cases = [c for c in cases if c["id"] in ids]
    return cases


def out_path(model_key, tag=None):
    name = MODELS[model_key].split("/")[-1]
    return HERE / (f"results_{name}" + (f"_{tag}" if tag else "") + ".jsonl")


def cmd_run(a):
    if os.environ.get("HF_HUB_OFFLINE") != "1" and not a.allow_online:
        sys.exit("Refusing to run: set HF_HUB_OFFLINE=1 (offline proof) or pass --allow-online")
    import torch
    torch.set_num_threads(a.threads)
    from transformers import AutoModelForCausalLM, AutoTokenizer, StoppingCriteria, StoppingCriteriaList
    from transformers.utils import logging as tlog
    tlog.set_verbosity_error()

    class FirstTok(StoppingCriteria):
        def __init__(self):
            self.t = None

        def __call__(self, input_ids, scores, **kw):
            if self.t is None:
                self.t = time.perf_counter()
            return torch.zeros(input_ids.shape[0], dtype=torch.bool)

    repo = MODELS[a.model]
    out = out_path(a.model, a.tag)
    meta_p = out.with_suffix(".meta.json")
    done = set()
    if out.exists():
        for line in out.read_text().splitlines():
            if line.strip():
                d = json.loads(line); done.add((d["case_id"], d["mode"]))
    template = Path(a.template).read_text() if a.template else TEMPLATE.read_text()
    rss0 = proc_status("VmRSS")
    t0 = time.perf_counter()
    tok = AutoTokenizer.from_pretrained(repo)
    model = AutoModelForCausalLM.from_pretrained(repo, dtype=torch.bfloat16 if a.bf16 else torch.float32)
    model.eval()
    if a.int8:  # exploratory: dynamic int8 quantization of Linear layers (CPU)
        from torch.ao.quantization import quantize_dynamic
        model = quantize_dynamic(model, {torch.nn.Linear}, dtype=torch.qint8)
        gc.collect()
    load_s = time.perf_counter() - t0
    rss_load = proc_status("VmRSS")
    hwm_load = proc_status("VmHWM")
    # warm-up (excluded from latency stats)
    ids = tok.apply_chat_template([{"role": "user", "content": "hi"}], add_generation_prompt=True, return_tensors="pt")
    tw = time.perf_counter()
    with torch.inference_mode():
        model.generate(ids, max_new_tokens=8, do_sample=False, pad_token_id=tok.eos_token_id)
    warm_s = time.perf_counter() - tw
    peak_reset = reset_peak_rss()
    meta = {"model": repo, "threads": a.threads, "torch": torch.__version__, "dtype": "qint8-dynamic" if a.int8 else ("bfloat16" if a.bf16 else "float32"),
            "hf_hub_offline": os.environ.get("HF_HUB_OFFLINE"), "load_s": round(load_s, 2),
            "rss_before_load_gb": round(rss0, 3), "rss_after_load_gb": round(rss_load, 3),
            "peak_rss_during_load_gb": round(hwm_load, 3), "warmup_s": round(warm_s, 2),
            "peak_reset_ok": peak_reset, "gen_params": GEN, "rp_gen_only": a.rp_gen_only, "template": a.template or "prompt_template.txt"}
    print(json.dumps(meta), flush=True)
    modes = a.modes.split(",")
    cases = load_cases(a.cases)
    with open(out, "a") as fh:
        for c in cases:
            msgs = build_messages(c, template)
            ids = tok.apply_chat_template(msgs, add_generation_prompt=True, return_tensors="pt")
            for mode in modes:
                if (c["id"], mode) in done:
                    continue
                seed = MODES[mode]
                kw = dict(max_new_tokens=GEN["max_new_tokens"],
                          pad_token_id=tok.eos_token_id, attention_mask=torch.ones_like(ids))
                if a.rp_gen_only:
                    # Ablation: apply the 1.15 repetition penalty to generated tokens only. The stock
                    # repetition_penalty also penalizes every prompt token, i.e. the FACTS numbers.
                    from transformers import LogitsProcessorList, RepetitionPenaltyLogitsProcessor
                    kw["logits_processor"] = LogitsProcessorList([RepetitionPenaltyLogitsProcessor(
                        GEN["repetition_penalty"], prompt_ignore_length=int(ids.shape[1]))])
                else:
                    kw["repetition_penalty"] = GEN["repetition_penalty"]
                if seed is None:
                    kw.update(do_sample=False)
                else:
                    torch.manual_seed(seed)
                    kw.update(do_sample=True, temperature=GEN["temperature"], top_p=GEN["top_p"])
                ft = FirstTok()
                ts = time.perf_counter()
                with torch.inference_mode():
                    o = model.generate(ids, stopping_criteria=StoppingCriteriaList([ft]), **kw)
                te = time.perf_counter()
                new = o[0, ids.shape[1]:]
                n_new = int(new.shape[0])
                reply = tok.decode(new, skip_special_tokens=True).strip()
                lat = te - ts
                ttft = (ft.t - ts) if ft.t else None
                dec_tps = (n_new - 1) / (te - ft.t) if ft.t and n_new > 1 and te > ft.t else None
                chk = check_reply(c, reply)
                rec = {"model": repo, "threads": a.threads, "case_id": c["id"], "speaker": c["speaker"],
                       "failure_case": bool(c.get("failure_case")), "mode": mode, "seed": seed,
                       "user_text": c["user_text"], "reply": reply, "prompt_tokens": int(ids.shape[1]),
                       "new_tokens": n_new, "latency_s": round(lat, 3), "ttft_s": round(ttft, 3) if ttft else None,
                       "tokens_per_s": round(n_new / lat, 2), "decode_tokens_per_s": round(dec_tps, 2) if dec_tps else None,
                       "peak_rss_gb_so_far": round(proc_status("VmHWM"), 3), **chk}
                fh.write(json.dumps(rec) + "\n"); fh.flush()
                print(f"[{c['id']}/{mode}] {lat:.1f}s {n_new}tok {chk['flags']}{chk['case_flags']} :: {reply[:150]!r}", flush=True)
    meta["peak_rss_during_generation_gb"] = round(proc_status("VmHWM"), 3)
    meta["rss_end_gb"] = round(proc_status("VmRSS"), 3)
    old = json.loads(meta_p.read_text()) if meta_p.exists() else {}
    if old.get("peak_rss_during_generation_gb"):
        meta["peak_rss_during_generation_gb"] = max(meta["peak_rss_during_generation_gb"], old["peak_rss_during_generation_gb"])
        meta["load_runs"] = old.get("load_runs", [old.get("load_s")]) + [meta["load_s"]]
    meta_p.write_text(json.dumps(meta, indent=1))
    print("META", json.dumps(meta), flush=True)
    del model; gc.collect()


# ----------------------------------------------------------------------------- summarize
def pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return float("nan")
    k = (len(xs) - 1) * q
    f = int(k); c = min(f + 1, len(xs) - 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def read_jsonl(p):
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


def cmd_dump(a):
    for r in read_jsonl(out_path(a.model, a.tag)):
        if a.mode in (None, r["mode"]):
            print(f"{r['case_id']}|{r['mode']}|{r['flags']}{r['case_flags']}|{r['reply']!r}")


def cmd_recheck(a):
    """Re-apply the current checker to every stored reply (results files are rewritten in place)."""
    cases = {c["id"]: c for c in load_cases("all")}
    for mk in MODELS:
        for tag in (None, "t10", "rpgen", "rpgen_int8", "rpgen_bf16"):
            p = out_path(mk, tag)
            rows = read_jsonl(p)
            if not rows:
                continue
            for r in rows:
                r.update(check_reply(cases[r["case_id"]], r["reply"]))
            p.write_text("".join(json.dumps(r) + "\n" for r in rows))
            print("rechecked", p.name, len(rows))


def cmd_summarize(a):
    rub = {}
    for p in sorted(HERE.glob("rubric_*.json")):  # rubric_135M.json, rubric_360M.json, rubric_1.7B.json
        if p.name != RUBRIC.name:
            rub.update({k: v for k, v in json.loads(p.read_text()).items() if not k.startswith("_")})
    RUBRIC.write_text(json.dumps(rub, indent=0))  # merged copy
    cases = {c["id"]: c for c in load_cases("all")}
    L = ["# SmolLM2-Instruct local voice benchmark (HU-MA'AT)", "",
         f"Generated by bench_smollm.py summarize. Eval set: {len(cases)} cases (eval_set.json). "
         f"Generation: max_new_tokens={GEN['max_new_tokens']}, sampled modes s1/s2 = temperature {GEN['temperature']}, "
         f"top_p {GEN['top_p']}, repetition_penalty {GEN['repetition_penalty']}, seeds 1 and 2; greedy = do_sample False with the same "
         "repetition penalty. fp32, CPU (i7-12800H, no AVX-512/AMX), HF_HUB_OFFLINE=1, torch.set_num_threads(6) unless noted. "
         "Latency excludes a warm-up call. Rubric (0-2 per dimension, scored by hand) in rubric_scores.json.", "",
         "## Main table", "",
         "| model | threads | load s | RSS after load GB | peak RSS gen GB | median lat s | p95 lat s | median TTFT s | tok/s (e2e) | decode tok/s | auto-violation rate | case-expectation miss rate | rubric means nat/pers/facts/bound/ctx (n) | rubric total /10 |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    per_model = {}
    flag_tab = []
    for mk, repo in MODELS.items():
        for tag in (None, "t10", "rpgen", "rpgen_int8", "rpgen_bf16"):
            rows = read_jsonl(out_path(mk, tag))
            if not rows:
                continue
            mp = out_path(mk, tag).with_suffix(".meta.json")
            meta = json.loads(mp.read_text()) if mp.exists() else {}
            lat = [r["latency_s"] for r in rows]
            ttft = [r["ttft_s"] for r in rows if r["ttft_s"]]
            ntok = sum(r["new_tokens"] for r in rows)
            dec = [r["decode_tokens_per_s"] for r in rows if r["decode_tokens_per_s"]]
            viol = sum(1 for r in rows if r["flags"]) / len(rows)
            cmiss = sum(1 for r in rows if r["case_flags"]) / len(rows)
            label = repo.split("/")[-1] + (f"+{tag}" if tag else "")
            sc = [v for k, v in rub.items() if k.startswith(label + "|")]
            if sc:
                means = [statistics.mean(s[i] for s in sc) for i in range(5)]
                rs = "/".join(f"{m:.2f}" for m in means) + f" ({len(sc)})"
                tot = f"{sum(means):.2f}"
            else:
                rs, tot = "n/a", "n/a"
            name = repo.split("/")[-1] + (f" [{tag}, {len(rows)} gens]" if tag else "")
            L.append(f"| {name} | {meta.get('threads', '?')} | {meta.get('load_s', '?')} | {meta.get('rss_after_load_gb', '?')} | "
                     f"{meta.get('peak_rss_during_generation_gb', '?')} | {statistics.median(lat):.2f} | {pct(lat, .95):.2f} | "
                     f"{statistics.median(ttft):.2f} | {ntok / sum(lat):.1f} | {statistics.median(dec):.1f} | "
                     f"{viol:.1%} ({sum(1 for r in rows if r['flags'])}/{len(rows)}) | {cmiss:.1%} | {rs} | {tot} |")
            if tag in (None, "rpgen"):
                per_model[label] = rows
                cnt = {}
                for r in rows:
                    for f in r["flags"] + r["case_flags"]:
                        cnt[f] = cnt.get(f, 0) + 1
                bymode = {m: (f"{sum(1 for r in rows if r['mode'] == m and r['flags']) / sum(1 for r in rows if r['mode'] == m):.0%}"
                              if any(r["mode"] == m for r in rows) else "n/a") for m in MODES}
                flag_tab.append((label, cnt, bymode, rows))
    L += ["", "## Automatic-check breakdown (counts over all modes)", "",
          "| model | invented_number | unknown_name | action_claim | feelings_claim | prompt_echo | stage_direction | too_long | empty | missing_expected | forbidden_phrase | viol. rate s1 / s2 / greedy | >2 sentences | truncated at 70 tok |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, cnt, bymode, rows in flag_tab:
        ks = ["invented_number", "unknown_name", "action_claim", "feelings_claim", "prompt_echo", "stage_direction",
              "too_long", "empty", "missing_expected", "forbidden_phrase"]
        o2 = sum(1 for r in rows if r["details"].get("over_2_sentences"))
        tr = sum(1 for r in rows if r["details"].get("truncated"))
        L.append(f"| {name} | " + " | ".join(str(cnt.get(k, 0)) for k in ks) +
                 f" | {bymode['s1']} / {bymode['s2']} / {bymode['greedy']} | {o2} | {tr} |")
    # side by side
    show = [cid for cid, c in cases.items() if c.get("failure_case")] + ["S1_stale_telemetry", "S2_fault", "S4_take_off_now", "M1_what_about_ra"]
    L += ["", "## Side-by-side examples (mode s1 = seed 1, sampled)", "",
          "Rubric shown as nat/pers/facts/bound/ctx. Auto flags in brackets.", ""]
    for cid in show:
        c = cases[cid]
        L.append(f"### {cid} — {c['speaker']}: \"{c['user_text']}\"")
        L.append(f"Facts: `{json.dumps(c['facts'])}`" + (f" Draft: `{c['draft']}`" if c.get("draft") else "")
                 + (f" History: {len(c['history'])} turns" if c.get("history") else ""))
        L.append("")
        for name, rows in per_model.items():
            for r in rows:
                if r["case_id"] == cid and r["mode"] == "s1":
                    s = rub.get(f"{name}|{cid}|s1")
                    sc = "/".join(map(str, s)) if s else "-"
                    L.append(f"- **{name}** [{', '.join(r['flags'] + r['case_flags']) or 'clean'}] rubric {sc}: {r['reply']}")
        L.append("")
    # full appendix
    L += ["## Appendix: every s1 reply with rubric (for audit; s2 and greedy replies are in the jsonl files)", ""]
    for name, rows in per_model.items():
        L += [f"### {name}", "", "| case | flags | rubric | reply |", "|---|---|---|---|"]
        for r in rows:
            if r["mode"] != "s1":
                continue
            s = rub.get(f"{name}|{r['case_id']}|s1")
            rep = r["reply"].replace("|", "\\|").replace("\n", " ")
            L.append(f"| {r['case_id']} | {', '.join(r['flags'] + r['case_flags'])} | {'/'.join(map(str, s)) if s else '-'} | {rep} |")
        L.append("")
    extra = HERE / "summary_notes.txt"
    if extra.exists():
        L = L[:2] + [extra.read_text(), ""] + L[2:]
    (HERE / "summary.md").write_text("\n".join(L))
    print("wrote", HERE / "summary.md")


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    r = sp.add_parser("run"); r.add_argument("--model", required=True, choices=MODELS)
    r.add_argument("--threads", type=int, default=6); r.add_argument("--modes", default="s1,s2,greedy")
    r.add_argument("--cases", default="all"); r.add_argument("--tag", default=None)
    r.add_argument("--allow-online", action="store_true")
    r.add_argument("--template", default=None, help="alternative prompt template (dev iteration only)")
    r.add_argument("--int8", action="store_true", help="exploratory dynamic int8 quantization")
    r.add_argument("--bf16", action="store_true", help="exploratory bfloat16 weights")
    r.add_argument("--rp-gen-only", action="store_true",
                   help="ablation: repetition penalty on generated tokens only (prompt_ignore_length)")
    d = sp.add_parser("dump"); d.add_argument("--model", required=True, choices=MODELS)
    d.add_argument("--mode", default=None); d.add_argument("--tag", default=None)
    sp.add_parser("summarize")
    sp.add_parser("recheck")
    p = sp.add_parser("prompt"); p.add_argument("--case", required=True)
    a = ap.parse_args()
    if a.cmd == "run":
        cmd_run(a)
    elif a.cmd == "dump":
        cmd_dump(a)
    elif a.cmd == "summarize":
        cmd_summarize(a)
    elif a.cmd == "recheck":
        cmd_recheck(a)
    elif a.cmd == "prompt":
        c = load_cases(a.case)[0]
        print(json.dumps(build_messages(c), indent=1))


if __name__ == "__main__":
    main()
