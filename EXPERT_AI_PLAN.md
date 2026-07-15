# Expert AI — Implementation Plan

Expert-level American and Soviet computer opponents for NORAD. Build the
**American side first**; the shared components are designed so the Soviet expert
reuses them. Joint Monte-Carlo tuning is the final step once both sides exist.

## Status
- **AMERICAN expert: DONE** (Phases 1–6 below), refined via several human games.
- **SOVIET expert: BUILT (Jul 9c), not yet human-validated.** `UsDefenseBelief` +
  `ExpertSovietAI` in `game_ai_expert.py`; `RussianAI(game,"expert")` dispatch;
  menu "expert_sov"; `tier5`. Mirror belief: the 5 US missiles are public
  (distinct all-real silhouette → known cities); real vs decoy fighters share a
  silhouette (12R+4D US, 3R+1D CA, separate pools) and a fighter reveals as real
  only by MOVING — so decoys FLUSH real fighters. Policy: bait the known missile
  cities with decoys, flush fighters, escalate reals as the fighter pool drains,
  Cuban 3R+2D at southern jewels, SLBM decoy-baits-first. Next: user plays it
  (solo_us) and gives recommendations, same loop that refined the American.
- **Phase 1 — BeliefTracker: DONE** (`game_ai_expert.py`, `tier4a_belief`).
- **Phase 2 — ThreatModel: DONE.**
- **Phase 3 — Expert setup (interdiction placement): DONE.** `tier4c`.
- **Phase 4 — Expert turn/fire policy: DONE.** `tier4d`. Controlled 3-way
  benchmark on identical seeds (54 league games each): Soviet win rate
  screen/screen **69%** -> expert-setup/screen-play **35%** -> full expert
  **24%** (avg Soviet pts 101.1 -> 92.5 -> 91.8). Each component adds value;
  parameters are still UNTUNED (Phase 5).
- **Phase 5 — Tuning harness + deferred setup upgrades: DONE (harness built).**
  `tools/tune_ai.py` (CEM, common random numbers, writes tools/expert_params.json;
  `tier4e`). Setup now has Cuban-column position-awareness (CUBAN_THETA_SCALE)
  and controlled randomization (SETUP_EPS; 0 = deterministic). Baseline (untuned
  defaults) on the harness's 18-game league: 14/18 American wins. A full
  optimization run is an overnight job; applying its output params is the
  remaining step.
- **Phase 6 — Integration: DONE.** `game_ai.AmericanAI(game, "expert")` delegates
  to ExpertAmericanAI (lazy import; loads tools/expert_params.json if present,
  else class defaults). Menu game-option "Expert American AI" wires the American
  AI to the expert (else a random legacy doctrine); "expert" is NOT in the random
  US_STYLES rotation. `tier4f` covers dispatch + menu wiring + a benchmark
  (expert >= screen over an 18-game league).

## Status is HONEST, not "done". The expert AI is fully BUILT and integrated
(Phases 1-6 all wired: belief, threat, setup, turn/fire, tuner, menu), and the
suite is green - but its PLAYING STRENGTH IS NOT VALIDATED. Earlier "American
wins ~76%" numbers were favourable-hash-seed noise. Under reproducible fixed-seed
benchmarks the current expert is NOT reliably better than the simple `screen`
doctrine, and is worse against blitz (which rushes real bombers early). See the
correction notes below.

### Why the benchmarks were misleading (the core lesson)
1. Tuning American win-rate against blitz/feint/flank optimises against WEAK,
   known-flawed opponents. They all lead with decoys and never punish a fighter
   stack, so real weaknesses (over-committing to early waves; stacking fighters)
   were invisible to 175+ AI games but obvious in ONE human game.
2. Benchmarks were not even reproducible: the same game returned different
   results run-to-run (e.g. one screen game gave 92/92/97 points), so quantitative
   comparisons were unreliable. Root cause NOT yet isolated (a fixed PYTHONHASHSEED
   did not fully determinise it - suspect residual set/dict-order or unseeded
   global random somewhere in the play path). MUST be fixed before any real
   quantitative tuning.
3. Do NOT treat win-rate-vs-bots as a quality oracle. Use it for REGRESSION only
   (crash / gross-loss / legality). Real validation needs a competent adversary
   and human games as scenario tests.

### Corrections applied (stabilised baseline)
- SETUP_EPS back to 0 (deterministic best placement). The randomize-among-near-
  best mechanism is kept but OFF - as written it cost too much defensive quality.
- FRONT_LOAD back to a modest 0.3 (honest structural prior, not noise-tuned;
  high values ignore early REAL bombers and lose to blitz).
- Anti-stacking KEPT: fighters/decoys never share a city cell (a single bomb
  takes the whole stack - the Omaha failure from human play). Opponent-agnostic,
  principled. tier4c asserts <=1 US unit per city.
- tier4f is now a STRUCTURAL regression only (full games complete, setup legal,
  no stacks) - no flaky win-rate assertion.

### Real next steps (in order)
1. Fix reproducibility (deterministic set iteration or a fully-seeded run) so any
   measurement is trustworthy.
2. Human-game capture -> scenario tests: record games; encode the user's winning
   line (decoys north, 3 real Cubans, overload the jewel) as a scripted STRONG
   Soviet opponent; add each human finding as a regression assertion.
3. Build the SOVIET expert (mirror BeliefTracker/ThreatModel) so American tuning
   has a competent adversary; only then is joint self-play tuning meaningful.

## Locked decisions
- Start with the **American** expert.
- Expose the expert as a **selectable difficulty** in the menu (not only the
  random doctrine rotation).
- **Assigned-targets rule is NOT used with this expert AI.** It is excluded from
  the training (Phase 5) and regression (Phase 6) leagues. The expert still runs
  without crashing if targets happens to be enabled (it never reads the hidden
  `u.target`), but it is neither designed nor tuned for that rule.
- No new dependencies (no scipy) — all optimization hand-rolled.
- **fortress** is a broken US doctrine (0/9 in the baseline tournament); exclude
  it from the *training* league but keep it as a regression benchmark.

## Baseline (legacy doctrines, 36-game tournament)
Soviets win ~53%, average 101 pts (games land right at the 100 threshold).
flank is the strongest Soviet; screen/picket the strongest Americans; fortress
loses everything. Board = 227 pts over 37 cities; Soviets need 100.

---

## Design constraints (non-negotiable)

**Fairness.** The expert uses only what a human sees: unit *positions*,
silhouette *class* (bomber-type vs missile-type), *reveals*, *staging*,
*destroyed cities*, and the *option rules* in play. It must NEVER read an
unrevealed enemy unit's real/decoy flag.
- Enforced by `BeliefTracker.observed(u)` — the sole reader of `u.real`, gated
  on `u.revealed`. `tier4a` greps that `u.real` appears nowhere else in
  `game_ai_expert.py`.
- `u.kind` distinguishes `bomber` from `decoy_bomber` (i.e. real from decoy), so
  it must NOT be used to tell them apart — group only by the pair
  `("bomber","decoy_bomber")` (and `("missile","decoy_missile")`).
- The fair reachability helper (`reachable_cities`) deliberately avoids
  `game_rules.can_reach`'s real-only lateral relaxation, so its output can't
  leak identity.

**Observable state the expert may use:** `u.cell`, `u.revealed` (+`u.real` once
revealed), `u.group` (entry edge — Cuban units visibly come from the south),
`u.frozen`, `g.staged_units()`, `g.destroyed`, `g.points`, `g.turn`, `g.opt`,
and all board geometry.

**Location.** All expert code lives in `game_ai_expert.py`. Integration seam:
`game_ai.AmericanAI(game, style)` gains a dispatch — `style == "expert"` composes
an `ExpertAmericanAI` and forwards `place_all_units` / `ask_fire` / `take_turn`.
Legacy doctrines stay untouched.

---

## Phase 1 — BeliefTracker  (DONE)
`P_real[uid] ∈ [0,1]` for every alive bomber-type (and SLBM) silhouette.

**Three layers → each P(real):**
1. **Group prior** — Cuban silhouettes come from a 3-real-of-8 pool
   (`CUBAN_PRIOR = 3/8`); the main force is real-heavy (`NORTH_PRIOR = 20/23`).
   Only the *ratio* matters (layer 3 sets the absolute level).
2. **Behavioural** — a silhouette that walks away from the best city it could
   still have bombed (without bombing) looks like a decoy; log-evidence ∝ the
   *forfeited* reachable-unguarded value (`BETA = 0.15`/pt). Passing a low city
   while a higher one stays reachable is ~no evidence; missile-guarded cities
   count for nothing.
3. **Count anchor** — one global log-odds shift (bisection) makes the expected
   reals among *visible* unrevealed units equal the pool's remaining reals
   scaled by the visible fraction. The decoy cap falls out for free: all 8
   decoys revealed ⇒ every survivor reads P=1.

Reveals collapse a unit to 0/1 and shrink the pool counts.

**API:** `update()` once per American decision; `prob_real(u)`;
`all_decoys_revealed()`; `expected_reals_visible(cls)`.
**Validation:** `tier4a` + a 30-game smoke check — 0 crashes, Brier 0.173 vs
~0.245 baseline, crisp extremes (P≤0.4 → 0 % real, P=1.0 → 100 %). Mid-range
miscalibration is left for Phase 5 tuning.

## Phase 2 — ThreatModel  (IN PROGRESS)
Answers "how many points can the Soviets still score, at most?" and ranks
American actions by how much they lower it.

- `menu(u)` — undestroyed cities a silhouette could still bomb (fair
  `reachable_cities` BFS), best value first.
- **Expected ceiling** — greedy max-weight assignment of on-board silhouettes →
  *distinct* cities, weight `points × P_real`; each city bombable once.
- **Worst-case ceiling** — admissible upper bound: every non-revealed-decoy
  attacker treated as real, top-R reachable city values (pool/off-board units
  reach anything). Used for the closing rule.
- **Marginals** — a silhouette's assigned value (interception priority); a
  city's threat = Σ P_real of units that can bomb it.
- **Provably-won gate** — `worst_case_ceiling() < 100` ⇒ the American can stop
  spending fighters entirely.

**Test (`tier4b`):** monotonicity (killing a bomber never raises either
ceiling); worst-case ≥ expected; provably-won fires at end states and not at the
start; fully-revealed board's expected ceiling = exact greedy.

## Phase 3 — Expert setup (interdiction)
Place 5 missiles + fighters + 4 decoys to **minimize the ceiling**, not to
"cover value."
- **Predicted axes** from staging + options: `cuban` → read row-V staging, shift
  south ∝ staged count; `siberian` → weight west (`theta_mid`/`extremeness`);
  `dew` → prioritise Anchorage+Godthab denial; `slbm` → hold coastal coverage.
- **Missiles** — greedily on the city whose defence most reduces the ceiling
  (respect `can_place_us`).
- **Fighters** — maximise expected interceptions of high-value threats within a
  bomber-move of top cities, along predicted axes.
- **Decoys** (visually identical to fighters) — inflate *apparent* coverage over
  cities being conceded, forcing the Soviet to spend real bombers to test them.

### Deferred into Phase 4/5 (from the deterministic-setup review)
The Phase 3 setup is currently fully deterministic (identical observable inputs →
identical placement; it keys only on the Cuban *count*, not the staged columns).
Two upgrades are folded into Phase 4/5: (a) **position-awareness** — weight a
city's Cuban threat by angular alignment to the *actual* staged columns (east vs
west lean), a strict information gain; (b) **controlled randomization** — when
several placements are within ε priority of the best, pick via a seeded RNG so
the defense isn't memorisable across games (ε is a Phase-5 tunable trading EV for
unpredictability). Mixed-strategy randomization already applies to `ask_fire`.

## Phase 4 — Expert turn policy
- **Interception by marginal value** — rank engagements by `Δceiling × P_real`;
  spend a fighter on the top one over a threshold that tightens as bullets
  deplete, loosens near the endgame.
- **Bullet economy** — never spend the last fighters on low-`P_real` threats
  unless decisive.
- **Provably-won cutoff** — worst-case ceiling < 100 ⇒ hold all fighters.
- **`ask_fire` as EV** — fire iff `P_real × protected_value > deterrence_value`
  (a live missile keeps its city off the Soviet menu). Endgame override to
  prevent a 100-crossing bomb; small randomisation near the threshold (mixed
  strategy) so a human can't learn a fixed schedule.

## Phase 5 — Tournament / tuning harness
`tools/tune_ai.py`: parameterise thresholds/weights, optimise (CEM/hill-climb)
over the league (all legacy Soviet doctrines × option sets × seed batches),
objective = American win rate (tie-break: minimise Soviet margin over 100).
fortress excluded from training; option sets are drawn from {dew, siberian,
cuban, slbm(+canadian)} - **DEW is included**, the **assigned-targets rule is
excluded from every option set** (not used with this AI). When the Soviet expert exists, alternate
self-play tuning (2–3 rounds) against a diverse league to avoid overfitting.

## Phase 6 — Integration + regression tier
`US_STYLES["expert"]`, menu "difficulty" toggle, `AmericanAI` dispatch. New
`tier4` tests: fairness grep; belief/threat unit tests; setup legality;
**benchmark** (expert win rate ≥ best legacy doctrine over fixed seeds);
full-game no-crash across every option set. Update `HANDOFF.md`.

---

## Build order & risk
Belief + Threat (shared) → Setup → Turn policy → Harness → Integration.
Main risks: belief mis-calibration (validate via Brier offline); a too-loose
worst-case bound (keep it strictly admissible); overfitting to legacy Soviets
(fixed by the joint-tuning phase). RL/neural approaches are explicitly avoided —
the game is small and legible; a belief-plus-optimization AI is strong,
debuggable, and explainable in the game log.
