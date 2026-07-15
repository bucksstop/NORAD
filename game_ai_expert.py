"""Expert-level AI for NORAD, built on two side-agnostic components.

  BeliefTracker - a probability estimate P(real) for every hidden Soviet
                  silhouette, from PUBLIC information only: the known force
                  composition (pool arithmetic), observed movement, and
                  reveals. It never inspects the hidden real/decoy flag of an
                  UNREVEALED enemy unit - the single guarded accessor
                  `observed()` reads it only once a unit is revealed (which is
                  public). A test enforces that no other code reads the flag.
  ThreatModel   - (Phase 2, not yet built) the maximum Soviet score still
                  achievable, used to rank American actions.

This module has no pygame dependency.

Model (BeliefTracker)
---------------------
Three layers combine into each P(real):
  1. Group prior     - a Soviet bomber silhouette that entered from Cuba comes
                       from a 3-real-of-8 pool; the main (north/Siberian) force
                       is real-heavy. Only the Cuban-vs-north RATIO matters
                       here; the absolute level is set by layer 3.
  2. Behavioural     - a silhouette that walks AWAY from the best city it could
                       still have bombed (and did not bomb) looks like a decoy;
                       the log-evidence is proportional to the reachable value
                       it forfeited (missile-guarded cities count for nothing,
                       and giving up a low city while a higher one stays
                       reachable is no evidence - per the refined rule).
  3. Count anchor    - the expected number of reals among the VISIBLE unrevealed
                       silhouettes must equal the pool's remaining reals scaled
                       by the visible fraction. Enforced by one global log-odds
                       shift (bisection). This makes the decoy cap automatic:
                       once all 8 decoys are revealed every remaining silhouette
                       is certainly real.
"""
import json
import math
import os
import random

import game_ai
import game_rules

_PARAMS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "tools", "expert_params.json")


def load_tuned_params():
    """Best parameter vector written by tools/tune_ai.py, or {} if none/invalid.
    Only the production dispatch (game_ai.AmericanAI style 'expert') applies
    these; constructing ExpertAmericanAI directly keeps the class defaults, so
    tests stay independent of this file."""
    try:
        with open(_PARAMS_FILE) as f:
            return json.load(f).get("params", {}) or {}
    except (OSError, ValueError):
        return {}

# Known force composition - printed on the counters, so public information.
BOMBER_REAL = 23
BOMBER_DECOY = 8
SLBM_REAL = 3
SLBM_DECOY = 1

# Relative per-group priors P(real) for a bomber silhouette (layer 1). The
# Cuban set is 3 real of 8; the main force is the remaining 20 real of 23. The
# absolute level is corrected every update by the count anchor (layer 3), so
# only the Cuban-vs-north ratio here is load-bearing.
CUBAN_PRIOR = 3.0 / 8.0
NORTH_PRIOR = 20.0 / 23.0
SLBM_PRIOR = SLBM_REAL / float(SLBM_REAL + SLBM_DECOY)

_BOMBER_KINDS = ("bomber", "decoy_bomber")
_SLBM_KINDS = ("missile", "decoy_missile")


async def _anoop(*_):
    """Awaitable no-op: the default on_event when the UI supplies none (headless
    AI-vs-AI). take_turn awaits on_event, so the fallback must be awaitable."""
    return None


def _sigmoid(x):
    if x <= -60.0:
        return 0.0
    if x >= 60.0:
        return 1.0
    return 1.0 / (1.0 + math.exp(-x))


def _logit(p):
    p = min(1.0 - 1e-9, max(1e-9, p))
    return math.log(p / (1.0 - p))


def _solve_shift(logodds, target):
    """Global additive log-odds shift s so that sum(sigmoid(lo + s)) == target
    (the count anchor). Monotonic in s, so a bisection always converges."""
    n = len(logodds)
    if n == 0:
        return 0.0
    target = min(float(n), max(0.0, target))
    lo, hi = -80.0, 80.0
    for _ in range(64):
        mid = 0.5 * (lo + hi)
        s = sum(_sigmoid(x + mid) for x in logodds)
        if s < target:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def reachable_cities(game, u, from_cell, cache=None):
    """Every undestroyed city a bomber-type silhouette could legally reach and
    stop on, starting from `from_cell`.

    FAIR: uses only class-level movement - direction by `u.group` and the <=2
    lateral cap that applies to every bomber - never the real-only lateral
    relaxation in game_rules.can_reach, so the result cannot depend on (or leak)
    whether the unit is real. Multi-turn: a legal stop becomes a fresh start.
    Cached by (group, cell, #destroyed) since same-group units move identically.
    """
    g = game
    key = (u.group, from_cell, len(g.destroyed))
    if cache is not None and key in cache:
        return cache[key]
    reached = set()
    seen_stops = {from_cell}
    stops = [from_cell]
    allowance = u.move
    while stops:
        s = stops.pop()
        turn_seen = {(s, 0, 0)}
        frontier = [(s, 0, 0)]
        while frontier:
            cell, steps, lat = frontier.pop()
            if steps >= allowance:
                continue
            srow = g.board.row_i[cell]
            for nb in g.board.nbrs[cell]:
                if not g._step_ok(u, cell, nb):
                    continue
                nlat = lat + (1 if g.board.row_i[nb] == srow else 0)
                if nlat > game_rules.LATERAL_LIMIT:
                    continue
                st = (nb, steps + 1, nlat)
                if st in turn_seen:
                    continue
                turn_seen.add(st)
                frontier.append(st)
                if g.board.is_city(nb) and nb not in g.destroyed:
                    reached.add(nb)
                if nb not in seen_stops and g._dest_geom_ok(u, s, nb):
                    seen_stops.add(nb)
                    stops.append(nb)
    reached = frozenset(reached)
    if cache is not None:
        cache[key] = reached
    return reached


class BeliefTracker:
    """P(real) for every hidden Soviet silhouette, from public info only.

    Call update() once at the start of each American decision (take_turn /
    ask_fire); query prob_real(unit) thereafter.
    """

    BETA = 0.20         # decoy log-evidence per point of forfeited value. A unit
    #                     that flew PAST bombable cities (a real bomber almost
    #                     always bombs its first chance) looks strongly like a
    #                     decoy - this is what should stop the AI shooting a decoy
    #                     that has passed many cities (the St. Louis case).
    FRONT_LOAD = 0.55   # decoys tend to LEAD the waves, so an early/first-wave
    #                     silhouette is more likely a decoy (0 = random sample of
    #                     the pool; 1 = every hidden decoy is already on board).
    #                     Safe to keep fairly high now that interception is
    #                     VALUE-GATED: an early REAL bomber heading for an
    #                     undefended jewel is still engaged (low P bar for high-
    #                     value cities), so front-loading only relaxes defence of
    #                     LOW-value early threats, which are usually decoys.
    CUBAN_STALL_CAP = 0.25  # Rule C: a Cuban that has moved but not bombed and
    #                     can't reach an undefended jewel next move is capped at
    #                     this P(real) - highly likely, but NOT certain, a decoy.

    def __init__(self, game):
        self.g = game
        # Counter mix is PUBLIC: derive the bomber pool from the actual force so
        # the Play Balance rule (one fewer Soviet decoy: 8 -> 7) is reflected.
        self.n_real = sum(1 for u in game.soviet_units() if u.kind == "bomber")
        self.n_decoy = sum(1 for u in game.soviet_units()
                           if u.kind == "decoy_bomber")
        self.n_real_slbm = SLBM_REAL
        self.n_decoy_slbm = SLBM_DECOY
        self.p = {}                     # uid -> P(real) for unrevealed units
        self._counted = set()           # uids already accounted at reveal time
        self._prev_cell = {}            # uid -> cell at the previous update
        self._ev = {}                   # uid -> accumulated decoy log-evidence
        self._reach_cache = {}          # (group, cell, #destroyed) -> cities.
        #                                 PERSISTS across turns: `destroyed` only
        #                                 grows, so a cached key stays valid for
        #                                 the whole game (stale keys never re-hit).
        self._confirmed_decoy = set()   # uids proven decoy by their behaviour
        self._prob_cap = {}             # uid -> soft P(real) cap (Rule C), per-turn

    # ---- the ONE guarded read of hidden enemy identity -------------------
    def observed(self, u):
        """A Soviet unit's real/decoy status, readable ONLY once the unit is
        revealed (a public event - combat, bombing, or a reveal). Returns
        'real', 'decoy', or None while still hidden. This is the sole place the
        expert AI inspects the hidden flag; a fairness test asserts as much."""
        if not u.revealed:
            return None
        return "real" if u.real else "decoy"

    # ---- observable silhouettes -----------------------------------------
    def _kinds(self, cls):
        return _SLBM_KINDS if cls == "slbm" else _BOMBER_KINDS

    def silhouettes(self, cls="bomber"):
        """Alive, on-board-or-staged Soviet units of a silhouette class. A
        decoy is indistinguishable from a real unit here (same silhouette)."""
        kinds = self._kinds(cls)
        return [u for u in self.g.soviet_units()
                if u.alive and u.kind in kinds
                and (u.cell is not None or u.staged)]

    # ---- per-update refresh ---------------------------------------------
    def update(self):
        # NB: _reach_cache is NOT cleared - see its definition; keying on
        # #destroyed keeps every entry valid for the whole game, and reusing it
        # across turns is the main speedup for the behavioural-evidence pass.
        self._account_reveals()
        self._detect_passed_decoys()
        self._update_class("bomber")
        self._update_class("slbm")
        self._prev_cell = {u.id: u.cell for u in self.g.soviet_units()
                           if u.alive}

    def _detect_passed_decoys(self):
        """Prove a silhouette is a decoy from its behaviour (a real bomber almost
        always bombs its first chance at a high city). Once proven, prob_real
        returns 0 forever - the AI stops firing at it or intercepting it.
          Rule B: it ENDS a turn on an undestroyed 7/8/9-pt city (it was there
                  and did not bomb).
          Rule A: it passes (south for the northern force, north for Cuban)
                  WITHIN 4 squares east/west of an UNDEFENDED 7/8/9-pt city.
          Rule C (Cuban only, SOFT): a Cuban bomber that has had its first full
                  move (the turn after it launched) and did not bomb has its
                  P(real) CAPPED at CUBAN_STALL_CAP (highly likely, not certain,
                  a decoy) - UNLESS it can still reach an undefended 7/8/9 city
                  on its NEXT move. Re-evaluated each turn (not a permanent
                  verdict), so a real Cuban travelling two moves to a jewel is
                  doubted but not written off."""
        g = self.g
        highs = [c for c in g.board.cells
                 if g.board.is_city(c) and g.board.city(c)["points"] >= 7]

        def undefended_high(cids):
            return any(g.board.city(c)["points"] >= 7 and not g.missile_defended(c)
                       and c not in g.destroyed for c in cids)

        for u in self.silhouettes("bomber"):
            if u.revealed or u.id in self._confirmed_decoy or u.cell is None:
                continue
            cur = u.cell
            if (g.board.is_city(cur) and cur not in g.destroyed
                    and g.board.city(cur)["points"] >= 7):
                self._confirmed_decoy.add(u.id)          # Rule B
                continue
            rcur = g.board.row_i[cur]
            hit = False
            for c in highs:
                if c in g.destroyed or g.missile_defended(c):
                    continue
                rc = g.board.row_i[c]
                passed = (rcur < rc) if u.group == "cuban" else (rcur > rc)
                if passed and self._lateral_gap(cur, c) <= 4:
                    self._confirmed_decoy.add(u.id)      # Rule A
                    hit = True
                    break
            if hit:
                continue
            # Rule C - Cuban timing (SOFT cap, re-evaluated each turn).
            et = getattr(u, "entered_turn", -1)
            stalled = (u.group == "cuban" and et >= 0 and g.turn > et
                       and not undefended_high(self._one_move_cities(u, cur)))
            if stalled:
                self._prob_cap[u.id] = self.CUBAN_STALL_CAP
            else:
                self._prob_cap.pop(u.id, None)       # cap lifts (or never applied)

    def _one_move_cities(self, u, cell):
        """Undestroyed cities `u` could reach and stop (hence bomb) in a SINGLE
        move from `cell` - its next turn. Fair (class-level movement only)."""
        g = self.g
        reached = set()
        seen = {(cell, 0, 0)}
        frontier = [(cell, 0, 0)]
        while frontier:
            c, steps, lat = frontier.pop()
            if steps >= u.move:
                continue
            srow = g.board.row_i[c]
            for nb in g.board.nbrs[c]:
                if not g._step_ok(u, c, nb):
                    continue
                nlat = lat + (1 if g.board.row_i[nb] == srow else 0)
                if nlat > game_rules.LATERAL_LIMIT:
                    continue
                st = (nb, steps + 1, nlat)
                if st in seen:
                    continue
                seen.add(st)
                frontier.append(st)
                if g.board.is_city(nb) and nb not in g.destroyed:
                    reached.add(nb)
        return reached

    def _lateral_gap(self, cell, city):
        """How many squares east/west `cell`'s column is from `city`, measured
        along the city's row (a public, geometry-only quantity)."""
        b = self.g.board
        order = b.west_order(b.cells[city]["row"])       # west -> east
        ci = order.index(city)

        def theta(x):
            w, e = b.interval[x]
            return (w + e) / 2.0

        cth = theta(cell)
        ui = min(range(len(order)), key=lambda i: abs(theta(order[i]) - cth))
        return abs(ui - ci)

    def _account_reveals(self):
        """Fold every newly revealed unit into the pool counts (public event)."""
        for u in self.g.soviet_units():
            if u.id in self._counted:
                continue
            o = self.observed(u)
            if o is None:
                continue
            self._counted.add(u.id)
            self.p[u.id] = 1.0 if o == "real" else 0.0
            slbm = u.kind in _SLBM_KINDS
            if o == "real":
                if slbm:
                    self.n_real_slbm = max(0, self.n_real_slbm - 1)
                else:
                    self.n_real = max(0, self.n_real - 1)
            else:
                if slbm:
                    self.n_decoy_slbm = max(0, self.n_decoy_slbm - 1)
                else:
                    self.n_decoy = max(0, self.n_decoy - 1)

    def _update_class(self, cls):
        if cls == "slbm":
            n_real, n_decoy = self.n_real_slbm, self.n_decoy_slbm
            behavioural = False
        else:
            n_real, n_decoy = self.n_real, self.n_decoy
            behavioural = True
        units = [u for u in self.silhouettes(cls) if not u.revealed]
        remaining = n_real + n_decoy
        if not units:
            return
        if remaining <= 0 or n_real <= 0:
            for u in units:
                self.p[u.id] = 1.0 if remaining > 0 and n_decoy <= 0 else 0.0
            return
        if behavioural:
            self._accrue_behaviour(units)
        # Layer 3: anchor the expected number of reals among the visible units.
        # The naive assumption is that the visible units are a random sample of
        # the unrevealed pool -> V/U of the remaining reals. But a rational
        # Soviet LEADS WITH DECOYS (decoys screen/bait early; reals are held or
        # committed later), so the already-appeared (visible) set is decoy-
        # enriched relative to the still-hidden pool. FRONT_LOAD tilts the
        # expected hidden decoys toward the visible set: 0 = random sample
        # (V/U), 1 = every remaining hidden decoy is already on the board. This
        # is what stops the AI wasting fighters on an early all-decoy wave.
        V = len(units)
        if behavioural:
            prop = (V / float(remaining)) * n_decoy      # random-sample decoys
            exp_decoy = ((1.0 - self.FRONT_LOAD) * prop
                         + self.FRONT_LOAD * n_decoy)
            exp_decoy = min(float(V), float(n_decoy), exp_decoy)
            target = V - exp_decoy
        else:
            target = n_real * (V / float(remaining))
        logodds = [_logit(self._base_prior(cls, u)) - self._ev.get(u.id, 0.0)
                   for u in units]
        shift = _solve_shift(logodds, target)
        for u, lo in zip(units, logodds):
            self.p[u.id] = _sigmoid(lo + shift)

    def _base_prior(self, cls, u):
        if cls == "slbm":
            return SLBM_PRIOR
        return CUBAN_PRIOR if u.group == "cuban" else NORTH_PRIOR

    # ---- behavioural evidence -------------------------------------------
    def _accrue_behaviour(self, units):
        for u in units:
            prev = self._prev_cell.get(u.id)
            cur = u.cell
            if prev is None or cur is None or prev == cur or u.entering:
                continue
            # Value forfeited: the best undefended city it could bomb from where
            # it was, minus the best it can still bomb now. A real bomber closes
            # on value (forfeit ~0); a decoy weaving around sheds it.
            forfeit = self._best_unguarded(u, prev) - self._best_unguarded(u, cur)
            if forfeit > 0:
                self._ev[u.id] = self._ev.get(u.id, 0.0) + self.BETA * forfeit

    def _best_unguarded(self, u, cell):
        g = self.g
        best = 0
        for cid in self._reachable_cities(u, cell):
            if not g.missile_defended(cid):
                pts = g.board.city(cid)["points"]
                if pts > best:
                    best = pts
        return best

    def _reachable_cities(self, u, from_cell):
        return reachable_cities(self.g, u, from_cell, self._reach_cache)

    # ---- queries ---------------------------------------------------------
    def prob_real(self, u):
        o = self.observed(u)
        if o is not None:
            return 1.0 if o == "real" else 0.0
        if u.id in self._confirmed_decoy:      # proven decoy by its behaviour
            return 0.0
        cls = "slbm" if u.kind in _SLBM_KINDS else "bomber"
        p = self.p.get(u.id, self._base_prior(cls, u))
        cap = self._prob_cap.get(u.id)         # Rule C soft cap (a stalled Cuban)
        return min(p, cap) if cap is not None else p

    def all_decoys_revealed(self):
        """Every bomber decoy has been revealed - all remaining silhouettes are
        certainly real."""
        return self.n_decoy <= 0

    def expected_reals_visible(self, cls="bomber"):
        return sum(self.prob_real(u) for u in self.silhouettes(cls)
                   if not u.revealed)


class ThreatModel:
    """How many points can the Soviets still score, at most - and which American
    action lowers that ceiling the most.

    Built on a BeliefTracker: `expected` numbers weight each silhouette by
    P(real); the `worst_case` bound treats every non-revealed-decoy attacker as
    real and is a true UPPER bound (so "worst_case < 100" proves the game won).
    Rebuild once per American decision, AFTER belief.update().
    """

    WIN_THRESHOLD = 100

    def __init__(self, game, belief):
        self.g = game
        self.b = belief
        # Share the belief's PERSISTENT reach cache. A ThreatModel is rebuilt
        # every American decision, so its own cache would start cold each turn;
        # borrowing the belief's keeps reachability computed once per game.
        self._reach = belief._reach_cache

    # ---- reachability / menus -------------------------------------------
    def menu(self, u):
        """(cid, points) for every undestroyed city `u` could still bomb, best
        first. Empty for an off-board / frozen / dead unit."""
        if not u.alive or u.frozen or u.cell is None:
            return []
        out = [(c, self.g.board.city(c)["points"])
               for c in reachable_cities(self.g, u, u.cell, self._reach)]
        out.sort(key=lambda cp: -cp[1])
        return out

    def _real_capable(self, cls):
        """Units of a class that could still deliver a REAL bomb: alive, not
        frozen, and not a KNOWN (revealed) decoy. Unrevealed units are included -
        worst case they are real. Uses only public reveal state."""
        kinds = _SLBM_KINDS if cls == "slbm" else _BOMBER_KINDS
        out = []
        for u in self.g.soviet_units():
            if u.kind not in kinds or not u.alive or u.frozen:
                continue
            if self.b.observed(u) == "decoy":
                continue
            out.append(u)
        return out

    # ---- ceilings --------------------------------------------------------
    def assignment(self):
        """Greedy max-weight matching of on-board bomber silhouettes to DISTINCT
        cities, weight points*P(real). Returns {uid: (cid, expected_value)} and
        drives both the expected ceiling and interception priorities."""
        g = self.g
        pairs = []
        for u in self._real_capable("bomber"):
            if u.cell is None:
                continue
            pr = self.b.prob_real(u)
            if pr <= 0.0:
                continue
            for c, pts in self.menu(u):
                pairs.append((pts * pr, c, u))
        pairs.sort(key=lambda t: -t[0])
        used_city, out = set(), {}
        for val, c, u in pairs:
            if c in used_city or u.id in out:
                continue
            used_city.add(c)
            out[u.id] = (c, val)
        return out

    def expected_ceiling(self):
        """Points already scored + expected points the on-board wave still
        delivers (belief-weighted greedy assignment)."""
        return self.g.points + sum(v for _c, v in self.assignment().values())

    def worst_case_ceiling(self):
        """Admissible UPPER bound on the final Soviet score: every non-revealed-
        decoy attacker treated as real, taking the R highest-value cities any of
        them can still reach (off-board/pool units can reach anything, so they
        are not restricted). Over-counts on purpose - never under-estimates."""
        g = self.g
        bombers = self._real_capable("bomber")
        slbms = self._real_capable("slbm")
        R = len(bombers) + len(slbms)
        if R == 0:
            return g.points
        reach = set()
        if any(u.cell is None for u in bombers):
            reach = {c for c in g.board.cells
                     if g.board.is_city(c) and c not in g.destroyed}
        else:
            for u in bombers:
                reach |= reachable_cities(g, u, u.cell, self._reach)
        if slbms:
            reach |= {c for c in game_rules.COASTAL_CITIES
                      if c not in g.destroyed and g.board.is_city(c)}
        vals = sorted((g.board.city(c)["points"] for c in reach), reverse=True)
        return g.points + sum(vals[:R])

    def provably_won(self):
        """True when even the worst case cannot reach 100 - the American may
        stop spending fighters."""
        return self.worst_case_ceiling() < self.WIN_THRESHOLD

    # ---- action ranking (for Phases 3-4) --------------------------------
    def intercept_value(self, u):
        """Expected points removed by killing silhouette `u` this turn - its
        value in the greedy assignment (0 if it is not the best claimant of any
        city)."""
        got = self.assignment().get(u.id)
        return got[1] if got else 0.0

    def city_threat(self, cid):
        """Sum of P(real) over on-board silhouettes that could still bomb cid -
        how 'hot' a city is, for missile placement and interception targeting."""
        if cid in self.g.destroyed:
            return 0.0
        total = 0.0
        for u in self._real_capable("bomber"):
            if u.cell is None:
                continue
            if cid in reachable_cities(self.g, u, u.cell, self._reach):
                total += self.b.prob_real(u)
        return total


class ExpertAmericanAI:
    """Expert American play, driven by the BeliefTracker + ThreatModel.

    Setup (place_all_units) is an interdiction problem: hard assets (5 missiles)
    on the cities whose denial hurts most, fighters spread to cover high-priority
    clusters, decoys bluffing defence over conceded jewels. City priority folds
    in the option rules and OBSERVED Cuban staging - all public information.

    Turn (take_turn) spends fighters by MARGINAL VALUE - the expected Soviet
    points each interception removes (the ThreatModel assignment) - with bullet
    economy (skip likely decoys and low-value threats while safe) and a
    provably-won cutoff (hold every fighter once the worst case cannot reach 100).

    Fire (ask_fire) weighs damage prevented (P(real) x city value) against the
    deterrent kept by holding fire, with an endgame override and a soft (mixed)
    threshold so the cutoff can't be memorised.

    Fairness: identity is read only through belief.observed (revealed units)."""

    # City-priority axis weights (setup; tuned in Phase 5). DEW anchors get an
    # explicit missile per a fixed random split in place_all_units, not a
    # priority weight here.
    W_SLBM = 2.0       # coastal cities under sub-launched-missile threat
    W_SIB = 2.5        # western cities when the Siberian line is in play
    W_CUBAN = 0.7      # southern cities, scaled by observed Cuban staging
    CUBAN_THETA_SCALE = 20.0  # angular falloff of Cuban threat from staged cols
    FIGHTER_REACH = 6  # a fighter covers threats to cities within its move
    SETUP_EPS = 0.0    # 0 = deterministic best placement. (>0 randomizes among
    #                    near-best placements for unpredictability, but benchmarks
    #                    showed it costs too much defensive quality as written -
    #                    revisit with a proper mixed strategy, not this heuristic.)

    # Interception policy. The decision is VALUE-GATED: for a threat to a city
    # worth V points, engage if P(real) clears a threshold that DROPS as V rises
    # - so a jewel is defended against all but a probable decoy, while a 5-pointer
    # is defended only against a likely-real attacker. The choice is probabilistic
    # (a soft threshold) so near-boundary threats (e.g. Cuban units) are engaged
    # ~half the time and the AI is not perfectly predictable.
    PT_AT5 = 0.50      # P(real) threshold to defend a 5-point city...
    PT_SLOPE = 0.10    # ...dropping this much per extra point of city value
    ENGAGE_TEMP = 0.15  # softness of the engage threshold (mixed strategy)
    MISSILE_COVER = 0.4  # a missile-defended city's value counts this much for
    #                     fighter priority (the missile is already one layer;
    #                     spend fighters on UNDEFENDED cities first)
    URGENCY = 0.40     # lower the whole bar as the Soviets approach 100 points
    FIRST_WAVE_ENGAGE = 0.5  # cap interception of first-wave (turn-1) north/
    #                    Siberian bombers here: they are decoy-heavy and slow to
    #                    threaten, so it's not worth over-committing...
    JEWEL_VALUE = 7.0  # ...UNLESS they menace an UNDEFENDED city of >= this value
    LOW_BULLETS = 3    # "scarce" fighter count - be pickier
    SCARCE_BUMP = 0.15  # raise the bar this much while fighters are scarce
    P_ENDGAME = 0.30   # fire on this real-chance if a bomb here would clinch 100
    P_FIRE_FLOOR = 0.15  # below this P(real) never fire (keep the deterrent)
    P_FIRE_CEIL = 0.98  # at/above this P(real) the attacker is (near-)certainly
    #                    real: it will bomb the city, which destroys it and makes
    #                    the missile useless anyway (nothing enters a bombed
    #                    city), so holding fire is strictly dominated - ALWAYS
    #                    fire. Deterrence only has value against a POSSIBLE decoy.
    DETERRENCE = 3.0   # value of keeping a missile standing as a deterrent
    FIRE_TEMP = 2.0    # softness of the fire threshold (mixed strategy)

    def __init__(self, game, params=None):
        self.g = game
        self.b = BeliefTracker(game)
        self.tm = ThreatModel(game, self.b)
        # Own RNG (seeded off the game) so the mixed-strategy draws are
        # reproducible yet independent of the Soviet AI's rng stream.
        self.rng = random.Random(game.rng.random())
        self._cuban_pressure = 0
        self._cuban_thetas = []          # theta of each staged Cuban column
        # Apply a tuned parameter vector (only when explicitly supplied - the
        # production dispatch passes load_tuned_params(); direct construction
        # keeps class defaults). Only known tunable attributes are accepted.
        for k, v in (params or {}).items():
            if isinstance(getattr(type(self), k, None), (int, float)):
                setattr(self, k, v)

    # ---- city priority (predicted Soviet interest) ----------------------
    def _city_priority(self, cid):
        g = self.g
        pr = float(g.board.city(cid)["points"])
        r = g.board.row_i[cid]
        th = game_ai.theta_mid(g, cid)          # ~121 (west) .. -1 (east)
        if g.opt["cuban"] and self._cuban_thetas:
            # Cuban bombers enter at row V and fly north, hitting SOUTHERN
            # cities first; weight by how far south AND by angular alignment to
            # the columns the Cuban force actually staged in (east/west lean).
            align = sum(1.0 / (1.0 + abs(th - t) / self.CUBAN_THETA_SCALE)
                        for t in self._cuban_thetas)
            pr += self.W_CUBAN * align * (r / 21.0)
        if g.opt["siberian"]:
            pr += self.W_SIB * (th / 121.0)      # western cities
        if g.opt["slbm"] and cid in game_rules.COASTAL_CITIES:
            pr += self.W_SLBM
        return pr

    # ---- setup -----------------------------------------------------------
    def _choose(self, cands, score):
        """Pick a candidate maximising `score`; when several are within
        SETUP_EPS of the best, pick one at random weighted toward higher score
        (mixed strategy, so the defence is not memorisable across games).
        SETUP_EPS <= 0 gives strict argmax (deterministic - used by tests)."""
        best_c, best_s, scored = None, None, []
        for c in cands:
            s = score(c)
            scored.append((s, c))
            if best_s is None or s > best_s:
                best_s, best_c = s, c
        if best_c is None or self.SETUP_EPS <= 0:
            return best_c
        near = [(s, c) for s, c in scored if s >= best_s - self.SETUP_EPS]
        weights = [s - (best_s - self.SETUP_EPS) + 0.1 for s, _ in near]
        x = self.rng.random() * sum(weights)
        acc = 0.0
        for (s, c), w in zip(near, weights):
            acc += w
            if x <= acc:
                return c
        return near[-1][1]

    def _weighted_pick(self, items, weights):
        """One item chosen with probability proportional to its weight."""
        total = sum(weights)
        if total <= 0:
            return items[0] if items else None
        x = self.rng.random() * total
        acc = 0.0
        for it, w in zip(items, weights):
            acc += w
            if x <= acc:
                return it
        return items[-1]

    def place_all_units(self):
        g = self.g
        staged_cuban = [u for u in g.staged_units()
                        if u.stage_group == "cuban" and u.staged]
        self._cuban_pressure = len(staged_cuban)
        self._cuban_thetas = [game_ai.theta_mid(g, u.staged)
                              for u in staged_cuban]
        units = g.us_placement_units()
        us_missiles = [u for u in units if u.kind == "missile" and not u.canadian]
        us_fighters = [u for u in units if u.kind == "fighter" and not u.canadian]
        us_decoys = [u for u in units
                     if u.kind == "decoy_fighter" and not u.canadian]
        ca_fighters = [u for u in units if u.kind == "fighter" and u.canadian]
        ca_decoys = [u for u in units
                     if u.kind == "decoy_fighter" and u.canadian]

        cities = [c for c in g.board.cells if g.board.is_city(c)]
        if g.opt["canadian"]:
            ca_cities = [c for c in cities if g.board.city(c).get("canadian")]
            us_cities = [c for c in cities
                         if not g.board.city(c).get("canadian")]
        else:
            ca_cities, us_cities = [], list(cities)
        prio = {c: self._city_priority(c) for c in cities}
        dmaps = {c: game_ai.bfs_dist(g.board, c) for c in cities}
        covered = {c: 0.0 for c in cities}

        # 1) missiles -> the highest-value COASTAL cities. A missile on a coastal
        #    city defends it against BOTH a bomber and a sub-launched missile
        #    (double duty), so missiles earn their keep on the coast while the
        #    (more numerous, mobile) fighters cover the inland jewels. On the
        #    standard map this places NY + DC (coastal 9s), San Diego +
        #    Jacksonville (coastal 8s), and a RANDOM coastal 7 (Norfolk / Seattle
        #    / San Francisco) - the ties are broken by rng so the 5th varies.
        coastal = [c for c in us_cities if c in game_rules.COASTAL_CITIES]
        decoys = [x for x in units if x.kind == "us_decoy_missile"]

        # 1a) DEW-anchor defence (non-deterministic): put a missile on Anchorage
        #     (H121) / Godthab (G212) per a fixed split - both 30%, Anchorage
        #     only 20%, Godthab only 20%, neither 30%. Each anchor's missile is
        #     drawn at RANDOM from the whole pool (5 real + any Play-Balance
        #     decoy), so an anchor may get a real defender OR the bluff. These
        #     come OUT of the pool, leaving fewer for the coastal jewels.
        if g.opt["dew"]:
            r = self.rng.random()
            want = (["H121", "G212"] if r < 0.30 else ["H121"] if r < 0.50
                    else ["G212"] if r < 0.70 else [])
            missile_pool = us_missiles + decoys
            for anchor in want:
                if anchor not in us_cities or not missile_pool:
                    continue
                m = self.rng.choice(missile_pool)
                if g.place_us(m, anchor)[0]:
                    covered[anchor] += 100.0
                    missile_pool.remove(m)
                    if m in us_missiles:
                        us_missiles.remove(m)
                    if m in decoys:
                        decoys.remove(m)

        # Coastal JEWELS (7/8/9 pts) are the missile candidates. Under Play
        # Balance the decoy may hide on ANY of them, weighted toward LOWER value
        # (P ~ 1/points). Because the map has MORE 7s than 8s or 9s, a 7 is the
        # likeliest bluff (P(7) well above its 3/7 equal share). The five real
        # missiles then take the highest-value cities that remain, so one coastal
        # 7 is left with no missile at all.
        jewels = [c for c in coastal if g.board.city(c)["points"] >= 7]
        decoy_cities, pool = [], list(jewels)
        for _ in decoys:
            if not pool:
                break
            pick = self._weighted_pick(
                pool, [1.0 / g.board.city(c)["points"] for c in pool])
            decoy_cities.append(pick)
            pool.remove(pick)

        def rank(cs):
            return sorted(cs, key=lambda c: (-g.board.city(c)["points"],
                                             self.rng.random()))
        real_order = (rank([c for c in jewels if c not in decoy_cities])
                      + rank([c for c in us_cities if c not in jewels]))
        for u, c in zip(us_missiles, real_order):
            if g.place_us(u, c)[0]:
                covered[c] += 100.0          # keep fighters from piling here
        for u, c in zip(decoys, decoy_cities):
            if g.place_us(u, c)[0]:
                covered[c] += 100.0          # a defended-LOOKING city (bluff)

        # A lone fighter/decoy at Anchorage is a free kill, not a defence: with
        # Siberian entry a real bomber can reach it turn 1 and bomb it the same
        # turn - the fighter dies with the city before it ever gets to act
        # (fighters only intercept BY MOVING to an adjacent threat; they cannot
        # stop their own city from being bombed the instant a bomber walks in).
        # Only a missile (real or decoy) gives Anchorage a chance to intercept
        # BEFORE the bomb goes off, so bar fighters/decoys from it unless a
        # missile silhouette is already there from step 1a.
        fighter_us_cities = ([c for c in us_cities if c != "H121"]
                             if not g.has_missile_look("H121") else us_cities)

        # 2) fighters - facility-location greedy: each goes where it adds the
        #    most still-uncovered priority within its move radius.
        self._place_coverage(us_fighters, fighter_us_cities, cities, prio,
                             dmaps, covered)
        # 3) decoys - bluff: sit on the highest-priority cities still showing NO
        #    defence, so conceded jewels look guarded (decoys are identical to
        #    fighters on the map).
        self._place_bluff(us_decoys, fighter_us_cities, prio, covered)

        # 4) Canadian force (fighters + one decoy) - restricted to Canada.
        self._place_coverage(ca_fighters, ca_cities, cities, prio, dmaps,
                             covered)
        self._place_bluff(ca_decoys, ca_cities, prio, covered)

        # safety net: place any stragglers on any legal city (never a bare
        # fighter/decoy at Anchorage either).
        for u in list(g.us_placement_units()):
            if u.canadian:
                pool = ca_cities
            elif (u.kind in ("fighter", "decoy_fighter")
                  and not g.has_missile_look("H121")):
                pool = fighter_us_cities
            else:
                pool = us_cities
            for c in sorted(pool, key=lambda c: -prio[c]):
                if g.place_us(u, c)[0]:
                    break
        g.finish_us_setup()

    def _place_coverage(self, fighters, spot_cities, all_cities, prio, dmaps,
                        covered):
        g = self.g

        def gain(c):
            dm = dmaps[c]
            return sum(prio[o] / (1.0 + covered[o]) for o in all_cities
                       if dm.get(o, 99) <= self.FIGHTER_REACH)

        for u in fighters:
            # NEVER pile onto an already-occupied city: bombing a city kills the
            # whole stack on it, so a cluster of fighters is a single-bomb
            # jackpot. Spread to distinct cities (fall back only if none free).
            free = [c for c in spot_cities if not g.at(c, "us")] or spot_cities
            best = self._choose(free, gain)
            if best is None or not g.place_us(u, best)[0]:
                continue
            dm = dmaps[best]
            for o in all_cities:
                if dm.get(o, 99) <= self.FIGHTER_REACH:
                    covered[o] += 1.0

    def _place_bluff(self, decoys, spot_cities, prio, covered):
        g = self.g
        for u in decoys:
            free = [c for c in spot_cities if not g.at(c, "us")]
            cand = [c for c in free if covered[c] <= 0.0] or free or spot_cities
            # score: high priority, low current cover
            pick = self._choose(cand, lambda c: prio[c] - 5.0 * covered[c])
            if pick and g.place_us(u, pick)[0]:
                covered[pick] += 1.0

    # ---- fire decision (EV vs deterrence, soft/mixed threshold) ----------
    def ask_fire(self, missile, unit):
        """A Soviet silhouette has entered a missile-defended city. Fire iff the
        expected damage prevented beats the value of keeping the missile as a
        standing deterrent - with an endgame override and a soft threshold."""
        g = self.g
        pr = self.b.prob_real(unit)
        city = g.board.city(missile.cell)
        val = float(city["points"]) if city else 0.0
        # Endgame: a real bomb here would clinch the Soviet win -> fire on any
        # meaningful chance the unit is real.
        if g.points + val >= ThreatModel.WIN_THRESHOLD and pr >= self.P_ENDGAME:
            return True
        if pr < self.P_FIRE_FLOOR:       # (near-)certain decoy - keep the missile
            return False
        if pr >= self.P_FIRE_CEIL:       # (near-)certain real - always fire: the
            return True                  # missile dies with the city if we don't
        # Otherwise a soft (mixed) threshold on (damage prevented - deterrence):
        # high-P(real) high-value targets draw fire; likely decoys are let
        # through to preserve the missile and call the bluff.
        score = pr * val - self.DETERRENCE
        return self.rng.random() < _sigmoid(score / self.FIRE_TEMP)

    # ---- fighter movement (value-gated, probabilistic interception) -------
    async def take_turn(self, mover=None, on_event=None):
        g = self.g
        note = on_event or _anoop
        self.b.update()
        tm = ThreatModel(g, self.b)
        if tm.provably_won():
            return                       # worst case can't reach 100 - hold fire
        # Gather interceptable threats: on-board bomber silhouettes and SLBMs
        # that surfaced THIS Soviet turn (their one interceptable moment, before
        # they move onto the coastal city and bomb next turn).
        threats = []
        for u in g.soviet_units():
            if not (u.alive and u.cell and not u.frozen):
                continue
            if self.b.observed(u) == "decoy":        # a KNOWN decoy - never worth it
                continue
            if u.kind in _SLBM_KINDS and u.slbm_turn != g.turn:
                continue                             # not a fresh, interceptable SLBM
            if u.kind not in _BOMBER_KINDS and u.kind not in _SLBM_KINDS:
                continue
            v = self._value_at_risk(u, tm)
            if v <= 0.0:
                continue
            pr = self.b.prob_real(u)
            threats.append((v * pr, v, pr, u))
        # FAIRNESS: shuffle BEFORE the (stable) value sort. soviet_units() is in
        # manifest order - real bombers before decoys - so a plain stable sort
        # would break ties (identical value*P silhouettes) in favour of the
        # reals, letting the AI "magically" pick every real out of a mixed wave.
        # The human sees no such ordering; randomising ties removes the leak.
        self.rng.shuffle(threats)
        threats.sort(key=lambda t: -t[0])            # biggest expected loss first
        used = set()
        for _ev, v, pr, u in threats:
            bullets_left = len(g.movable_fighters()) - len(used)
            if bullets_left <= 0:
                break
            if u.kind in _SLBM_KINDS:
                p = self._slbm_engage_prob(u)
            else:
                p = self._wave_adjust(u, v,
                                      self._engage_prob(v, pr, bullets_left))
            if self.rng.random() >= p:
                continue
            f = self._best_interceptor(u, used)
            if f is None:
                continue
            path = g.fighter_path(f, u.cell)
            if mover:
                await mover(f, path)
            else:
                g.move_fighter(f, u.cell)
            used.add(f.id)
            await note("intercept")

    def _value_at_risk(self, u, tm):
        """Points at stake if `u` is a real attacker: the most valuable city it
        still threatens. Missile-defended cities count less (the missile is
        already a layer, so fighters go to UNDEFENDED cities first)."""
        g = self.g

        def weigh(cid, pts):
            return pts * (self.MISSILE_COVER if g.missile_defended(cid) else 1.0)

        if u.kind in _BOMBER_KINDS:
            return max((weigh(cid, pts) for cid, pts in tm.menu(u)), default=0.0)
        # a surfaced SLBM threatens the coastal cities one step away
        best = 0.0
        for nb in g.board.nbrs[u.cell]:
            if (g.board.is_city(nb) and nb not in g.destroyed
                    and nb in game_rules.COASTAL_CITIES):
                best = max(best, weigh(nb, g.board.city(nb)["points"]))
        return best

    def _slbm_engage_prob(self, u):
        """Fighter-interception probability for a surfaced sub-launched missile.
        Only the highest-value adjacent city NOT already covered by a US missile
        matters (a missile-defended city defends itself when the SLBM moves onto
        it). If none, don't intercept. Otherwise: KNOWN decoy (3 reals already
        spent, P~0) -> never; KNOWN real (the decoy was found, P~1) -> intercept;
        UNKNOWN (P~0.75) -> 0.1 x that city's point value."""
        g = self.g
        undef = [nb for nb in g.board.nbrs[u.cell]
                 if g.board.is_city(nb) and nb not in g.destroyed
                 and not g.missile_defended(nb)]
        if not undef:
            return 0.0
        # A (real) fighter already sitting on one of those undefended cities
        # ALWAYS intercepts - it is right there and the city has no missile.
        if any(f.cell in undef for f in g.movable_fighters()):
            return 1.0
        pr = self.b.prob_real(u)
        if pr <= 0.02:                       # known decoy -> never
            return 0.0
        if pr >= 0.98:                       # known real -> always (undefended city)
            return 1.0
        v = max(g.board.city(c)["points"] for c in undef)
        return min(1.0, 0.1 * v)             # unknown -> scale by value at risk

    def _wave_adjust(self, u, v, p):
        """Cap interception of a FIRST-WAVE (turn-1) north/Siberian bomber near
        FIRST_WAVE_ENGAGE - it is decoy-heavy and there is time to see it commit,
        so ~half is the right rate. Exception: an UNDEFENDED jewel (v >=
        JEWEL_VALUE; missile-defended cities have a discounted v and stay capped)
        is defended normally. Cuban and later-wave units are unaffected."""
        if (u.kind in _BOMBER_KINDS and u.group != "cuban"
                and getattr(u, "entered_turn", -1) == 1
                and v < self.JEWEL_VALUE):
            return min(p, self.FIRST_WAVE_ENGAGE)
        return p

    def _engage_prob(self, v, pr, bullets_left):
        """Probability of spending a fighter on a threat to a value-`v` city.
        The P(real) bar falls as v rises (defend jewels against probable decoys)
        and as the Soviets near 100 (defend harder); it rises while fighters are
        scarce. A soft (sigmoid) bar makes the choice a mixed strategy."""
        thr = self.PT_AT5 - self.PT_SLOPE * (v - 5.0)
        if bullets_left <= self.LOW_BULLETS:
            thr += self.SCARCE_BUMP
        thr -= self.URGENCY * (self.g.points / float(ThreatModel.WIN_THRESHOLD))
        thr = min(0.95, max(0.03, thr))
        return _sigmoid((pr - thr) / self.ENGAGE_TEMP)

    def _best_interceptor(self, s, used):
        """Cheapest movable fighter that can reach silhouette s this turn,
        preferring one sitting on a low-value city (so a valuable city keeps its
        defender)."""
        g = self.g
        best, best_key = None, None
        for f in g.movable_fighters():
            if f.id in used:
                continue
            dests = g.legal_fighter_dests(f)
            if s.cell not in dests:
                continue
            home = g.board.city(f.cell)["points"] if g.board.is_city(f.cell) else 0
            key = (dests[s.cell], home)
            if best_key is None or key < best_key:
                best_key, best = key, f
        return best


# ======================================================================
# Soviet expert side
# ======================================================================

# American force composition - printed on the counter mix, so public.
FIGHTER_REAL_US = 12
FIGHTER_DECOY_US = 4
FIGHTER_REAL_CA = 3
FIGHTER_DECOY_CA = 1


def us_silhouette(u):
    """Public silhouette class of an American unit: 'missile' or 'fighter'.
    Real missiles and (under Play Balance) the missile decoy share one 'missile'
    silhouette - a missile position no longer proves a real defender. Real and
    decoy fighters share the 'fighter' silhouette; both are indistinguishable
    until revealed."""
    return "missile" if u.kind in ("missile", "us_decoy_missile") else "fighter"


class UsDefenseBelief:
    """Soviet-side belief about the American defence, from PUBLIC info only.

    - The missile cities are simply KNOWN (distinct all-real silhouette).
    - Fighter silhouettes hide 12 real + 4 decoy (US) and 3 real + 1 decoy
      (Canadian) - separate pools, since the counters differ. P(real) is the
      hypergeometric marginal: unrevealed reals / unrevealed silhouettes of
      the pool (dead-but-unrevealed units, e.g. killed by a bombing, stay in
      the unknown denominator).
    - A fighter is REMOVED (revealed) the turn it moves, so every live fighter
      silhouette has never moved, and every interception publicly drains the
      real pool. That is the flushing arithmetic: the more fighters the decoys
      draw out, the safer the real bombers behind them.
    """

    def __init__(self, game):
        self.g = game
        self._dmaps = {}        # fighter cell -> bfs dist map (they never move)

    def observed(self, u):
        """Identity of an American unit, readable ONLY once it is revealed (a
        public event). The mirror of BeliefTracker.observed, and the only place
        the Soviet side reads the hidden flag."""
        if not u.revealed:
            return None
        return "real" if u.real else "decoy"

    def missile_cities(self):
        """Cities showing a missile silhouette - real OR (under Play Balance) a
        decoy. What the Soviet SEES; not a guarantee of a real defender."""
        return {u.cell for u in self.g.us_units()
                if u.alive and u.cell and us_silhouette(u) == "missile"}

    def prob_missile_real(self, cid):
        """P(the missile silhouette on `cid` is a REAL interceptor). Without the
        Play Balance rule every missile is real -> 1.0. With it, 5 of 6 missile
        silhouettes are real; a hypergeometric marginal over the UNREVEALED ones
        (a revealed decoy drops its city to 0 and lifts the rest toward 1)."""
        here = [u for u in self.g.us_units()
                if u.alive and u.cell == cid and us_silhouette(u) == "missile"]
        if not here:
            return 0.0
        o = self.observed(here[0])
        if o is not None:
            return 1.0 if o == "real" else 0.0
        reals = sum(1 for u in self.g.us_units() if u.kind == "missile")
        unknown = 0
        for x in self.g.us_units():
            if us_silhouette(x) != "missile":
                continue
            ox = self.observed(x)
            if ox == "real":
                reals -= 1
            elif ox is None:
                unknown += 1
        return (reals / float(unknown)) if unknown > 0 else 0.0

    def fighter_silhouettes(self):
        """Live American fighter-type silhouettes (real or decoy, unknown)."""
        return [u for u in self.g.us_units()
                if u.alive and u.cell and us_silhouette(u) == "fighter"]

    def prob_real_fighter(self, u):
        o = self.observed(u)
        if o is not None:
            return 1.0 if o == "real" else 0.0
        ca = bool(u.canadian)
        reals = FIGHTER_REAL_CA if ca else FIGHTER_REAL_US
        unknown = 0
        for x in self.g.us_units():
            if bool(x.canadian) != ca or us_silhouette(x) != "fighter":
                continue
            ox = self.observed(x)
            if ox == "real":
                reals -= 1
            elif ox is None:
                unknown += 1        # live or dead-unrevealed: identity unknown
        return (reals / float(unknown)) if unknown > 0 else 0.0

    def fighters_remaining(self):
        """Expected REAL fighters still unspent (public arithmetic)."""
        return sum(self.prob_real_fighter(u)
                   for u in self.fighter_silhouettes())

    def no_real_fighters_left(self):
        """True once every real fighter has been spent - the count of unspent
        reals is zero, so every remaining fighter silhouette is certainly a
        decoy and there is no interception threat left."""
        return self.fighters_remaining() <= 1e-9

    def _dmap(self, cell):
        got = self._dmaps.get(cell)
        if got is None:
            got = game_ai.bfs_dist(self.g.board, cell)
            self._dmaps[cell] = got
        return got

    def pressure(self, cid, reach=6):
        """Expected real fighters able to reach cid: sum of P(real) over live
        fighter silhouettes within `reach` squares."""
        tot = 0.0
        for u in self.fighter_silhouettes():
            if self._dmap(u.cell).get(cid, 99) <= reach:
                tot += self.prob_real_fighter(u)
        return tot


class ExpertSovietAI:
    """Expert Soviet attacker, driven by UsDefenseBelief.

    Principles (distilled from the strongest human Soviet play):
      - The missile cities are PUBLIC: plan around them, and BAIT them with
        decoys - a fired missile dies for a worthless decoy; a held one costs
        nothing the Soviets need.
      - Fighters are one-shot: every interception a decoy draws kills a real
        fighter. Lead the waves with decoys where fighters cluster; press the
        reals down the thin lanes; escalate the reals as the fighter pool
        drains (fighters_remaining is public arithmetic).
      - Cuban force: reals bomb the undefended southern cities on their first
        full move; decoys mimic the same approach.
      - SLBMs: the decoy goes first as bait; reals surface beside the best
        coastal city without a missile.
    Fairness: a US fighter silhouette's identity is read only through
    UsDefenseBelief (revealed units); missiles are public; candidate lists are
    shuffled before value sorts so manifest order can never break ties.
    """

    EARLY_WAVE = 8       # units staged per turn, turns 1-2
    LATE_WAVE = 6        # ... later turns
    FLUSH_HI = 6.0       # est. real fighters above which decoys lead hard
    FLUSH_MID = 3.0
    DECOY_FRAC_HI = 0.6  # decoy fraction of the wave in each regime
    DECOY_FRAC_MID = 0.4
    DECOY_FRAC_LO = 0.15
    N_BAIT = 2           # decoys assigned to walk onto missile cities
    HOLD_EV = 0.45       # value multiplier for targeting a missile city
    RISK_W = 0.6         # fighter-pressure weight in real routing/targeting
    BOMB_MIN_EARLY = 6.0  # bomb any city worth >= this when standing on it
    BOMB_MIN_LATE = 5.0
    LATE_TURN = 5
    ENDGAME_GAP = 12.0   # within this of 100: bomb anything
    CUBAN_REALS = 3      # user-proven Cuban composition: 3 real + 2 decoys
    DEW_ANCHORS = ("H121", "G212")  # Anchorage / Godthab - exempt from the
    #                    "don't bomb 5-pt cities until the higher ones are gone"
    #                    endgame rule (handled exactly as before this rule)
    DEW_KILL_MIN_DECOYS = 3  # anchor-kill priority expires once this many or
    #                    fewer of the Soviet's own decoys still have to cross the
    #                    DEW line (kill them early or not at all).
    # DEW anchor-kill priority chances, keyed by (Anchorage bucket, Godthab
    # bucket) where each bucket is the number of American units seen on that
    # anchor capped at 2 ("2+"). Each value is (neither, Anchorage-only,
    # Godthab-only, both). The more heavily the American defends an anchor, the
    # less the Soviet bothers targeting it; a weakly-held anchor is hit hard.
    _DEW_PRIORITY_WEIGHTS = {
        (0, 0): (1 / 9, 1 / 9, 1 / 9, 6 / 9),
        (0, 1): (1 / 9, 6 / 9, 1 / 9, 1 / 9),
        (0, 2): (1 / 9, 6 / 9, 1 / 9, 1 / 9),
        (1, 0): (1 / 9, 1 / 9, 6 / 9, 1 / 9),
        (2, 0): (1 / 9, 1 / 9, 6 / 9, 1 / 9),
        (1, 1): (1 / 4, 1 / 4, 1 / 4, 1 / 4),
        (1, 2): (1 / 2, 1 / 4, 1 / 8, 1 / 8),
        (2, 1): (1 / 2, 1 / 8, 1 / 4, 1 / 8),
        (2, 2): (9 / 12, 1 / 12, 1 / 12, 1 / 12),
    }

    def __init__(self, game):
        self.g = game
        self.d = UsDefenseBelief(game)
        self.rng = random.Random(game.rng.random())
        self._goal = {}      # unit.id -> planned target city
        self._role = {}      # decoy unit.id -> "bait" | "flush"
        self._dist = {}      # goal cid -> bfs dist map
        self._reach = {}     # shared fair-reachability cache
        # DEW anchor-kill priority: decided ONCE from the American's actual
        # anchor defence, but that setup does not exist yet at construction -
        # deferred (None) and rolled lazily on the first Soviet turn via
        # _ensure_dew_kill_targets(). Killing an anchor disables the DEW
        # detection culling the decoy screen.
        self._dew_kill_targets = None

    # ---------------- helpers
    def _dmap(self, cid):
        got = self._dist.get(cid)
        if got is None:
            got = game_ai.bfs_dist(self.g.board, cid)
            self._dist[cid] = got
        return got

    def _bomb_min(self):
        g = self.g
        if 100 - g.points <= self.ENDGAME_GAP:
            return 1.0
        return (self.BOMB_MIN_EARLY if g.turn < self.LATE_TURN
                else self.BOMB_MIN_LATE)

    def _city_value(self, cid, missiles):
        pts = self.g.board.city(cid)["points"]
        if cid not in missiles:
            base = pts * 1.0
        else:
            # A missile-silhouette city is discounted (HOLD_EV) only to the
            # extent it is PROBABLY a real defender. Under Play Balance one
            # silhouette is a bluff, so a missile city is worth points x
            # (HOLD_EV.p + 1.(1-p)) - the likelier the bluff, the closer to full
            # value (worth attacking).
            p = self.d.prob_missile_real(cid)
            base = pts * (self.HOLD_EV * p + (1.0 - p))
        return base

    # ---------------- DEW-anchor destruction priority
    def _anchor_defenders(self, cid):
        """PUBLIC count of American units sitting on anchor `cid`, bucketed to
        0 / 1 / 2 (2 means "two or more"). Every US silhouette counts - real or
        decoy, fighter or missile alike - because the Soviet sees the count but
        never the hidden real/decoy identity (mirroring the American's own blind
        spot for Soviet decoys)."""
        return min(len(self.g.at(cid, "us")), 2)

    def _ensure_dew_kill_targets(self):
        """Roll the anchor-kill priority ONCE, the first time it is needed (the
        first Soviet turn, after American setup is complete). The chances depend
        on how heavily the American defended each anchor - see
        _DEW_PRIORITY_WEIGHTS. No priority when the DEW rule is off (killing an
        anchor then buys nothing). Idempotent: a no-op once decided."""
        if self._dew_kill_targets is not None:
            return
        g = self.g
        if not g.opt["dew"]:
            self._dew_kill_targets = set()
            return
        key = (self._anchor_defenders("H121"), self._anchor_defenders("G212"))
        w_neither, w_anch, w_god, w_both = self._DEW_PRIORITY_WEIGHTS[key]
        outcomes = (set(), {"H121"}, {"G212"}, {"H121", "G212"})
        r = self.rng.random()
        cum = 0.0
        self._dew_kill_targets = outcomes[-1]        # fallback = "both"
        for tgt, wgt in zip(outcomes, (w_neither, w_anch, w_god, w_both)):
            cum += wgt
            if r < cum:
                self._dew_kill_targets = tgt
                break

    def _dew_decoys_pending(self):
        """The Soviet's own decoys that have NOT yet crossed the DEW line and so
        would still benefit from detection being off: non-Cuban decoy-bombers
        off-board (pool + staged) or on the map north of row H (Cuban decoys are
        immune to DEW detection and excluded)."""
        g = self.g
        return sum(1 for u in g.soviet_units()
                   if u.alive and u.kind == "decoy_bomber" and u.group != "cuban"
                   and (u.cell is None or g.board.row_i[u.cell] < 7))

    def _dew_priority_goal(self, u):
        """If the anchor-kill priority is active and this real bomber can reach a
        selected anchor that still needs another bomber, return that anchor as
        its goal - else None. Sends 2 bombers to an anchor showing a missile
        silhouette (overload a possible real missile), 1 otherwise, and re-sends
        after an interception (only LIVE assignees count). Prefers to keep the
        bomber on the anchor it is already committed to."""
        g = self.g
        self._ensure_dew_kill_targets()          # decide once (post-US-setup)
        if (not self._dew_kill_targets
                or self._dew_decoys_pending() <= self.DEW_KILL_MIN_DECOYS):
            return None                          # priority expired / never set

        def short(anchor):                       # still needs another bomber?
            if anchor not in self._dew_kill_targets or anchor in g.destroyed:
                return False
            if u.cell is not None and anchor not in reachable_cities(
                    g, u, u.cell, self._reach):
                return False                     # this bomber can't reach it
            need = 2 if g.has_missile_look(anchor) else 1
            live = sum(1 for x in g.soviet_units()
                       if x.alive and not x.frozen and x.kind == "bomber"
                       and x.id != u.id and self._goal.get(x.id) == anchor)
            return live < need

        cur = self._goal.get(u.id)
        if cur in self._dew_kill_targets and short(cur):
            return cur                           # stay on the anchor it is killing
        return next((a for a in self.DEW_ANCHORS if short(a)), None)

    # ---------------- goals
    def _slbm_claimed_cities(self):
        """Undefended coastal cities a real, surfaced SLBM of ours is set to
        destroy on its attack turn. A real bomber must NOT bomb these: the SLBM
        takes the city for free, so a bomber that grabs it first only forces the
        SLBM onto a lower-value city - two hits collapse into one. Only cities
        with NO missile silhouette count (a defended city may intercept the SLBM,
        and the SLBM itself dodges it for a free one). Empty under Assigned
        Targets, where SLBMs carry fixed targets and the engine already scrubs
        the redundant ones."""
        g = self.g
        if g.opt["targets"]:
            return set()
        missiles = self.d.missile_cities()
        claimed = set()
        for u in g.slbms():
            if not (u.alive and u.entered and not u.frozen
                    and u.kind == "missile"):
                continue
            adj = [c for c in g.board.nbrs[u.cell]
                   if g.board.is_city(c) and c not in g.destroyed
                   and c not in missiles]                # undefended only
            if adj:
                claimed.add(max(adj, key=lambda c: g.board.city(c)["points"]))
        return claimed

    def _pick_real_goal(self, u, missiles):
        """Best city for a real bomber: value (missile-discounted), close,
        low fighter pressure; spread across cities (one bomb per city)."""
        g = self.g
        taken = {c for uid, c in self._goal.items() if uid != u.id}
        if u.cell is not None:
            cands = [c for c in reachable_cities(g, u, u.cell, self._reach)]
        else:
            cands = [c for c in g.board.cells if g.board.is_city(c)
                     and c not in g.destroyed]
        cands = [c for c in cands if c not in g.destroyed]
        # steer clear of cities a real SLBM already has covered (it takes them
        # for free) - keep them only if nothing else is reachable.
        claimed = self._slbm_claimed_cities()
        cands = [c for c in cands if c not in claimed] or cands
        if not cands:
            return None
        self.rng.shuffle(cands)

        def score(c):
            v = self._city_value(c, missiles)
            if c in taken:
                v *= 0.25
            dist = self._dmap(c).get(u.cell, 10) if u.cell else 8
            return v / (dist + 2.0) / (1.0 + self.RISK_W * self.d.pressure(c))
        return max(cands, key=score)

    def _pick_decoy_goal(self, u, missiles):
        """Bait decoys walk onto the biggest missile cities (force fire/hold);
        flushers head for the fighter-covered jewels to draw interceptions."""
        g = self.g
        role = self._role.get(u.id)
        if role is None:
            n_bait = sum(1 for i, r in self._role.items() if r == "bait")
            role = "bait" if (missiles and n_bait < self.N_BAIT) else "flush"
            self._role[u.id] = role
        cands = [c for c in g.board.cells if g.board.is_city(c)
                 and c not in g.destroyed]
        if u.cell is not None:
            reach = reachable_cities(g, u, u.cell, self._reach)
            cands = [c for c in cands if c in reach] or cands
        if not cands:
            return None
        self.rng.shuffle(cands)
        if role == "bait":
            live = [c for c in cands if c in missiles]
            if live:
                return max(live, key=lambda c: g.board.city(c)["points"])
            self._role[u.id] = "flush"      # no missiles left to bait
        return max(cands, key=lambda c: (g.board.city(c)["points"]
                                         * (1.0 + self.d.pressure(c))))

    def _ensure_goal(self, u, missiles):
        g = self.g
        # Assigned Targets: a real bomber may bomb ONLY its assigned city, so
        # that IS its goal. A destroyed target -> no goal (it roams as a decoy).
        if g.opt["targets"] and u.kind == "bomber":
            tgt = getattr(u, "target", None)
            return tgt if (tgt and tgt not in g.destroyed) else None
        # DEW-anchor destruction priority overrides normal goal selection for a
        # real bomber, until the anchor falls or the priority expires.
        if u.kind == "bomber":
            anchor = self._dew_priority_goal(u)
            if anchor:
                self._goal[u.id] = anchor
                return anchor
        goal = self._goal.get(u.id)
        if goal and goal not in g.destroyed:
            if u.cell is None or goal in reachable_cities(g, u, u.cell,
                                                          self._reach):
                return goal
        goal = (self._pick_real_goal(u, missiles) if u.kind == "bomber"
                else self._pick_decoy_goal(u, missiles))
        if goal:
            self._goal[u.id] = goal
        return goal

    # ---------------- setup (Cuban composition)
    def do_setup_phase(self):
        g = self.g
        if g.phase == "cuban_setup":
            g.setup_cuban()
            pool = g.cuban_to_place()
            reals = [u for u in pool if u.kind == "bomber"]
            decs = [u for u in pool if u.kind == "decoy_bomber"]
            self.rng.shuffle(reals)
            self.rng.shuffle(decs)
            wave = reals[:self.CUBAN_REALS] + decs[:g.CUBAN_MAX
                                                   - self.CUBAN_REALS]
            self.rng.shuffle(wave)
            for u in wave:
                cells = g.cuban_start_cells()
                if not cells or g.cuban_staged_count() >= g.CUBAN_MAX:
                    break
                g.place_cuban(u, self.rng.choice(cells))
            g.finish_cuban_setup()
        elif g.phase == "slbm_targets":
            self._assign_slbm_targets()
        elif g.phase == "bomber_targets":
            self._assign_bomber_targets()

    # ---------------- Assigned Targets (optional rule)
    def _assign_slbm_targets(self):
        """Assign each REAL sub-launched missile a distinct top-value coastal
        city (the American has not set up yet, so no missile info - just aim at
        the biggest jewels). Decoys carry no target."""
        g = self.g
        coastal = sorted((c for c in game_rules.COASTAL_CITIES
                          if g.board.is_city(c) and c not in g.destroyed),
                         key=lambda c: -g.board.city(c)["points"])
        for u in list(g.assignable_bombers()):      # reals only, this phase
            taken = {x.target for x in g.slbms()
                     if x.kind == "missile" and x.target}
            pick = next((c for c in coastal if c not in taken),
                        coastal[0] if coastal else None)
            if pick is None:
                break
            g.assign_target(u, pick)
            self._goal[u.id] = pick

    def _assign_bomber_targets(self):
        """Assign each real bomber a city now that the American defence is
        visible. Value each real slot on a city by an AMORTISED marginal: an
        undefended city is worth its points to the first bomber (and nothing
        after - a city is bombed once); a missile-defended city needs TWO
        bombers to overload it, so each of its first two is worth points/2.
        5-point cities (Anchorage/Godthab included, as today) are pushed to the
        bottom so 6+ cities and defended-jewel overloads fill first (#5).
        Cuban reals are staged, so they take only REACHABLE cities."""
        g = self.g
        missiles = self.d.missile_cities()
        all_cities = [c for c in g.board.cells if g.board.is_city(c)
                      and c not in g.destroyed]

        def marginal(c, k):                          # value of the k-th real
            pts = g.board.city(c)["points"]
            need = 2 if c in missiles else 1         # defended -> overload
            m = (pts / float(need)) if k <= need else 0.0
            if m > 0.0 and pts <= 5:                 # avoid 5-pt per #5
                m -= 50.0
            return m

        count = {}
        reals = list(g.assignable_bombers())
        reals.sort(key=lambda u: 0 if (u.cell or u.staged) else 1)  # staged 1st
        for u in reals:
            if u.cell or u.staged:                   # Cuban real: reachable only
                cand = [c for c in g.reachable_target_cities(u)
                        if c not in g.destroyed] or all_cities
            else:                                    # northern real: any city
                cand = all_cities
            self.rng.shuffle(cand)
            best = max(cand, key=lambda c: marginal(c, count.get(c, 0) + 1))
            if not g.assign_target(u, best)[0]:      # reachability guard
                reach = g.reachable_target_cities(u)
                g.assign_target(u, reach[0] if reach else best)
            tgt = getattr(u, "target", best)
            count[tgt] = count.get(tgt, 0) + 1
            self._goal[u.id] = tgt
        if g.assignable_bombers():                   # safety: never hang setup
            g.auto_assign_targets()

    # ---------------- staging
    async def _stage_wave(self, note):
        g = self.g
        pool = g.offboard_bombers()
        if pool and not g.staging_blocked():
            want = min(len(pool),
                       self.EARLY_WAVE if g.turn <= 2 else self.LATE_WAVE)
            fr = self.d.fighters_remaining()
            frac = (self.DECOY_FRAC_HI if fr >= self.FLUSH_HI
                    else self.DECOY_FRAC_MID if fr >= self.FLUSH_MID
                    else self.DECOY_FRAC_LO)
            decs = [u for u in pool if u.kind == "decoy_bomber"]
            reals = [u for u in pool if u.kind == "bomber"]
            self.rng.shuffle(decs)
            self.rng.shuffle(reals)
            n_dec = min(len(decs), int(round(want * frac)))
            wave = decs[:n_dec] + reals[:max(0, want - n_dec)]
            missiles = self.d.missile_cities()
            for u in wave:
                slots = {c: "north" for c in g.stage_cells("north")}
                if g.opt["siberian"]:
                    for c in g.stage_cells("siberian"):
                        slots.setdefault(c, "siberian")
                if not slots:
                    break
                goal = self._ensure_goal(u, missiles)
                dm = self._dmap(goal) if goal else {}
                items = list(slots.items())
                self.rng.shuffle(items)
                start = min(items, key=lambda kv: dm.get(kv[0], 99))
                ok, _ = g.stage_unit(u, start[0], start[1])
                if ok:
                    await note("staged")
        g.finish_staging(force=True)

    async def _launch_staged(self, mover, ask_fire, note):
        g = self.g
        for u in list(g.staged_units()):
            if u.stage_group == "cuban":
                continue
            ok, _ = g.launch_staged(u)
            if not ok:
                continue
            dests = g.legal_entry_dests(u)
            if not dests:
                g.abort_russian_move(u)
                continue
            goal = self._goal.get(u.id)
            dm = self._dmap(goal) if goal else {}
            items = list(dests)
            self.rng.shuffle(items)
            best = min(items, key=lambda d0: (dm.get(d0, 99),
                                              -g.board.row_i[d0]))
            if mover:
                await mover(u, dests[best])
            else:
                for cid in dests[best]:
                    if g.russian_step(u, cid, ask_fire) == "dead":
                        break
                else:
                    g.end_russian_move(u)
            await note("entered")
            # a Siberian entry can end ON Anchorage - snipe it
            if u.alive and u.cell and g.can_bomb(u):
                g.bomb(u)
                await note("bombed")

    # ---------------- SLBMs
    async def _place_slbm(self, note):
        g = self.g
        if not g.opt["slbm"] or g.turn < 2:
            return
        waiting = g.offboard_slbms()
        if not waiting:
            return
        decs = [u for u in waiting if u.kind == "decoy_missile"]
        u = decs[0] if decs else waiting[0]     # decoy goes FIRST (bait)
        missiles = self.d.missile_cities()
        if g.opt["targets"] and u.kind == "missile":
            # Assigned Targets: launch a real SLBM from a cell beside its city.
            tgt = getattr(u, "target", None)
            if not tgt or tgt in g.destroyed:
                return                          # engine retires a dead-target SLBM
            beside = [c for c in g.entry_cells("slbm")
                      if tgt in g.board.nbrs[c]]
            if beside:
                g.enter_unit(u, self.rng.choice(beside), "slbm")
                await note("slbm placed")
            return
        cells = list(g.entry_cells("slbm"))
        self.rng.shuffle(cells)
        best = None
        for cid in cells:
            for nb in g.board.nbrs[cid]:
                if not (g.board.is_city(nb) and nb not in g.destroyed):
                    continue
                pts = g.board.city(nb)["points"]
                if u.kind == "decoy_missile":
                    # bait: prefer a missile city (force the fire/hold call)
                    # and fighter presence (an interception spends a fighter)
                    v = pts + (6.0 if nb in missiles else 0.0)
                    v += 2.0 * self.d.pressure(cid)
                else:
                    v = pts * (0.3 if nb in missiles else 1.0)
                    v -= 1.5 * sum(self.d.prob_real_fighter(f)
                                   for f in self.d.fighter_silhouettes()
                                   if f.cell == nb)
                    v -= 0.5 * self.d.pressure(cid)
                if best is None or v > best[0]:
                    best = (v, cid)
        if best:
            g.enter_unit(u, best[1], "slbm")
            await note("slbm placed")

    async def _move_slbm(self, u, do_move, note):
        g = self.g
        dests = g.legal_russian_dests(u)
        if not dests:
            return
        if g.opt["targets"] and u.kind == "missile":
            # a real SLBM moves onto its ASSIGNED city and bombs it; if that
            # city is already destroyed it still moves on (the engine then
            # retires it on its attack turn).
            tgt = getattr(u, "target", None)
            if tgt and tgt in dests:
                res = await do_move(u, dests[tgt])
                await note("moved")
                if res != "dead" and g.can_bomb(u):
                    g.bomb(u)
                    await note("bombed")
            return
        missiles = self.d.missile_cities()
        cities = [d for d in dests
                  if g.board.is_city(d) and d not in g.destroyed]
        self.rng.shuffle(cities)
        if not cities:
            return
        if u.kind == "missile":
            free = [c for c in cities if c not in missiles]
            dest = max(free or cities,
                       key=lambda c: g.board.city(c)["points"])
        else:                               # decoy: walk onto the missile
            baited = [c for c in cities if c in missiles]
            dest = max(baited or cities,
                       key=lambda c: g.board.city(c)["points"])
        res = await do_move(u, dests[dest])
        await note("moved")
        if res != "dead" and g.can_bomb(u):
            g.bomb(u)
            await note("bombed")

    # ---------------- endgame targeting (no real fighters left)
    def _assign_endgame(self, missiles):
        """Once no real fighters remain, assign the on-map real bombers to
        DISTINCT undefended (non-missile) undestroyed cities each can eventually
        reach, claiming the highest value first. Every bomber thus heads for a
        separate 6+ city; the ones that can't be matched to an unclaimed 6+ get
        a 5-pt city (which minimises how many 5-pt cities are hit). Writes the
        chosen city into self._goal[uid] and returns the map."""
        g = self.g
        bombers = [u for u in g.soviet_units()
                   if u.alive and u.entered and not u.frozen
                   and u.kind == "bomber" and u.cell]
        pairs = []
        for u in bombers:
            for c in reachable_cities(g, u, u.cell, self._reach):
                if c in g.destroyed or c in missiles:
                    continue
                pairs.append((g.board.city(c)["points"], self.rng.random(),
                              c, u.id))
        pairs.sort(reverse=True)             # highest-value city first
        used_city, goal = set(), {}
        for _pts, _j, c, uid in pairs:
            if c in used_city or uid in goal:
                continue
            goal[uid] = c
            used_city.add(c)
        for uid, c in goal.items():
            self._goal[uid] = c
        return goal

    def _endgame_5pt_blocked(self, u, cid):
        """When no real fighters remain, a real bomber must NOT bomb a non-anchor
        5-pt city unless it is that bomber's own assigned target (i.e. no
        undefended 6+ city was reachable/unclaimed for it). Anchors and 6+ cities
        are never blocked; before the trigger nothing is blocked."""
        g = self.g
        if not self.d.no_real_fighters_left():
            return False
        if g.board.city(cid)["points"] != 5 or cid in self.DEW_ANCHORS:
            return False
        return self._goal.get(u.id) != cid

    # ---------------- movement
    async def _move_real(self, u, dests, goal, do_move, note):
        g = self.g
        missiles = self.d.missile_cities()
        # Cities a real, surfaced SLBM of ours already covers: treat them like
        # destroyed - never bomb them and don't camp on them (the SLBM takes
        # them for free; grabbing one first only forces it onto a lesser city).
        claimed = self._slbm_claimed_cities()
        if g.opt["targets"]:
            # may bomb ONLY the assigned city; with a dead/absent target there
            # is nothing to bomb and the bomber just advances (roams).
            bombable = [d for d in dests if d == goal and d not in g.destroyed]
        else:
            bmin = self._bomb_min()
            bombable = [d for d in dests
                        if g.board.is_city(d) and d not in g.destroyed
                        and d not in claimed
                        and not self._endgame_5pt_blocked(u, d)
                        and (d == goal or (g.board.city(d)["points"] >= bmin
                                           and d not in missiles))]
        if bombable:
            self.rng.shuffle(bombable)
            dest = max(bombable, key=lambda c: g.board.city(c)["points"])
        else:
            dm = self._dmap(goal) if goal else {}
            items = [d for d in dests if d not in claimed] or list(dests)
            self.rng.shuffle(items)
            dest = min(items, key=lambda d0: (dm.get(d0, 99)
                                              + self.RISK_W
                                              * self.d.pressure(d0)))
        res = await do_move(u, dests[dest])
        await note("moved")
        if res != "dead" and g.can_bomb(u) and u.cell not in claimed:
            g.bomb(u)
            await note("bombed")

    async def _move_decoy(self, u, dests, goal, do_move, note):
        g = self.g
        role = self._role.get(u.id, "flush")
        if role == "bait" and goal in dests:
            dest = goal                     # walk onto the missile city
        else:
            # mimic a real: close on the goal, but PREFER fighter-covered
            # squares (drawing an interception kills a real fighter), and
            # never stop on an undestroyed 7+ city (a decoy tell).
            dm = self._dmap(goal) if goal else {}
            items = [d for d in dests
                     if not (g.board.is_city(d) and d not in g.destroyed
                             and g.board.city(d)["points"] >= 7)] or list(dests)
            self.rng.shuffle(items)
            dest = min(items, key=lambda d0: (dm.get(d0, 99)
                                              - 0.5 * self.d.pressure(d0)))
        await do_move(u, dests[dest])
        await note("moved")

    async def _move_units(self, do_move, ask_fire, note):
        g = self.g
        missiles = self.d.missile_cities()
        # The endgame 5-pt reassignment is inert under Assigned Targets: targets
        # are fixed at setup and can never be reassigned (its spirit moved into
        # _assign_bomber_targets, which already prefers 6+ and minimises 5-pt).
        if not g.opt["targets"] and self.d.no_real_fighters_left():
            # spread the reals across distinct undefended 6+ cities before moving
            self._assign_endgame(missiles)
        movers = [x for x in g.soviet_units()
                  if x.alive and x.entered and not x.frozen
                  and x.moved_turn != g.turn]
        self.rng.shuffle(movers)
        movers.sort(key=lambda x: x.kind == "bomber")   # decoys bait first
        for u in movers:
            if g.winner:
                return
            if u.kind in ("missile", "decoy_missile"):
                await self._move_slbm(u, do_move, note)
                continue
            dests = g.legal_russian_dests(u)
            if not dests:
                continue
            goal = self._ensure_goal(u, missiles)
            if u.kind == "bomber":
                await self._move_real(u, dests, goal, do_move, note)
            else:
                await self._move_decoy(u, dests, goal, do_move, note)

    async def _cleanup_stacks(self, do_move, note):
        g = self.g
        for _pass in range(4):
            occ = {}
            for x in g.soviet_units():
                if x.alive and x.entered and x.cell and not x.entering:
                    occ.setdefault(x.cell, []).append(x)
            moved_any = False
            for cell, grp in occ.items():
                if len(grp) < 2:
                    continue
                for x in grp:
                    if x.frozen or x.moved_turn == g.turn:
                        continue
                    dests = g.legal_russian_dests(x)
                    empty = [d for d in dests
                             if not any(z is not x
                                        for z in g.at(d, "soviet"))]
                    if empty:
                        d = max(empty, key=lambda d0: g.board.row_i[d0])
                        await do_move(x, dests[d])
                        await note("moved")
                        if (g.can_bomb(x) and x.kind == "bomber"
                                and x.cell in self._slbm_claimed_cities()):
                            pass                 # leave an SLBM-covered city be
                        elif g.can_bomb(x):
                            g.bomb(x)
                            await note("bombed")
                        moved_any = True
                        if g.winner:
                            return
                        break
            if not moved_any:
                break

    # ---------------- turn
    async def take_turn(self, ask_fire, mover=None, on_event=None):
        g = self.g
        note = on_event or _anoop
        if mover is None:
            async def do_move(u, pa):
                return g.move_russian(u, pa, ask_fire)
        else:
            do_move = mover
        await self._stage_wave(note)
        await self._launch_staged(mover, ask_fire, note)
        for u in [x for x in g.staged_units() if x.stage_group == "cuban"]:
            ok, _ = g.cuban_launch(u)
            if ok:
                await note("cuban entry")
        await self._place_slbm(note)
        await self._move_units(do_move, ask_fire, note)
        if g.winner:
            return
        await self._cleanup_stacks(do_move, note)
        if g.winner:
            return
        g.end_russian_turn(force=True)
