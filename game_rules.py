"""NORAD rules engine - UI-agnostic (no pygame).

Implements the 1977 Mishler rules: turn structure, Russian entry and
movement restrictions, missile/fighter combat, bombing, win conditions,
and the optional rules (DEW Line, Siberian placement, Cuban-based units,
Soviet sub-launched missiles, Canadian Air Defense, Assigned Targets).

Movement supports two styles:
- step tracing: begin_*_move / *_step / end_*_move / abort_*_move
- classic jump: legal_russian_dests + move_russian / move_fighter

Interpretations of ambiguous points are marked with "NOTE:".
"""
import json
import os
import random

ROWS = "ABCDEFGHIJKLMNOPQRSTUV"

# Angular column boundaries (degrees from the fan apex), west -> east.
# Duplicated from tools/build_grid.py - single source of measured geometry.
BASE = [121.07, 109.52, 99.81, 89.80, 79.85, 69.84, 60.18, 49.94,
        40.00, 30.05, 19.61, 9.23, -1.02]
HALF_MEASURED = {
    (109.52, 99.81): 104.64, (99.81, 89.80): 94.60, (89.80, 79.85): 84.55,
    (79.85, 69.84): 74.50, (69.84, 60.18): 64.80, (60.18, 49.94): 54.91,
    (49.94, 40.00): 44.90, (40.00, 30.05): 35.20, (30.05, 19.61): 24.80,
    (19.61, 9.23): 14.45, (9.23, -1.02): 4.30, (121.07, 109.52): 114.45,
}
QMEASURED = [92.08, 87.17, 82.28, 77.28, 72.22, 67.28, 62.42, 57.38, 52.23,
             47.30, 42.27, 37.48, 32.42, 27.32, 22.18, 17.32, 12.22, 7.17,
             2.02]

COASTAL_CITIES = {  # for Soviet sub-launched missile placement
    "H121", "L142", "M142", "O142", "Q1422", "R1511", "S1512", "T1721",
    "T1811", "S1812", "U1822", "T1822", "S1911", "R1912", "Q1912", "P1912",
    "P1921", "O192", "M192", "G212",
}
# NOTE: Cuban-based placement box location read from the map's south-east
# edge; entry squares are the row-V squares directly north of it.
CUBAN_ENTRY_COLS = {"1812", "1821", "1822", "1911", "1912"}

# Sub-launched missile launch squares: ocean cells one step from a coastal
# city, enumerated from the map by the user (grid.json has no land/ocean flag).
# ("1911" was given as U1911 in the Key West group.)
SLBM_LAUNCH_CELLS = {
    "F211", "F212", "G211", "H211", "H212",
    "I112", "I121", "I122",
    "L141", "M141", "N141", "O141",
    "O201", "P1922", "P2011",
    "Q1921", "Q1922",
    "P1421", "Q1421", "R1421", "R1422", "S1422", "S1511",
    "R1921", "S1912", "S1921",
    "T1511", "T1512",
    "T1812", "T1821",
    "T1911", "T1912",
    "T1712", "T1722", "U1712", "U1721", "U1722",
    "U1811", "U1812",
    "U1821", "U1911", "V1821", "V1822", "V1911",
}

# A bomber/decoy may move at most this many squares east/west per turn.
LATERAL_LIMIT = 2

RUSSIAN_MIN_ENTRIES_PER_TURN = 4
OFFBOARD = "__offboard__"   # move_start sentinel for entry moves


def _half_bounds():
    out = [BASE[0]]
    for a, b in zip(BASE, BASE[1:]):
        out += [HALF_MEASURED[(a, b)], b]
    return out


def _quarter_bounds():
    hb = _half_bounds()
    out = [hb[0]]
    for a, b in zip(hb, hb[1:]):
        mid = (a + b) / 2
        best = min(QMEASURED, key=lambda q: abs(q - mid))
        out += [best if abs(best - mid) < 0.7 else mid, b]
    return out


def col_interval(col):
    """(theta_west, theta_east) for a column label like '14', '141', '1412'."""
    base_i = int(col[:2]) - 10
    if len(col) == 2:
        return BASE[base_i], BASE[base_i + 1]
    hb = _half_bounds()
    if len(col) == 3:
        j = 2 * base_i + (int(col[2]) - 1)
        return hb[j], hb[j + 1]
    qb = _quarter_bounds()
    j = 4 * base_i + 2 * (int(col[2]) - 1) + (int(col[3]) - 1)
    return qb[j], qb[j + 1]


class Board:
    """Cells, adjacency, distances."""

    def __init__(self, grid_path):
        with open(grid_path) as f:
            data = json.load(f)
        self.apex = tuple(data["apex"])
        self.cells = data["cells"]              # cid -> {poly, row, col, city?}
        self.row_i = {}
        self.interval = {}
        for cid, c in self.cells.items():
            self.row_i[cid] = ROWS.index(c["row"])
            self.interval[cid] = col_interval(c["col"])
            c["center"] = (sum(p[0] for p in c["poly"]) / len(c["poly"]),
                           sum(p[1] for p in c["poly"]) / len(c["poly"]))
        self._build_adjacency()

    def _build_adjacency(self):
        eps = 0.35
        by_row = {}
        for cid in self.cells:
            by_row.setdefault(self.row_i[cid], []).append(cid)
        self.nbrs = {cid: set() for cid in self.cells}
        for cid in self.cells:
            r = self.row_i[cid]
            w1, e1 = self.interval[cid]
            for dr in (-1, 0, 1):
                for other in by_row.get(r + dr, ()):
                    if other == cid:
                        continue
                    w2, e2 = self.interval[other]
                    if dr == 0:
                        if abs(w1 - e2) < eps or abs(e1 - w2) < eps:
                            self.nbrs[cid].add(other)
                    else:
                        # overlap or corner touch (diagonal)
                        if min(w1, w2) - max(e1, e2) > -eps:
                            self.nbrs[cid].add(other)

    def is_city(self, cid):
        return "city" in self.cells.get(cid, {})

    def city(self, cid):
        return self.cells[cid].get("city")

    def west_order(self, row_letter):
        r = ROWS.index(row_letter)
        cells = [c for c in self.cells if self.row_i[c] == r]
        return sorted(cells, key=lambda c: -self.interval[c][0])


class Unit:
    def __init__(self, meta):
        self.id = meta["id"]
        self.side = meta["side"]            # 'us' | 'soviet'
        self.kind = meta["kind"]
        self.real = meta["real"]
        self.move = meta.get("move", 0)
        self.canadian = meta.get("canadian", False)
        self.optional = meta.get("optional", False)
        self.cell = None
        self.alive = True
        self.frozen = False                 # bombed / stuck on V / expended
        self.revealed = False               # back permanently shown (cloud)
        self.moved_turn = -1                # turn number of last move/entry
        self.move_start = None              # cell before this turn's move
        self.entered = False
        self.entered_turn = -1
        self.group = "north"                # north | cuban | slbm
        self.entering = False               # entry move in progress
        self.staged = None                  # start square its slot touches
        self.stage_group = None             # north | siberian while staged
        self.entry_start = None             # start square of its entry move
        self.target = None                  # assigned-targets city cid
        self.slbm_turn = None               # turn the SLBM was placed
        self.dew_checked = False            # DEW row-H decoy check already rolled
        self.dew_exposed = False            # exposed by DEW; shown then removed


class Game:
    """Full game state. UI or AI drives it through the public methods.

    options: dict with bools: dew, siberian, cuban, slbm, canadian, targets
    """

    def __init__(self, root, options=None, rng=None):
        self.opt = {k: False for k in
                    ("dew", "siberian", "cuban", "slbm", "canadian",
                     "targets", "balance")}
        if options:
            self.opt.update({k: v for k, v in options.items()
                             if k in self.opt})
        if self.opt["slbm"]:
            self.opt["canadian"] = True     # rule: SLBM requires Canadian AD
        self.rng = rng or random.Random()
        self.board = Board(os.path.join(root, "data", "grid.json"))
        with open(os.path.join(root, "assets", "units",
                               "manifest.json")) as f:
            manifest = json.load(f)
        self.units = [Unit(m) for m in manifest if self._in_play(m)]
        # Soviet SLBM force is 3 real + 1 decoy (4 counters total). The 4th
        # missile counter is turned into the decoy below.
        n_missiles = 0
        for u in self.units:
            if u.side == "soviet" and u.kind == "missile":
                n_missiles += 1
                if n_missiles == 4:
                    u.real = False
                    u.kind = "decoy_missile"
        # Play Balance optional rule: the American gains a missile decoy (added
        # via the manifest) and the Soviet loses one decoy bomber (8 -> 7).
        if self.opt["balance"]:
            drop = next((u for u in self.units if u.side == "soviet"
                         and u.kind == "decoy_bomber"), None)
            if drop is not None:
                self.units.remove(drop)
        self.points = 0
        self.destroyed = set()
        self.turn = 0
        self.winner = None
        self.log = []
        self.entered_this_turn = 0
        self.staging_done = False           # per-Soviet-turn staging step
        self.cuban_ready = False            # Cuban force composition chosen
        self._stuck_msgs = []               # units destroyed for having no move
        self.dew_break_turn = None          # turn the DEW line was broken
        self.victory_announced = False
        self._steps_left = {}               # unit.id -> remaining trace steps
        self._lateral = {}                  # unit.id -> E/W steps this move
        self._phases = ["cuban_setup", "slbm_targets", "us_setup",
                        "bomber_targets", "russian"]
        if self.opt["cuban"]:
            self._init_cuban_force()        # fixed 3 real + 5 decoy stack
        else:
            self._phases.remove("cuban_setup")
        if not (self.opt["slbm"] and self.opt["targets"]):
            self._phases.remove("slbm_targets")
        if not self.opt["targets"]:
            self._phases.remove("bomber_targets")
        self.phase = self._phases[0]

    def _in_play(self, meta):
        if not meta.get("optional"):
            return True
        if meta.get("balance"):
            return self.opt["balance"]
        if meta.get("canadian"):
            return self.opt["canadian"]
        return self.opt["slbm"]

    # -------------------------------------------------- unit queries
    def us_units(self):
        return [u for u in self.units if u.side == "us"]

    def soviet_units(self):
        return [u for u in self.units if u.side == "soviet"]

    def at(self, cid, side=None):
        return [u for u in self.units if u.alive and u.cell == cid
                and (side is None or u.side == side)]

    def bombers(self):
        return [u for u in self.soviet_units()
                if u.kind in ("bomber", "decoy_bomber")]

    def slbms(self):
        return [u for u in self.soviet_units()
                if u.kind in ("missile", "decoy_missile")]

    def say(self, msg):
        self.log.append(msg)

    def next_phase(self):
        i = self._phases.index(self.phase)
        self.phase = self._phases[i + 1]
        if self.phase == "russian":
            self.turn = 1
            self.entered_this_turn = 0
            self.staging_done = False

    # -------------------------------------------------- setup: Cuba
    #  Fixed Cuban force: 3 real bombers + 5 decoys are set aside from the main
    #  pool; the player stages up to CUBAN_MAX (5) of them on the start line.
    CUBAN_REAL = 3
    CUBAN_DECOY = 5
    CUBAN_MAX = 5

    def _init_cuban_force(self):
        reals = [u for u in self.bombers() if u.real][:self.CUBAN_REAL]
        decs = [u for u in self.bombers() if not u.real][:self.CUBAN_DECOY]
        for u in reals + decs:
            u.group = "cuban"
        self.cuban_ready = True

    def setup_cuban(self, *args):
        """Back-compat shim: the Cuban force is now fixed (3 real + 5 decoy).
        Any arguments are ignored."""
        if not self.cuban_ready:
            self._init_cuban_force()

    def cuban_staged_count(self):
        return sum(1 for u in self.soviet_units()
                   if u.stage_group == "cuban" and u.staged and not u.entered)

    # ---------------- Cuban-based placement: units are STAGED on the red
    #                  band adjacent to row V (the "Optional Russian Start
    #                  Line"), exactly like the north/Siberian start bands.
    def is_cuban_start(self, cid):
        c = self.board.cells.get(cid)
        return bool(c and c["row"] == "V" and c["col"] in CUBAN_ENTRY_COLS)

    def cuban_start_cells(self):
        """Empty Cuban start-line slots (the yellow squares south of row V)."""
        return self.stage_cells("cuban")

    def cuban_to_place(self):
        return [u for u in self.soviet_units()
                if u.group == "cuban" and u.alive
                and not u.entered and u.staged is None]

    def place_cuban(self, unit, cid):
        assert self.phase == "cuban_setup"
        return self.stage_unit(unit, cid, "cuban")

    def unplace_cuban(self, unit):
        return self.unstage_unit(unit)

    def finish_cuban_setup(self):
        assert self.phase == "cuban_setup"
        # Cuban units not placed on the start line rejoin the main (northern)
        # force and become available for normal Soviet staging, instead of
        # being retired. (cuban_to_place = alive, group cuban, not entered,
        # NOT staged.)  offboard_bombers() then picks them up because they are
        # group "north" and off the board.
        for u in self.cuban_to_place():
            u.group = "north"
        self.next_phase()

    def cuban_launch(self, unit):
        """A staged Cuban unit moves from the red band onto its row-V start
        square and holds there this turn; it advances north on later turns."""
        if unit.stage_group != "cuban" or unit.entered or unit.staged is None:
            return False, "That is not a staged Cuban unit."
        cell = unit.staged
        if self.at(cell, "soviet"):
            return False, f"{cell} is occupied - move that unit first."
        unit.cell = cell
        unit.entered = True
        unit.entered_turn = self.turn
        unit.moved_turn = self.turn          # enters this turn, advances later
        unit.move_start = None
        unit.entering = False
        unit.staged = None
        unit.stage_group = None
        self.say(f"Cuban-based unit enters the board at {cell}.")
        return True, ""

    # -------------------------------------------------- setup: targets
    def assignable_bombers(self):
        if self.phase == "slbm_targets":
            return [u for u in self.slbms() if u.real and not u.target]
        return [u for u in self.bombers() if u.real and not u.target]

    def assign_target(self, unit, cid):
        if not self.board.is_city(cid):
            return False, "Not a city square."
        if self.phase == "slbm_targets" and cid not in COASTAL_CITIES:
            return False, ("Sub-launched missiles can only attack "
                           "coastal cities.")
        unit.target = cid
        if not self.assignable_bombers():
            self.next_phase()
        return True, ""

    def auto_assign_targets(self):
        cities = sorted((c for c in self.board.cells
                         if self.board.is_city(c)
                         and (self.phase != "slbm_targets"
                              or c in COASTAL_CITIES)),
                        key=lambda c: -self.board.city(c)["points"])
        todo = self.assignable_bombers()
        for i, u in enumerate(todo):
            u.target = cities[i % len(cities)]
        if self.phase in ("slbm_targets", "bomber_targets"):
            self.next_phase()

    # -------------------------------------------------- assigned targets:
    #  reachability + human staging-time assignment.
    def bomber_start_cell(self, u):
        """Square a bomber's reachability is measured from: its board cell,
        or (while still staged) the start square its slot touches."""
        if u.cell is not None:
            return u.cell
        return u.staged

    def can_reach(self, u, from_cell, target):
        """Could bomber `u`, starting from `from_cell`, still legally reach
        `target` under the movement rules - forward-only direction, the
        per-turn lateral limit, and legal stopping squares? Stacking and
        occupancy are ignored: this is about movement geometry, not who is in
        the way. Returns True for missiles / no target."""
        if not target or from_cell is None or from_cell == target:
            return True
        if u.kind in ("missile", "decoy_missile"):
            return True
        allowance = self._russian_allowance(u)
        seen_stops = {from_cell}
        stops = [from_cell]
        while stops:
            s = stops.pop()
            # one turn's move from s: state = (cell, steps_used, lateral_used)
            turn_seen = {(s, 0, 0)}
            frontier = [(s, 0, 0)]
            while frontier:
                cell, steps, lat = frontier.pop()
                if steps >= allowance:
                    continue
                srow = self.board.row_i[cell]
                for nb in self.board.nbrs[cell]:
                    if not self._step_ok(u, cell, nb):
                        continue
                    nlat = lat + (1 if self.board.row_i[nb] == srow else 0)
                    # A real bomber may exceed the lateral cap on the approach
                    # that bombs its target (nb == target); any other turn the
                    # cap holds.
                    if nlat > LATERAL_LIMIT and not (u.kind == "bomber"
                                                     and nb == target):
                        continue
                    st = (nb, steps + 1, nlat)
                    if st in turn_seen:
                        continue
                    turn_seen.add(st)
                    frontier.append(st)
                    if nb == target:
                        return True
                    # a legal square to END this turn -> a fresh next-turn start
                    if nb not in seen_stops and self._dest_geom_ok(u, s, nb):
                        seen_stops.add(nb)
                        stops.append(nb)
        return False

    def can_reach_target(self, u):
        """Can u still reach its OWN assigned target from where it is now?"""
        return self.can_reach(u, self.bomber_start_cell(u),
                              getattr(u, "target", None))

    def assign_bomber_target(self, u, cid):
        """Human staging-time assignment: choose one target city for a real
        bomber. Rejects non-cities and cities unreachable from its start
        line (the reachability rule the player asked for)."""
        if not self.board.is_city(cid):
            return False, "Click a city square."
        start = self.bomber_start_cell(u)
        if not self.can_reach(u, start, cid):
            return False, ("That city cannot be reached from this "
                           "bomber's start line.")
        u.target = cid
        return True, ""

    def reachable_target_cities(self, u):
        """City cids a bomber could still reach from its start line."""
        start = self.bomber_start_cell(u)
        return [c for c in self.board.cells
                if self.board.is_city(c) and self.can_reach(u, start, c)]

    def clear_bomber_target(self, u):
        u.target = None

    # -------------------------------------------------- setup: American
    def us_placement_units(self):
        return [u for u in self.us_units() if u.cell is None]

    def can_place_us(self, unit, cid):
        if not self.board.is_city(cid):
            return False, f"{cid} is not a city square."
        # Canadian Air Defense optional rule: Canadian units set up only in
        # Canada; American units set up only in the US or Godthab (i.e. NOT in
        # Canadian cities). Godthab (G212) is not flagged Canadian, so the
        # non-Canadian test admits it automatically.
        if self.opt["canadian"]:
            is_ca = bool(self.board.city(cid).get("canadian"))
            if unit.canadian and not is_ca:
                return False, "Canadian units may only start in Canada."
            if not unit.canadian and is_ca:
                return False, ("American units may only start in the US or "
                               "Godthab, not in Canadian cities.")
        return True, ""

    def place_us(self, unit, cid):
        ok, why = self.can_place_us(unit, cid)
        if ok:
            unit.cell = cid
        return ok, why

    def unplace_us(self, unit):
        unit.cell = None

    def finish_us_setup(self):
        assert not self.us_placement_units()
        self.next_phase()

    # -------------------------------------------------- Russian entry
    def offboard_bombers(self):
        return [u for u in self.bombers()
                if not u.entered and u.alive and u.group == "north"
                and not u.staged]

    def offboard_cuban(self):
        return [u for u in self.bombers()
                if not u.entered and u.alive and u.group == "cuban"]

    def offboard_slbms(self):
        return [u for u in self.slbms() if not u.entered and u.alive]

    def unmoved_missiles(self):
        """On-map sub-launched missiles that started this turn on the board (it
        is their attack turn) and have not moved yet. They will be removed at
        the end of the Soviet turn if left unmoved - the UI warns about this."""
        return [u for u in self.slbms()
                if u.alive and u.entered and u.cell is not None
                and not u.frozen and u.slbm_turn is not None
                and self.turn == u.slbm_turn + 1
                and u.moved_turn != self.turn]

    def dew_broken(self):
        return (self.opt["dew"] and "H121" in self.destroyed
                and "G212" in self.destroyed)

    def staging_blocked(self):
        """DEW line just broken: no staging for the following two turns."""
        return (self.dew_break_turn is not None
                and self.turn <= self.dew_break_turn + 2)

    def dew_stage_active(self):
        """Staging has shifted to the row-H start line."""
        return (self.dew_broken() and self.dew_break_turn is not None
                and self.turn > self.dew_break_turn + 2)

    def _dew_expose_check(self, u):
        """DEW-line decoy detection. The FIRST time a North- or Siberian-staged
        DECOY bomber enters (or starts on) a row-H square, the DEW line may
        expose and remove it - 50% while FULLY active (both Anchorage H121 and
        Godthab G212 stand), 25% while PARTIALLY active (exactly one stands),
        0% inactive (both destroyed). One roll per decoy, judged the instant it
        first occupies row H. Cuban decoys and real bombers are unaffected.
        Returns True if the decoy was exposed and removed."""
        if not (self.opt["dew"] and u.alive and not u.dew_checked
                and u.kind == "decoy_bomber" and u.group != "cuban"):
            return False
        if u.cell is None or self.board.cells[u.cell]["row"] != "H":
            return False
        u.dew_checked = True
        standing = (("H121" not in self.destroyed)
                    + ("G212" not in self.destroyed))
        prob = (0.0, 0.25, 0.5)[standing]        # 0 stand->0, 1->0.25, 2->0.5
        if prob and self.rng.random() < prob:
            # Reveal it (its blank back is shown) and freeze it in place; the UI
            # displays it with the message, then it is removed on click-to-
            # continue. Headless play / end_russian_turn finalise the removal.
            u.revealed = True
            u.frozen = True
            u.dew_exposed = True
            self.say(f"The DEW Line detects a Soviet decoy crossing row H at "
                     f"{u.cell} and exposes it - the unit is removed.")
            return True
        return False

    # ---------------- staging on the red start line
    def staged_units(self):
        return [u for u in self.soviet_units()
                if u.alive and u.staged and not u.entered]

    def needs_staging(self):
        if self.staging_blocked():
            return False
        north_staged = [u for u in self.staged_units()
                        if u.stage_group in ("north", "siberian")]
        slbm_wait = (self.opt["slbm"] and self.turn >= 2
                     and bool(self.offboard_slbms()))
        return (not self.staging_done
                and bool(self.offboard_bombers() or north_staged
                         or slbm_wait))

    def unenter_slbm(self, u):
        """Pull a just-surfaced sub-launched missile back off the board so it
        can be placed again somewhere else."""
        u.cell = None
        u.entered = False
        u.slbm_turn = None
        u.moved_turn = -1
        u.move_start = None

    def stage_cells(self, group):
        """Start squares whose red-band slot is currently empty."""
        if self.staging_blocked():
            return []
        taken = {(u.stage_group, u.staged) for u in self.staged_units()}
        out = []
        if group == "north":
            row = 7 if self.dew_stage_active() else 0
            for cid in self.board.cells:
                if self.board.row_i[cid] == row \
                        and ("north", cid) not in taken:
                    if row == 7 and self.at(cid, "soviet"):
                        continue            # H-line slot is on the board
                    out.append(cid)
        elif group == "siberian":
            for row in "ABCDEFGH":
                order = self.board.west_order(row)
                if order and ("siberian", order[0]) not in taken:
                    out.append(order[0])
        elif group == "cuban":
            for cid in self.board.cells:
                c = self.board.cells[cid]
                if (c["row"] == "V" and c["col"] in CUBAN_ENTRY_COLS
                        and ("cuban", cid) not in taken):
                    out.append(cid)
        return out

    def stage_unit(self, unit, cid, group):
        if cid not in self.stage_cells(group):
            return False, "That slot is already occupied."
        unit.staged = cid
        unit.stage_group = group
        self.say("Soviet unit staged on the start line.")
        return True, ""

    def unstage_unit(self, unit):
        if unit.staged and not unit.entered:
            unit.staged = None
            unit.stage_group = None
            return True
        return False

    def finish_staging(self, force=False):
        """(ok, why). Requires at least 4 staged while units remain."""
        pool = len(self.offboard_bombers())
        n = len(self.staged_units())
        need = min(RUSSIAN_MIN_ENTRIES_PER_TURN,
                   n + min(pool, len(self.stage_cells("north"))
                           + (len(self.stage_cells("siberian"))
                              if self.opt["siberian"] else 0)))
        if n < need and not force:
            return False, (f"Stage at least {need} unit(s) before moving "
                           f"({n} staged so far).")
        self.staging_done = True
        return True, ""

    def launch_staged(self, unit, ask_fire=None):
        """Move a staged unit onto its start square, beginning its entry
        move. Returns (ok, why)."""
        if not self.staging_done:
            return False, "Finish staging first (press Done staging)."
        if unit.staged is None or unit.entered:
            return False, "That unit is not staged."
        start = unit.staged
        if self.at(start, "soviet"):
            return False, (f"{start} is occupied - move that unit first.")
        group = unit.stage_group
        unit.cell = start
        unit.entered = True
        unit.entered_turn = self.turn
        unit.group = "north"
        unit.entry_from = group
        unit.entry_start = start
        unit.entering = True
        unit.move_start = OFFBOARD
        unit.staged = None
        free_start = (group == "north" and self.dew_stage_active())
        self._steps_left[unit.id] = unit.move - (0 if free_start else 1)
        self._lateral[unit.id] = 0           # entry moves are lateral-exempt
        self.entered_this_turn += 1
        self.say(f"Soviet unit moves onto the board at {start}.")
        # A Siberian decoy whose start square is on row H meets the DEW line now.
        self._dew_expose_check(unit)
        return True, ""

    def entry_cells(self, group):
        """Squares where an entering unit is first placed (its first
        square of movement): row A from the north (row H once the DEW
        line is broken); the westernmost square of its row from Siberia.
        It then continues moving and must stop inside the entry end zone
        (rows B-D / 2nd-4th square from the west edge)."""
        out = []
        if group in ("north", "siberian"):
            return self.stage_cells(group)   # staging replaces direct entry
        if group == "cuban":
            for cid in self.board.cells:
                c = self.board.cells[cid]
                if (c["row"] == "V" and c["col"] in CUBAN_ENTRY_COLS
                        and not self.at(cid, "soviet")):
                    out.append(cid)
        elif group == "slbm":
            for cid in SLBM_LAUNCH_CELLS:
                if not self.at(cid, "soviet"):
                    out.append(cid)
        return out

    def in_entry_end_zone(self, u, cid):
        if u.group == "cuban" or u.kind in ("missile", "decoy_missile"):
            return True                     # those stop on their first square
        if getattr(u, "entry_from", "north") == "siberian":
            row = self.board.cells[cid]["row"]
            if row not in "ABCDEFGHI":
                return False
            # Stop within the 1st-4th COLUMN from the west edge (base columns
            # 10-13). Column 10 (base 0) is included: a Siberian-staged unit may
            # legally move straight SOUTH down the west edge (e.g. E10 -> H10),
            # not only east into 11-13. Count by base column, NOT the row's
            # west_order position, which miscounts where the columns split
            # (rows A-E 2-digit, F-O 3-digit).
            base_col = int(self.board.cells[cid]["col"][:2]) - 10
            return 0 <= base_col <= 3
        rows = range(9, 12) if self.dew_stage_active() else range(1, 4)
        return self.board.row_i[cid] in rows

    def legal_entry_dests(self, u):
        """{dest: path} for finishing an entry move in progress."""
        if not u.entering:
            return {}
        allowance = self._steps_left.get(u.id, 0)
        best = {u.cell: (0, None)}
        frontier = [u.cell]
        for step in range(1, allowance + 1):
            nxt = []
            for cid in frontier:
                if best[cid][0] != step - 1:
                    continue
                for nb in self.board.nbrs[cid]:
                    if self._step_ok(u, cid, nb) and nb not in best:
                        best[nb] = (step, cid)
                        nxt.append(nb)
            frontier = nxt
        out = {}
        for dest in best:
            if dest == u.cell or not self.in_entry_end_zone(u, dest):
                continue
            if not self._stack_end_ok(u, dest):
                continue
            path = [dest]
            c = dest
            while best[c][1] is not None:
                c = best[c][1]
                path.append(c)
            out[dest] = list(reversed(path))[1:]
        return out

    def enter_unit(self, unit, cid, group=None):
        group = group or unit.group
        assert cid in self.entry_cells(group), "illegal entry square"
        unit.cell = cid
        unit.entered = True
        unit.entered_turn = self.turn
        if group == "slbm":
            unit.group = "slbm"
            unit.slbm_turn = self.turn
            unit.moved_turn = self.turn
            unit.move_start = None
            self.say(f"Soviet sub-launched missile surfaces at {cid}.")
        elif group == "cuban":
            unit.moved_turn = self.turn     # may move no farther this turn
            unit.move_start = None
            self.say(f"Soviet Cuban-based unit enters at {cid}.")
        else:                               # north / siberian: stage it
            unit.cell = None
            unit.entered = False
            ok, why = self.stage_unit(unit, cid, group)
            if ok:
                ok2, why2 = self.launch_staged(unit)
                if not ok2:
                    self.unstage_unit(unit)
                    raise AssertionError(why2)

    # -------------------------------------------------- movement helpers
    def _russian_allowance(self, u):
        if u.kind in ("missile", "decoy_missile"):
            return 1
        return u.move

    def _step_ok(self, u, frm, to):
        dr = self.board.row_i[to] - self.board.row_i[frm]
        if u.kind in ("missile", "decoy_missile"):
            return True                     # 1 square, any direction
        if u.group == "cuban":
            return dr <= 0                  # never S
        return dr >= 0                      # never N

    def _dest_geom_ok(self, u, start, dest):
        """Distance/direction rule for stopping on `dest` (ignores stacking)."""
        if u.kind in ("missile", "decoy_missile"):
            return True
        dr = self.board.row_i[dest] - self.board.row_i[start]
        if self.board.is_city(dest):
            return True                     # stopping on a city is exempt
        if u.group == "cuban":
            if self.board.cells[dest]["row"] == "A":
                return dr <= -1             # reaching A (unit then stops)
            return dr <= -2
        if self.board.cells[dest]["row"] == "V":
            return dr >= 1                  # reaching V (unit then freezes)
        return dr >= 2

    def _dest_ok(self, u, start, dest):
        # Classic/AI destination lists: may stop here only if the stacking
        # end-rule allows it (empty, or one occupant that can still move off).
        if not self._stack_end_ok(u, dest):
            return False
        return self._dest_geom_ok(u, start, dest)

    def _occupant_can_vacate(self, x, sq):
        """Could another Soviet unit x still move off square sq this turn to an
        *empty* square it may legally stop on? Stacking is only forbidden once
        both units have finished moving, so a stack is legal for now only while
        the occupant genuinely has somewhere else to go (not boxed in). Uses a
        bounded BFS over x's own allowance; never re-checks stacking, so it
        cannot recurse."""
        if not (self.russian_can_move(x) and not x.entering):
            return False
        allowance = self._russian_allowance(x)
        seen = {sq}
        frontier = [(sq, 0)]
        for cur, step in frontier:
            if step >= allowance:
                continue
            for nb in self.board.nbrs[cur]:
                if nb in seen or not self._step_ok(x, cur, nb):
                    continue
                seen.add(nb)
                frontier.append((nb, step + 1))
                if (self._dest_geom_ok(x, sq, nb)
                        and not any(z is not x
                                    for z in self.at(nb, "soviet"))):
                    return True
        return False

    def _stack_end_ok(self, u, sq):
        """May u *finish* its move on sq? Allowed onto an empty square, or one
        holding a single other unit that can still move away this turn. Never
        onto a city square that is already occupied, and never onto a square
        already holding two+ units, so piles cannot grow."""
        others = [x for x in self.at(sq, "soviet") if x is not u]
        if not others:
            return True
        if self.board.is_city(sq) or len(others) > 1:
            return False
        return self._occupant_can_vacate(others[0], sq)

    def _stack_resolvable(self, u, dest):
        """May u *step* onto dest? A pass-through (u still has a step left to
        leave) is fine; otherwise u would stop here, so the end rule applies."""
        steps_after = self._steps_left.get(u.id, 0) - 1
        if steps_after > 0 and any(self._step_ok(u, dest, nb)
                                   for nb in self.board.nbrs[dest]):
            return True
        return self._stack_end_ok(u, dest)

    def _can_bomb_at(self, u, dest):
        """Could real bomber `u` bomb if it finished its move on `dest`? Used
        to permit a >2 east/west move only when it results in a bombing (the
        cell/frozen state of `can_bomb` is not checked - `dest` is hypothetical
        and `u` has not moved there yet)."""
        if u.kind != "bomber":
            return False
        if not self.board.is_city(dest) or dest in self.destroyed:
            return False
        if self.opt["targets"] and u.target != dest:
            return False
        return True

    def missile_defended(self, cid):
        """A live American anti-bomber missile sits on city `cid` - GAME TRUTH:
        a REAL missile that can actually intercept. A Play-Balance missile decoy
        (kind 'us_decoy_missile') is NOT a real defender and is excluded here."""
        return any(m.kind == "missile" and m.alive
                   for m in self.at(cid, "us"))

    def has_missile_look(self, cid):
        """A live American MISSILE SILHOUETTE sits on `cid` - PUBLIC info the
        Soviet sees: a real missile OR a Play-Balance decoy missile. Soviet-
        facing rules key off this (not missile_defended) so a decoy's identity
        never leaks through move legality."""
        return any(m.kind in ("missile", "us_decoy_missile") and m.alive
                   for m in self.at(cid, "us"))

    def _lateral_dash_dest_ok(self, u, dest):
        """May a unit that moved MORE than LATERAL_LIMIT squares east/west end
        its move on `dest`?  A real bomber may - only onto a city it can bomb.
        A decoy may - only onto a city defended by an American missile (it
        forces the fire/hold decision, then is removed either way)."""
        if u.kind == "bomber":
            return self._can_bomb_at(u, dest)
        if u.kind == "decoy_bomber":
            return self.board.is_city(dest) and self.has_missile_look(dest)
        return False

    def bomber_exceeded_lateral(self, u):
        """True if a real strategic bomber has moved more than LATERAL_LIMIT
        squares east/west during its current (or just-completed) move. Such a
        move is legal only if the bomber bombs a city this turn; the UI checks
        this after the move ends and undoes it otherwise."""
        return (u.kind == "bomber"
                and self._lateral.get(u.id, 0) > LATERAL_LIMIT)

    def lateral_exceeded(self, u):
        """True if a bomber OR decoy has moved more than LATERAL_LIMIT squares
        east/west this move. A real bomber must then bomb; a decoy must have
        ended on a missile-defended city (else the move is illegal)."""
        return (u.kind in ("bomber", "decoy_bomber")
                and self._lateral.get(u.id, 0) > LATERAL_LIMIT)

    def russian_can_move(self, u):
        return (u.alive and not u.frozen and u.cell is not None
                and u.moved_turn != self.turn
                and (u.kind not in ("missile", "decoy_missile")
                     or (u.slbm_turn is not None
                         and self.turn == u.slbm_turn + 1)))

    # ---------------- step-traced movement (Soviet)
    def begin_russian_move(self, u):
        if self.needs_staging():
            return False
        if u.entering or not self.russian_can_move(u):
            return False
        u.move_start = u.cell
        self._steps_left[u.id] = self._russian_allowance(u)
        self._lateral[u.id] = 0
        return True

    def russian_step_options(self, u):
        if self._steps_left.get(u.id, 0) <= 0:
            return []
        row = self.board.row_i[u.cell]
        # A bomber may move more than LATERAL_LIMIT squares east/west ONLY if
        # it bombs a city that turn; a decoy only if it ends on a missile-
        # defended city. The extra lateral steps are offered here and the
        # legality of the destination is enforced when the move ends
        # (lateral_exceeded + _lateral_dash_dest_ok). Missiles are unaffected
        # (they move a single square).
        lat_maxed = (not u.entering
                     and u.kind not in ("bomber", "decoy_bomber")
                     and self._lateral.get(u.id, 0) >= LATERAL_LIMIT)
        opts = []
        for nb in self.board.nbrs[u.cell]:
            if not self._step_ok(u, u.cell, nb):
                continue
            if lat_maxed and self.board.row_i[nb] == row:
                continue
            if not self._stack_resolvable(u, nb):
                continue                     # would create a dead stack
            opts.append(nb)
        return opts

    def russian_step(self, u, cid, ask_fire):
        """One square of movement. Returns 'ok' or 'dead'."""
        assert (self._steps_left.get(u.id, 0) > 0
                and cid in self.board.nbrs[u.cell]
                and self._step_ok(u, u.cell, cid))
        lateral = self.board.row_i[cid] == self.board.row_i[u.cell]
        u.cell = cid
        self._steps_left[u.id] -= 1
        if lateral and not u.entering:
            self._lateral[u.id] = self._lateral.get(u.id, 0) + 1
        for m in [x for x in self.at(cid, "us") if x.kind == "missile"]:
            if ask_fire(m, u):
                self._missile_kill(m, u)
                self._steps_left.pop(u.id, None)
                return "dead"
        if self._dew_expose_check(u):            # crossed row H -> maybe exposed
            self._steps_left.pop(u.id, None)
            return "dead"
        return "ok"

    def can_end_russian_move(self, u):
        stack_stuck = not self._stack_end_ok(u, u.cell)
        if u.entering:
            if stack_stuck:
                return False, ("Can't stop here - the other Soviet unit can "
                               "no longer move. Move on to an empty square.")
            if not self.in_entry_end_zone(u, u.cell):
                if getattr(u, "entry_from", "north") == "siberian":
                    return False, ("An entering unit must stop on the "
                                   "2nd-4th square from the west edge.")
                hi = "L" if self.dew_stage_active() else "D"
                lo = "J" if self.dew_stage_active() else "B"
                return False, (f"An entering unit must stop between rows "
                               f"{lo} and {hi}.")
            return True, ""
        if u.cell == u.move_start:
            return False, "The unit has not moved."
        if stack_stuck:
            return False, ("Can't stop here - the other Soviet unit can no "
                           "longer move. Move on to an empty square.")
        if not self._dest_geom_ok(u, u.move_start, u.cell):
            return False, ("Must end at least 2 rows further "
                           + ("north" if u.group == "cuban" else "south")
                           + " (or stop on a city).")
        return True, ""

    def end_russian_move(self, u):
        ok, why = self.can_end_russian_move(u)
        if not ok:
            return False, why
        u.moved_turn = self.turn
        u.entering = False
        self._steps_left.pop(u.id, None)
        if (self.board.cells[u.cell]["row"] == "V"
                and u.kind in ("bomber", "decoy_bomber")
                and u.group != "cuban"):
            u.frozen = True
            self.say(f"Soviet unit at {u.cell} reaches row V and is stuck.")
        elif (self.board.cells[u.cell]["row"] == "A"
                and u.kind in ("bomber", "decoy_bomber")
                and u.group == "cuban"):
            # Cuban-based bombers (real or decoy) flying north stop for good
            # once they reach row A - there is no farther north to go.
            u.frozen = True
            self.say(f"Soviet unit at {u.cell} reaches row A and stops.")
        self.resolve_decoy_dash(u)
        return True, ""

    def resolve_decoy_dash(self, u):
        """Resolve a decoy's east/west "dash" onto a missile-defended city.

        A decoy is allowed to move more than LATERAL_LIMIT squares east/west
        only to reach a city holding an American missile. If it survived the
        move (the missile fired would already have destroyed it), the American
        chose to HOLD FIRE - and since a decoy cannot bomb, it is now exposed
        and removed from play. Returns the message shown, or None if this move
        was not such a dash. (An *illegal* dash - >2 E/W ending anywhere else -
        is caught by the UI, which aborts it.)"""
        if u.kind != "decoy_bomber" or not u.alive:
            return None
        if self._lateral.get(u.id, 0) <= LATERAL_LIMIT:
            return None
        if not (self.board.is_city(u.cell) and self.has_missile_look(u.cell)):
            return None
        u.alive = False
        u.revealed = True
        name = self.board.city(u.cell)["name"]
        msg = (f"The American holds fire at {name}. The Soviet unit does not "
               "bomb the city - it is exposed as a decoy and removed from "
               "play.")
        self.say(msg)
        self.check_winner()
        return msg

    def abort_russian_move(self, u):
        """Cancel a move in progress OR restart a completed move.
        Aborting an entry move returns the unit to the off-board pool."""
        if u.move_start is None or u.frozen or not u.alive:
            return False
        if u.move_start == OFFBOARD:
            u.cell = None
            u.entered = False
            u.entering = False
            u.entered_turn = -1
            u.moved_turn = -1
            u.move_start = None
            u.staged = u.entry_start        # back onto its red-band slot
            u.stage_group = getattr(u, "entry_from", "north")
            self.entered_this_turn = max(0, self.entered_this_turn - 1)
            self._steps_left.pop(u.id, None)
            return True
        u.cell = u.move_start
        u.moved_turn = -1
        self._steps_left.pop(u.id, None)
        return True

    # ---------------- classic jump movement (Soviet)
    def legal_russian_dests(self, u):
        """{dest: path}; paths avoid known US-missile squares if possible."""
        if not self.russian_can_move(u):
            return {}
        allowance = self._russian_allowance(u)
        best = {u.cell: (0, 0, None, 0)}    # cid -> (missiles, steps, prev, lat)
        frontier = [u.cell]
        for step in range(1, allowance + 1):
            nxt = []
            for cid in frontier:
                m0, s0, _, lat0 = best[cid]
                if s0 != step - 1:
                    continue
                for nb in self.board.nbrs[cid]:
                    if not self._step_ok(u, cid, nb):
                        continue
                    lat = lat0 + (1 if self.board.row_i[nb]
                                  == self.board.row_i[cid] else 0)
                    # Bombers and decoys may exceed the lateral cap (bounded by
                    # their move allowance); missiles never can. Far-lateral
                    # dests are kept below only if that unit could legally end
                    # there (bomber -> bombable city; decoy -> missile city).
                    lat_cap = (allowance
                               if u.kind in ("bomber", "decoy_bomber")
                               else LATERAL_LIMIT)
                    if lat > lat_cap:
                        continue
                    cost = m0 + (1 if any(x.kind == "missile" for x in
                                          self.at(nb, "us")) else 0)
                    if nb not in best or (cost, step) < best[nb][:2]:
                        best[nb] = (cost, step, cid, lat)
                        nxt.append(nb)
            frontier = nxt
        out = {}
        for dest in best:
            if dest == u.cell or not self._dest_ok(u, u.cell, dest):
                continue
            if (best[dest][3] > LATERAL_LIMIT
                    and not self._lateral_dash_dest_ok(u, dest)):
                continue                     # >2 E/W only to bomb / bait a missile
            path = [dest]
            c = dest
            while best[c][2] is not None:
                c = best[c][2]
                path.append(c)
            out[dest] = list(reversed(path))[1:]
        return out

    def move_russian(self, u, path, ask_fire):
        """Classic: walk the whole path at once. 'dead' or 'arrived'."""
        u.move_start = u.cell
        u.moved_turn = self.turn
        lat = 0
        prev_row = self.board.row_i[u.cell]
        for cid in path:
            r = self.board.row_i[cid]
            if r == prev_row:
                lat += 1
            prev_row = r
            u.cell = cid
            for m in [x for x in self.at(cid, "us") if x.kind == "missile"]:
                if ask_fire(m, u):
                    self._missile_kill(m, u)
                    self._lateral[u.id] = lat
                    return "dead"
            if self._dew_expose_check(u):        # crossed row H -> maybe exposed
                self._lateral[u.id] = lat
                return "dead"
        self._lateral[u.id] = lat            # for lateral_exceeded checks
        if (self.board.cells[u.cell]["row"] == "V"
                and u.kind in ("bomber", "decoy_bomber")
                and u.group != "cuban"):
            u.frozen = True
            self.say(f"Soviet unit at {u.cell} reaches row V and is stuck.")
        elif (self.board.cells[u.cell]["row"] == "A"
                and u.kind in ("bomber", "decoy_bomber")
                and u.group == "cuban"):
            u.frozen = True
            self.say(f"Soviet unit at {u.cell} reaches row A and stops.")
        self.resolve_decoy_dash(u)
        return "arrived"

    def _missile_kill(self, missile, target):
        target.entering = False
        missile.alive = False
        missile.revealed = True
        target.alive = False
        target.revealed = True
        what = "a bomber" if target.real else "a decoy"
        if target.kind in ("missile", "decoy_missile"):
            what = ("a sub-launched missile" if target.real
                    else "a decoy missile")
        self.say(f"Missile combat at {missile.cell}: {what} destroyed.")
        self.check_winner()

    # -------------------------------------------------- bombing
    def can_bomb(self, u):
        if not (u.alive and u.real and u.cell and not u.frozen):
            return False
        if u.kind not in ("bomber", "missile"):
            return False
        if not self.board.is_city(u.cell) or u.cell in self.destroyed:
            return False
        if self.opt["targets"] and u.target != u.cell:
            return False
        if u.kind == "missile" and u.slbm_turn is not None \
                and self.turn != u.slbm_turn + 1:
            return False
        return True

    def bomb(self, u):
        assert self.can_bomb(u)
        city = self.board.city(u.cell)
        self.points += city["points"]
        self.destroyed.add(u.cell)
        if (self.opt["dew"] and self.dew_break_turn is None
                and "H121" in self.destroyed and "G212" in self.destroyed):
            self.dew_break_turn = self.turn
            self.say("The DEW Line is broken! Soviet staging halts for two "
                     "turns; afterwards units stage on the row-H line.")
        u.frozen = True
        u.revealed = True
        killed = [x for x in self.at(u.cell, "us")]
        for x in killed:
            x.alive = False
            # A Play-Balance missile decoy is unmasked only now - a real bomber
            # reached its city and the bluff is called (flips to its blank back).
            if x.kind == "us_decoy_missile":
                x.revealed = True
        extra = ""
        if killed:
            n_ca = sum(1 for x in killed if x.canadian)
            n_us = len(killed) - n_ca
            who = " and ".join(
                p for p in (f"{n_us} American unit(s)" if n_us else "",
                            f"{n_ca} Canadian unit(s)" if n_ca else "") if p)
            extra = f" {who} destroyed with it."
        self.say(f"{city['name']} destroyed! Soviets gain {city['points']} "
                 f"points (total {self.points}).{extra}")
        # Assigned Targets: an OFF-BOARD sub-launched missile aimed at this city
        # can never launch now - it is scrubbed from play at once. (A surfaced
        # one instead makes its move onto the dead city and is cleared on its
        # attack turn by the normal end-of-turn SLBM sweep.)
        if self.opt["targets"]:
            for m in self.slbms():
                if (m.alive and m.real and not m.entered
                        and m.target == u.cell):
                    m.alive = False
                    m.revealed = True
                    self.say("A Soviet sub-launched missile is scrubbed - its "
                             f"target {city['name']} is already destroyed.")
        self.check_winner()

    # -------------------------------------------------- Russian turn end
    def russian_turn_problems(self):
        """List of (message, offending_square_or_None) blocking turn end."""
        problems = []

        def kind_name(u):
            return "bomber" if u.kind == "bomber" else "decoy"

        if self.needs_staging():
            problems.append(("Finish staging first (press 'Done staging').",
                             None))
        for u in self.staged_units():
            if (u.stage_group in ("north", "siberian")
                    and self.staging_done
                    and not self.at(u.staged, "soviet")):
                problems.append(
                    (f"The staged {kind_name(u)} beside {u.staged} must "
                     "move onto the board.", u.staged))
        for u in self.soviet_units():
            if u.alive and u.entering:
                problems.append((f"The entering {kind_name(u)} at {u.cell} "
                                 "must finish its entry move.", u.cell))

        for u in self.bombers():
            if not (u.alive and u.entered and not u.frozen):
                continue
            if u.moved_turn == self.turn:
                continue
            # A bomber that BOMBS a city is frozen (excluded above). One merely
            # SITTING on a city has not bombed - so, real or decoy, it must keep
            # advancing at least 2 rows in its direction every turn. Only frozen
            # units (bombed / row V / Cuban row A) may stay put; there is no
            # "parked on a city" exemption.
            if self.legal_russian_dests(u):
                dirn = "north" if u.group == "cuban" else "south"
                problems.append(
                    (f"The {kind_name(u)} at {u.cell} must move at least 2 "
                     f"rows {dirn} (or stop on a city).", u.cell))
        stacks = {}
        for u in self.soviet_units():
            if u.alive and u.cell is not None and not u.entering:
                stacks.setdefault(u.cell, []).append(u)
        for cid, group in stacks.items():
            if len(group) > 1 and any(self.legal_russian_dests(x)
                                      for x in group):
                problems.append(
                    (f"Two Soviet units are stacked at {cid}; one of them "
                     "must move on before the turn ends.", cid))
        return problems

    def _destroy_stuck_units(self):
        """A bomber/decoy that must advance but has no legal move (blocked by
        its direction of movement or by stacking) is destroyed. A bomber that
        has reached row V is exempt - it is frozen and simply no longer moves.
        Returns human-readable messages for any units lost."""
        msgs = []
        for u in self.soviet_units():
            if not (u.alive and u.entered and not u.entering and not u.frozen):
                continue
            if u.kind not in ("bomber", "decoy_bomber"):
                continue
            if u.moved_turn == self.turn:
                continue
            # No "parked on a city" exemption: a bomber that has not bombed (and
            # so is not frozen) must be able to advance. If it cannot, it is
            # stuck and destroyed - being on a city does not save it.
            if self.legal_russian_dests(u):
                continue
            u.alive = False
            u.revealed = True
            what = "bomber" if u.kind == "bomber" else "decoy"
            m = (f"Soviet {what} at {u.cell} has no legal move (blocked by "
                 "direction or stacking) and is destroyed.")
            self.say(m)
            msgs.append(m)
        return msgs

    def end_russian_turn(self, force=False):
        problems = self.russian_turn_problems()
        if problems and not force:
            return problems
        self._stuck_msgs = self._destroy_stuck_units()
        for u in self.slbms():
            if (u.alive and u.entered and not u.frozen
                    and u.slbm_turn is not None
                    and self.turn == u.slbm_turn + 1):
                u.alive = False
                if (self.opt["targets"] and u.real and u.target
                        and u.target in self.destroyed):
                    self.say(f"Soviet sub-launched missile at {u.cell} is "
                             "removed - its assigned city was already "
                             "destroyed.")
                else:
                    self.say(f"Soviet sub-launched missile at {u.cell} is "
                             "removed (attack window passed).")
        for u in self.soviet_units():
            u.move_start = None
            if u.dew_exposed and u.alive:        # finalise DEW removal (headless
                u.alive = False                  # / any not cleared by a UI gate)
        self._steps_left.clear()
        self.phase = "american"
        self.check_winner()
        return []

    # -------------------------------------------------- American turn
    def movable_fighters(self):
        return [u for u in self.us_units()
                if u.alive and u.cell and u.kind == "fighter"
                and u.moved_turn != self.turn]

    # ---------------- step-traced movement (American fighters)
    def begin_fighter_move(self, u):
        if u not in self.movable_fighters():
            return False
        u.move_start = u.cell
        self._steps_left[u.id] = u.move
        return True

    def fighter_step_options(self, u):
        if self._steps_left.get(u.id, 0) <= 0:
            return []
        return list(self.board.nbrs[u.cell])

    def fighter_step(self, u, cid):
        assert cid in self.fighter_step_options(u)
        u.cell = cid
        self._steps_left[u.id] -= 1
        return "ok"

    def end_fighter_move(self, u):
        if u.cell == u.move_start:
            return False, "The fighter has not moved."
        u.moved_turn = self.turn
        self._steps_left.pop(u.id, None)
        self.say(f"American fighter committed to {u.cell}.")
        return True, ""

    def abort_fighter_move(self, u):
        if u.move_start is None or not u.alive:
            return False
        u.cell = u.move_start
        u.moved_turn = -1
        self._steps_left.pop(u.id, None)
        return True

    # ---------------- classic jump movement (American fighters)
    def legal_fighter_dests(self, u):
        if u not in self.movable_fighters():
            return {}
        dist = {u.cell: 0}
        frontier = [u.cell]
        for step in range(1, u.move + 1):
            nxt = []
            for cid in frontier:
                for nb in self.board.nbrs[cid]:
                    if nb not in dist:
                        dist[nb] = step
                        nxt.append(nb)
            frontier = nxt
        del dist[u.cell]
        return dist

    def fighter_path(self, u, dest):
        """Shortest path (list of cells, excluding start) to dest."""
        prev = {u.cell: None}
        frontier = [u.cell]
        while frontier and dest not in prev:
            nxt = []
            for cid in frontier:
                for nb in self.board.nbrs[cid]:
                    if nb not in prev:
                        prev[nb] = cid
                        nxt.append(nb)
            frontier = nxt
        if dest not in prev:
            return []
        path = [dest]
        c = dest
        while prev[c] is not None:
            c = prev[c]
            path.append(c)
        return list(reversed(path))[1:]

    def move_fighter(self, u, dest):
        assert dest in self.legal_fighter_dests(u)
        u.move_start = u.cell
        u.cell = dest
        u.moved_turn = self.turn
        self.say(f"American fighter committed to {dest}.")

    # ---------------- combat resolution
    def fighter_combat_preview(self):
        """[(square, [fighters], soviet_target_or_None)] for this turn."""
        moved = [u for u in self.us_units()
                 if u.alive and u.moved_turn == self.turn
                 and u.kind == "fighter"]
        out = []
        for sq in sorted({u.cell for u in moved}):
            fighters = [u for u in moved if u.cell == sq]
            targets = [x for x in self.at(sq, "soviet") if x.alive]
            out.append((sq, fighters, targets[0] if targets else None))
        return out

    def combat_outcome(self, sq):
        """Result message for square sq, computed WITHOUT removing units."""
        moved = [u for u in self.us_units()
                 if u.alive and u.moved_turn == self.turn
                 and u.kind == "fighter" and u.cell == sq]
        targets = [x for x in self.at(sq, "soviet") if x.alive]
        if targets:
            t = targets[0]
            what = "a bomber" if t.real else "a decoy"
            if t.kind in ("missile", "decoy_missile"):
                what = ("a sub-launched missile" if t.real
                        else "a decoy missile")
            extra = (f" ({len(moved) - 1} extra fighter(s) also eliminated.)"
                     if len(moved) > 1 else "")
            return f"Fighter combat at {sq}: {what} destroyed.{extra}"
        return f"Fighter at {sq} found no target and is removed."

    def resolve_square(self, sq):
        """Resolve one combat square. Returns a description string."""
        moved = [u for u in self.us_units()
                 if u.alive and u.moved_turn == self.turn
                 and u.kind == "fighter" and u.cell == sq]
        targets = [x for x in self.at(sq, "soviet") if x.alive]
        msg = self.combat_outcome(sq)
        for a in moved:
            a.revealed = True
            a.alive = False
        if targets:
            targets[0].revealed = True
            targets[0].alive = False
        self.say(msg)
        return msg

    def finish_american_turn(self):
        """Call after all combat squares are resolved (or to skip combat)."""
        for sq, fighters, target in self.fighter_combat_preview():
            self.resolve_square(sq)
        for u in self.us_units():
            u.move_start = None
        self._steps_left.clear()
        self.phase = "russian"
        self.turn += 1
        self.entered_this_turn = 0
        self.staging_done = False
        self.check_winner()

    # -------------------------------------------------- winning
    def _attacker_can_still_score(self, u):
        if not (u.alive and u.real and not u.frozen):
            return False
        if self.opt["targets"] and u.target and u.target in self.destroyed:
            return False                # may only bomb its assigned city
        if u.kind in ("missile", "decoy_missile"):
            if u.entered:               # attack window is place-turn + 1
                return (u.slbm_turn is not None
                        and self.turn <= u.slbm_turn + 1)
            return any(c in COASTAL_CITIES and c not in self.destroyed
                       and (not self.opt["targets"] or not u.target
                            or c == u.target)
                       for c in self.board.cells if self.board.is_city(c))
        if not u.entered:
            return True
        # direction feasibility: the main force can only fly south, the
        # Cuban force only north - a bomber past every remaining city can
        # never score again.
        r = self.board.row_i[u.cell]
        for c in self.board.cells:
            if not self.board.is_city(c) or c in self.destroyed:
                continue
            if self.opt["targets"] and u.target and c != u.target:
                continue
            rc = self.board.row_i[c]
            if (rc <= r) if u.group == "cuban" else (rc >= r):
                return True
        return False

    def live_soviet_attackers(self):
        """Real Soviet units that could still score points."""
        return [u for u in self.soviet_units()
                if self._attacker_can_still_score(u)]

    def check_winner(self):
        """The game runs until the last bomber is destroyed, has bombed,
        is stuck on row V, or can no longer reach any city. Passing 100
        points assures a Soviet victory but play continues."""
        if self.winner:
            return self.winner
        if self.points >= 100 and not self.victory_announced:
            self.victory_announced = True
            self.say("The Soviets have passed 100 points - victory is "
                     "assured, but the raid continues until the last "
                     "bomber is spent.")
        if not self.live_soviet_attackers():
            if self.points >= 100:
                self.winner = "soviet"
                self.say(f"SOVIET VICTORY - {self.points} points of "
                         "American cities destroyed.")
            else:
                self.winner = "american"
                self.say(f"AMERICAN VICTORY - the attack is defeated with "
                         f"{self.points} points, short of 100.")
            self.phase = "over"
        return self.winner
