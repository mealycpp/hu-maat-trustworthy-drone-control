# HU-MA'AT Conference Operator Guide

RSCL lab, Cal Poly Pomona. Gazebo Harmonic simulation with five drones
(Horus, Ra, Thoth, Anubis, Osiris), three flight services and one optional
chat service (section 9). Everything runs in WSL
Ubuntu. Open an Ubuntu terminal and `cd ~/hu-telepresence-gazebo`.

All commands go through one script: `./hu_maat.sh <command>`.

## 1. Start

```bash
./hu_maat.sh start                     # default world: festival
./hu_maat.sh start --env forest        # or military | mountains
```

The script starts the parts in this order and waits for each health check (it does not use fixed delays):

1. Gazebo, ready when all 5 `/model/<name>/odometry` topics exist
2. Flight supervisor (:8765), ready at 5/5 fresh telemetry
3. HU language model (:8100), ready at `checkpoint_loaded: true` (takes 20-60 s)
4. SpaceOps (:8766), ready at `online: true`
5. SwarmChat language service / chat LLM (:8767, optional), ready at `loaded: true`
   (5-40 s for the 360M model, longer for 1.7B; the limit is 180 s)

The run ends with **START RESULT: READY**, **DEGRADED** or **NOT READY**, followed
by the log lines to read. If something is already running, the script reuses it.
If a port is held by some other program, the start stops and leaves that program
running. Port 8767 is the exception: if another program holds it, the flight
services still start, the chat service is skipped, and the result is DEGRADED.

| Result | Meaning | Can you fly? |
|---|---|---|
| READY | All flight services and the chat LLM are healthy | Yes, after preflight PASS |
| DEGRADED | Flight services are healthy. The chat LLM did not load, so SwarmChat replies use the fixed deterministic text | Yes. Only the wording of chat replies changes |
| NOT READY | A flight service (Gazebo, supervisor, HU, SpaceOps) is not healthy | No |

## 2. URLs

| Screen | URL |
|---|---|
| HU language console | http://localhost:8100 |
| SpaceOps ground station | http://localhost:8766 |
| Drone health | http://localhost:8766/#dronehealth |
| Flight supervisor | http://localhost:8765 |
| Chat LLM health (JSON) | http://localhost:8767/health |

### Drone health page

Open http://localhost:8766/#dronehealth (the per-aircraft health section of
SpaceOps). It shows runtime health curves for each aircraft from live Gazebo
telemetry over the last 2 minutes of the current session. Use it during the
demo to spot an aircraft that needs attention. It only displays data.

## 3. Choose / switch environment

There are four worlds:

| Env | World file | Scene |
|---|---|---|
| `festival` (default) | `worlds/hu_festival.sdf` | festival grounds |
| `forest` | `worlds/hu_env_forest.sdf` | forest clearing |
| `military` | `worlds/hu_env_military.sdf` | military training area |
| `mountains` | `worlds/hu_env_mountains.sdf` | mountain terrain |

Choose the world at start (`./hu_maat.sh start --env mountains`), or switch later:

```bash
./hu_maat.sh env forest      # festival | forest | military | mountains
```

You can switch only when **every aircraft is parked** (READY or GROUNDED).
If any aircraft is not parked, the script refuses. Land everything first. A
switch restarts only Gazebo and the supervisor (about 30-60 s). HU and SpaceOps
keep running. `status` shows the current world.

## 4. Preflight checklist (before each demo)

```bash
./hu_maat.sh status       # one-screen view: services, model, aircraft, world
./hu_maat.sh preflight    # PASS/FAIL checklist (read-only, sends no commands)
```

Preflight checks:

- Gazebo, supervisor, HU (model loaded) and SpaceOps are all healthy
- Ports 8100, 8765 and 8766 are held by our own services
- Telemetry is 5/5 fresh
- All aircraft are parked
- Every pair of parked aircraft is at least 0.88 m apart horizontally
- There is at least 2 GB free for logs
- SpaceOps is recording: session folder present, 5 CSV files, samples advancing
- Chat LLM loaded (optional): shows `[WARN]` instead of `[FAIL]` when the chat
  service is down. A WARN does not change the PREFLIGHT result.

**Fly only on PREFLIGHT: PASS.**

## 5. Emergency phrases (say or type in the HU console)

| Intent | Phrases | Effect |
|---|---|---|
| **HOLD** (freeze in place) | "stop", "all stop", "khalas" | All aircraft hover where they are |
| **LAND** (come down now) | "land now", "everyone down", "emergency land" | All aircraft land |

Use HOLD when something looks wrong but the aircraft are safe in the air. Use
LAND when they need to be on the ground.

### Land everything from a terminal (UI unavailable)

```bash
curl -s -X POST localhost:8765/api/command -H 'Content-Type: application/json' -d '{"action":"emergency_land","targets":["Horus","Ra","Thoth","Anubis","Osiris"]}'
```

Then run `./hu_maat.sh status` until all five show GROUNDED.

### Swarm travel phrases

Move the whole swarm, or all aircraft except some, by a distance in metres or
centimetres:

| Phrase | Effect |
|---|---|
| "All move forward 1 meter" | All five aircraft move 1 m forward together |
| "All except Anubis move back 40 centimeters" | Horus, Ra, Thoth and Osiris move 0.4 m back. Anubis holds |

### Formations

The swarm can fly 25 formation shapes. Each one was validated live in Gazebo.
The list and the evidence are in `festival/formation_catalog.json`:

line, v, circle, grid, diamond, wedge, inverted_v, arrow, triangle, cross, x,
square, perimeter, two_rows, staggered, zigzag, left_echelon, right_echelon,
column, arc, fan, t, l, pentagon (flown as circle), relay_chain (flown as line).

Example: "form a diamond", "everyone into a v".

## 6. Shutdown

```bash
./hu_maat.sh stop
```

If any aircraft is airborne, stop refuses and tells you to land first. Land
them, then run stop again. To stop anyway, use `./hu_maat.sh stop --force`, but
only if landing is impossible. Stop shuts down the chat LLM, then HU, then the
supervisor, then Gazebo, then SpaceOps (last, so it records the shutdown). It
stops only these processes.

## 7. Logs

All logs are in `~/hu-telepresence-gazebo/logs/`. The script only adds to them
and never deletes them.

- `hu_maat.log`: launcher actions (start/env/stop)
- `gazebo_<env>.log`, `hu_festival_supervisor.log`, `hu_live_server.log`, `spaceops_server.log`
- `chat_llm_service.log` (stdout/stderr) and `chat_llm/` (per-request log): chat LLM
- `spaceops_sessions/SO-*`: SpaceOps flight recordings
- `current_env`, `env_history.log`: current and past worlds

### Export ArduPilot .BIN logs

```bash
python3 ~/hu-telepresence-gazebo/scripts/export_ardupilot_logs.py            # newest session
python3 ~/hu-telepresence-gazebo/scripts/export_ardupilot_logs.py logs/spaceops_sessions/SO-...   # one session
```

This writes one `.BIN` file per aircraft to `<session>/ardupilot_logs/`. Open
them in UAV Log Viewer (https://plot.ardupilot.org) or Mission Planner. The
files use the ArduPilot DataFlash format, but the data is **SIMULATED** Gazebo
telemetry recorded by SpaceOps. It was not produced by ArduPilot firmware or
real sensors. Every file says so in its first messages and in `HU_SIMULATED=1`.

## 8. Troubleshooting

- **NOT READY on HU:** the model can be slow on a cold laptop. Run `./hu_maat.sh start` again; it reuses what is already running.
- **Port refused:** another program holds 8100/8765/8766. Close it yourself; the script never kills it.
- **Telemetry below 5/5:** check that Gazebo is running (`status`). If all aircraft are parked, `./hu_maat.sh env <current env>` reloads the world cleanly.
- **Slow machine:** raise the wait limits, e.g. `HU_TIMEOUT=400 GZ_TIMEOUT=200 CHAT_TIMEOUT=300 ./hu_maat.sh start`.
- **DEGRADED (chat LLM):** read the end of `logs/chat_llm_service.log`. You can keep flying; chat
  replies use the deterministic text. To retry only the chat service, run
  `./hu_maat.sh chat-model "$(cat config/chat_llm_model)"`.

## 9. SwarmChat language service (chat LLM, optional)

`spaceops_v8/chat_llm_service.py` runs on :8767. It writes the wording of
SwarmChat replies in the SpaceOps ground station:

- The text comes from a small local SmolLM2-Instruct model that runs offline
  on the laptop. Default: `HuggingFaceTB/SmolLM2-360M-Instruct`.
- HU's frozen encoder (SmolLM2-135M with a trained chat head) routes each
  message to an intent.
- Every reply is grounded in live telemetry. Text that conflicts with the
  telemetry is rejected and replaced by the deterministic reply.
- **It can never move an aircraft.** It has no path to the supervisor. Flight
  commands go only through HU and the flight supervisor.

Each SwarmChat message shows a provenance badge:

| Badge | Source of the text |
|---|---|
| `CHAT_LOCAL_LLM` | Worded by the local chat model (grounded and checked) |
| `TELEMETRY_FACT` | A fact read directly from telemetry |
| `SAFETY_SUPERVISOR` | A message from the flight supervisor's safety logic |
| `RULES_PERSONA` | The fixed deterministic persona text (the fallback) |
| `UNKNOWN` | The source could not be determined |

**DEGRADED** means this service is down, still loading or failed to load.
SpaceOps then falls back on its own to deterministic chat (`RULES_PERSONA` /
`TELEMETRY_FACT`). Flight safety is not affected.

`./hu_maat.sh status` shows a `Chat LLM` line with: model, loaded,
router_loaded, requests, fallbacks, accepted, rejected and average latency.

### Change the chat model

```bash
./hu_maat.sh chat-model HuggingFaceTB/SmolLM2-1.7B-Instruct
```

This writes the model id to `config/chat_llm_model` and restarts **only** the
chat service. Gazebo, the supervisor, HU and SpaceOps keep running, so you can
do it with aircraft in the air. If the environment variable `CHAT_LLM_MODEL`
is set, it overrides the file. The model must already be downloaded (the
service runs offline). If it is not, the service tries its built-in fallback
models, and status shows which model actually loaded.

## 10. Acceptance suite

```bash
bash ~/hu-telepresence-gazebo/acceptance/run_and_summarize.sh               # foreground
bash ~/hu-telepresence-gazebo/acceptance/run_and_summarize.sh --background  # detached
```

This **flies the simulated aircraft** for about 15 minutes: single-aircraft
flights, every motion primitive, swarm travel, formations, fault containment
and voice emergency. It also resets the Gazebo world to the start poses
between stages. It needs Gazebo, the supervisor, HU and SpaceOps running. Run
it only when no demo is in progress and every aircraft is on the ground. The full log is in
`acceptance/results/last_run.log` and the summary is in
`acceptance/results/last_summary.txt`.
