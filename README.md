# HU–MA'AT · Trustworthy Natural-Language Control of a Drone Fleet

**Reconfigurable Space Computing Lab (RSCL) · California State Polytechnic University, Pomona**

HU lets a person command a fleet of drones in plain language — spoken or typed, addressed to named
drones, groups or exclusions — and decides **EXECUTE**, **CONFIRM** or **REFUSE** *before* anything moves.
MA'AT is the independent safety supervisor: the only authority that lets an aircraft move.

This repository is the live demonstration package: five simulated quadcopters (**Horus, Ra, Thoth,
Anubis, Osiris**) in **Gazebo Harmonic**, the HU language pipeline, the MA'AT supervisor, the SpaceOps
ground station, and SwarmChat — a conversation room where the drones answer questions from telemetry.

> **Scope.** The HU paper's experimental results were obtained on a *physical three-drone fleet*. This package
> runs the identical frozen HU model and safety pipeline on five *simulated* aircraft. Simulated values
> (e.g. battery) are labelled as such everywhere.

## What it demonstrates

| Capability | How |
|---|---|
| Risk-asymmetric command interpretation | Frozen SmolLM2-135M encoder + trained HU heads; EXECUTE ≥ 0.85, CONFIRM 0.55–0.85, REFUSE below |
| Named / group / exclusion targeting | "Ra and Thoth, take off", "Everyone except Anubis, hover" + exact-target safety guard |
| Model-independent safety | "stop", "khalas", "land now" → fleet-wide hold/land without any language model |
| Formations and swarm travel | 25 shapes (23 distinct), separation-checked assignment; rigid group moves |
| Fault containment | Simulated link fault → refusal → re-plan without the faulted aircraft → recovery |
| Cooperative missions | Deterministic agent bids, slot negotiation, MA'AT plan check, operator approval, telemetry-verified execution |
| Conversation | SwarmChat: intent via HU's frozen encoder + trained head, wording via local SmolLM2-Instruct, facts from telemetry, every message tagged with its source |
| Evidence | Per-aircraft CSV/JSONL recordings, health curves, latency timeline, ArduPilot-format `.BIN` export |

## Quick start

```bash
bash hu_maat.sh start --env military
```

```bash
bash hu_maat.sh preflight
```

Then open the HU console at <http://localhost:8100> and SpaceOps at <http://localhost:8766>.
Full installation, demo script and emergency procedures: **[docs/RUNNING_THE_DEMO.md](docs/RUNNING_THE_DEMO.md)**.
Things for visitors to try: **[docs/VISITOR_COMMAND_CARD.md](docs/VISITOR_COMMAND_CARD.md)**.

## Verification

```bash
bash acceptance/run_offline.sh
```

```bash
bash acceptance/run_and_summarize.sh
```

The live suite flies the simulator and passes only when Gazebo telemetry confirms each outcome —
a dispatched command is never counted as success.

## Repository layout

| Path | Contents |
|---|---|
| `hu_maat.sh` | master launcher: start / env / status / preflight / stop / chat-model |
| `festival/` | HU bridge (:8100), MA'AT flight supervisor (:8765), mission parser, formation planner, mission coordinator |
| `spaceops_v8/` | SpaceOps ground station (:8766), SwarmChat, local chat-language service (:8767), intent head |
| `worlds/` | Gazebo worlds: festival stage, forest, military range, mountains |
| `acceptance/` | offline and live acceptance tests, demo runner |
| `scripts/` | environment builder, ArduPilot `.BIN` exporter |
| `docs/` | run guide, visitor card |

The HU research code and trained heads live in a separate repository (`rscl-hu-latest`).

## Citation

Mohamed El-Hadedy et al., *HU: Risk-Asymmetric Natural-Language Command of a Physical Multi-Drone Fleet*,
short paper / Demonstration 7.
