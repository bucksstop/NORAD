"""Computer opponents for NORAD, each with three selectable doctrines.

Soviet doctrines:
  blitz - mass waves on the shortest lines to the biggest cities; accepts
          risk, brings extra units on early, bombs at the first chance.
  feint - deception: decoys lead into missile-defended cities to soak the
          defense; bombers follow into cleared or soft targets; spreads
          the attack widely.
  flank - splits the wave between the east and west edges, each unit
          entering the flank aligned with its target's side of the map, to
          hit peripheral and coastal cities on BOTH coasts where fighter
          coverage is thin; patient routing around defenses.

American doctrines:
  fortress - missiles and fighters mass on the highest-value cities;
             fighters launch only against point-blank threats.
  screen   - balanced: fighters placed for maximum city coverage,
             intercepting anything within a bomber-move of a city.
  picket   - a northern tribwire: fighters forward on the northern-tier
             cities to kill raiders early, aggressive interception.
  sentinel - probabilistic engagement. Missiles emplace on Omaha, Chicago,
             Detroit and New York and hold fire early (25% before turn 5,
             50/75/100% on turns 5/6/7+). Fighters engage a raider only on a
             roll: 33% for the first silhouette to close within six squares,
             66% for the second, then certain. Every detected decoy raises all
             of these odds by 12.5 points, so the screen tightens as the bluff
             is called.

Both AIs use only information a human would have: positions and
silhouettes. They never peek at whether a hidden enemy unit is real.

take_turn() accepts a `mover(unit, path)` callback so the UI can animate
movement square by square; it must return 'arrived' or 'dead'.
"""

RUS_STYLES = {
    "blitz": dict(early=8, late=6, fp_risk=0.10, sam_avoid=2.0,
                  bomber_def=0.85, decoy_def=1.2, crowd=0.7,
                  entry="near", periph=1.0),
    "feint": dict(early=7, late=5, fp_risk=0.35, sam_avoid=5.0,
                  bomber_def=0.45, decoy_def=2.0, crowd=1.5,
                  entry="near", periph=1.0),
    "flank": dict(early=6, late=5, fp_risk=0.55, sam_avoid=6.0,
                  bomber_def=0.5, decoy_def=1.6, crowd=2.0,
                  entry="flank", periph=1.7),
}

US_STYLES = {
    "fortress": dict(place="value", reach=3, hunt_row=None),
    "screen": dict(place="coverage", reach=4, hunt_row=None),
    "picket": dict(place="north", reach=5, hunt_row=10),
    "sentinel": dict(place="coverage", reach=6, hunt_row=None, prob=True,
                     missile_cities=["Omaha", "Chicago", "Detroit",
                                     "New York"]),
}


def bfs_dist(board, start):
    dist = {start: 0}
    frontier = [start]
    while frontier:
        nxt = []
        for cid in frontier:
            for nb in board.nbrs[cid]:
                if nb not in dist:
                    dist[nb] = dist[cid] + 1
                    nxt.append(nb)
        frontier = nxt
    return dist


def cities_by_value(game, undestroyed=True):
    out = [c for c in game.board.cells if game.board.is_city(c)]
    if undestroyed:
        out = [c for c in out if c not in game.destroyed]
    return sorted(out, key=lambda c: -game.board.city(c)["points"])


def missile_looking(game, cid):
    """Visible missile silhouettes on a square (public information)."""
    return [u for u in game.at(cid, "us") if u.kind == "missile"]


def fighter_looking(game, cid):
    return [u for u in game.at(cid, "us")
            if u.kind in ("fighter", "decoy_fighter")]


def theta_mid(game, cid):
    w, e = game.board.interval[cid]
    return (w + e) / 2


def extremeness(game, cid):
    """0 at the fan edges, larger toward the middle of the map."""
    t = theta_mid(game, cid)
    return min(t - (-1.0), 121.1 - t)


class RussianAI:
    def __init__(self, game, style="feint"):
        self.g = game
        self.style = style
        # "expert" is a selectable difficulty (not in the random RUS_STYLES
        # rotation): delegate to the belief-driven ExpertSovietAI. Lazy import
        # avoids a game_ai <-> game_ai_expert import cycle.
        if style == "expert":
            import game_ai_expert
            self.expert = game_ai_expert.ExpertSovietAI(game)
            return
        self.expert = None
        self.p = RUS_STYLES[style]
        self._dist_cache = {}

    def dist_to(self, goal):
        if goal not in self._dist_cache:
            self._dist_cache[goal] = bfs_dist(self.g.board, goal)
        return self._dist_cache[goal]

    def do_setup_phase(self):
        if self.expert:
            return self.expert.do_setup_phase()
        g = self.g
        if g.phase == "cuban_setup":
            # Fixed 3 real + 5 decoy Cuban force is already set aside; stage up
            # to CUBAN_MAX (5) of them - prefer the real bombers first.
            g.setup_cuban()                       # no-op if already built
            pool = sorted(g.cuban_to_place(), key=lambda u: (not u.real))
            for u in pool:
                cells = g.cuban_start_cells()
                if not cells or g.cuban_staged_count() >= g.CUBAN_MAX:
                    break
                g.place_cuban(u, g.rng.choice(cells))
            g.finish_cuban_setup()
        elif g.phase in ("slbm_targets", "bomber_targets"):
            g.auto_assign_targets()

    # ---------------- threat awareness (public info only)
    def fighter_pressure(self, cid):
        g = self.g
        n = 0
        dmap = self.dist_to(cid)
        for u in g.us_units():
            if (u.alive and u.cell and u.moved_turn != g.turn
                    and u.kind in ("fighter", "decoy_fighter")):
                if dmap.get(u.cell, 99) <= 6:
                    n += 1
        return n

    def _goal_score(self, u, cid, usage):
        g, p = self.g, self.p
        v = g.board.city(cid)["points"]
        d = self.dist_to(cid).get(u.cell, 99) if u.cell else 12
        s = v / (d + 2.0)
        s /= (1.0 + p["crowd"] * usage.get(cid, 0))
        if missile_looking(g, cid):
            s *= p["bomber_def"] if u.real else p["decoy_def"]
        s /= (1.0 + p["fp_risk"] * self.fighter_pressure(cid))
        if p["periph"] > 1.0 and extremeness(g, cid) < 30:
            s *= p["periph"]
        return s

    def _pick_goal(self, u, usage):
        g = self.g
        if g.opt["targets"] and u.real and u.target:
            return u.target if u.target not in g.destroyed else None
        cities = cities_by_value(g)[:18]
        if not cities:
            return None
        best = max(cities, key=lambda c: self._goal_score(u, c, usage))
        usage[best] = usage.get(best, 0) + 1
        return best

    def _dest_score(self, u, dest, goal):
        g, p = self.g, self.p
        d = self.dist_to(goal).get(dest, 99)
        risk = p["fp_risk"] * 1.7 * self.fighter_pressure(dest)
        if missile_looking(g, dest) and dest != goal:
            risk += p["sam_avoid"]
        return d + risk - 0.01 * g.board.row_i[dest]

    def _entry_key(self, cid, dmap, goal=None):
        if self.p["entry"] == "flank" and goal is not None:
            # Flank enters the edge aligned with the goal's SIDE of the map:
            # match the entry column (angular position) to the goal's, then
            # prefer the more extreme (edge) cell. Raw BFS distance is avoided
            # here because the Siberian line reaches far south and would pull
            # even east-bound waves onto the west edge.
            align = abs(theta_mid(self.g, cid) - theta_mid(self.g, goal))
            return (round(align), round(extremeness(self.g, cid)))
        return (dmap.get(cid, 999), -self.g.board.row_i[cid])

    # ---------------- turn
    def take_turn(self, ask_fire, mover=None, on_event=None):
        if self.expert:
            return self.expert.take_turn(ask_fire, mover=mover,
                                         on_event=on_event)
        g, p = self.g, self.p
        note = on_event or (lambda *_: None)
        do_move = mover or (lambda u, pa: g.move_russian(u, pa, ask_fire))
        usage = {}

        # 1a. stage the wave on the red start line (visible buildup)
        pool = g.offboard_bombers()
        want = min(len(pool), p["early"] if g.turn <= 2 else p["late"])
        pool.sort(key=lambda u: u.real)     # decoys lead the waves
        goals = cities_by_value(g)
        for u in pool[:want]:
            starts = {c: "north" for c in g.stage_cells("north")}
            if g.opt["siberian"]:
                for c in g.stage_cells("siberian"):
                    starts.setdefault(c, "siberian")
            if not starts:
                break
            goal = (u.target if g.opt["targets"] and u.real and u.target
                    else goals[(id(u) // 8 + g.turn)
                               % max(1, min(10, len(goals)))])
            dmap = self.dist_to(goal)
            start = min(starts,
                        key=lambda c: self._entry_key(c, dmap, goal))
            ok, _ = g.stage_unit(u, start, starts[start])
            if ok:
                note("staged")
        g.finish_staging(force=True)

        # 1b. fly each staged unit onto the board, one at a time
        #     (north/Siberian only; Cuban units launch separately, below)
        for u in list(g.staged_units()):
            if u.stage_group == "cuban":
                continue
            goal = (u.target if g.opt["targets"] and u.real and u.target
                    else goals[(id(u) // 8 + g.turn)
                               % max(1, min(10, len(goals)))])
            dmap = self.dist_to(goal)
            ok, _ = g.launch_staged(u)
            if not ok:
                continue                    # blocked; stays staged
            dests = g.legal_entry_dests(u)
            if not dests:
                g.abort_russian_move(u)
                continue
            best = min(dests, key=lambda d: (dmap.get(d, 999),
                                             -g.board.row_i[d]))
            if mover:
                mover(u, dests[best])
            else:
                for cid in dests[best]:
                    if g.russian_step(u, cid, ask_fire) == "dead":
                        break
                else:
                    g.end_russian_move(u)
            note("entered")

        for u in [x for x in g.staged_units() if x.stage_group == "cuban"]:
            ok, _ = g.cuban_launch(u)
            if ok:
                note("cuban entry")

        if g.opt["slbm"] and g.turn >= 2:
            waiting = g.offboard_slbms()
            if waiting:
                u = waiting[0]
                best = None
                for cid in g.entry_cells("slbm"):
                    if g.at(cid, "soviet"):
                        continue
                    for nb in g.board.nbrs[cid]:
                        if (g.board.is_city(nb) and nb not in g.destroyed
                                and (not g.opt["targets"]
                                     or u.target in (None, nb))):
                            v = g.board.city(nb)["points"]
                            v -= 6 * len(missile_looking(g, nb))
                            v -= 2 * len(fighter_looking(g, cid))
                            if best is None or v > best[0]:
                                best = (v, cid)
                if best:
                    g.enter_unit(u, best[1], "slbm")
                    note("slbm placed")

        movers = [x for x in g.soviet_units()
                  if x.alive and x.entered and not x.frozen
                  and x.moved_turn != g.turn]
        movers.sort(key=lambda x: (x.real, -g.board.row_i.get(x.cell, 0)))
        for u in movers:
            dests = g.legal_russian_dests(u)
            if not dests:
                continue
            goal = self._pick_goal(u, usage)
            if goal is None:
                continue
            best = None
            if u.real:
                # A real bomber grabs the best city it can actually reach and
                # bomb THIS turn rather than flying past it toward a distant
                # goal (which used to let bombers drift all the way to row V
                # without ever bombing). Prefer high value, low defence.
                bombable = [d for d in dests
                            if g.board.is_city(d) and d not in g.destroyed
                            and (not g.opt["targets"]
                                 or u.target in (None, d))]
                if bombable:
                    best = max(bombable, key=lambda c: (
                        g.board.city(c)["points"]
                        - p["sam_avoid"] * len(missile_looking(g, c))
                        - p["fp_risk"] * self.fighter_pressure(c)))
            if best is None:
                if goal in dests and (u.real or not g.board.is_city(goal)
                                      or missile_looking(g, goal)):
                    best = goal
                else:
                    best = min(dests,
                               key=lambda d: self._dest_score(u, d, goal))
            res = do_move(u, dests[best])
            note("moved")
            if res != "dead" and g.can_bomb(u):   # bomb whenever we can
                g.bomb(u)
                note("bombed")
            if g.winner:
                return
        # Resolve any transient stacks the wave created: slide a still-movable
        # unit off each shared square to an empty legal destination.
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
                             if not any(z is not x for z in g.at(d, "soviet"))]
                    if empty:
                        d = max(empty, key=lambda d: g.board.row_i[d])
                        do_move(x, dests[d])
                        note("moved")
                        if g.can_bomb(x):    # a >2 E/W move must end in a bomb
                            g.bomb(x)
                            note("bombed")
                        moved_any = True
                        if g.winner:
                            return
                        break
            if not moved_any:
                break
        g.end_russian_turn(force=True)


class AmericanAI:
    def __init__(self, game, style="screen"):
        self.g = game
        self.style = style
        # "expert" is a selectable difficulty (not in the random US_STYLES
        # rotation): delegate to the belief/threat-driven ExpertAmericanAI.
        # Lazy import avoids a game_ai <-> game_ai_expert import cycle.
        if style == "expert":
            import game_ai_expert
            self.expert = game_ai_expert.ExpertAmericanAI(
                game, params=game_ai_expert.load_tuned_params())
            return
        self.expert = None
        self.p = US_STYLES[style]
        self._fseen = {}       # fighter id -> silhouettes already counted
        self._fcount = {}      # fighter id -> how many have closed within six

    # ---------------- public-information helpers
    def _detected_decoys(self):
        return sum(1 for u in self.g.soviet_units()
                   if u.kind in ("decoy_bomber", "decoy_missile")
                   and u.revealed)

    def _city_cell(self, name):
        g = self.g
        for c in g.board.cells:
            if g.board.is_city(c) and g.board.city(c)["name"] == name:
                return c
        return None

    # ---------------- setup
    def place_all_units(self):
        if self.expert:
            return self.expert.place_all_units()
        g, p = self.g, self.p
        cities = cities_by_value(g)
        ca_cities = [c for c in cities if g.board.city(c).get("canadian")]
        us_cities = [c for c in cities if not g.board.city(c).get("canadian")]
        missiles = [u for u in g.us_placement_units()
                    if u.kind == "missile" and not u.canadian]
        # Play Balance: a missile decoy is placed like a real missile (it looks
        # like one) - on a top city, so it bluffs a sixth defended jewel.
        missile_decoys = [u for u in g.us_placement_units()
                          if u.kind == "us_decoy_missile"]
        fighters = [u for u in g.us_placement_units()
                    if u.kind == "fighter" and not u.canadian]
        decoys = [u for u in g.us_placement_units()
                  if u.kind == "decoy_fighter" and not u.canadian]
        if self.p.get("missile_cities"):
            named = [self._city_cell(nm) for nm in self.p["missile_cities"]]
            named = [c for c in named if c and c in us_cities]
            order = named + [c for c in us_cities if c not in named]
        else:
            order = us_cities
        hard = missiles + missile_decoys
        for u, c in zip(hard, order):
            g.place_us(u, c)
        bluff = us_cities[len(hard):] or us_cities
        for i, u in enumerate(decoys):
            g.place_us(u, bluff[i % len(bluff)])

        spots = self._fighter_spots(len(fighters), cities, us_cities,
                                    len(missiles))
        for u, c in zip(fighters, spots):
            g.place_us(u, c)
        ca = [u for u in g.us_placement_units() if u.canadian]
        for i, u in enumerate(ca):
            g.place_us(u, ca_cities[i % len(ca_cities)])
        g.finish_us_setup()

    def _fighter_spots(self, n, cities, us_cities, n_missiles):
        g = self.g
        mode = self.p["place"]
        if mode == "value":
            pool = us_cities[n_missiles:]
            return [pool[i % len(pool)] for i in range(n)]
        if mode == "north":
            northern = sorted(us_cities,
                              key=lambda c: (g.board.row_i[c],
                                             -g.board.city(c)["points"]))
            picket = [c for c in northern[:8]]
            rest = self._coverage_spots(n - min(6, len(picket)), cities,
                                        us_cities)
            return picket[:6] + rest
        return self._coverage_spots(n, cities, us_cities)

    def _coverage_spots(self, n, cities, us_cities):
        g = self.g
        taken = set()
        out = []
        for _ in range(n):
            best, score = None, -1
            for c in us_cities:
                if c in taken:
                    continue
                dmap = bfs_dist(g.board, c)
                s = sum(g.board.city(o)["points"]
                        for o in cities if dmap.get(o, 99) <= 6)
                s += g.board.city(c)["points"]
                if s > score:
                    best, score = c, s
            out.append(best or us_cities[0])
            taken.add(best)
        return out

    # ---------------- decisions
    def ask_fire(self, missile, unit):
        if self.expert:
            return self.expert.ask_fire(missile, unit)
        if not self.p.get("prob"):
            return True
        g, t = self.g, self.g.turn
        base = 0.25 if t <= 4 else 0.50 if t == 5 else 0.75 if t == 6 else 1.0
        prob = min(1.0, base + 0.125 * self._detected_decoys())
        return g.rng.random() < prob

    def _take_turn_prob(self, mover, note):
        """Sentinel fighters: each rolls whether to engage a raider that has
        newly closed within six squares (33/66/100% for the 1st/2nd/3rd, plus
        12.5% per detected decoy). A committed fighter flies onto the raider,
        where combat is resolved; fighters that decline hold their cities."""
        g = self.g
        decoys = self._detected_decoys()
        sils = [s for s in g.soviet_units()
                if s.alive and s.entered and s.cell and not s.frozen
                and s.kind in ("bomber", "decoy_bomber")]
        picks = []
        for f in list(g.movable_fighters()):
            dmap = bfs_dist(g.board, f.cell)
            seen = self._fseen.setdefault(f.id, set())
            approaching = sorted(
                (s for s in sils
                 if dmap.get(s.cell, 99) <= 6 and id(s) not in seen),
                key=lambda s: dmap.get(s.cell, 99))
            chosen = None
            for s in approaching:
                seen.add(id(s))
                self._fcount[f.id] = self._fcount.get(f.id, 0) + 1
                n = self._fcount[f.id]
                base = 0.33 if n == 1 else 0.66 if n == 2 else 1.0
                prob = min(1.0, base + 0.125 * decoys)
                if g.rng.random() < prob and chosen is None:
                    chosen = s
            if chosen is not None:
                picks.append((dmap.get(chosen.cell, 99), id(f), f, chosen))
        picks.sort()                       # resolve the closest engagements first
        hit = set()
        for _d, _fid, f, s in picks:
            if not s.alive or id(s) in hit:
                continue
            if s.cell in g.legal_fighter_dests(f):
                path = g.fighter_path(f, s.cell)
                if mover:
                    mover(f, path)
                else:
                    g.move_fighter(f, s.cell)
                hit.add(id(s))
                note("intercept")

    def take_turn(self, mover=None, on_event=None):
        """Fighter moves only; the UI resolves combat afterwards."""
        if self.expert:
            return self.expert.take_turn(mover=mover, on_event=on_event)
        g, p = self.g, self.p
        note = on_event or (lambda *_: None)
        if p.get("prob"):
            return self._take_turn_prob(mover, note)
        threats = []
        for s in g.soviet_units():
            if not (s.alive and s.entered and s.cell and not s.frozen):
                continue
            dmap = bfs_dist(g.board, s.cell)
            best_v, best_d = 0, 99
            for c in cities_by_value(g):
                d = dmap.get(c, 99)
                v = g.board.city(c)["points"]
                if [x for x in g.at(c, "us") if x.kind == "missile"]:
                    v -= 3
                if d < best_d or (d == best_d and v > best_v):
                    best_d, best_v = d, v
            reach = p["reach"] if s.kind in ("bomber", "decoy_bomber") else 1
            hunt = (p["hunt_row"] is not None
                    and g.board.row_i[s.cell] <= p["hunt_row"])
            if best_d <= reach or hunt:
                threats.append((-best_v, best_d, id(s), s))
        threats.sort()
        used = set()
        for negv, dmin, _, s in threats:
            best = None
            for f in g.movable_fighters():
                if f.id in used:
                    continue
                dests = g.legal_fighter_dests(f)
                if s.cell in dests:
                    home_v = g.board.city(f.cell)["points"] \
                        if g.board.is_city(f.cell) else 0
                    k = (dests[s.cell], home_v)
                    if best is None or k < best[0]:
                        best = (k, f)
            if best:
                f = best[1]
                path = g.fighter_path(f, s.cell)
                if mover:
                    mover(f, path)
                else:
                    g.move_fighter(f, s.cell)
                used.add(f.id)
                note("intercept")
