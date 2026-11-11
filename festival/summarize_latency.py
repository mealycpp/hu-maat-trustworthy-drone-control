#!/usr/bin/env python3
"""Print reproducible descriptive stats for HU voice-to-motion observed latency."""
import json, math, statistics
from pathlib import Path
p=Path.home()/'hu-telepresence-gazebo'/'logs'/'hu_voice_to_motion.jsonl'
if not p.exists():raise SystemExit('No latency log found: '+str(p))
rows=[]
for line in p.read_text(encoding='utf-8').splitlines():
    try:
        r=json.loads(line)
        if r.get('version')=='hu-latency-v5':rows.append(r)
    except (ValueError,TypeError):pass
print('HU latency records:',len(rows))
print('Outcome counts:')
for k in sorted({r.get('outcome','UNKNOWN') for r in rows}):
    print(' ',k,':',sum(r.get('outcome')==k for r in rows))
print('Accepted flight response events: strictly OBSERVED_ALL, voice-only, detected voice onset.')
valid=[r for r in rows if r.get('source')=='voice' and r.get('voice_onset_detected') and r.get('outcome')=='OBSERVED_ALL']
def percentile(values,pct):
    ordered=sorted(values)
    if not ordered:return None
    q=(len(ordered)-1)*pct/100
    low=int(math.floor(q));high=int(math.ceil(q));return ordered[low]+(ordered[high]-ordered[low])*(q-low)
for field,label in [('asr_ms','Speech-onset to transcript'),('hu_inference_ms','HU inference only'),
                    ('voice_to_first_observed_ms','Voice-onset to FIRST observed motion'),
                    ('voice_to_all_observed_ms','Voice-onset to ALL observed motion'),
                    ('operator_confirmation_wait_ms','Operator confirmation wait')]:
    values=[float(r[field]) for r in valid if isinstance(r.get(field),(int,float)) and math.isfinite(r[field])]
    if values:
        print(f'{label}: n={len(values)}, median={statistics.median(values):.1f}ms, p95={percentile(values,95):.1f}ms, max={max(values):.1f}ms')
    else:print(f'{label}: no valid measurements')
print('NOTE: 150ms browser telemetry sampling and 10cm movement threshold. These are observed-motion times, not actuator timestamps.')
