# Try It — Every Kind of Command

Type a command in the **HU console** (<http://localhost:8100>) or press the microphone and say it.
Five drones: **Horus · Ra · Thoth · Anubis · Osiris**.

Every command gets one of four answers **before anything moves**:

| Answer | Meaning |
|---|---|
| 🟢 **EXECUTE** | HU is confident (≥ 0.85); MA'AT checks safety and the drone moves |
| 🟡 **CONFIRM** | A human must press **Confirm** first |
| 🔴 **REFUSE** | Not a safe or valid command; nothing moves, and the reason is shown |
| 🛑 **EMERGENCY** | Fleet-wide hold or land; no language model involved |

**Golden rule:** always say *who* (a name, several names, `everyone`, or `everyone except …`) and *what*.
One action per command.

---

## 1. Who you can talk to

| Pattern | Example |
|---|---|
| One drone | `Horus, take off` |
| Several drones | `Ra and Thoth, take off` · `Horus, Ra and Osiris, take off` |
| Whole fleet | `Everyone, take off` · `All drones, take off` · `All, hover` |
| Everyone but one | `Everyone except Ra, land` · `All except Anubis, hover` |

---

## 2. Take off · hover · land — decided by HU (the trained language model)

HU reads these sentences and gives a confidence score, so the same command can come back EXECUTE or
CONFIRM depending on wording. That is the point: **when HU is unsure, a human decides.**

| Try | Typical answer (measured live) |
|---|---|
| `Horus, take off` | 🟢 EXECUTE (≈ 0.93) |
| `Ra and Thoth, take off` | 🟢 EXECUTE (≈ 0.87) |
| `Everyone, take off` | 🟢 EXECUTE (≈ 0.97); close drones launch in turns (staggered takeoff) |
| `Everyone except Ra, land` | 🟢 EXECUTE (≈ 0.92) |
| `Everyone except Anubis, hover` | 🟡 CONFIRM (≈ 0.82) |
| `Osiris, land` | 🟡 CONFIRM (≈ 0.83) |

More to try (HU decides each one live):

```
Ra, launch
Thoth, lift off
Ra, please take off
All drones, take off
Anubis, hover
Horus, hold position
Everyone, hover
Thoth, touch down
Ra and Thoth, land
Everyone, land
```

---

## 3. Move — up, down, forward, back, left, right (always 🟡 CONFIRM)

Say a direction **and** a distance between **10 centimeters and 2 meters** (`cm`, `centimeters`,
`m`, `meter`, `meters`).

| Try | What happens |
|---|---|
| `Horus, go up 50 centimeters` | Horus climbs 0.5 m |
| `Ra, climb 1 meter` | Ra climbs 1 m |
| `Thoth, go down 30 cm` | Thoth descends 0.3 m |
| `Anubis, move forward 1 meter` | Forward along its own heading |
| `Osiris, move back 50 centimeters` | Backward |
| `Horus, move left 1 meter` | Left |
| `Ra, move right 0.5 meters` | Right |

Altitude stays between 0.65 m and 2.8 m, and the arena is ±3.35 m; MA'AT refuses anything outside it.

### Swarm travel: move several drones together, keeping their shape

| Try | What happens |
|---|---|
| `Everyone, move right 1 meter` | The whole fleet slides right, keeping the formation |
| `Ra and Thoth, move forward 1 meter` | The pair moves together |
| `Everyone except Osiris, move back 50 cm` | Four drones move back |
| `All, go up 30 centimeters` | The whole fleet climbs |

Tip: form a shape first (`All form a V`), then `Everyone, move forward 1 meter`.

---

## 4. Turn and spin (always 🟡 CONFIRM)

| Try | What happens |
|---|---|
| `Horus, turn left 90 degrees` | Rotates in place 90° left |
| `Ra, turn right 45 degrees` | 45° right |
| `Thoth, rotate left 30 degrees` | 30° left |
| `Everyone, turn right 90 degrees` | The whole fleet turns |
| `Osiris, spin` | Full 360° spin in place |
| `Horus, spin left` | Full 360° spin |

Turns must be between **5° and 180°** and say **left or right**.

---

## 5. Formations: 25 shapes (always 🟡 CONFIRM)

Needs at least two drones (the drones must be flying). MA'AT assigns slots so no two drones come closer
than 0.88 m.

```
All form a V                 All form an inverted V        All form a line
All form a column            All form a circle             All form a ring
All form a diamond           All form a square             All form a grid
All form a triangle          All form a cross              All form an X
All form an arc              All form a fan                All form a wedge
All form an arrow            All form a zigzag             All form a staggered line
All form two rows            All form a left echelon       All form a right echelon
All form a T shape           All form an L shape           All form a perimeter
All form a pentagon          All form a relay chain
```

Other wordings and subsets:

```
All form a V shape
Everyone, change to a diamond
Horus, Ra and Thoth, form a triangle
Everyone except Anubis, form a line
```

Try switching shape while flying: `All form a line` → `All form a circle` → `All form an X`.

---

## 6. Things HU will refuse (try them!)

Refusing is a feature: a wrong action is worse than no action.

| Try | Why it is refused |
|---|---|
| `Land` · `Take off` | No addressee; HU never guesses "all" |
| `Horus or Ra, land` | Ambiguous: which one? |
| `All and Ra, take off` | Mixed targets; use "all except …" |
| `Everyone except, land` | Incomplete exclusion |
| `Horus, move forward` | No distance given |
| `Horus, move forward 5 meters` | Over the 2 m per-command limit |
| `Horus, move forward 2 centimeters` | Under the 10 cm minimum |
| `Ra, move left and right 1 meter` | Two directions |
| `Ra, turn left 270 degrees` | Turns are limited to 5–180° |
| `Horus, spin 90 degrees` | "Spin" means 360°; use "turn" for an angle |
| `Horus, take off and move forward 1 meter` | Two actions at once; give one at a time |
| `Everyone, take off then form a circle` | Two actions at once |
| `Ra, form a circle` | A formation needs at least two drones |
| `All form a hexagon` | Unknown shape |
| `Thoth, do a backflip` | Not an authorized maneuver |
| `Thoth, orbit the stage` · `Anubis, patrol the area` | Missions not implemented as flight commands |
| `This project is really taking off` | Not a command, even with "taking off" in it |
| `What a beautiful day` · `I love drones` · `Can you hear me?` | Not a flight command |

---

## 7. Emergency words: always work, from any state

No language model is involved, so these cannot be misunderstood.

| Say | Effect |
|---|---|
| `stop` · `all stop` · `everyone stop` · `abort` · `khalas` · `All, khalas` | 🛑 **HOLD**: every drone stops and hovers in place |
| `land now` · `everyone down` · `everybody down` · `emergency land` · `Everyone, land now` | 🛑 **LAND**: every drone lands |

A plain `land` with no name is *not* an emergency (it is refused); say `Everyone, land` or `land now`.

---

## 8. A five-minute demo script

```
Everyone, take off
All form a V
Everyone, move forward 1 meter
All form a circle
Horus, turn left 90 degrees
Everyone except Anubis, hover
This project is really taking off        (watch it refuse)
All, khalas                              (emergency hold)
Everyone, land
```

---

## 9. Talking to the drones (SwarmChat, in SpaceOps)

Open <http://localhost:8766> and use the chat panel. **Chat can never move a drone.**

```
How are you all doing?
Horus, what is your battery?
Ra, how high are you?
Thoth, how is your tracking error?
Anubis, are there any faults?
Ra, what's your favourite formation?
Thoth, do you actually understand me?
Who is the best drone?
Tell me a joke
Can you explain that more simply?
What about Osiris?
Team, can you inspect the north sector?
Horus, take off                          (chat refuses and points you to the HU console)
```

Every reply shows a badge saying where it came from: `TELEMETRY_FACT`, `CHAT_LOCAL_LLM`,
`RULES_PERSONA`, `SAFETY_SUPERVISOR`, …
