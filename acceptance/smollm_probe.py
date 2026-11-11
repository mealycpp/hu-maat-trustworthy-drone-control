#!/usr/bin/env python3
"""Feasibility probe: can local SmolLM2-135M-Instruct turn telemetry facts into a natural reply?
Offline only (HF_HUB_OFFLINE); prints replies, latency, and whether a fact checker would accept them."""
import os, re, time
os.environ['HF_HUB_OFFLINE'] = '1'
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM

M = 'HuggingFaceTB/SmolLM2-135M-Instruct'
tok = AutoTokenizer.from_pretrained(M); model = AutoModelForCausalLM.from_pretrained(M).eval()
torch.set_num_threads(8)
FACTS = {
 'Horus': 'mode FORMATION; altitude 1.90 m; tracking error now 0.02 m, peak 0.31 m during the last transition; nearest aircraft Thoth 1.39 m',
 'Anubis': 'mode FORMATION; altitude 1.90 m; nearest aircraft Horus 1.42 m; no fault',
 'Ra': 'mode FORMATION; altitude 1.90 m; formations flown and validated today: line, V, circle, grid, diamond',
 'FLEET': 'all 5 aircraft airborne in FORMATION; minimum separation 1.39 m; acceptance tests today: all passed',
}
PERSONA = {'Horus': 'watchful, calm leader', 'Anubis': 'careful guardian', 'Ra': 'cheerful and energetic', 'FLEET': 'the five drones speaking as a team'}
TESTS = [('Horus', 'nice. you looked a bit wobbly earlier tho'),
         ('Anubis', 'anubis you always hang around horus, you two besties or what'),
         ('Ra', 'ra whats your favorite formation'),
         ('FLEET', "i'm kinda nervous about presenting you all at the conference tomorrow"),
         ('Horus', 'do you actually understand me or are you just pattern matching')]


def numbers(s):
    return set(re.findall(r'\d+(?:\.\d+)?', s))


for who, text in TESTS:
    sys_p = (f'You are {who}, a simulated quadcopter drone in a research demo. Personality: {PERSONA[who]}. '
             'Reply in 1-2 short friendly sentences. Use ONLY these facts, never invent numbers: ' + FACTS[who] +
             '. You are a small language model, not a person.')
    msgs = [{'role': 'system', 'content': sys_p}, {'role': 'user', 'content': text}]
    ids = tok.apply_chat_template(msgs, add_generation_prompt=True, return_tensors='pt')
    t0 = time.monotonic()
    with torch.no_grad():
        out = model.generate(ids, max_new_tokens=60, do_sample=True, temperature=0.4, top_p=0.9,
                             pad_token_id=tok.eos_token_id)
    reply = tok.decode(out[0][ids.shape[1]:], skip_special_tokens=True).strip()
    bad = numbers(reply) - numbers(FACTS[who])
    print(f'\nME -> {who}: {text}\n   {who}: {reply}\n   [{(time.monotonic()-t0)*1000:.0f} ms] checker: '
          + ('ACCEPT' if not bad else f'REJECT (invented numbers {sorted(bad)})'))
