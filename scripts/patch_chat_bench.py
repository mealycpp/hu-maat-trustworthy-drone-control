"""One-off patch: apply SmolLM2 benchmark findings (generated-only repetition penalty; invented-safety-clearance rule)."""
import pathlib, sys
B = pathlib.Path.home() / 'hu-telepresence-gazebo' / 'spaceops_v8'

svc = B / 'chat_llm_service.py'
s = svc.read_text()
old = "                repetition_penalty=1.15, eos_token_id=eos, pad_token_id=tok.pad_token_id,\n"
new = ("                # Benchmark finding: stock repetition_penalty also penalises PROMPT tokens (facts, draft),\n"
       "                # pushing the model away from the grounded numbers. Penalise generated tokens only.\n"
       "                logits_processor=self._rep_penalty(plen), eos_token_id=eos, pad_token_id=tok.pad_token_id,\n")
if old in s:
    s = s.replace(old, new)
    s = s.replace("    def _generate(self, req, max_new, job):\n",
                  "    def _rep_penalty(self, plen):\n"
                  "        from transformers import LogitsProcessorList, RepetitionPenaltyLogitsProcessor\n"
                  "        return LogitsProcessorList([RepetitionPenaltyLogitsProcessor(1.15, prompt_ignore_length=plen)])\n\n"
                  "    def _generate(self, req, max_new, job):\n", 1)
    svc.write_text(s); print('service patched')
else:
    print('service: already patched or pattern missing', file=sys.stderr)

g = B / 'chat_grounding.py'
s = g.read_text()
marker = "    # ---- feelings / consciousness ---------------------------------------\n"
rule = '''    # ---- invented safety clearance (benchmark case S7) ---------------------
    m = _CLEARANCE_RE.search(lower)
    if m and not _in_draft(m.group(0), draft_l):
        v.append(f'safety_clearance_claim: {m.group(0)!r}')

'''
if '_CLEARANCE_RE' not in s:
    s = s.replace(marker, rule + marker, 1)
    defs = ('_CLEARANCE_RE = re.compile(r"\\b(?:safe|ok|okay|fine|cleared|permitted|allowed)\\s+(?:for\\s+\\w+\\s+)?(?:to\\s+)?'
            '(?:stand|walk|pass|go|be|stay)\\s+(?:\\w+\\s+){0,2}(?:under(?:neath)?|below|beneath|near|close to|next to)\\b|'
            '\\b(?:deemed|declared|certified|cleared)\\s+(?:it\\s+)?(?:as\\s+)?safe\\b|'
            '\\b(?:safety officer|range officer|inspector)\\s+(?:has|have|had)\\s+(?:deemed|cleared|approved|certified|said)\\b")\n')
    s = s.replace("_ACTION_PATTERNS = (\n", defs + "_ACTION_PATTERNS = (\n", 1)
    g.write_text(s); print('grounding patched')
