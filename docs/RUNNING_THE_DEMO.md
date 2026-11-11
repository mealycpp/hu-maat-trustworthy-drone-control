# Running the HU–MA'AT Demo

Live demonstration of **HU: risk-asymmetric natural-language command of a multi-drone fleet**
(RSCL, Cal Poly Pomona). Visitors speak or type commands to five simulated quadcopters in Gazebo;
HU decides **EXECUTE / CONFIRM / REFUSE** before anything moves, and the independent **MA'AT** safety
layer is the only authority that lets an aircraft move.

> **Scope.** The HU paper's results were obtained on a physical three-drone fleet. This package runs the
> same frozen HU model and safety pipeline on five simulated aircraft (Gazebo Harmonic, X3 multicopter
> physics with CoDrone-style visuals). It is not a calibrated digital twin.

---

## 1. Architecture in one picture

```
 voice / text ──► HU console :8100 ──► HU (frozen SmolLM2-135M + trained heads) ──┐
                     │  symbolic emergency channel (no language model) ─────────────┤
                     │  deterministic mission parser (formations, moves, turns) ────┤
                     ▼                                                              ▼
              operator CONFIRM when uncertain                    MA'AT flight supervisor :8765
                                                                 (separation, bounds, faults,
                                                                  staggered takeoff, watchdog)
                                                                              │
                                                                              ▼
                                                                   Gazebo Harmonic (5 aircraft)
                                                                              │ odometry
                                                                              ▼
          SwarmChat (conversation, read-only)  ◄──  SpaceOps :8766  ◄──  telemetry recorder 4 Hz
          intent: HU frozen encoder + chat head      dashboards, health curves, formation results,
          wording: local SmolLM2-Instruct :8767      fault lab, exports (CSV/JSONL/ArduPilot .BIN)
          facts: telemetry · safety: MA'AT
```

Chat and generated text can never move an aircraft. Only HU-authorized commands (or the symbolic emergency
channel) reach the supervisor, and the supervisor re-checks every command against live telemetry.

## 2. Requirements

| Item | Tested with |
|---|---|
| OS | Windows 11 + WSL2 **Ubuntu 24.04** (native Ubuntu 24.04 also works) |
| Simulator | **Gazebo Harmonic** (`gz sim` 8) with WSLg for the 3-D window |
| Python | system `python3` 3.12 (supervisor, SpaceOps) + a venv with **torch (CPU)**, **transformers 4.5x**, **pyyaml** (HU, chat) |
| CPU / RAM | 8+ cores, 16 GB RAM recommended (HU ≈ 1.1 GB, chat model 0.7–7 GB, Gazebo ≈ 1–2 GB) |
| GPU | not required |
| Disk | ~5 GB for language models |

## 3. One-time installation

```bash
# Gazebo Harmonic + Python bindings
sudo apt update
sudo apt install -y gz-harmonic python3-gz-transport13 python3-gz-msgs10 python3-pip python3-venv curl
```

```bash
# Python environment for HU and the chat service
python3 -m venv ~/hu-telepresence-gazebo/hu-venv
~/hu-telepresence-gazebo/hu-venv/bin/pip install --index-url https://download.pytorch.org/whl/cpu torch
~/hu-telepresence-gazebo/hu-venv/bin/pip install "transformers>=4.45" pyyaml
```

```bash
# Language models (one-time download, then everything runs offline)
~/hu-telepresence-gazebo/hu-venv/bin/python -c "
from huggingface_hub import snapshot_download as d
for m in ['HuggingFaceTB/SmolLM2-135M','HuggingFaceTB/SmolLM2-135M-Instruct','HuggingFaceTB/SmolLM2-360M-Instruct']:
    print(d(m))"
```

Expected folder layout:

```
~/rscl-hu-latest/                      HU research code (hu/ package) + trained heads checkpoint
    hu/models/hu_heads_v1_1_REGISTERS.pt
~/hu-telepresence-gazebo/
    hu_maat.sh                         master launcher
    worlds/                            hu_festival.sdf + hu_env_{forest,military,mountains}.sdf
    festival/                          HU bridge, flight supervisor, mission parser, formation planner
    spaceops_v8/                       SpaceOps dashboard, SwarmChat, chat service, intent head
    acceptance/                        automated + live acceptance tests
    scripts/                           environment builder, ArduPilot log exporter
    docs/                              this guide, visitor command card
```

Create the HU bridge token once (shared secret between the HU bridge and the supervisor; never commit it):

```bash
python3 -c "import secrets;print(secrets.token_hex(32))" > ~/hu-telepresence-gazebo/festival/hu_bridge_token
chmod 600 ~/hu-telepresence-gazebo/festival/hu_bridge_token
```

## 4. Start the demo

```bash
bash ~/hu-telepresence-gazebo/hu_maat.sh start --env military
```

Order: Gazebo → flight supervisor → HU → SpaceOps → chat service. Each step waits on a real health check.
`READY` = everything up · `DEGRADED` = flight stack ready, chat uses deterministic replies · `NOT READY` = do not fly.

Environments: `festival` (stage), `forest`, `military`, `mountains`. Switch while all aircraft are parked:

```bash
bash ~/hu-telepresence-gazebo/hu_maat.sh env forest
```

Read-only checks before visitors arrive:

```bash
bash ~/hu-telepresence-gazebo/hu_maat.sh preflight
```

## 5. Screens

| URL | What it is |
|---|---|
| http://localhost:8100 | **HU console** — visitors type/speak; shows EXECUTE / CONFIRM / REFUSE, latency |
| http://localhost:8766 | **SpaceOps** — radar, fleet, separation, altitude, health curves, formations, fault lab, SwarmChat |
| http://localhost:8766/#dronehealth | per-aircraft runtime health curves |
| Gazebo window | the 3-D world |

Hands-free voice: open the HU console in **Chrome**, press **START HANDS-FREE** once, allow the microphone.

## 6. Demo script (10 minutes)

1. Preflight shows PASS. Point at the five parked aircraft in Gazebo.
2. *"Horus, take off"* → **EXECUTE** (HU model). *"Ra and Thoth, take off"* → group EXECUTE.
3. *"Everyone except Anubis, hover"* → **CONFIRM**: explain risk-asymmetric bands; say "confirm".
4. *"This project is really taking off"* → **REFUSE** by HU itself. *"Horus or Ra, land"* → ambiguous → REFUSE.
5. *"All, form a V shape"* → mission planner proposes, operator confirms, watch the V form; then
   *"All move forward 1 meter"* — the formation travels as one rigid group.
6. *"All, khalas"* → instant **emergency hold** through the symbolic channel (no language model).
7. SpaceOps: show health curves, formation results table, separation history.
8. SwarmChat: *"hey team, how are you?"*, *"Anubis, who is closest to you?"*, *"can you do a backflip?"*
9. *"All, land"* → EXECUTE; landing verified by telemetry.

Visitor-friendly commands with expected outcomes: [`docs/VISITOR_COMMAND_CARD.md`](VISITOR_COMMAND_CARD.md).

## 7. Emergencies

| Say / type | Effect |
|---|---|
| `stop`, `all stop`, `khalas`, `abort`, `All, khalas` | **hold** (hover in place) — whole fleet |
| `land now`, `everyone down`, `emergency land` | **land** — whole fleet |

If the HU console is unavailable:

```bash
curl -s -X POST localhost:8765/api/command -H 'Content-Type: application/json' -d '{"action":"emergency_land","targets":["Horus","Ra","Thoth","Anubis","Osiris"]}'
```

## 8. Stop

```bash
bash ~/hu-telepresence-gazebo/hu_maat.sh stop
```

Refuses while aircraft are airborne (land first, or add `--force`). Logs are always kept.

## 9. Evidence and exports

* Session recordings: `~/hu-telepresence-gazebo/logs/spaceops_sessions/SO-*/` (per-aircraft CSV, events JSONL).
* ArduPilot DataFlash logs (**simulated telemetry in ArduPilot format**, opens in UAV Log Viewer / Mission Planner):
  ```bash
  python3 ~/hu-telepresence-gazebo/scripts/export_ardupilot_logs.py
  ```
* HU decisions: `~/hu-telepresence-gazebo/logs/hu_real_decisions.jsonl`; voice→motion latency: `hu_voice_to_motion.jsonl`.

## 10. Verify everything (acceptance suite)

Offline (nothing flies, ~10 s):

```bash
bash ~/hu-telepresence-gazebo/acceptance/run_offline.sh
```

Live (flies the simulator, ~15 min; all aircraft must be parked):

```bash
bash ~/hu-telepresence-gazebo/acceptance/run_and_summarize.sh
```

Covers: 28 command-safety cases with zero unintended motion, five single-aircraft flights, every motion
primitive, swarm travel, five formations with transitions, and a fault-containment scenario with voice
emergency landing. The full 25-formation catalog: `python3 acceptance/formation_catalog_test.py acceptance/results`.

## 11. Honesty notes

* Battery values are a **simulated estimate**, never measured.
* SwarmChat drones are five logical agents sharing one local language model plus deterministic rules;
  every message is tagged with its source (`CHAT_LOCAL_LLM`, `TELEMETRY_FACT`, `SAFETY_SUPERVISOR`, …).
* A dispatched command is not a success: tests pass only when Gazebo telemetry confirms the motion.
