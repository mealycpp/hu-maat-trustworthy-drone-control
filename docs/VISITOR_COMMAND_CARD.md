# HU — Try It Yourself (Visitor Command Card)

Type or say a command at **http://localhost:8100** and watch HU decide **before** anything moves.
Five simulated quadcopters in Gazebo: **Horus · Ra · Thoth · Anubis · Osiris**.

> The paper's results come from a **physical three-drone fleet**. This live demo runs the
> **identical frozen HU model and safety pipeline** on five simulated drones in Gazebo.

| Try saying… | What HU does | Why |
|---|---|---|
| **Horus, take off** | ✅ **EXECUTE** (conf ≈ 0.93) | Clear command, one named drone |
| **Ra and Thoth, take off** | ✅ **EXECUTE** (≈ 0.87) | Group addressed by name |
| **Everyone except Ra, land** | ✅ **EXECUTE** (≈ 0.92) | Exclusion: four drones, Ra stays |
| **Everyone, take off** | ✅ **EXECUTE** (≈ 0.97) | Whole fleet; close pairs launch staggered |
| **Everyone except Anubis, hover** | 🟡 **CONFIRM** (≈ 0.82) | Less certain → operator must confirm |
| **Osiris, land** | 🟡 **CONFIRM** (≈ 0.83) | Below the 0.85 execute threshold |
| **Horus or Ra, land** | ⛔ **REFUSE** | Ambiguous: which drone? |
| **Land** | ⛔ **REFUSE** | No addressee — never guesses "all" |
| **This project is really taking off** | ⛔ **REFUSE** (HU, ≈ 0.90) | Not a flight command |
| **Thoth, do a backflip** | ⛔ **REFUSE** | Outside authorized maneuvers |
| **All, khalas** / **stop** | 🛑 **EMERGENCY HOLD** | Symbolic safety channel — no language model involved |
| **Land now** / **everyone down** | 🛑 **EMERGENCY LAND** | Symbolic safety channel |

**Risk-asymmetric bands:** EXECUTE only at confidence ≥ 0.85 · CONFIRM between 0.55 and 0.85 · REFUSE below.
A wrong action is worse than no action: HU refuses rather than guesses.

**Also try (deterministic mission planner, always asks for confirmation):**
*All form a V shape* · *All form a circle* · *All move forward 1 meter* · *Thoth, turn left 90 degrees* · *Horus, spin*

*Confidence values were measured live on 2026-11-11; they vary slightly with wording.*
