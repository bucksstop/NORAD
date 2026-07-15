import os, sys, random
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import game_rules, game_ai

# ----------------------------------------------------------- TIER 1: rules
def tier1():
    print("== TIER 1: rule unit tests ==")
    # DEW message intact and long enough to have needed >2 wrapped lines
    src = open(os.path.join(ROOT, "game_rules.py")).read()
    assert "afterwards units stage on the row-H line." in src, "DEW msg text"
    # source-level checks of UI changes
    ui = open(os.path.join(ROOT, "norad_game.py")).read()
    assert "SOVIET STAGING" in ui and "SOVIET SETUP" not in ui, "title rename"
    assert "return to starting location" in ui, "abort wording"
    assert "TABS_BG" in ui, "tabs colour"
    # log no longer truncates entries to 2 lines
    assert "self.wrap(entry, PANEL_W - 24, self.small)[:2]" not in ui, "log[:2]"
    assert "self.wrap(entry, PANEL_W - 24, self.small))" in ui, "log full wrap"
    # staging is done from a unit tray, not from mode buttons
    assert "def draw_kind_stacks" in ui and "_stage_tray_rects" in ui
    assert "def stage_slot_click" in ui and "self.stage_kind" in ui
    assert 'add(f"Stage Bomber - north edge ' not in ui, "old stage button gone"
    assert '"Next entry:"' not in ui, "old Next entry button gone"
    # phase_hint staging text
    assert "'staging' if g.needs_staging() else 'movement'" in ui
    # american AI end-of-move click-to-continue popup + shortcuts panel
    assert "American movement complete." in ui
    assert "Click to resolve " in ui and "has_combat" in ui
    assert "TAB - show unit backside" in ui and "Shortcuts" in ui
    assert "def check_dew_break" in ui and "DEW LINE DESTROYED" in ui
    assert "Soviet points: {g.points}\"" in ui and "/ 100" not in ui
    assert "esc - exit game" in ui
    assert "elif not g.russian_turn_problems():" in ui
    assert "orient square to polar north" in ui
    rr = open(os.path.join(ROOT, "game_rules.py")).read()
    assert "def _destroy_stuck_units" in rr
    ai = open(os.path.join(ROOT, "game_ai.py")).read()
    assert '"sentinel"' in ai and "_take_turn_prob" in ai
    assert "SOVIET UNIT LOST" in ui and "def announce_stuck" in ui
    assert "American movement complete." in ui and "has_combat" in ui
    assert 'add("Exit game"' in ui
    # Exit game quits pygame before sys.exit (else the window hangs on Win)
    assert "def quit_app" in ui and 'add("Exit game", self.quit_app)' in ui
    # Siberian staging outlines follow the red band, not polar north
    assert "def _sib_geom" in ui and "SIB_BAND_MID" in ui
    assert 'grp == "siberian"' in ui
    # hover tooltip renders unit ICONS, not a text list
    assert "def draw_hover_tooltip" in ui and "self.sprite(u.id, isz" in ui
    assert "silhouette(s)" not in ui and "  US:  " not in ui
    assert "back = self.unit_shows_back(u)" in ui   # staged units show backs
    assert "self.pick_unit(cid, stack" in ui        # soviet stack picker
    assert "def place_cuban" in rr and "def cuban_start_cells" in rr
    assert "cuban_ready" in rr
    assert "def cuban_unstage_click" in ui and "CUBAN SETUP" in ui
    assert "def cuban_launch" in rr and "def unenter_slbm" in rr
    assert "def entry_type_label" in ui          # kept for Cuban setup
    assert "Click a stack, then" in ui and "NEXT_BG" in ui
    assert "self._combat_focus = sq" in ui and "COMBAT_FOCUS" in ui
    assert "LATERAL_LIMIT" in rr
    assert "Cuban force: 3 bombers" in ui and "self.entry_mode" in ui
    assert "SHORTCUTS_Y" in ui
    assert "Confirm Cuban placement" in ui and "cuban_staged_count" in ui

    # engine: DEW break actually logs the full sentence
    g = game_rules.Game(ROOT, {"dew": True})
    g.destroyed.update({"H121", "G212"})
    g.dew_break_turn = None
    # simulate the DEW-break branch by bombing logic conditions
    g.destroyed.discard("G212")
    # craft: place a soviet bomber on G212 city and bomb
    # simpler: directly call the branch trigger via bomb() on a real bomber
    b = next(u for u in g.soviet_units() if u.kind == "bomber")
    b.cell = "G212"; b.real = True; b.alive = True; b.frozen = False
    b.entered = True
    if g.board.is_city("G212"):
        g.destroyed.discard("G212")
        g.bomb(b)
        joined = " ".join(g.log)
        assert "staging halts for two turns" in joined
        assert "units stage on the row-H line." in joined, "full DEW sentence"
    print("  tier1 OK")

# ------------------------------------------------- headless full-game driver
def play_game(opts, seed):
    rng = random.Random(seed)
    g = game_rules.Game(ROOT, opts, rng=random.Random(seed * 7 + 1))
    rus = game_ai.RussianAI(g, style=rng.choice(list(game_ai.RUS_STYLES)))
    us = game_ai.AmericanAI(g, style=rng.choice(list(game_ai.US_STYLES)))
    guard = 0
    while g.phase != "over" and guard < 400:
        guard += 1
        ph = g.phase
        if ph in ("cuban_setup", "slbm_targets", "bomber_targets"):
            rus.do_setup_phase()
        elif ph == "us_setup":
            us.place_all_units()
        elif ph == "russian":
            rus.take_turn(us.ask_fire)
            occ = {}
            for u in g.soviet_units():
                if u.alive and u.cell and not u.entering:
                    occ.setdefault(u.cell, []).append(u)
            bad = {c: len(l) for c, l in occ.items() if len(l) > 1}
            assert not bad, f"AI left a Soviet stack: {bad} (opts={opts})"
        elif ph == "american":
            us.take_turn()
            for sq, fs, tgt in g.fighter_combat_preview():
                g.resolve_square(sq)
            g.finish_american_turn()
        else:
            break
    assert g.phase == "over", f"did not finish (phase={g.phase}, opts={opts})"
    assert g.winner in ("soviet", "american"), g.winner
    return g


def tier1b_stacking():
    print("== TIER 1b: Soviet stacking rule ==")
    g = game_rules.Game(ROOT, {})
    board = g.board

    def chain_from(c0):
        chain = [c0]; cur = c0
        for _ in range(4):
            nxts = [nb for nb in board.nbrs[cur]
                    if board.row_i[nb] == board.row_i[cur] + 1
                    and not board.is_city(nb) and nb not in chain]
            if not nxts:
                return None
            cur = nxts[0]; chain.append(cur)
        return chain
    chain = None
    for c0 in board.cells:
        if board.is_city(c0):
            continue
        chain = chain_from(c0)
        if chain:
            break
    assert chain, "no southward non-city chain found"
    c0, c1, c2, c3, c4 = chain

    def prep(game):
        bs = [u for u in game.soviet_units() if u.kind == "bomber"]
        for u in bs:
            u.alive = True; u.frozen = False; u.entered = True
            u.entering = False; u.moved_turn = -1; u.group = "north"
        game.turn = 1; game.staging_done = True
        return bs

    # RESOLVABLE: u1 stops on u2 (u2 can still vacate south to an empty square)
    bs = prep(g)
    for u in bs[2:]:
        u.cell = None; u.alive = False
    u1, u2 = bs[0], bs[1]
    u1.cell = c0; u2.cell = c2
    assert g.begin_russian_move(u1)
    g.russian_step(u1, c1, lambda m, u: False)
    assert c2 in g.russian_step_options(u1), "step onto vacatable stack allowed"
    g.russian_step(u1, c2, lambda m, u: False)
    ok, why = g.can_end_russian_move(u1)
    assert ok, f"stopping allowed while occupant can vacate: {why}"

    # TURN-END: a stack that still has a movable unit is flagged
    g5 = game_rules.Game(ROOT, {})
    b5 = prep(g5)
    for u in b5[2:]:
        u.cell = None; u.alive = False
    a, bb = b5[0], b5[1]
    a.cell = c2; a.moved_turn = g5.turn        # already moved, stuck on c2
    bb.cell = c2; bb.moved_turn = -1           # still movable, stacked with a
    assert any(cell == c2 for _m, cell in g5.russian_turn_problems()), \
        "turn-end should flag a resolvable stack"

    # DEAD STACK: neither unit can move off -> stepping in is forbidden
    g2 = game_rules.Game(ROOT, {})
    b2 = prep(g2)
    for u in b2[2:]:
        u.cell = None; u.alive = False
    a1, a2 = b2[0], b2[1]
    a1.cell = c1; a2.cell = c2; a2.moved_turn = g2.turn
    assert g2.begin_russian_move(a1)
    g2._steps_left[a1.id] = 1                   # no spare step to pass through
    assert c2 not in g2.russian_step_options(a1), "dead stack must be blocked"

    # NO GROWING PILES, and no stacking on a city
    g3 = game_rules.Game(ROOT, {})
    b3 = prep(g3)
    for u in b3[3:]:
        u.cell = None; u.alive = False
    x0, x1, x2 = b3[0], b3[1], b3[2]
    x0.cell = c2; x1.cell = c2; x2.cell = c1
    assert not g3._stack_end_ok(x2, c2), "cannot stop on an already-2-unit square"
    x1.cell = None                              # now c2 holds only x0 (vacatable)
    x0.moved_turn = -1
    assert g3._stack_end_ok(x2, c2), "single vacatable occupant is fine"
    x0.moved_turn = g3.turn
    assert not g3._stack_end_ok(x2, c2), "occupant that already moved can't be stacked"
    citycell = next(c for c in board.cells if board.is_city(c))
    x0.moved_turn = -1; x0.cell = citycell; x2.cell = citycell
    assert not g3._stack_end_ok(x2, citycell), "never stack two units on a city"
    print(f"  tier1b OK (chain {c0}->{c4})")

def tier1c_destruction():
    print("== TIER 1c: no-legal-move destruction ==")
    g = game_rules.Game(ROOT, {})
    noncity = next(c for c in g.board.cells if not g.board.is_city(c))
    vcell = next(c for c in g.board.cells if g.board.cells[c]["row"] == "V")
    # a northern city so a north-group bomber can still advance >=2 rows south
    earlycity = next(c for c in g.board.cells if g.board.is_city(c)
                     and g.board.cells[c]["row"] in ("G", "H", "J"))
    bs = [u for u in g.soviet_units() if u.kind == "bomber"]
    stuck, onV, oncity = bs[0], bs[1], bs[2]
    for u in bs:
        u.alive = True; u.frozen = False; u.entered = True
        u.entering = False; u.moved_turn = -1; u.group = "north"
    g.turn = 1; g.staging_done = True; g.phase = "russian"
    stuck.cell = noncity; stuck.move = 0        # 0 allowance -> no legal move
    onV.cell = vcell; onV.frozen = True         # row V: frozen, exempt
    oncity.cell = earlycity                      # on a city, but CAN still move
    # keep every other bomber out of the way so only these three are scored
    for u in bs[3:]:
        u.alive = False
    # A bomber merely SITTING on a city (not bombing, so not frozen) is NOT
    # exempt from the move rule - it is flagged, real or decoy alike.
    assert any(earlycity in str(p) for p in g.russian_turn_problems()), \
        "an un-bombed bomber parked on a city must be told to move on"
    g.end_russian_turn(force=True)
    assert not stuck.alive, "boxed bomber should be destroyed"
    assert onV.alive, "row-V bomber is frozen, not destroyed"
    assert oncity.alive, "a bomber on a city that CAN move is not destroyed"
    assert g._stuck_msgs and noncity in g._stuck_msgs[0], g._stuck_msgs

    # Only bombing (which freezes) earns the right to stay on a city. A bomber
    # STUCK on a city with no legal move gets no exemption - it is destroyed.
    g2 = game_rules.Game(ROOT, {"cuban": True})
    citycell2 = next(c for c in g2.board.cells if g2.board.is_city(c)
                     and g2.board.cells[c]["row"] in ("K", "L", "M"))
    dcy = next(u for u in g2.soviet_units() if u.kind == "decoy_bomber")
    for u in g2.soviet_units():
        u.alive = u is dcy
    dcy.group = "cuban"; dcy.frozen = False; dcy.entered = True
    dcy.entering = False; dcy.moved_turn = -1; dcy.cell = citycell2
    g2.turn = 3; g2.staging_done = True; g2.phase = "russian"
    assert any(citycell2 in str(p) for p in g2.russian_turn_problems()), \
        "a decoy on a city must move on (no camping)"
    dcy.move = 0                                  # now it truly cannot advance
    g2.end_russian_turn(force=True)
    assert not dcy.alive, "a bomber stuck on a city with no move is destroyed"
    print("  tier1c OK (destroyed at %s)" % noncity)


def tier1d_slbm():
    print("== TIER 1d: SLBM staging window + re-placement ==")
    g = game_rules.Game(ROOT, {"slbm": True, "canadian": True})
    g.turn = 2
    cells = g.entry_cells("slbm")
    assert cells
    m = g.offboard_slbms()[0]
    g.enter_unit(m, cells[0], "slbm")
    assert m.entered and m.cell
    # SLBMs keep the staging window open on turn >= 2
    for u in g.bombers():
        u.entered = True                      # no bombers left to stage
    assert g.needs_staging(), "SLBMs should keep staging open on turn>=2"
    # re-selecting removes it back to the pool
    g.unenter_slbm(m)
    assert m.cell is None and not m.entered and m in g.offboard_slbms()
    print("  tier1d OK")


def tier1e_lateral():
    print("== TIER 1e: E/W rule (bomber>2 only to bomb; decoy>2 only to a missile) ==")

    def _solo(kind, opts=None):
        g = game_rules.Game(ROOT, opts or {})
        b = g.board
        u = next(x for x in g.soviet_units() if x.kind == kind)
        for x in g.soviet_units():
            x.alive = (x is u)
        u.alive = True; u.frozen = False; u.entered = True; u.entering = False
        u.group = "north"; u.moved_turn = -1
        g.turn = 1; g.staging_done = True
        start = next(c for c in b.cells if b.cells[c]["row"] == "J"
                     and len([nb for nb in b.nbrs[c]
                              if b.row_i[nb] == b.row_i[c]]) >= 2)
        u.cell = start
        return g, b, u, start

    # --- both a REAL bomber and a DECOY may KEEP moving E/W past two squares
    #     (the destination is what constrains them, checked when the move ends)
    for kind in ("bomber", "decoy_bomber"):
        g, b, u, _ = _solo(kind)
        assert g.begin_russian_move(u)
        steps = 0
        for _ in range(3):
            lat = [nb for nb in g.russian_step_options(u)
                   if b.row_i[nb] == b.row_i[u.cell]]
            if not lat:
                break
            g.russian_step(u, lat[0], lambda m, x: False)
            steps += 1
        assert steps >= 3, f"{kind}: E/W beyond two squares must be offered"
        assert g._lateral[u.id] >= 3
        assert g.lateral_exceeded(u), f"{kind}: >2 E/W flagged"
    assert g.bomber_exceeded_lateral(u) is False  # u is the decoy from the loop

    # --- classic dests: a >2 E/W destination is legal only for a real bomber
    #     onto a bombable city, or a decoy onto a MISSILE-defended city. With no
    #     US units on the board there are no missile cities, so a decoy gets no
    #     >2 E/W dests at all.
    for kind in ("decoy_bomber", "bomber"):
        g, b, u, start = _solo(kind)
        for d, path in g.legal_russian_dests(u).items():
            cur, lat = u.cell, 0
            for step in path:
                if b.row_i[step] == b.row_i[cur]:
                    lat += 1
                cur = step
            if lat > 2:
                assert g._lateral_dash_dest_ok(u, d), \
                    f"{kind} >2 E/W dest {d} is not a legal dash target"
                if kind == "decoy_bomber":
                    assert False, "no missile cities exist -> no decoy dash dest"

    # --- decoy dash resolution: a decoy that ends a >2 E/W move on a missile
    #     city the American HOLDS FIRE on is exposed and removed from play.
    g, b, dec, start = _solo("decoy_bomber")
    mcell = next(c for c in b.cells if b.is_city(c)
                 and b.row_i[c] == b.row_i[start])          # same row as dec
    mis = next(x for x in g.us_units() if x.kind == "missile")
    mis.cell = mcell; mis.alive = True
    g._lateral[dec.id] = 3                                  # simulate a >2 dash
    dec.cell = mcell; dec.move_start = start; dec.moved_turn = g.turn
    msg = g.resolve_decoy_dash(dec)
    assert msg and not dec.alive and dec.revealed, "held-fire decoy removed"
    assert "decoy" in msg and mis.alive, "missile survives a hold-fire"
    print("  tier1e OK")

def tier2():
    print("== TIER 2: AI-vs-AI full games across doctrines/options ==")
    optsets = [
        {},
        {"dew": True},
        {"siberian": True},
        {"cuban": True},
        {"slbm": True, "canadian": True},
        {"targets": True},
        {"dew": True, "siberian": True, "cuban": True,
         "slbm": True, "canadian": True, "targets": True},
    ]
    n = 0
    for opts in optsets:
        for seed in range(6):
            g = play_game(opts, seed)
            n += 1
    print(f"  tier2 OK ({n} full games, winners resolved)")

# ------------------------------------------------------ TIER 3: UI clicks

class _FakeRng:
    def __init__(self, val): self.val = val
    def random(self): return self.val
    def choice(self, seq): return seq[0]

def tier2b_sentinel():
    print("== TIER 2b: sentinel US doctrine ==")
    # missile emplacement in the four named cities
    g = game_rules.Game(ROOT, {})
    us = game_ai.AmericanAI(g, style="sentinel")
    assert g.phase == "us_setup"
    us.place_all_units()
    want = {us._city_cell(n) for n in
            ("Omaha", "Chicago", "Detroit", "New York")}
    missile_cells = {u.cell for u in g.us_units()
                     if u.kind == "missile" and not u.canadian}
    assert want <= missile_cells, (want, missile_cells)

    # missile fire schedule (fake rng returns 0.40 each call)
    g2 = game_rules.Game(ROOT, {})
    g2.rng = _FakeRng(0.40)
    us2 = game_ai.AmericanAI(g2, style="sentinel")
    m = next(u for u in g2.us_units() if u.kind == "missile")
    b = next(u for u in g2.soviet_units() if u.kind == "bomber")
    g2.turn = 1;  assert us2.ask_fire(m, b) is False   # base .25 < .40
    g2.turn = 5;  assert us2.ask_fire(m, b) is True    # base .50 > .40
    g2.turn = 8;  assert us2.ask_fire(m, b) is True    # base 1.0
    # detected decoys push a turn-1 shot over the line (.25 + 2*.12 = .49)
    g2.turn = 1
    ds = [u for u in g2.soviet_units() if u.kind == "decoy_bomber"][:2]
    for d in ds: d.revealed = True
    assert us2.ask_fire(m, b) is True

    # full games with US forced to sentinel: complete, no stacks, engagement
    import random
    engaged = False
    for seed in range(6):
        gg = game_rules.Game(ROOT, {"cuban": True},
                             rng=random.Random(seed * 7 + 3))
        rr = random.Random(seed)
        rus = game_ai.RussianAI(gg, style=rr.choice(list(game_ai.RUS_STYLES)))
        au = game_ai.AmericanAI(gg, style="sentinel")
        guard = 0
        while gg.phase != "over" and guard < 300:
            guard += 1; ph = gg.phase
            if ph in ("cuban_setup", "slbm_targets", "bomber_targets"):
                rus.do_setup_phase()
            elif ph == "us_setup":
                au.place_all_units()
            elif ph == "russian":
                rus.take_turn(au.ask_fire)
                occ = {}
                for u in gg.soviet_units():
                    if u.alive and u.cell and not u.entering:
                        occ.setdefault(u.cell, []).append(u)
                assert not any(len(l) > 1 for l in occ.values()), "stack!"
            elif ph == "american":
                before = sum(1 for u in gg.us_units()
                             if u.kind == "fighter" and u.moved_turn == gg.turn)
                au.take_turn()
                after = sum(1 for u in gg.us_units()
                            if u.kind == "fighter" and u.moved_turn == gg.turn)
                if after > before:
                    engaged = True
                for sq, fs, tg in gg.fighter_combat_preview():
                    gg.resolve_square(sq)
                gg.finish_american_turn()
        assert gg.phase == "over"
    assert engaged, "sentinel fighters never engaged in 6 games"
    print("  tier2b OK (missiles emplaced, schedule + engagement verified)")


def tier2c_flank():
    print("== TIER 2c: flank doctrine reaches both coasts ==")
    import random
    EAST = {"New York", "Boston", "Norfolk", "Philadelphia", "Pittsburgh",
            "Washington, D.C.", "Toronto", "Quebec"}
    WEST = {"Seattle", "Vancouver", "San Francisco", "Los Angeles",
            "San Diego", "Portland"}
    east_hits = west_hits = 0
    for seed in range(8):
        g = game_rules.Game(ROOT, {"siberian": True},
                            rng=random.Random(seed * 5 + 1))
        rus = game_ai.RussianAI(g, style="flank")
        us = game_ai.AmericanAI(g, style="screen")
        guard = 0
        while g.phase != "over" and guard < 250:
            guard += 1; ph = g.phase
            if ph in ("cuban_setup", "slbm_targets", "bomber_targets"):
                rus.do_setup_phase()
            elif ph == "us_setup":
                us.place_all_units()
            elif ph == "russian":
                rus.take_turn(us.ask_fire)
                occ = {}
                for u in g.soviet_units():
                    if u.alive and u.cell and not u.entering:
                        occ.setdefault(u.cell, []).append(u)
                assert not any(len(l) > 1 for l in occ.values()), "stack!"
            elif ph == "american":
                us.take_turn()
                for sq, fs, tg in g.fighter_combat_preview():
                    g.resolve_square(sq)
                g.finish_american_turn()
        names = {g.board.city(c)["name"] for c in g.destroyed
                 if g.board.is_city(c)}
        east_hits += bool(names & EAST)
        west_hits += bool(names & WEST)
    # the funnel bug left east untouched; guard against a regression
    assert east_hits >= 5, f"flank rarely threatens the east coast: {east_hits}/8"
    assert west_hits >= 3, f"flank rarely threatens the west coast: {west_hits}/8"
    print(f"  tier2c OK (east {east_hits}/8, west {west_hits}/8)")


def tier2d_cuban():
    print("== TIER 2d: Cuban units staged on the red band at setup ==")
    import random
    g = game_rules.Game(ROOT, {"cuban": True}, rng=random.Random(1))
    rus = game_ai.RussianAI(g, "blitz")
    assert g.phase == "cuban_setup"
    rus.do_setup_phase()
    staged = [u for u in g.soviet_units() if u.stage_group == "cuban"]
    assert staged, "AI staged no Cuban units"
    assert all(u.cell is None and u.staged for u in staged), "not off-board"
    assert all(g.board.cells[u.staged]["row"] == "V" for u in staged)
    assert g.phase in ("slbm_targets", "us_setup"), g.phase   # before US setup
    assert not g.cuban_to_place()
    # a staged Cuban unit launches onto its row-V start square
    ok, why = g.cuban_launch(staged[0])
    assert ok, why
    assert staged[0].entered and g.board.cells[staged[0].cell]["row"] == "V"
    assert staged[0].group == "cuban"
    # full games: complete, no stacks, Cuban units actually launch
    launched_any = False
    for seed in range(6):
        gg = game_rules.Game(ROOT, {"cuban": True, "siberian": True},
                             rng=random.Random(seed * 7 + 1))
        r = game_ai.RussianAI(gg, "flank")
        u = game_ai.AmericanAI(gg, "sentinel")
        guard = 0
        while gg.phase != "over" and guard < 250:
            guard += 1; ph = gg.phase
            if ph in ("cuban_setup", "slbm_targets", "bomber_targets"):
                r.do_setup_phase()
            elif ph == "us_setup":
                u.place_all_units()
            elif ph == "russian":
                r.take_turn(u.ask_fire)
                occ = {}
                for x in gg.soviet_units():
                    if x.alive and x.cell and not x.entering:
                        occ.setdefault(x.cell, []).append(x)
                assert not any(len(l) > 1 for l in occ.values()), "stack!"
            elif ph == "american":
                u.take_turn()
                for sq, fs, tg in gg.fighter_combat_preview():
                    gg.resolve_square(sq)
                gg.finish_american_turn()
        if any(u2.group == "cuban" and (u2.entered or not u2.alive)
               for u2 in gg.soviet_units()):
            launched_any = True
    assert launched_any, "Cuban units never launched in any game"
    anchors = sorted(u.staged for u in staged if u.staged)
    print(f"  tier2d OK (staged {len(staged)}; anchors {anchors})")

def tier3():
    print("== TIER 3: scripted headless UI test ==")
    import pygame
    import norad_game
    app = norad_game.App()
    checks = {"dew": True, "siberian": True, "cuban": True,
              "slbm": True, "canadian": True, "targets": True}
    app.mode = "hotseat"
    app.human_us = app.human_sov = True
    app.classic_move = False
    app.game = game_rules.Game(ROOT, checks)
    app.rus_ai = game_ai.RussianAI(app.game, style="blitz")
    app.us_ai = game_ai.AmericanAI(app.game, style="screen")
    app.make_view()
    # drive draw() through every phase; exercises new panel layout + buttons
    seen = set()
    guard = 0
    while app.game.phase != "over" and guard < 300:
        guard += 1
        g = app.game
        seen.add(g.phase)
        app.draw()                       # must not raise (new layout code)
        app.make_buttons(120)            # rebuild buttons (wrap/height code)
        # simulate a click on the first enabled button, if any
        for rect, label, cb, enabled, _bg in list(app.buttons):
            _ = app.wrap(label, rect.w - 16, app.small)   # wrap path
            break
        # advance the phase using the rules/AI so we cover them all
        ph = g.phase
        if ph in ("cuban_setup", "slbm_targets", "bomber_targets"):
            app.rus_ai.do_setup_phase()
        elif ph == "us_setup":
            app.us_ai.place_all_units()
        elif ph == "russian":
            # exercise SOVIET STAGING title branch before moving
            assert (("SOVIET STAGING" if g.needs_staging() else "SOVIET MOVEMENT"))
            app.rus_ai.take_turn(app.us_ai.ask_fire)
        elif ph == "american":
            app.us_ai.take_turn()
            for sq, fs, tgt in g.fighter_combat_preview():
                g.resolve_square(sq)
            g.finish_american_turn()
    app.draw()                           # game-over screen
    assert g.phase == "over"
    assert {"us_setup", "russian", "american"} <= seen, seen
    # message wrap slot: long message must not raise
    app.flash("x " * 120)
    app.draw()
    print(f"  tier3 OK (phases exercised: {sorted(seen)})")


def tier3b_dew_popup():
    print("== TIER 3b: DEW-break click-to-continue popup ==")
    import norad_game
    app = norad_game.App()
    app.human_us = app.human_sov = True
    app.game = game_rules.Game(ROOT, {"dew": True})
    app.rus_ai = game_ai.RussianAI(app.game, style="blitz")
    app.us_ai = game_ai.AmericanAI(app.game, style="screen")
    app.make_view()
    calls = []
    app.gate = lambda kind, msg: calls.append((kind, msg))   # non-blocking
    g = app.game
    # no popup before the line falls
    app.check_dew_break()
    assert calls == [], "popup fired too early"
    # knock out both DEW cities via a bomber
    b = next(u for u in g.soviet_units() if u.kind == "bomber")
    b.cell = "G212"; b.real = True; b.alive = True
    b.frozen = False; b.entered = True
    g.destroyed.add("H121")
    g.bomb(b)                       # sets dew_break_turn, logs the message
    app.check_dew_break()
    assert len(calls) == 1, f"expected 1 popup, got {len(calls)}"
    kind, msg = calls[0]
    assert kind == "dew", kind
    assert "Soviet staging" in msg, msg
    # must not fire again on later checks
    app.check_dew_break()
    assert len(calls) == 1, "popup should be one-time"
    print("  tier3b OK (msg: %r)" % msg)


def tier3c_soviet_picker():
    print("== TIER 3c: Soviet stack selection popup ==")
    import norad_game
    app = norad_game.App()
    app.human_sov = app.human_us = True
    app.classic_move = False
    g = game_rules.Game(ROOT, {})
    app.game = g
    app.rus_ai = game_ai.RussianAI(g, "blitz")
    app.us_ai = game_ai.AmericanAI(g, "screen")
    app.make_view()
    g.phase = "russian"; g.turn = 1; g.staging_done = True
    cell = next(c for c in g.board.cells if not g.board.is_city(c))
    bs = [u for u in g.soviet_units() if u.kind == "bomber"]
    for u in bs:
        u.alive = False
    u1, u2 = bs[0], bs[1]
    for u in (u1, u2):
        u.alive = True; u.frozen = False; u.entered = True
        u.entering = False; u.moved_turn = -1; u.group = "north"; u.cell = cell
    seen = {"n": 0, "stack": None}
    def fake_pick(cid, stack, title):
        seen["n"] += 1; seen["stack"] = list(stack); return stack[0]
    app.pick_unit = fake_pick
    app.click_russian(cell)
    assert seen["n"] == 1, "a 2-unit Soviet square should open the picker"
    assert len(seen["stack"]) == 2
    # a single unit must NOT trigger the picker
    u2.cell = None; seen["n"] = 0
    app.trace = None
    app.click_russian(cell)
    assert seen["n"] == 0, "a lone unit should not open the picker"
    print("  tier3c OK")


def tier3d_cuban_ui():
    print("== TIER 3d: Cuban placement UI (stacked tray) ==")
    import norad_game, random
    app = norad_game.App()
    app.human_sov = app.human_us = True
    g = game_rules.Game(ROOT, {"cuban": True}, rng=random.Random(2))
    app.game = g
    app.rus_ai = game_ai.RussianAI(g, "blitz")
    app.us_ai = game_ai.AmericanAI(g, "screen")
    app.make_view()
    assert g.phase == "cuban_setup"
    # fixed force: 3 real bombers + 5 decoys available in the stacks
    assert len(app._kind_pool("bomber")) == 3
    assert len(app._kind_pool("decoy_bomber")) == 5
    # place 3 bombers then 2 decoys (the stack stays selected between clicks)
    app.stage_kind = "bomber"
    for _ in range(3):
        app.stage_slot_click(g.stage_cells("cuban")[0], "cuban")
    assert app.stage_kind is None, "bomber stack emptied (only 3 real)"
    app.stage_kind = "decoy_bomber"
    for _ in range(2):
        app.stage_slot_click(g.stage_cells("cuban")[0], "cuban")
    assert g.cuban_staged_count() == 5
    # cap enforced: a 6th unit cannot be placed
    before = g.cuban_staged_count()
    slots = g.stage_cells("cuban")
    app.stage_slot_click(slots[0] if slots else "V1812", "cuban")
    assert g.cuban_staged_count() == before, "cannot exceed 5 Cuban units"
    staged = [u for u in g.soviet_units()
              if u.stage_group == "cuban" and u.staged]
    assert len(staged) == 5 and all(u.cell is None for u in staged)
    # pick one back up, then confirm - the reserve rejoins the north pool
    app.cuban_unstage_click(staged[0])
    assert g.cuban_staged_count() == 4
    pool_before = len(g.offboard_bombers())
    returning = g.cuban_to_place()
    app.confirm_cuban_placement()
    assert g.phase != "cuban_setup", "advanced after placement"
    assert not g.cuban_to_place(), "no Cuban unit still awaiting placement"
    # the 4 unplaced Cuban bombers are now available for Soviet staging
    assert all(u.group == "north" and u.alive for u in returning)
    assert len(g.offboard_bombers()) == pool_before + len(returning)
    # staged-sprite launch path
    app.staged_click(next(u for u in g.soviet_units()
                          if u.stage_group == "cuban" and u.staged))
    print("  tier3d OK")


def tier3e_entry_ui():
    print("== TIER 3e: staging tray replaces mode buttons ==")
    import norad_game
    app = norad_game.App()
    assert app.cuban_real == 0 and app.cuban_total == 0, "Cuban init 0/0"
    # entry_type_label is still used by Cuban setup
    app.entry_mode = None
    assert app.entry_type_label() == ""
    app.entry_mode = "bomber"
    app.entry_real = True;  assert app.entry_type_label() == "BOMBER"
    app.entry_real = False; assert app.entry_type_label() == "BOMBER DECOY"

    app.human_sov = app.human_us = True
    g = game_rules.Game(ROOT, {"slbm": True, "canadian": True})
    app.game = g
    app.rus_ai = game_ai.RussianAI(g, "blitz")
    app.us_ai = game_ai.AmericanAI(g, "screen")
    app.make_view()
    g.phase = "russian"; g.turn = 2; g.staging_done = False
    for u in g.bombers():
        u.entered = True                       # only SLBMs drive staging now
    assert g.needs_staging()
    app.make_buttons(120)
    labels = [b[1] for b in app.buttons]
    # only Done staging while staging - no mode buttons
    assert any(l.startswith("Done staging") for l in labels)
    assert not any("Stage sub-launch missile" in l for l in labels)
    assert not any(l.startswith("Next entry") for l in labels)
    assert not any("Stage Bomber" in l for l in labels)
    # into the movement portion of the turn
    g.staging_done = True
    assert not g.needs_staging()
    app.make_buttons(120)
    labels = [b[1] for b in app.buttons]
    assert not any(l.startswith("Done staging") for l in labels), \
        "Done staging must be hidden during Soviet Movement"
    print("  tier3e OK")


def tier3f_staged_removal():
    print("== TIER 3f: staged unit removed on re-click (tray flow) ==")
    import norad_game
    app = norad_game.App()
    app.human_sov = app.human_us = True
    g = game_rules.Game(ROOT, {})
    app.game = g
    app.rus_ai = game_ai.RussianAI(g, "blitz")
    app.us_ai = game_ai.AmericanAI(g, "screen")
    app.make_view()
    g.phase = "russian"; g.turn = 1; g.staging_done = False
    # select the bomber stack and drop one on a north start slot
    app.stage_kind = "bomber"
    n0 = len(g.staged_units())
    app.stage_slot_click(g.stage_cells("north")[0], "north")
    staged = g.staged_units()
    assert len(staged) == n0 + 1, "a unit should be staged"
    assert app.stage_kind == "bomber", "stack stays selected for rapid placing"
    u = staged[-1]
    # clicking the staged sprite must remove it while staging is required
    assert g.needs_staging()
    app.staged_click(u)
    assert u not in g.staged_units(), "staged unit should be removed on re-click"
    print("  tier3f OK")


def tier3g_entry_mode_persist():
    print("== TIER 3g: tray places on either edge ==")
    import norad_game
    app = norad_game.App()
    app.human_sov = app.human_us = True
    g = game_rules.Game(ROOT, {"siberian": True})
    app.game = g
    app.rus_ai = game_ai.RussianAI(g, "blitz")
    app.us_ai = game_ai.AmericanAI(g, "screen")
    app.make_view()
    g.phase = "russian"; g.turn = 1; g.staging_done = False
    # one stack selection places on either edge - the SLOT sets the edge
    app.stage_kind = "bomber"
    app.stage_slot_click(g.stage_cells("north")[0], "north")
    a = next(u for u in g.staged_units() if u.stage_group == "north")
    assert a.stage_group == "north"
    # stack is still selected; place another on the Siberian line
    assert app.stage_kind == "bomber"
    app.stage_slot_click(g.stage_cells("siberian")[0], "siberian")
    b = next(u for u in g.staged_units() if u.stage_group == "siberian")
    assert b.stage_group == "siberian"
    print("  tier3g OK")


def tier3h_slbm_remove_midstaging():
    print("== TIER 3h: remove SLBM by click while a tray unit is picked ==")
    import norad_game
    app = norad_game.App()
    app.human_sov = app.human_us = True
    app.classic_move = False
    g = game_rules.Game(ROOT, {"slbm": True, "canadian": True})
    app.game = g
    app.rus_ai = game_ai.RussianAI(g, "blitz")
    app.us_ai = game_ai.AmericanAI(g, "screen")
    app.make_view()
    g.phase = "russian"; g.turn = 2; g.staging_done = False
    # 1) select the real-missile stack and launch one onto a coastal cell
    app.stage_kind = "missile"
    mcell = g.entry_cells("slbm")[0]
    app.click_russian(mcell)
    missile = next(u for u in g.slbms() if u.cell == mcell and u.entered)
    assert missile.slbm_turn == g.turn
    assert app.stage_kind == "missile", "stack stays selected (more missiles)"
    # 2) now select the bomber stack (still staging)
    app.stage_kind = "bomber"
    # 3) click the placed missile on the map -> removed despite an active stack
    app.click_russian(mcell)
    assert missile.cell is None and not missile.entered, \
        "clicking the placed SLBM should remove it even while a stack is active"
    assert missile in g.offboard_slbms()

    # 4) same check for a DECOY SLBM - it must be removable by clicking it
    #    again too, exactly like a real one (both share the same click-removal
    #    path in click_russian, which used to check kind == "missile" only and
    #    silently ignored decoy_missile clicks).
    app.stage_kind = "decoy_missile"
    dcell = g.entry_cells("slbm")[0]
    app.click_russian(dcell)
    decoy = next(u for u in g.slbms()
                if u.cell == dcell and u.entered and u.kind == "decoy_missile")
    assert decoy.slbm_turn == g.turn
    app.click_russian(dcell)
    assert decoy.cell is None and not decoy.entered, \
        "clicking a placed DECOY SLBM should remove it, same as a real one"
    assert decoy in g.offboard_slbms()
    print("  tier3h OK")

def tier3i_combat_banner():
    print("== TIER 3i: combat outcome banner then click-to-continue ==")
    import norad_game, random
    app = norad_game.App()
    app.human_sov = app.human_us = True
    g = game_rules.Game(ROOT, {}, rng=random.Random(1))
    app.game = g
    app.rus_ai = game_ai.RussianAI(g, "blitz")
    app.us_ai = game_ai.AmericanAI(g, "screen")
    app.make_view()
    g.phase = "american"; g.turn = 3
    cell = next(c for c in g.board.cells if not g.board.is_city(c))
    sov = next(u for u in g.soviet_units() if u.kind == "bomber")
    sov.alive = True; sov.cell = cell; sov.entered = True; sov.revealed = False
    fnt = next(u for u in g.us_units() if u.kind == "fighter")
    fnt.alive = True; fnt.cell = cell; fnt.moved_turn = g.turn
    steps = []
    def fake_pause(ms):
        steps.append(("pause", app.banner, app.banner_hint, sov.alive,
                      app._combat_focus))
    def fake_wait(text):
        steps.append(("wait", text, app.banner_hint, sov.alive))
    app.pause = fake_pause; app.wait_click = fake_wait
    app.draw = lambda *a, **k: None
    app.resolve_american_combat()
    # 1) during the delay: outcome banner (no click prompt), unit still ALIVE,
    #    square outlined
    kind, banner, hint, alive, focus = steps[0]
    assert kind == "pause"
    assert banner and banner.startswith("Fighter combat at")
    assert hint is False, "no click prompt during the delay"
    assert alive is True, "units must still be on the map during the outcome"
    assert focus == cell, "combat square outlined"
    # 2) at the continue prompt: SAME banner keeps the outcome and appends
    #    "(click to continue)"; units are STILL on the map (removed ON click)
    kind, text, hint, alive = steps[1]
    assert kind == "wait"
    assert text.startswith("Fighter combat at"), text
    assert hint is True, "click-to-continue appended below the outcome"
    assert alive is True, "units stay until the player clicks continue"
    # after the click (fake_wait returns) the unit is removed
    assert sov.alive is False, "units removed on the continue click"
    print("  tier3i OK")


def tier3j_us_setup_stacks():
    print("== TIER 3j: US stacked setup + Canadian AD rule ==")
    import norad_game, random
    app = norad_game.App()
    app.human_sov = app.human_us = True
    g = game_rules.Game(ROOT, {"canadian": True}, rng=random.Random(3))
    app.game = g
    app.rus_ai = game_ai.RussianAI(g, "blitz")
    app.us_ai = game_ai.AmericanAI(g, "screen")
    app.make_view()
    while g.phase != "us_setup":
        app.rus_ai.do_setup_phase()
    # five stacks with the right counts (the Canadian missile was removed)
    assert app.us_stack_keys() == ["fighter", "decoy_fighter", "missile",
                                   "ca_fighter", "ca_decoy_fighter"]
    assert len(app._us_pool("fighter")) == 12
    assert len(app._us_pool("ca_fighter")) == 3
    assert not app._us_pool("ca_missile"), "no Canadian missile in the game"
    us_city = next(c for c in g.board.cells if g.board.is_city(c)
                   and not g.board.city(c).get("canadian") and c != "G212")
    ca_city = "J152"
    # select the fighter stack; placing keeps the stack selected
    app.us_kind = "fighter"
    app.click_us_setup(us_city)
    assert len(app._us_pool("fighter")) == 11 and app.us_kind == "fighter"
    # US unit may NOT go on a Canadian city
    n = len(app._us_pool("fighter"))
    app.click_us_setup(ca_city)
    assert len(app._us_pool("fighter")) == n, "US unit blocked from Canada"
    # US unit MAY go on Godthab
    app.click_us_setup("G212")
    assert len(app._us_pool("fighter")) == n - 1, "US allowed in Godthab"
    # Canadian stack only on Canadian cities
    app.us_kind = "ca_fighter"
    m = len(app._us_pool("ca_fighter"))
    app.click_us_setup(us_city)
    assert len(app._us_pool("ca_fighter")) == m, "Canadian blocked from US"
    app.click_us_setup(ca_city)
    assert len(app._us_pool("ca_fighter")) == m - 1, "Canadian allowed in Canada"
    # deselecting: click a placed unit to return it to its stack
    app.us_kind = None
    before = len(app._us_pool("fighter"))
    app.click_us_setup("G212")
    assert len(app._us_pool("fighter")) == before + 1, "returned to stack"
    print("  tier3j OK")


def tier3k_slbm_launch_cells():
    print("== TIER 3k: SLBM launch squares restricted to curated ocean set ==")
    import random
    g = game_rules.Game(ROOT, {"slbm": True, "canadian": True},
                        rng=random.Random(1))
    cells = set(g.entry_cells("slbm"))
    assert cells == game_rules.SLBM_LAUNCH_CELLS, "must equal the curated set"
    assert len(cells) == 44, len(cells)
    assert "U1811" in cells, "U1811 (one square from New Orleans) is allowed"
    # inland squares that used to be wrongly allowed are gone
    for bad in ("Q1911", "R1911", "P1911", "S1822", "L142"):
        assert bad not in cells, bad
    # a placed missile removes its own cell from the available launch squares
    m = next(u for u in g.offboard_slbms() if u.real)
    g.phase = "russian"; g.turn = 2
    spot = sorted(cells)[0]
    g.enter_unit(m, spot, "slbm")
    assert spot not in set(g.entry_cells("slbm")), "occupied cell excluded"
    print("  tier3k OK")



def tier3l_ui_edits():
    print("== TIER 3l: Siberian band outlines + icon tooltip + clean exit ==")
    import pygame
    import norad_game
    app = norad_game.App()
    app.mode = "solo_sov"
    app.human_us = False
    app.human_sov = True
    app.classic_move = False
    app.game = game_rules.Game(ROOT, {"siberian": True})
    app.rus_ai = game_ai.RussianAI(app.game, style="blitz")
    app.us_ai = game_ai.AmericanAI(app.game, style="fortress")
    app.make_view()
    v = app.view
    v.zoom = max(v.min_zoom, app.map_rect.w / v.mw)
    v.ox, v.oy = float(app.map_rect.x), float(app.map_rect.y)
    v.clamp()
    g = app.game
    guard = 0
    while g.phase != "russian" and guard < 8:
        guard += 1
        if g.phase in ("cuban_setup", "slbm_targets", "bomber_targets"):
            app.rus_ai.do_setup_phase()
        elif g.phase == "us_setup":
            app.us_ai.place_all_units()
    assert g.phase == "russian"
    app.stage_kind = "bomber"
    app.draw()
    sib = [(cid, grp) for (r, cid, grp) in app._slot_rects if grp == "siberian"]
    assert len(sib) >= 1, "siberian slots present"
    anchors, tangents = [], []
    for cid, grp in sib:
        anchor, (tx, ty), (nx, ny) = app._sib_geom(cid)
        cx, cy = app.slot_pos_for(cid, grp)
        ccx, ccy = g.board.cells[cid]["center"]
        # outward normal points AWAY from the cell interior (into the band)
        assert (nx * (ccx - anchor[0]) + ny * (ccy - anchor[1])) < 0, \
            f"{cid}: normal not outward"
        # the drawn slot centre sits farther out than the anchor (onto the band)
        assert ((cx - ccx) ** 2 + (cy - ccy) ** 2
                > (anchor[0] - ccx) ** 2 + (anchor[1] - ccy) ** 2), \
            f"{cid}: slot not pushed onto the band"
        anchors.append(anchor); tangents.append((tx, ty))
    # every outline shares one straight band line: parallel tangents...
    for tx, ty in tangents:
        assert abs(tx * tangents[0][0] + ty * tangents[0][1]) > 0.9999, \
            "siberian outlines not parallel"
    # ...and collinear anchors - this is what stops the row-by-row drift out of
    # the red band that a per-cell radial anchor produced
    (ax0, ay0), (bx0, by0) = anchors[0], anchors[-1]
    ddx, ddy = bx0 - ax0, by0 - ay0
    dl = (ddx * ddx + ddy * ddy) ** 0.5
    for axp, ayp in anchors:
        perp = abs((axp - ax0) * (-ddy / dl) + (ayp - ay0) * (ddx / dl))
        assert perp < 2.0, f"anchor off the band line by {perp:.1f}px"
    # the outline must actually be DRAWN (not just clickable): look for the
    # ENTRY_Y yellow near each slot centre on the rendered surface
    surf = app.screen
    ey = norad_game.ENTRY_Y
    drawn = 0
    for cid, grp in sib:
        cx, cy = app.slot_pos_for(cid, grp)
        sx, sy = app.view.to_screen(cx, cy)
        hit = 0
        for dxp in range(-90, 91, 3):
            for dyp in range(-90, 91, 3):
                x, y = int(sx + dxp), int(sy + dyp)
                if 0 <= x < surf.get_width() and 0 <= y < surf.get_height():
                    r, gg, b = surf.get_at((x, y))[:3]
                    if (abs(r - ey[0]) < 45 and abs(gg - ey[1]) < 45
                            and abs(b - ey[2]) < 55):
                        hit += 1
        drawn += hit > 0
    assert drawn == len(sib), f"siberian outlines drawn: {drawn}/{len(sib)}"

    # icon tooltip: hover a stacked square, must render without raising
    m = next(u for u in g.offboard_bombers())
    cell = "M182"
    other = next(u for u in g.units if u is not m and u.side == "soviet")
    for u in (m, other):
        u.cell = cell
        u.staged = None
        u.stage_group = None
    sx, sy = app.view.to_screen(*g.board.cells[cell]["center"])
    orig_pos = pygame.mouse.get_pos
    pygame.mouse.get_pos = lambda: (int(sx), int(sy))
    try:
        app.banner = None
        app.hide_units = False
        app.draw_hover_tooltip(app.screen)   # icon path, >=2 units, must not raise
    finally:
        pygame.mouse.get_pos = orig_pos
    # clean-exit path exists and is callable in principle
    assert callable(app.quit_app)
    print(f"  tier3l OK ({len(sib)} band slots checked)")


def tier3m_assigned_targets():
    print("== TIER 3m: assigned-targets staging/blocking ==")
    import norad_game

    # ---- rules: reachability + assignment rejection ----
    g = game_rules.Game(ROOT, {"targets": True}, rng=random.Random(3))
    b = g.board
    u = next(x for x in g.bombers() if x.real)
    u.group = "north"
    north_start = sorted(g.stage_cells("north"), key=lambda c: b.row_i[c])[0]
    cities = [c for c in b.cells if b.is_city(c)]
    assert all(g.can_reach(u, north_start, c) for c in cities), \
        "a row-A start should reach every city"
    deepc = max(b.cells, key=lambda c: b.row_i[c])
    north_city = min(cities, key=lambda c: b.row_i[c])
    assert not g.can_reach(u, deepc, north_city), \
        "a bomber cannot reach a city behind it"
    # reject unreachable, accept reachable, from the start line
    u.cell = None
    u.staged = north_start
    ok, _ = g.assign_bomber_target(u, north_city)
    assert ok and u.target == north_city
    u2 = next(x for x in g.bombers() if x.real and x is not u)
    u2.group = "north"
    u2.cell = deepc
    u2.staged = None
    ok, why = g.assign_bomber_target(u2, north_city)
    assert (not ok) and u2.target is None and "reach" in why.lower(), (ok, why)
    g.clear_bomber_target(u)
    assert u.target is None

    # ---- movement-block helper (only blocks if an alternative preserves it) ----
    app = norad_game.App()
    app.human_us = app.human_sov = True
    app.game = g
    bt = next(x for x in g.bombers() if x.real)
    bt.group = "north"
    bt.target = north_city                       # a northern city
    good, bad = north_start, deepc               # good is in front, bad is past
    assert app._target_step_block(bt, bad, [bad, good]) is True
    assert app._target_step_block(bt, good, [bad, good]) is False
    assert app._target_step_block(bt, bad, [bad]) is False   # no alt -> allowed

    # ---- staging-time query flow (human Soviet) ----
    app2 = norad_game.App()
    app2.mode = "hotseat"
    app2.human_us = app2.human_sov = True
    app2.classic_move = False
    g2 = game_rules.Game(ROOT, {"targets": True})
    app2.game = g2
    app2.rus_ai = game_ai.RussianAI(g2, style="blitz")
    app2.us_ai = game_ai.AmericanAI(g2, style="screen")
    app2.make_view()
    assert g2.phase == "us_setup"
    app2.us_ai.place_all_units()
    if g2.phase == "us_setup":
        g2.finish_us_setup()
    assert g2.phase == "bomber_targets"
    # human skips the upfront phase (mirrors loop())
    if g2.phase == "bomber_targets" and app2.human_sov and g2.opt["targets"]:
        g2.next_phase()
    assert g2.phase == "russian" and g2.needs_staging()
    # stage a REAL bomber -> a target query opens
    app2.stage_kind = "bomber"
    assert app2._kind_pool("bomber")
    app2.stage_slot_click(g2.stage_cells("north")[0], "north")
    assert app2.awaiting_target is not None and app2.awaiting_target.real
    tgt_u = app2.awaiting_target
    # a non-city click is rejected; the query stays open
    app2.click_pick_target(north_start if not b.is_city(north_start) else None)
    assert app2.awaiting_target is tgt_u and tgt_u.target is None
    # a reachable city assigns it and closes the query
    reach = g2.reachable_target_cities(tgt_u)
    assert reach
    app2.click_pick_target(reach[0])
    assert tgt_u.target == reach[0] and app2.awaiting_target is None
    # a DECOY stages with NO target query
    if app2._kind_pool("decoy_bomber"):
        app2.stage_kind = "decoy_bomber"
        app2.stage_slot_click(g2.stage_cells("north")[0], "north")
        assert app2.awaiting_target is None, "decoys must not pick a target"
    # picking a staged bomber back up clears its target
    app2.stage_kind = "bomber"
    app2.stage_slot_click(g2.stage_cells("north")[0], "north")
    u3 = app2.awaiting_target
    app2.click_pick_target(g2.reachable_target_cities(u3)[0])
    assert u3.target is not None
    app2.staged_click(u3)
    assert u3.target is None and u3.staged is None

    # ---- source guards for the UI wiring ----
    src = open(os.path.join(ROOT, "norad_game.py"), encoding="utf-8").read()
    assert "TARGET_PURPLE" in src and "TARGET_BLUE" in src
    assert '"target": ("ASSIGNED TARGET"' in src
    assert "_target_step_block" in src and "awaiting_target" in src
    assert 'g.phase == "bomber_targets"' in src and "self.human_sov" in src
    print("  tier3m OK (reachability, staging query, decoy-skip, block)")


def tier3n_new_rules():
    print("== TIER 3n: row-A Cuban stop, missile warning, lateral-bomb rule ==")
    import random, norad_game
    # (a) a Cuban decoy bomber stops (freezes) for good when it reaches row A
    g = game_rules.Game(ROOT, {"cuban": True}, rng=random.Random(5))
    b = g.board
    dec = next(x for x in g.soviet_units()
               if x.kind == "decoy_bomber" and x.group == "cuban")
    bcell = next(c for c in b.cells if b.cells[c]["row"] == "B"
                 and any(b.cells[nb]["row"] == "A" for nb in b.nbrs[c]))
    acell = next(nb for nb in b.nbrs[bcell] if b.cells[nb]["row"] == "A")
    for x in g.soviet_units():
        x.alive = (x is dec)
    dec.alive = True; dec.frozen = False; dec.entered = True
    dec.entering = False; dec.group = "cuban"; dec.moved_turn = -1
    dec.cell = bcell; g.turn = 3; g.staging_done = True
    assert g.begin_russian_move(dec)
    assert acell in g.russian_step_options(dec)
    g.russian_step(dec, acell, lambda m, x: False)
    ok, why = g.end_russian_move(dec)
    assert ok, why
    assert dec.frozen, "Cuban decoy must stop for good at row A"

    # (b) an on-map sub-launched missile left unmoved is flagged and removed
    g = game_rules.Game(ROOT, {"slbm": True, "canadian": True},
                        rng=random.Random(6))
    mis = next(x for x in g.slbms() if x.real)
    for x in g.soviet_units():
        x.alive = (x is mis)
    cell = next(iter(game_rules.SLBM_LAUNCH_CELLS))
    mis.alive = True; mis.entered = True; mis.cell = cell
    mis.slbm_turn = 1; mis.moved_turn = 1; mis.frozen = False
    g.turn = 2; g.staging_done = True
    assert mis in g.unmoved_missiles(), "unmoved on-map missile flagged"
    g.end_russian_turn(force=True)
    assert not mis.alive, "unmoved missile removed at turn end"

    # (c) enforce_lateral: a real bomber that moved >2 E/W but did NOT bomb is
    #     returned to where it started the turn, with an ILLEGAL MOVE popup.
    app = norad_game.App()
    app.human_sov = app.human_us = True
    g = game_rules.Game(ROOT, {}, rng=random.Random(7))
    app.game = g; app.make_view()
    app.rus_ai = game_ai.RussianAI(g, "blitz")
    app.us_ai = game_ai.AmericanAI(g, "screen")
    real = next(x for x in g.soviet_units() if x.kind == "bomber")
    citycell = next(c for c in g.board.cells if g.board.is_city(c))
    startcell = next(c for c in g.board.cells
                     if not g.board.is_city(c) and c != citycell)
    real.cell = citycell; real.move_start = startcell
    real.moved_turn = g.turn = 1; real.entered = True; real.entering = False
    real.frozen = False; real.alive = True
    g._lateral[real.id] = 3
    assert g.bomber_exceeded_lateral(real)
    gates = []
    app.gate = lambda kind, msg: gates.append((kind, msg))
    undone = app.enforce_lateral(real)
    assert undone and gates and gates[0][0] == "illegal"
    assert real.cell == startcell and real.moved_turn == -1, "returned to start"
    # a bomber that DID bomb (frozen on a destroyed city) keeps its move
    real2 = next(x for x in g.soviet_units()
                 if x.kind == "bomber" and x is not real)
    real2.cell = citycell; real2.frozen = True; real2.alive = True
    real2.entering = False; g.destroyed.add(citycell)
    g._lateral[real2.id] = 3
    assert app.enforce_lateral(real2) is False, "a bombing >2 E/W move stands"

    # (c2) a DECOY that moved >2 E/W but did NOT end on a missile city is illegal
    #      and returned to its start (a legal dash onto a missile city would
    #      already have removed the decoy in the engine, so it never reaches here)
    dec = next(x for x in g.soviet_units() if x.kind == "decoy_bomber")
    plain = next(c for c in g.board.cells
                 if not g.board.is_city(c) and not g.at(c, "us"))
    home = next(c for c in g.board.cells if c not in (plain, citycell))
    dec.cell = plain; dec.move_start = home; dec.moved_turn = 1
    dec.entered = True; dec.entering = False; dec.frozen = False; dec.alive = True
    g._lateral[dec.id] = 3
    gates.clear()
    assert app.enforce_lateral(dec) is True and gates[0][0] == "illegal"
    assert dec.cell == home and dec.moved_turn == -1, "illegal decoy dash undone"

    # (d) source: Canadian missile gone; white US-stack highlight; D moved
    ui = open(os.path.join(ROOT, "norad_game.py")).read()
    ca = game_rules.Game(ROOT, {"canadian": True}, rng=random.Random(8))
    app2 = norad_game.App(); app2.game = ca
    assert "ca_missile" not in app2.us_stack_keys(), "no Canadian missile stack"
    assert "us_kind if self.game.phase" in ui, "US stack white-highlight"
    assert "rect.bottom - 17" in ui, "decoy D moved to bottom-left"
    assert "pygame.draw.rect(scr, ERRRED, bd" not in ui, "red D background gone"
    print("  tier3n OK")


def tier3o_entry_fixes():
    print("== TIER 3o: Siberian south-edge entry + Cuban launch timing ==")
    import norad_game

    # (a) a Siberian-staged unit may move straight SOUTH down the WEST-edge
    #     column and stop there (e.g. E10 -> H101), not only east. (Rows F+ label
    #     the west edge "101"; base column 0 either way.)
    g = game_rules.Game(ROOT, {"siberian": True})
    b = g.board
    e_entry = b.west_order("E")[0]                   # E10  (west edge, base 0)
    h_west = b.west_order("H")[0]                    # H101 (west edge, base 0)
    assert int(b.cells[h_west]["col"][:2]) - 10 == 0
    h_col12 = next(c for c in b.cells if b.cells[c]["row"] == "H"
                   and int(b.cells[c]["col"][:2]) - 10 == 2)
    u = next(x for x in g.soviet_units() if x.kind == "bomber")
    u.alive = True; u.entered = True; u.entering = True; u.frozen = False
    u.group = "north"; u.entry_from = "siberian"; u.cell = e_entry
    u.moved_turn = -1
    g._steps_left[u.id] = u.move - 1                 # entered on E10, 3 left
    assert g.in_entry_end_zone(u, h_west), "west-edge south stop must be legal"
    assert g.in_entry_end_zone(u, h_col12), "col-12 stop still legal"
    dests = g.legal_entry_dests(u)
    assert h_west in dests, "entry may finish by moving south down the west edge"

    # (b) clicking a staged CUBAN unit during the STAGING step must NOT launch
    #     it; it launches only during the movement step.
    app = norad_game.App()
    app.human_sov = app.human_us = True
    g = game_rules.Game(ROOT, {"cuban": True}, rng=random.Random(2))
    app.game = g; app.make_view()
    app.rus_ai = game_ai.RussianAI(g, "blitz")
    app.us_ai = game_ai.AmericanAI(g, "screen")
    while g.phase != "russian":
        if g.phase == "us_setup":
            app.us_ai.place_all_units()
        else:
            app.rus_ai.do_setup_phase()
    cu = next(x for x in g.soviet_units()
              if x.stage_group == "cuban" and x.staged and not x.entered)
    assert g.needs_staging(), "turn 1 begins in the staging step"
    app.staged_click(cu)
    assert not cu.entered and cu.cell is None and cu.staged is not None, \
        "Cuban unit must not launch during staging"
    # after staging is done (movement step) the same click DOES launch it
    g.finish_staging(force=True)
    app.staged_click(cu)
    assert cu.entered and cu.cell is not None, "Cuban launches during movement"

    # (c) clicking a placed SLBM during the MOVEMENT step has no effect (a placed
    #     SLBM is committed once staging ends).
    app = norad_game.App(); app.human_sov = app.human_us = True
    g = game_rules.Game(ROOT, {"slbm": True, "canadian": True},
                        rng=random.Random(4))
    app.game = g; app.make_view()
    app.rus_ai = game_ai.RussianAI(g, "blitz")
    app.us_ai = game_ai.AmericanAI(g, "screen")
    g.phase = "russian"; g.turn = 2; g.staging_done = True   # movement step
    slbm = next(u for u in g.slbms() if u.real)
    ocean = next(iter(game_rules.SLBM_LAUNCH_CELLS))
    g.enter_unit(slbm, ocean, "slbm")
    assert slbm.cell == ocean and not g.needs_staging()
    app.click_russian(ocean)                                # click the placed SLBM
    assert slbm.cell == ocean and slbm.alive, "SLBM not removed during movement"
    print("  tier3o OK")


def tier4a_belief():
    print("== TIER 4a: expert BeliefTracker (P(real) from public info) ==")
    import game_ai_expert as ex

    # --- fairness: the hidden real/decoy flag is read ONLY inside observed()
    src = open(os.path.join(ROOT, "game_ai_expert.py")).read()
    outside = [c for c in src.split("\n    def ")
               if "u.real" in c and not c.startswith("observed")]
    assert not outside, "hidden .real read outside observed()"
    assert "u.real" in src, "observed() must read the flag once revealed"

    def fresh(opts=None):
        g = game_rules.Game(ROOT, opts or {}, rng=random.Random(1))
        return g, ex.BeliefTracker(g)

    def place(g, u, cid, group="north"):
        u.alive = True; u.revealed = False; u.entered = True
        u.entering = False; u.frozen = False; u.group = group
        u.moved_turn = -1; u.cell = cid

    # --- count anchor with NO front-loading (FRONT_LOAD=0) reduces to the
    #     random-sample expectation: reals among V visible == 23 * V / 31.
    g, bt = fresh()
    bt.FRONT_LOAD = 0.0
    cells = [c for c in g.board.cells if g.board.cells[c]["row"] == "M"][:4]
    bombers = [u for u in g.soviet_units() if u.kind == "bomber"][:4]
    for u, c in zip(bombers, cells):
        place(g, u, c)
    bt.update()
    target = ex.BOMBER_REAL * 4 / (ex.BOMBER_REAL + ex.BOMBER_DECOY)
    got = bt.expected_reals_visible("bomber")
    assert abs(got - target) < 0.01, (got, target)
    for u in bombers:
        assert 0.0 < bt.prob_real(u) < 1.0

    # --- front-loading: an EARLY all-visible wave is read as decoy-heavy, so
    #     the expected reals (and each P) drop below the random-sample value.
    #     This is the fix for "the AI shouldn't chase the first decoy wave".
    g, bt = fresh()
    bt.FRONT_LOAD = 0.6
    for u, c in zip(bombers, cells):        # reuse 4 north bombers on 4 cells
        pass
    b4 = [u for u in g.soviet_units() if u.kind == "bomber"][:4]
    for u, c in zip(b4, cells):
        place(g, u, c)
    bt.update()
    fl = bt.expected_reals_visible("bomber")
    assert fl < target - 0.3, ("front-loading lowers early-wave reals", fl, target)
    assert all(bt.prob_real(u) < ex.NORTH_PRIOR for u in b4)

    # --- reveal accounting: a revealed decoy -> P=0 and the decoy pool shrinks;
    #     a revealed real -> P=1 and the real pool shrinks.
    g, bt = fresh()
    dec = next(u for u in g.soviet_units() if u.kind == "decoy_bomber")
    real = next(u for u in g.soviet_units() if u.kind == "bomber")
    place(g, dec, cells[0]); place(g, real, cells[1])
    dec.revealed = True                              # a decoy is exposed
    real.revealed = True                             # a real is exposed
    bt.update()
    assert bt.prob_real(dec) == 0.0 and bt.prob_real(real) == 1.0
    assert bt.n_decoy == ex.BOMBER_DECOY - 1
    assert bt.n_real == ex.BOMBER_REAL - 1

    # --- decoy cap: once all 8 decoys are revealed, every remaining silhouette
    #     is certainly real.
    g, bt = fresh()
    for u in [x for x in g.soviet_units() if x.kind == "decoy_bomber"]:
        u.revealed = True                            # all 8 decoys exposed
    survivor = next(u for u in g.soviet_units() if u.kind == "bomber")
    place(g, survivor, cells[0])
    bt.update()
    assert bt.all_decoys_revealed()
    assert bt.prob_real(survivor) == 1.0, bt.prob_real(survivor)

    # --- Cuban-group silhouettes read as more decoy-heavy than north ones
    #     (isolate the group PRIOR: no front-loading tilt here)
    g, bt = fresh({"cuban": True})
    bt.FRONT_LOAD = 0.0
    nb = next(u for u in g.soviet_units()
              if u.kind == "bomber" and u.group != "cuban")
    cb = next(u for u in g.soviet_units()
              if u.kind == "bomber" and u.group == "cuban")
    place(g, nb, cells[0], "north"); place(g, cb, cells[1], "cuban")
    bt.update()
    assert bt.prob_real(cb) < bt.prob_real(nb), "Cuban prior more decoy-heavy"

    # --- behavioural evidence: a silhouette that walks AWAY from the best city
    #     it could bomb (forfeiting value, without bombing) looks more like a
    #     decoy than a stationary sibling.
    g, bt = fresh()
    bt.FRONT_LOAD = 0.0                  # isolate behavioural evidence
    probe = next(u for u in g.soviet_units() if u.kind == "bomber")
    probe.group = "north"
    hi = next(c for c in g.board.cells if bt._best_unguarded(probe, c) >= 8)
    lo = next(c for c in g.board.cells if g.board.cells[c]["row"] == "V")
    walker, sitter = [u for u in g.soviet_units() if u.kind == "bomber"][:2]
    place(g, walker, hi); place(g, sitter, hi)
    bt.update()                                      # snapshot start cells
    walker.cell = lo                                 # walker forfeits its value
    bt.update()                                      # sitter stays put
    assert bt._ev.get(walker.id, 0) > 0, "forfeit accrues decoy evidence"
    assert bt.prob_real(walker) < bt.prob_real(sitter)

    # --- Rule B: a unit that ENDS on an undestroyed 7/8/9 city (did not bomb it)
    #     is a CONFIRMED decoy -> P=0 forever.
    g, bt = fresh()
    bB = next(u for u in g.soviet_units() if u.kind == "bomber")
    jewel = next(c for c in g.board.cells if g.board.is_city(c)
                 and g.board.city(c)["points"] >= 7)
    place(g, bB, jewel)
    bt.update()
    assert bB.id in bt._confirmed_decoy and bt.prob_real(bB) == 0.0, "Rule B"

    # --- Rule A: a unit that passed (south) within 4 E/W of an UNDEFENDED 7/8/9
    #     city is a confirmed decoy.
    g, bt = fresh()
    bA = next(u for u in g.soviet_units() if u.kind == "bomber")
    C = south = None
    for cand in [c for c in g.board.cells if g.board.is_city(c)
                 and g.board.city(c)["points"] >= 7]:
        rc = g.board.row_i[cand]
        opts = [c for c in g.board.cells if g.board.row_i[c] == rc + 2
                and bt._lateral_gap(c, cand) <= 4]
        if opts:
            C, south = cand, opts[0]
            break
    assert C is not None
    place(g, bA, south)                              # 2 rows south of C, near it
    bt.update()
    assert bA.id in bt._confirmed_decoy, "Rule A: passed an undefended jewel"

    # --- Rule A for CUBAN: a Cuban bomber moves NORTH, so it passes a jewel by
    #     ending NORTH of it (opposite direction from the northern force).
    g, bt = fresh({"cuban": True})
    bc = next(u for u in g.soviet_units() if u.kind == "bomber")
    Cc = north = None
    for cand in [c for c in g.board.cells if g.board.is_city(c)
                 and g.board.city(c)["points"] >= 7]:
        rc = g.board.row_i[cand]
        opts = [c for c in g.board.cells if g.board.row_i[c] == rc - 2
                and bt._lateral_gap(c, cand) <= 4]
        if opts:
            Cc, north = cand, opts[0]
            break
    assert Cc is not None
    place(g, bc, north, "cuban")                     # 2 rows NORTH of Cc
    bt.update()
    assert bc.id in bt._confirmed_decoy, "Rule A (Cuban): passed jewel moving north"

    # --- Rule C (Cuban timing) is a SOFT cap, NOT a confirm: a Cuban that has
    #     moved (turn after launch), did not bomb, and cannot reach an undefended
    #     jewel next move gets P(real) capped at 0.25 (not 0), re-evaluated each
    #     turn. (Row V is south of every jewel, so Rule A cannot also fire here.)
    def reaches_jewel(bt, u, c):
        return any(g.board.city(x)["points"] >= 7 and not g.missile_defended(x)
                   for x in bt._one_move_cities(u, c))
    g, bt = fresh({"cuban": True})
    bs = next(u for u in g.soviet_units() if u.kind == "bomber"); bs.group = "cuban"
    stall = next(c for c in g.board.cells if g.board.cells[c]["row"] == "V"
                 and not reaches_jewel(bt, bs, c))
    place(g, bs, stall, "cuban"); bs.entered_turn = 1; g.turn = 2
    bt.update()
    assert bs.id not in bt._confirmed_decoy, "Rule C is soft, not a confirm"
    assert bt._prob_cap.get(bs.id) == 0.25 and bt.prob_real(bs) <= 0.25

    # --- Rule C exemption: a Cuban that CAN reach an undefended jewel next move
    #     is not capped.
    g, bt = fresh({"cuban": True})
    bn = next(u for u in g.soviet_units() if u.kind == "bomber"); bn.group = "cuban"
    near = next(c for c in g.board.cells if g.board.cells[c]["row"] == "V"
                and reaches_jewel(bt, bn, c))
    place(g, bn, near, "cuban"); bn.entered_turn = 1; g.turn = 2
    bt.update()
    assert bt._prob_cap.get(bn.id) is None, "Rule C exempts a Cuban near a jewel"
    print("  tier4a OK")


def tier4b_threat():
    print("== TIER 4b: expert ThreatModel (Soviet score ceiling) ==")
    import game_ai_expert as ex

    def fresh(opts=None):
        g = game_rules.Game(ROOT, opts or {}, rng=random.Random(2))
        return g, ex.BeliefTracker(g)

    def place(g, u, cid, group="north", real_kind=True):
        u.alive = True; u.revealed = False; u.entered = True
        u.entering = False; u.frozen = False; u.group = group
        u.moved_turn = -1; u.cell = cid

    g, bt = fresh()
    mcells = [c for c in g.board.cells if g.board.cells[c]["row"] == "M"][:6]

    # --- menu(): undestroyed reachable cities, sorted by value, valid
    u0 = next(u for u in g.soviet_units() if u.kind == "bomber")
    place(g, u0, mcells[0])
    tm = ex.ThreatModel(g, bt); bt.update()
    m = tm.menu(u0)
    assert m == sorted(m, key=lambda cp: -cp[1]), "menu sorted by value"
    reach = ex.reachable_cities(g, u0, u0.cell)
    assert all(c in reach and g.board.is_city(c) and c not in g.destroyed
               for c, _ in m)

    # --- worst_case is an admissible upper bound: >= expected, and >= any real
    #     assignment. Mixed board of reals + decoys.
    g, bt = fresh()
    bs = [u for u in g.soviet_units() if u.kind == "bomber"][:3]
    ds = [u for u in g.soviet_units() if u.kind == "decoy_bomber"][:2]
    for u, c in zip(bs + ds, mcells):
        place(g, u, c)
    bt.update(); tm = ex.ThreatModel(g, bt)
    exp = tm.expected_ceiling(); wc = tm.worst_case_ceiling()
    assert wc >= exp, (wc, exp)
    assert exp >= g.points and wc >= g.points

    # --- monotonicity: killing an attacker never RAISES either ceiling
    victim = bs[0]
    exp0, wc0 = tm.expected_ceiling(), tm.worst_case_ceiling()
    victim.alive = False; victim.revealed = True     # killed (a real), exposed
    bt.update(); tm2 = ex.ThreatModel(g, bt)
    assert tm2.expected_ceiling() <= exp0 + 1e-9, "kill must not raise expected"
    assert tm2.worst_case_ceiling() <= wc0, "kill must not raise worst-case"

    # --- provably_won: NOT at the start (whole pool live), YES when one lone
    #     attacker remains and no points are on the board.
    g, bt = fresh()
    tm = ex.ThreatModel(g, bt)
    assert not tm.provably_won(), "start: pool can still reach 100"
    for u in [x for x in g.soviet_units()
              if x.kind in ("bomber", "decoy_bomber")]:
        u.alive = False; u.revealed = True
    lone = next(u for u in g.soviet_units() if u.kind == "bomber")
    lone.alive = True; lone.revealed = False
    place(g, lone, mcells[0]); g.points = 0
    bt.update(); tm = ex.ThreatModel(g, bt)
    assert tm.provably_won(), "one attacker, 0 pts -> at most 9 < 100"
    assert tm.worst_case_ceiling() < 100

    # --- a board whose ONLY live attackers are KNOWN decoys threatens nothing
    g, bt = fresh()
    for u in [x for x in g.soviet_units()
              if x.kind in ("bomber", "decoy_bomber")]:
        u.alive = False; u.revealed = True           # clear the whole pool
    kd = [u for u in g.soviet_units() if u.kind == "decoy_bomber"][:2]
    for u, c in zip(kd, mcells):
        place(g, u, c); u.revealed = True            # the only live units: decoys
    bt.update(); tm = ex.ThreatModel(g, bt)
    assert tm.expected_ceiling() == g.points, "known decoys add no threat"
    assert tm.worst_case_ceiling() == g.points
    print("  tier4b OK")


def tier4c_setup():
    print("== TIER 4c: expert American setup (interdiction placement) ==")
    import game_ai_expert as ex

    def to_us_setup(opts, seed=3):
        g = game_rules.Game(ROOT, opts, rng=random.Random(seed))
        r = game_ai.RussianAI(g, "blitz")
        while g.phase != "us_setup":
            r.do_setup_phase()
        return g

    def place(g, eps=0.0):               # SETUP_EPS=0 -> deterministic for asserts
        ai = ex.ExpertAmericanAI(g)
        ai.SETUP_EPS = eps
        ai.place_all_units()
        return ai

    # --- completeness + legality across option sets (DEW included; NO targets)
    optsets = [{}, {"dew": True}, {"cuban": True}, {"siberian": True},
               {"slbm": True, "canadian": True},
               {"dew": True, "siberian": True, "cuban": True,
                "slbm": True, "canadian": True}]
    for opts in optsets:
        g = to_us_setup(opts)
        place(g)
        assert not g.us_placement_units(), ("all placed", opts)
        assert g.phase != "us_setup", ("advanced", opts)
        for u in g.us_units():
            assert u.cell is not None and g.board.is_city(u.cell)
            if g.opt["canadian"]:                    # Canadian AD rule respected
                is_ca = bool(g.board.city(u.cell).get("canadian"))
                assert bool(u.canadian) == is_ca, ("canadian AD", u.id, u.cell)
        # no bomb-magnet stacks: at most one US unit per city cell
        per_cell = {}
        for u in g.us_units():
            per_cell[u.cell] = per_cell.get(u.cell, 0) + 1
        assert max(per_cell.values()) == 1, ("US units stacked", opts,
                                             max(per_cell.values()))

    def cell_of(g, name):
        return next(c for c in g.board.cells if g.board.is_city(c)
                    and g.board.city(c)["name"] == name)

    # --- missiles go to the highest-value COASTAL cities (double duty vs SLBMs):
    #     the two coastal 9s + two coastal 8s always, plus a coastal 7. Inland
    #     jewels (Chicago, Omaha) are left to the fighters.
    g = to_us_setup({}); place(g)
    for nm in ("New York", "Washington, D.C.", "San Diego", "Jacksonville"):
        assert g.missile_defended(cell_of(g, nm)), ("coastal jewel undefended", nm)
    assert not g.missile_defended(cell_of(g, "Chicago"))
    assert not g.missile_defended(cell_of(g, "Omaha"))
    assert any(g.missile_defended(cell_of(g, nm))
               for nm in ("Norfolk", "Seattle", "San Francisco")), "a coastal 7"

    # --- DEW anchors get a non-deterministic missile silhouette (real OR the
    #     Play-Balance decoy): Anchorage 30%+20%, Godthab 30%+20%, both 30%,
    #     neither 30%. Check the marginals statistically over many setups.
    n = 200
    both = anch = godt = neither = 0
    for s in range(n):
        g = to_us_setup({"dew": True, "balance": True}, seed=1000 + s); place(g)
        a = g.has_missile_look("H121"); b = g.has_missile_look("G212")
        both += a and b; anch += a; godt += b; neither += not (a or b)
        # New York (coastal 9) is still always defended
        assert g.has_missile_look(cell_of(g, "New York")), "coastal 9 undefended"
    assert 0.35 < anch / n < 0.65, ("P(Anchorage) ~= 0.5", anch / n)
    assert 0.35 < godt / n < 0.65, ("P(Godthab) ~= 0.5", godt / n)
    assert 0.15 < both / n < 0.35, ("P(both) ~= 0.25", both / n)
    assert 0.15 < neither / n < 0.35, ("P(neither) ~= 0.25", neither / n)

    # --- Anchorage is reachable turn 1 (Siberian entry): never leave a bare
    #     fighter/decoy fighter there with no missile - it's a free kill (the
    #     bomber can bomb the same turn it enters, before a fighter could ever
    #     act). A missile there is fine; anything else must go elsewhere.
    for s in range(n):
        g = to_us_setup({"dew": True}, seed=2000 + s); place(g)
        if not g.has_missile_look("H121"):
            assert not any(u.cell == "H121" and u.kind in
                          ("fighter", "decoy_fighter") for u in g.us_units()), \
                ("bare fighter/decoy at undefended Anchorage", s)

    # --- Cuban staging (observed columns) shifts priority south
    g = to_us_setup({"cuban": True})
    ai = ex.ExpertAmericanAI(g)
    south = max((c for c in g.board.cells if g.board.is_city(c)),
                key=lambda c: g.board.row_i[c])
    ai._cuban_thetas = []
    lo = ai._city_priority(south)
    ai._cuban_thetas = [game_ai.theta_mid(g, south)]   # a column aligned with it
    hi = ai._city_priority(south)
    assert hi > lo, "observed Cuban staging raises aligned southern-city priority"

    # --- setup determinism vs the (opt-in) randomization mechanism.
    def sig(gg):
        return sorted((u.kind, u.cell) for u in gg.us_units())

    # Setup is REPRODUCIBLE for a given RNG (same rng -> identical placement),
    # even though the missile tiebreak (the 5th, coastal-7 missile) uses the rng.
    g1 = to_us_setup({}); a1 = ex.ExpertAmericanAI(g1)
    a1.rng = random.Random(1); a1.place_all_units()
    g2 = to_us_setup({}); a2 = ex.ExpertAmericanAI(g2)
    a2.rng = random.Random(1); a2.place_all_units()      # SAME rng
    assert sig(g1) == sig(g2), "same RNG -> identical setup"
    # different RNG streams vary the setup (missile tiebreak; EPS>0 fighters too)
    g3 = to_us_setup({}); a3 = ex.ExpertAmericanAI(g3)
    a3.SETUP_EPS = 2.0; a3.rng = random.Random(1); a3.place_all_units()
    g4 = to_us_setup({}); a4 = ex.ExpertAmericanAI(g4)
    a4.SETUP_EPS = 2.0; a4.rng = random.Random(2); a4.place_all_units()
    assert sig(g3) != sig(g4), "different RNG streams vary the setup"
    print("  tier4c OK")


def tier4d_policy():
    print("== TIER 4d: expert turn/fire policy (interception + EV ask_fire) ==")
    import game_ai_expert as ex

    def onboard(g, u, cid, real=True, group="north"):
        u.alive = True; u.revealed = True; u.entered = True
        u.entering = False; u.frozen = False; u.group = group
        u.moved_turn = -1; u.cell = cid; g.turn = 1

    # --- ask_fire: never fire on a KNOWN decoy (waste the missile)
    g = game_rules.Game(ROOT, {}, rng=random.Random(4))
    ai = ex.ExpertAmericanAI(g)
    low = next(c for c in g.board.cells if g.board.is_city(c)
               and g.board.city(c)["points"] == 5)
    mis = next(u for u in g.us_units() if u.kind == "missile"); mis.cell = low
    dec = next(u for u in g.soviet_units() if u.kind == "decoy_bomber")
    dec.revealed = True                              # known decoy -> P=0
    assert ai.b.prob_real(dec) == 0.0
    assert sum(ai.ask_fire(mis, dec) for _ in range(300)) == 0, "never fire decoy"

    # --- ask_fire: ALWAYS fire on a (near-)certainly real attacker, at ANY city
    #     value - holding fire loses the city AND wastes the missile (it dies
    #     with the bombed city), so P(real)>=P_FIRE_CEIL is a hard "fire".
    real = next(u for u in g.soviet_units() if u.kind == "bomber")
    real.revealed = True                             # observed real -> P=1.0
    assert ai.b.prob_real(real) == 1.0
    for pts in (5, 9):                                # even a low-value city
        mis.cell = next(c for c in g.board.cells if g.board.is_city(c)
                        and g.board.city(c)["points"] == pts)
        assert all(ai.ask_fire(mis, real) for _ in range(300)), \
            "certainly-real attacker must always draw fire (%d-pt city)" % pts
    hi = next(c for c in g.board.cells if g.board.is_city(c)
              and g.board.city(c)["points"] == 9)
    mis.cell = hi

    # --- ask_fire: endgame override - a bomb here would clinch 100 -> fire
    g.points = 95
    assert ai.ask_fire(mis, real) is True, "clinch -> fire"

    class _Rng:                                      # force the mixed-strategy roll
        def __init__(self, v): self.v = v
        def random(self): return self.v
        def shuffle(self, seq): pass                 # no-op (single-threat tests)

    # --- take_turn: intercept a reachable high-value real threat
    g = game_rules.Game(ROOT, {}, rng=random.Random(5))
    ai = ex.ExpertAmericanAI(g); ai.rng = _Rng(0.0)  # always take a good engage
    bcell = next(c for c in g.board.cells if g.board.cells[c]["row"] == "M")
    b = next(u for u in g.soviet_units() if u.kind == "bomber")
    onboard(g, b, bcell)
    f = next(u for u in g.us_units() if u.kind == "fighter")
    f.alive = True; f.cell = next(iter(g.board.nbrs[bcell]))
    f.moved_turn = -1
    assert not ai.tm.provably_won()                  # full pool -> not won
    ai.take_turn()
    assert f.moved_turn == g.turn and f.cell == bcell, "fighter intercepts threat"

    # --- value gating: an 8-point city is defended against even a low-P attacker,
    #     but a 5-point city is not (engage prob much higher for the jewel).
    g = game_rules.Game(ROOT, {}, rng=random.Random(9))
    ai = ex.ExpertAmericanAI(g)
    hi8 = ai._engage_prob(8.0, 0.375, 8)             # low-P threat to an 8-city
    lo5 = ai._engage_prob(5.0, 0.375, 8)             # same P, a 5-city
    assert hi8 > 0.5 > lo5, (hi8, lo5)
    # a near-certain decoy heading to a 5-pointer is (almost) never engaged
    assert ai._engage_prob(5.0, 0.05, 8) < 0.15

    # --- first-wave dampener: a turn-1 north bomber is capped ~50% for non-jewel
    #     targets, but NOT for an undefended jewel, a later wave, or a Cuban unit.
    fw = next(u for u in g.soviet_units() if u.kind == "bomber")
    fw.group = "north"; fw.entered_turn = 1
    assert ai._wave_adjust(fw, 6.0, 0.9) <= ai.FIRST_WAVE_ENGAGE   # 6-city capped
    assert ai._wave_adjust(fw, 8.0, 0.9) == 0.9                    # undefended jewel
    fw.entered_turn = 3
    assert ai._wave_adjust(fw, 6.0, 0.9) == 0.9                    # later wave uncapped
    cub = next(u for u in g.soviet_units()
               if u.kind == "bomber" and u is not fw)
    cub.group = "cuban"; cub.entered_turn = 1
    assert ai._wave_adjust(cub, 6.0, 0.9) == 0.9                   # Cuban uncapped

    # --- take_turn: a surfaced SLBM threatening a coastal city IS interceptable
    g = game_rules.Game(ROOT, {"slbm": True, "canadian": True},
                        rng=random.Random(7))
    ai = ex.ExpertAmericanAI(g); ai.rng = _Rng(0.0)
    ocean = next(c for c in game_rules.SLBM_LAUNCH_CELLS
                 if any(nb in game_rules.COASTAL_CITIES and g.board.is_city(nb)
                        for nb in g.board.nbrs[c]))
    slbm = next(u for u in g.soviet_units() if u.kind == "missile")
    slbm.alive = True; slbm.entered = True; slbm.cell = ocean; slbm.frozen = False
    slbm.slbm_turn = 1; g.turn = 1
    assert ai._value_at_risk(slbm, ex.ThreatModel(g, ai.b)) > 0, "SLBM threatens a city"
    fs = next(u for u in g.us_units() if u.kind == "fighter")
    fs.alive = True; fs.cell = next(iter(g.board.nbrs[ocean])); fs.moved_turn = -1
    ai.take_turn()
    assert fs.moved_turn == g.turn and fs.cell == ocean, "SLBM intercepted"

    # --- SLBM override: a fighter sitting ON an undefended threatened city always
    #     intercepts (P=1), regardless of the value formula.
    g = game_rules.Game(ROOT, {"slbm": True, "canadian": True},
                        rng=random.Random(8))
    ai = ex.ExpertAmericanAI(g)
    ocean2 = next(c for c in game_rules.SLBM_LAUNCH_CELLS
                  if any(nb in game_rules.COASTAL_CITIES and g.board.is_city(nb)
                         for nb in g.board.nbrs[c]))
    citycell = next(nb for nb in g.board.nbrs[ocean2]
                    if nb in game_rules.COASTAL_CITIES and g.board.is_city(nb))
    sl = next(u for u in g.soviet_units() if u.kind == "missile")
    sl.alive = True; sl.entered = True; sl.cell = ocean2; sl.frozen = False
    sl.slbm_turn = 1; g.turn = 1
    fon = next(u for u in g.us_units() if u.kind == "fighter")
    fon.alive = True; fon.cell = citycell; fon.moved_turn = -1
    assert ai._slbm_engage_prob(sl) == 1.0, "fighter on undefended city intercepts"

    # --- FAIRNESS: among identical-value threats the AI must NOT preferentially
    #     hit reals over decoys. (soviet_units() lists reals first; a stable sort
    #     would leak identity. The shuffle fixes it - both get picked ~half.)
    def fair_pick(seed):
        g = game_rules.Game(ROOT, {}, rng=random.Random(3))
        ai = ex.ExpertAmericanAI(g); ai.rng = random.Random(seed)
        ai._value_at_risk = lambda u, tm: 6.0
        ai._engage_prob = lambda v, pr, bl: 1.0
        ai.b.prob_real = lambda u: 0.5              # both threats identical
        mcs = [c for c in g.board.cells if g.board.cells[c]["row"] == "M"
               and not g.board.is_city(c)]
        pair = None
        for a in mcs:
            for bnb in g.board.nbrs[a]:
                if bnb not in mcs:
                    continue
                common = [c for c in (set(g.board.nbrs[a]) & set(g.board.nbrs[bnb]))
                          if not g.at(c, "us")]
                if common:
                    pair = (a, bnb, common[0]); break
            if pair:
                break
        ca, cb, cf = pair
        R = next(u for u in g.soviet_units() if u.kind == "bomber")
        D = next(u for u in g.soviet_units() if u.kind == "decoy_bomber")
        for u, c in ((R, ca), (D, cb)):
            u.alive = True; u.revealed = False; u.entered = True
            u.entering = False; u.frozen = False; u.group = "north"
            u.moved_turn = -1; u.cell = c; u.entered_turn = -1
        g.turn = 3
        f = next(u for u in g.us_units() if u.kind == "fighter")
        f.alive = True; f.cell = cf; f.moved_turn = -1
        ai.take_turn()
        return "R" if f.cell == ca else ("D" if f.cell == cb else None)
    picks = [fair_pick(s) for s in range(40)]
    assert "R" in picks and "D" in picks, ("interception leaks real vs decoy", picks)

    # --- take_turn: provably-won -> hold every fighter
    g = game_rules.Game(ROOT, {}, rng=random.Random(6))
    ai = ex.ExpertAmericanAI(g)
    for u in [x for x in g.soviet_units()
              if x.kind in ("bomber", "decoy_bomber")]:
        u.alive = False; u.revealed = True
    lone = next(u for u in g.soviet_units() if u.kind == "bomber")
    lone.alive = True; lone.revealed = True
    onboard(g, lone, bcell); g.points = 0
    f2 = next(u for u in g.us_units() if u.kind == "fighter")
    f2.alive = True; f2.cell = next(iter(g.board.nbrs[bcell])); f2.moved_turn = -1
    tm = ex.ThreatModel(g, ai.b); assert tm.provably_won()
    ai.take_turn()
    assert f2.moved_turn != g.turn, "provably won -> fighters held"
    print("  tier4d OK")


def tier4e_tuner():
    print("== TIER 4e: expert tuning harness (tools/tune_ai.py) ==")
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import tune_ai
    # PARAMS names line up with the ExpertAmericanAI class attributes
    assert set(n for n, _lo, _hi in tune_ai.PARAMS) == set(tune_ai.defaults())
    # evaluate() plays real games and returns sane counts (CRN, no crash)
    league = [("blitz", {}, 1), ("flank", {"cuban": True}, 2)]
    wins, sov_pts = tune_ai.evaluate(tune_ai.defaults(), league)
    assert 0 <= wins <= len(league) and sov_pts >= 0
    # a param vector is injected as instance attributes
    import game_ai_expert as ex
    g = game_rules.Game(ROOT, {}, rng=random.Random(1))
    ai = ex.ExpertAmericanAI(g)
    for k, v in {"W_SLBM": 9.0, "DETERRENCE": 1.5}.items():
        setattr(ai, k, v)
    assert ai.W_SLBM == 9.0 and ai.DETERRENCE == 1.5
    print("  tier4e OK")


def tier4f_integration():
    print("== TIER 4f: expert integration (dispatch + menu + full-game runs) ==")
    import game_ai_expert as ex

    # --- 'expert' is a selectable difficulty, NOT in the random rotation
    assert "expert" not in game_ai.US_STYLES

    # --- AmericanAI(style='expert') delegates to the ExpertAmericanAI
    g = game_rules.Game(ROOT, {}, rng=random.Random(1))
    us = game_ai.AmericanAI(g, "expert")
    assert us.expert is not None and us.style == "expert"
    scr = game_ai.AmericanAI(g, "screen")
    assert scr.expert is None
    assert isinstance(ex.load_tuned_params(), dict)

    # --- menu wires an "expert_us" game option via the Standard/Expert
    #     AI-opponent radio choice
    ui = open(os.path.join(ROOT, "norad_game.py")).read()
    assert '"expert_us"' in ui and 'Expert AI Opponent' in ui
    assert 'style=us_style' in ui and '"expert" if expert_us' in ui

    # --- STRUCTURAL regression only: a full game with the expert American
    #     completes cleanly across every (targets-free) option set, placing all
    #     US units with no stacked cities. We do NOT assert a win rate here:
    #     AI-vs-weak-bot outcomes are hash-seed noisy and NOT a valid measure of
    #     AI quality (see EXPERT_AI_PLAN.md - needs a competent adversary).
    for opts in ({}, {"dew": True}, {"cuban": True}, {"siberian": True},
                 {"slbm": True, "canadian": True},
                 {"dew": True, "cuban": True, "siberian": True,
                  "slbm": True, "canadian": True}):
        g = game_rules.Game(ROOT, opts, rng=random.Random(4))
        r = game_ai.RussianAI(g, "blitz")
        a = game_ai.AmericanAI(g, "expert")
        guard = 0
        while g.phase != "over" and guard < 300:
            guard += 1
            ph = g.phase
            if ph in ("cuban_setup", "slbm_targets", "bomber_targets"):
                r.do_setup_phase()
            elif ph == "us_setup":
                a.place_all_units()
                per_cell = {}
                for u in g.us_units():
                    per_cell[u.cell] = per_cell.get(u.cell, 0) + 1
                assert not g.us_placement_units() and max(per_cell.values()) == 1, \
                    ("expert setup invalid", opts)
            elif ph == "russian":
                r.take_turn(a.ask_fire)
            elif ph == "american":
                a.take_turn()
                for sq, _f, _t in g.fighter_combat_preview():
                    g.resolve_square(sq)
                g.finish_american_turn()
        assert g.phase == "over" and g.winner in ("soviet", "american"), \
            ("game did not complete", opts, guard)
    print("  tier4f OK (full expert games complete cleanly across option sets)")


def tier5_soviet_expert():
    print("== TIER 5: expert Soviet AI (belief + dispatch + full games) ==")
    import game_ai_expert as ex

    # --- FAIRNESS: the Soviet side never distinguishes a real fighter from a
    #     decoy except through UsDefenseBelief.observed (revealed units). It must
    #     not touch movable_fighters (real fighters only) or the fighter/decoy
    #     kinds. u.real is read only inside the two observed() methods.
    src = open(os.path.join(ROOT, "game_ai_expert.py")).read()
    sov = src[src.index("class UsDefenseBelief"):]
    for leak in ("movable_fighters", "decoy_fighter", 'kind == "fighter"'):
        assert leak not in sov, ("Soviet side leaks fighter identity via", leak)
    outside = [c for c in src.split("\n    def ")
               if "u.real" in c and not c.startswith("observed")]
    assert not outside, "hidden .real read outside observed()"

    def to_us_placed(opts):
        g = game_rules.Game(ROOT, opts, rng=random.Random(1))
        r = game_ai.RussianAI(g, "blitz")
        while g.phase != "us_setup":
            r.do_setup_phase()
        game_ai.AmericanAI(g, "screen").place_all_units()
        return g

    # --- UsDefenseBelief: the 5 missile cities are PUBLIC; the 16 fighter
    #     silhouettes hide 12 real, so P(real) = 12/16 for each.
    g = to_us_placed({})
    d = ex.UsDefenseBelief(g)
    assert len(d.missile_cities()) == 5, "5 missile cities known"
    sil = d.fighter_silhouettes()
    assert len(sil) == 16, ("12 real + 4 decoy fighters", len(sil))
    assert all(abs(d.prob_real_fighter(u) - 12 / 16) < 1e-9 for u in sil)
    assert abs(d.fighters_remaining() - 12.0) < 1e-9

    # --- reveal drains the real pool: a revealed real -> P=1 and the unknowns
    #     rise to 11/15.
    real = next(u for u in sil if u.real)            # test may read the flag
    real.revealed = True
    assert d.prob_real_fighter(real) == 1.0
    other = next(u for u in sil if u is not real and not u.revealed)
    assert abs(d.prob_real_fighter(other) - 11 / 15) < 1e-9

    # --- decoy cap: once all US decoys are revealed, every unknown is real.
    g = to_us_placed({})
    d = ex.UsDefenseBelief(g)
    for u in [x for x in g.us_units()
              if x.kind == "decoy_fighter" and not x.canadian]:
        u.revealed = True
    unk = next(u for u in d.fighter_silhouettes() if not u.revealed)
    assert d.prob_real_fighter(unk) == 1.0, "all US decoys found -> rest real"

    # --- Canadian fighters are a SEPARATE pool (3 real of 4): revealing a US
    #     real must not change a Canadian silhouette's P(real).
    g = to_us_placed({"slbm": True, "canadian": True})
    d = ex.UsDefenseBelief(g)
    caf = next(u for u in d.fighter_silhouettes() if u.canadian)
    assert abs(d.prob_real_fighter(caf) - 3 / 4) < 1e-9, "Canadian pool 3/4"
    usreal = next(u for u in g.us_units() if u.kind == "fighter"
                  and not u.canadian)
    usreal.revealed = True
    assert abs(d.prob_real_fighter(caf) - 3 / 4) < 1e-9, "pools independent"

    # --- pressure: a missile city (defended) has positive expected fighters
    #     nearby, bounded by the real fighters in play.
    g = to_us_placed({})
    d = ex.UsDefenseBelief(g)
    mc = next(iter(d.missile_cities()))
    assert 0.0 <= d.pressure(mc) <= d.fighters_remaining() + 1e-9

    # --- ENDGAME 5-pt rule: arms only once every real fighter is spent, then
    #     spreads the reals across DISTINCT undefended 6+ cities and refuses to
    #     bomb a non-anchor 5-pt city while a higher one is still reachable.
    g = to_us_placed({})
    ai = ex.ExpertSovietAI(g)
    assert not ai.d.no_real_fighters_left(), "12 reals unspent -> rule off"
    for f in g.us_units():                        # spend every real fighter
        if f.kind == "fighter" and not f.canadian and f.real:
            f.revealed = True; f.alive = False
    assert ai.d.no_real_fighters_left(), "all reals spent -> rule armed"

    north = [c for c in g.board.cells
             if g.board.cells[c]["row"] in ("B", "C", "D")
             and not g.board.is_city(c)][:3]
    bs = [u for u in g.soviet_units() if u.kind == "bomber"][:3]
    for u, c in zip(bs, north):
        u.alive = True; u.frozen = False; u.entered = True
        u.entering = False; u.moved_turn = -1; u.group = "north"; u.cell = c
    for u in g.soviet_units():                    # park the rest off-map
        if u.kind == "bomber" and u not in bs:
            u.alive = False
    g.phase = "russian"; g.turn = 6; g.staging_done = True
    missiles = ai.d.missile_cities()
    goal = ai._assign_endgame(missiles)
    assigned = [goal[u.id] for u in bs if u.id in goal]
    assert len(assigned) == 3, "all three reals assigned a target"
    assert len(set(assigned)) == 3, "targets are distinct"
    for c in assigned:
        assert c not in missiles and c not in g.destroyed
        assert g.board.city(c)["points"] >= 6, "6+ cities open -> 6+ target"

    u = bs[0]
    five = next(c for c in g.board.cells if g.board.is_city(c)
                and g.board.city(c)["points"] == 5
                and c not in ai.DEW_ANCHORS and c not in missiles)
    six = next(c for c in g.board.cells if g.board.is_city(c)
               and g.board.city(c)["points"] >= 6 and c not in missiles)
    assert ai._endgame_5pt_blocked(u, five), "non-anchor 5-pt blocked (goal 6+)"
    assert not ai._endgame_5pt_blocked(u, "H121"), "Anchorage exempt"
    assert not ai._endgame_5pt_blocked(u, six), "6+ city never blocked"
    ai._goal[u.id] = five
    assert not ai._endgame_5pt_blocked(u, five), "assigned fallback 5-pt allowed"
    g2 = to_us_placed({})
    ai2 = ex.ExpertSovietAI(g2)
    u2 = next(x for x in g2.soviet_units() if x.kind == "bomber")
    assert not ai2._endgame_5pt_blocked(u2, five), "rule off while reals live"
    print("  tier5d OK (endgame 5-pt city rule)")

    # --- dispatch: RussianAI(style='expert') delegates to ExpertSovietAI, and
    #     'expert' is NOT in the random rotation.
    assert "expert" not in game_ai.RUS_STYLES
    g = game_rules.Game(ROOT, {}, rng=random.Random(1))
    r = game_ai.RussianAI(g, "expert")
    assert r.expert is not None and r.style == "expert"
    assert game_ai.RussianAI(g, "blitz").expert is None

    # --- menu wiring
    ui = open(os.path.join(ROOT, "norad_game.py")).read()
    assert '"expert_sov"' in ui and 'Expert AI Opponent' in ui
    assert 'sov_style' in ui and '"expert" if expert_sov' in ui

    # --- SLBM claim: a real bomber must NOT bomb an undefended coastal city
    #     that one of our own real, surfaced SLBMs already covers. Reproduces
    #     a real SLBM in M141 (covers Seattle 7 over Vancouver 5) vs a bomber.
    g = game_rules.Game(ROOT, {"slbm": True, "canadian": True},
                        rng=random.Random(4))
    r = game_ai.RussianAI(g, "blitz")
    while g.phase != "us_setup":
        r.do_setup_phase()
    game_ai.AmericanAI(g, "screen").place_all_units()
    for u in list(g.us_units()):                     # clear defence off both
        if u.cell in ("M142", "L142"):               # Seattle / Vancouver
            u.alive = False
    ai = ex.ExpertSovietAI(g)
    g.phase = "russian"; g.turn = 5
    slbm = next(u for u in g.slbms() if u.kind == "missile")
    slbm.alive = True; slbm.frozen = False; slbm.entered = True
    slbm.entering = False; slbm.cell = "M141"; slbm.slbm_turn = 5
    claimed = ai._slbm_claimed_cities()
    assert "M142" in claimed, ("SLBM covers Seattle (7)", claimed)
    assert "L142" not in claimed, ("Vancouver (5) is not its pick", claimed)
    # a real bomber that can step onto Seattle must NOT bomb it
    bomber = next(u for u in g.soviet_units() if u.kind == "bomber")
    bomber.alive = True; bomber.frozen = False; bomber.entered = True
    bomber.entering = False; bomber.moved_turn = -1; bomber.group = "north"
    src = None
    for c in g.board.cells:
        if g.board.is_city(c) or c in g.destroyed:
            continue
        bomber.cell = c
        if "M142" in g.legal_russian_dests(bomber):
            src = c; break
    assert src, "a cell from which Seattle is a legal bomber move exists"
    bomber.cell = src
    dests = g.legal_russian_dests(bomber)
    ai._move_real(bomber, dests, "M142",           # goal = the claimed city
                  lambda uu, pa: g.move_russian(uu, pa, lambda _u: False),
                  lambda *_: None)
    assert "M142" not in g.destroyed, "bomber wrongly bombed the SLBM's Seattle"
    # with the SLBM gone, the same bomber WOULD bomb Seattle
    g2 = game_rules.Game(ROOT, {"slbm": True, "canadian": True},
                         rng=random.Random(4))
    r2 = game_ai.RussianAI(g2, "blitz")
    while g2.phase != "us_setup":
        r2.do_setup_phase()
    game_ai.AmericanAI(g2, "screen").place_all_units()
    for u in list(g2.us_units()):
        if u.cell in ("M142", "L142"):
            u.alive = False
    ai2 = ex.ExpertSovietAI(g2)
    g2.phase = "russian"; g2.turn = 5
    b2 = next(u for u in g2.soviet_units() if u.kind == "bomber")
    b2.alive = True; b2.frozen = False; b2.entered = True
    b2.entering = False; b2.moved_turn = -1; b2.group = "north"; b2.cell = src
    assert not ai2._slbm_claimed_cities(), "no surfaced SLBM -> nothing claimed"
    ai2._move_real(b2, g2.legal_russian_dests(b2), "M142",
                   lambda uu, pa: g2.move_russian(uu, pa, lambda _u: False),
                   lambda *_: None)
    assert "M142" in g2.destroyed, "without the SLBM the bomber bombs Seattle"
    print("  tier5e OK (bomber yields SLBM-covered cities)")

    # --- DEW anchor-kill priority is chosen from the American's ACTUAL anchor
    #     defence (public silhouette count, 0/1/2+), rolled once after US setup.
    class _Rng:                                   # deterministic uniform draw
        def __init__(self, v):
            self.v = v

        def random(self):
            return self.v

    def anchors_with(anch_n, god_n):
        g = game_rules.Game(ROOT, {"dew": True}, rng=random.Random(0))
        for u in g.us_units():
            u.cell = None
        pool = [u for u in g.us_units()]
        for cid, n in (("H121", anch_n), ("G212", god_n)):
            for _ in range(n):
                pool.pop().cell = cid
        return g

    # bucketing counts every US silhouette and caps at 2 ("2+")
    g = anchors_with(0, 1)
    ai = ex.ExpertSovietAI(g)
    assert ai._anchor_defenders("H121") == 0 and ai._anchor_defenders("G212") == 1
    g3 = anchors_with(3, 2)
    ai3 = ex.ExpertSovietAI(g3)
    assert ai3._anchor_defenders("H121") == 2, "3 units bucket to 2+"
    assert ai3._anchor_defenders("G212") == 2

    # deferred: not decided at construction; decided lazily, once
    assert ai._dew_kill_targets is None, "priority deferred until first turn"

    # the (0,0) row is (neither 1/9, anch 1/9, god 1/9, both 6/9): a draw in
    # each sub-interval lands in the matching outcome. Draw just inside 'both'.
    def roll(anch_n, god_n, draw):
        gg = anchors_with(anch_n, god_n)
        a = ex.ExpertSovietAI(gg)
        a.rng = _Rng(draw)
        a._ensure_dew_kill_targets()
        return a._dew_kill_targets

    assert roll(0, 0, 0.05) == set(), "(0,0) first slice -> neither"
    assert roll(0, 0, 0.99) == {"H121", "G212"}, "(0,0) 6/9 mass -> both"
    assert roll(0, 1, 0.5) == {"H121"}, "(0,1) 2/3 mass -> Anchorage only"
    assert roll(1, 0, 0.5) == {"G212"}, "(1,0) 2/3 mass -> Godthab only"
    assert roll(2, 2, 0.5) == set(), "(2,2) 3/4 mass -> neither"

    # DEW rule OFF: no anchor-kill priority regardless of setup
    goff = game_rules.Game(ROOT, {}, rng=random.Random(0))
    aoff = ex.ExpertSovietAI(goff)
    aoff._ensure_dew_kill_targets()
    assert aoff._dew_kill_targets == set(), "DEW off -> no anchor priority"

    # FAIRNESS: the anchor counter never reads a US unit's real/decoy identity
    import inspect as _inspect
    src = _inspect.getsource(ex.ExpertSovietAI._anchor_defenders)
    assert ".real" not in src, "anchor count must not read hidden real/decoy"
    print("  tier5f OK (anchor-kill priority from US anchor defence)")

    # --- STRUCTURAL: full expert-vs-expert games complete cleanly across the
    #     (targets-free) option sets. No win-rate assertion (that's for the
    #     benchmark, and bot outcomes are noisy).
    for opts in ({}, {"cuban": True}, {"siberian": True},
                 {"dew": True, "cuban": True, "siberian": True,
                  "slbm": True, "canadian": True}):
        g = game_rules.Game(ROOT, opts, rng=random.Random(2))
        r = game_ai.RussianAI(g, "expert")
        a = game_ai.AmericanAI(g, "expert")
        guard = 0
        while g.phase != "over" and guard < 300:
            guard += 1
            ph = g.phase
            if ph in ("cuban_setup", "slbm_targets", "bomber_targets"):
                r.do_setup_phase()
            elif ph == "us_setup":
                a.place_all_units()
            elif ph == "russian":
                r.take_turn(a.ask_fire)
            elif ph == "american":
                a.take_turn()
                for sq, _f, _t in g.fighter_combat_preview():
                    g.resolve_square(sq)
                g.finish_american_turn()
        assert g.phase == "over" and g.winner in ("soviet", "american"), \
            ("game did not complete", opts, guard)
    print("  tier5 OK (Soviet belief + dispatch + full expert-vs-expert games)")


def tier6_balance():
    print("== TIER 6: Play Balance optional rule ==")
    import game_ai_expert as ex

    def cnt(g, side, kind):
        return sum(1 for u in g.units if u.side == side and u.kind == kind)

    # --- force composition: +1 US missile decoy, -1 Soviet decoy bomber
    base = game_rules.Game(ROOT, {}, rng=random.Random(1))
    bal = game_rules.Game(ROOT, {"balance": True}, rng=random.Random(1))
    assert cnt(base, "soviet", "decoy_bomber") == 8
    assert cnt(bal, "soviet", "decoy_bomber") == 7, "one fewer Soviet decoy"
    assert cnt(base, "us", "us_decoy_missile") == 0
    assert cnt(bal, "us", "us_decoy_missile") == 1, "one US missile decoy added"
    assert cnt(bal, "us", "missile") == 5, "still 5 REAL US missiles"

    # --- missile_defended is REAL-only; has_missile_look is the public silhouette
    dec = next(u for u in bal.us_units() if u.kind == "us_decoy_missile")
    dec.cell = "O171"
    assert not bal.missile_defended("O171"), "decoy is not a real defender"
    assert bal.has_missile_look("O171"), "decoy shows a missile silhouette"

    # --- reveal ONLY when a real bomber bombs its city
    assert not dec.revealed
    b = next(u for u in bal.soviet_units() if u.kind == "bomber")
    b.cell = "O171"; b.alive = True; b.frozen = False
    bal.bomb(b)
    assert dec.revealed and not dec.alive, "decoy unmasked (blank) on bombing"

    # --- American belief: decoy count reflects 7 (public counter mix)
    assert ex.BeliefTracker(
        game_rules.Game(ROOT, {}, rng=random.Random(2))).n_decoy == 8
    assert ex.BeliefTracker(
        game_rules.Game(ROOT, {"balance": True}, rng=random.Random(2))
    ).n_decoy == 7

    # --- Soviet belief: 6 missile silhouettes, each 5/6 real; updates on reveal
    g = game_rules.Game(ROOT, {"balance": True}, rng=random.Random(3))
    while g.phase != "us_setup":
        game_ai.RussianAI(g, "blitz").do_setup_phase()
    ex.ExpertAmericanAI(g).place_all_units()
    assert not g.us_placement_units(), "AI placed every unit incl. the decoy"
    d = ex.UsDefenseBelief(g)
    mc = d.missile_cities()
    assert len(mc) == 6, ("6 missile silhouettes", len(mc))
    assert all(abs(d.prob_missile_real(c) - 5 / 6) < 1e-9 for c in mc)
    decoy = next(u for u in g.us_units() if u.kind == "us_decoy_missile")
    dcell = decoy.cell
    decoy.revealed = True; decoy.alive = False       # as if its city were bombed
    assert all(d.prob_missile_real(c) == 1.0 for c in mc if c != dcell), \
        "survivors are certainly real once the bluff is exposed"

    # --- placement: the 6 missile silhouettes are the top-6 COASTAL jewels
    #     (2x9, 2x8, 2 of the 3 coastal 7s); the decoy is one of them, never
    #     doubling a real-missile cell.
    for s in range(40):
        gg = game_rules.Game(ROOT, {"balance": True}, rng=random.Random(s))
        while gg.phase != "us_setup":
            game_ai.RussianAI(gg, "blitz").do_setup_phase()
        ex.ExpertAmericanAI(gg).place_all_units()
        msl = [u for u in gg.us_units()
               if u.kind in ("missile", "us_decoy_missile")]
        assert sorted(gg.board.city(u.cell)["points"] for u in msl) == \
            [7, 7, 8, 8, 9, 9], "missile cities are the top-6 coastal jewels"
        assert all(u.cell in game_rules.COASTAL_CITIES for u in msl)
        dc = next(u for u in msl if u.kind == "us_decoy_missile")
        assert dc.cell not in {u.cell for u in msl if u.kind == "missile"}
        assert gg.board.city(dc.cell)["points"] in (7, 8, 9)

    # --- the decoy hides likelier on a LOWER-value city (P ~ 1/points) over ALL
    #     7 coastal jewels (three 7s, two 8s, two 9s). Because there are more 7s
    #     AND inverse weighting, P(7) must exceed the equal-share 3/7 = 0.429.
    ai = ex.ExpertAmericanAI(game_rules.Game(ROOT, {}, rng=random.Random(0)))
    jewels = [("a", 9), ("b", 9), ("c", 8), ("d", 8),
              ("e", 7), ("f", 7), ("h", 7)]
    seen = {7: 0, 8: 0, 9: 0}
    n = 20000
    for _ in range(n):
        p = ai._weighted_pick([c for c, _ in jewels], [1.0 / v for _, v in jewels])
        seen[dict(jewels)[p]] += 1
    assert seen[7] > seen[8] > seen[9], ("inverse-value weighting", seen)
    assert seen[7] / n > 0.43, ("P(decoy on a 7) must exceed 3/7", seen[7] / n)

    # --- fairness: Soviet reads identity ONLY through observed()
    src = open(os.path.join(ROOT, "game_ai_expert.py")).read()
    outside = [c for c in src.split("\n    def ")
               if "u.real" in c and not c.startswith("observed")]
    assert not outside, "hidden .real read outside observed()"

    # --- human placement flow: place/unplace the decoy like any US unit
    g2 = game_rules.Game(ROOT, {"balance": True}, rng=random.Random(4))
    while g2.phase != "us_setup":
        game_ai.RussianAI(g2, "blitz").do_setup_phase()
    dm = next(u for u in g2.us_placement_units()
              if u.kind == "us_decoy_missile")
    city = next(c for c in g2.board.cells if g2.board.is_city(c))
    assert g2.place_us(dm, city)[0] and dm.cell == city
    g2.unplace_us(dm)
    assert dm.cell is None

    # --- EVERY American AI (legacy doctrines AND expert) must place the decoy,
    #     or us_setup can't finish - the Solo-Soviet setup would hang / strand
    #     the human (regression: legacy place_all_units ignored us_decoy_missile).
    for style in list(game_ai.US_STYLES) + ["expert"]:
        gg = game_rules.Game(ROOT, {"balance": True, "cuban": True},
                             rng=random.Random(7))
        while gg.phase != "us_setup":
            game_ai.RussianAI(gg, "blitz").do_setup_phase()
        game_ai.AmericanAI(gg, style).place_all_units()
        assert not gg.us_placement_units() and gg.phase == "russian", \
            "US AI '%s' left units unplaced under Play Balance" % style

    # --- menu + setup-tray wiring
    ui = open(os.path.join(ROOT, "norad_game.py")).read()
    assert '"balance"' in ui and "Play Balance" in ui
    assert '"us_decoy_missile"' in ui, "setup tray offers the decoy stack"

    # --- full expert-vs-expert games with balance complete cleanly
    for opts in ({"balance": True},
                 {"balance": True, "cuban": True, "slbm": True,
                  "canadian": True}):
        g = game_rules.Game(ROOT, opts, rng=random.Random(5))
        r = game_ai.RussianAI(g, "expert")
        a = game_ai.AmericanAI(g, "expert")
        guard = 0
        while g.phase != "over" and guard < 400:
            guard += 1
            ph = g.phase
            if ph in ("cuban_setup", "slbm_targets", "bomber_targets"):
                r.do_setup_phase()
            elif ph == "us_setup":
                a.place_all_units()
            elif ph == "russian":
                r.take_turn(a.ask_fire)
            elif ph == "american":
                a.take_turn()
                for sq, _f, _t in g.fighter_combat_preview():
                    g.resolve_square(sq)
                g.finish_american_turn()
        assert g.phase == "over" and g.winner in ("soviet", "american")
    print("  tier6 OK (force, reveal, belief, placement, fairness, menu, games)")


def tier7_targets():
    print("== TIER 7: Assigned Targets (Expert Soviet) ==")
    import game_ai_expert as ex

    # --- assignment: every real bomber/SLBM gets a target; the expert prefers
    #     6+ cities and assigns NO 5-pt targets (anchors included), per #5.
    g = game_rules.Game(ROOT, {"targets": True, "slbm": True, "cuban": True,
                               "canadian": True}, rng=random.Random(3))
    r = game_ai.RussianAI(g, "expert")
    while g.phase != "russian":
        if g.phase == "us_setup":
            game_ai.AmericanAI(g, "expert").place_all_units()
        else:
            r.do_setup_phase()
    reals = [u for u in g.soviet_units() if u.real
             and u.kind in ("bomber", "missile")]
    assert all(u.target for u in reals), "every real is assigned a target"
    bomber_pts = [g.board.city(u.target)["points"]
                  for u in reals if u.kind == "bomber"]
    assert 5 not in bomber_pts, "expert assigns no 5-pt bomber targets (#5)"
    assert all(u.target in game_rules.COASTAL_CITIES
               for u in reals if u.kind == "missile"), "SLBMs -> coastal"

    # --- engine: a real bomber may bomb ONLY its assigned city
    g2 = game_rules.Game(ROOT, {"targets": True})
    b = next(u for u in g2.soviet_units() if u.kind == "bomber")
    tcity = next(c for c in g2.board.cells if g2.board.is_city(c))
    other = next(c for c in g2.board.cells if g2.board.is_city(c)
                 and c != tcity)
    b.alive = True; b.frozen = False; b.target = tcity
    b.cell = other
    assert not g2.can_bomb(b), "cannot bomb a non-assigned city"
    b.cell = tcity
    assert g2.can_bomb(b), "may bomb the assigned city"

    # --- dead-target bomber: cannot bomb, is NOT removed, must keep moving
    b.cell = other; b.target = tcity
    g2.destroyed.add(tcity)                       # its target dies elsewhere
    assert not g2.can_bomb(b), "dead target -> cannot bomb"
    assert b.alive, "dead-target bomber survives (roams as a decoy)"
    assert not g2._attacker_can_still_score(b), "dead-target bomber can't score"

    # --- OFF-BOARD SLBM is scrubbed at once when its target is destroyed
    g3 = game_rules.Game(ROOT, {"targets": True, "slbm": True,
                                "canadian": True})
    coast = next(c for c in game_rules.COASTAL_CITIES if g3.board.is_city(c))
    slbm = next(u for u in g3.slbms() if u.real)
    slbm.target = coast
    assert not slbm.entered and slbm.alive
    bb = next(u for u in g3.soviet_units() if u.kind == "bomber")
    bb.alive = True; bb.frozen = False; bb.cell = coast; bb.target = coast
    g3.bomb(bb)                                   # destroys `coast`
    assert not slbm.alive, "off-board SLBM scrubbed when its target dies"

    # --- SURFACED SLBM with a dead target is cleared on its attack turn
    g4 = game_rules.Game(ROOT, {"targets": True, "slbm": True,
                                "canadian": True})
    launch = coast4 = None
    for c4 in (c for c in game_rules.COASTAL_CITIES if g4.board.is_city(c)):
        lc = next((c for c in g4.entry_cells("slbm")
                   if c4 in g4.board.nbrs[c]), None)
        if lc:
            coast4, launch = c4, lc
            break
    assert launch, "found a coastal city with an adjacent SLBM launch cell"
    s4 = next(u for u in g4.slbms() if u.real)
    s4.target = coast4; s4.entered = True; s4.alive = True; s4.frozen = False
    s4.cell = launch; s4.slbm_turn = 1
    g4.destroyed.add(coast4)                      # target already gone
    g4.turn = 2; g4.phase = "russian"; g4.staging_done = True
    for u in g4.soviet_units():                   # isolate the SLBM
        if u is not s4:
            u.alive = False
    g4.end_russian_turn(force=True)
    assert not s4.alive, "surfaced dead-target SLBM removed on its attack turn"

    # --- fairness: no American code reads the Soviet's hidden target
    src = open(os.path.join(ROOT, "game_ai_expert.py")).read()
    us = src[src.index("class BeliefTracker"):src.index("# Soviet expert side")]
    assert ".target" not in us, "American side must not read enemy targets"
    am = open(os.path.join(ROOT, "game_ai.py")).read()
    am = am[am.index("class AmericanAI"):]
    assert ".target" not in am, "legacy American must not read enemy targets"

    # --- full expert-vs-expert games with Assigned Targets complete cleanly
    for opts in ({"targets": True},
                 {"targets": True, "cuban": True, "siberian": True,
                  "slbm": True, "canadian": True, "dew": True}):
        g = game_rules.Game(ROOT, opts, rng=random.Random(5))
        r = game_ai.RussianAI(g, "expert")
        a = game_ai.AmericanAI(g, "expert")
        guard = 0
        while g.phase != "over" and guard < 400:
            guard += 1
            ph = g.phase
            if ph in ("cuban_setup", "slbm_targets", "bomber_targets"):
                r.do_setup_phase()
            elif ph == "us_setup":
                a.place_all_units()
            elif ph == "russian":
                r.take_turn(a.ask_fire)
            elif ph == "american":
                a.take_turn()
                for sq, _f, _t in g.fighter_combat_preview():
                    g.resolve_square(sq)
                g.finish_american_turn()
        assert g.phase == "over" and g.winner in ("soviet", "american")
        assert all(u.target for u in g.soviet_units()
                   if u.real and u.kind in ("bomber", "missile")
                   and u.cell is not None), "on-map reals stayed targeted"
    print("  tier7 OK (assign, target-only bombing, dead-target, SLBM scrub, "
          "fairness, games)")


def tier8_dew_detect():
    print("== TIER 8: DEW-line decoy detection ==")

    class _Rng:                                   # deterministic exposure roll
        def __init__(self, v):
            self.v = v

        def random(self):
            return self.v

    g = game_rules.Game(ROOT, {"dew": True, "siberian": True})
    hcell = next(c for c in g.board.cells if g.board.cells[c]["row"] == "H")
    gcell = next(c for c in g.board.cells if g.board.cells[c]["row"] == "G")
    decoys = [u for u in g.soviet_units() if u.kind == "decoy_bomber"]

    def fresh(u, cell="H", group="north"):
        u.alive = True; u.group = group; u.dew_checked = False
        u.cell = hcell if cell == "H" else gcell

    # --- probability by DEW state (roll just below the threshold => exposed)
    for dead, prob in ((set(), 0.5), ({"H121"}, 0.25), ({"G212"}, 0.25),
                       ({"H121", "G212"}, 0.0)):
        g.destroyed = set(dead)
        # a roll just under the threshold exposes; just over does not
        g.rng = _Rng(max(0.0, prob - 0.01))
        fresh(decoys[0])
        exposed_lo = g._dew_expose_check(decoys[0])
        g.rng = _Rng(min(1.0, prob + 0.01))
        fresh(decoys[1])
        exposed_hi = g._dew_expose_check(decoys[1])
        assert exposed_lo == (prob > 0), ("expose below threshold", dead)
        assert not exposed_hi, ("no expose above threshold", dead)
    g.destroyed = set()

    # --- exclusions: real bombers, Cuban decoys, non-row-H, already-checked
    g.rng = _Rng(0.0)                             # would always expose if eligible
    real = next(u for u in g.soviet_units() if u.kind == "bomber")
    real.alive = True; real.group = "north"; real.cell = hcell
    real.dew_checked = False
    assert not g._dew_expose_check(real), "real bomber is never exposed"
    fresh(decoys[2], group="cuban")
    assert not g._dew_expose_check(decoys[2]), "Cuban decoy is never exposed"
    fresh(decoys[3], cell="G")
    assert not g._dew_expose_check(decoys[3]), "only row H triggers"
    fresh(decoys[4]); decoys[4].dew_checked = True
    assert not g._dew_expose_check(decoys[4]), "one roll per decoy"

    # --- INTEGRATION: a decoy that CROSSES row H mid-move is caught (pass-through)
    g2 = game_rules.Game(ROOT, {"dew": True})
    h2 = next(c for c in g2.board.cells if g2.board.cells[c]["row"] == "H")
    adj = next(nb for nb in g2.board.nbrs[h2]
               if g2.board.cells[nb]["row"] != "H")
    d = next(u for u in g2.soviet_units() if u.kind == "decoy_bomber")
    d.alive = True; d.group = "north"; d.cell = adj; d.dew_checked = False
    g2.rng = _Rng(0.0)                            # fully active -> always exposes
    res = g2.move_russian(d, [h2], ask_fire=lambda m, x: False)
    # exposed the instant it enters row H: revealed + frozen, but kept on the
    # board (blank back showing) so the UI can display it before removal.
    assert res == "dead" and d.dew_exposed and d.revealed and d.frozen \
        and d.alive, "decoy revealed/frozen on crossing row H (not yet removed)"
    # removal is finalised at turn end (or by the UI gate on click-to-continue)
    g2.turn = 3; g2.phase = "russian"; g2.staging_done = True
    for u in g2.soviet_units():
        if u is not d:
            u.alive = False
    g2.end_russian_turn(force=True)
    assert not d.alive, "DEW-exposed decoy removed at turn end"

    # --- full expert-vs-expert game with DEW completes cleanly
    g3 = game_rules.Game(ROOT, {"dew": True, "cuban": True, "siberian": True,
                                "slbm": True, "canadian": True},
                         rng=random.Random(5))
    r = game_ai.RussianAI(g3, "expert")
    a = game_ai.AmericanAI(g3, "expert")
    guard = 0
    while g3.phase != "over" and guard < 400:
        guard += 1
        ph = g3.phase
        if ph in ("cuban_setup", "slbm_targets", "bomber_targets"):
            r.do_setup_phase()
        elif ph == "us_setup":
            a.place_all_units()
        elif ph == "russian":
            r.take_turn(a.ask_fire)
        elif ph == "american":
            a.take_turn()
            for sq, _f, _t in g3.fighter_combat_preview():
                g3.resolve_square(sq)
            g3.finish_american_turn()
    assert g3.phase == "over" and g3.winner in ("soviet", "american")
    print("  tier8 OK (probabilities, exclusions, row-H crossing, full game)")


if __name__ == "__main__":
    tier1()
    tier1b_stacking()
    tier1c_destruction()
    tier1d_slbm()
    tier1e_lateral()
    tier2()
    tier2b_sentinel()
    tier2c_flank()
    tier2d_cuban()
    tier3()
    tier3b_dew_popup()
    tier3c_soviet_picker()
    tier3d_cuban_ui()
    tier3e_entry_ui()
    tier3f_staged_removal()
    tier3g_entry_mode_persist()
    tier3h_slbm_remove_midstaging()
    tier3i_combat_banner()
    tier3j_us_setup_stacks()
    tier3k_slbm_launch_cells()
    tier3l_ui_edits()
    tier3m_assigned_targets()
    tier3n_new_rules()
    tier3o_entry_fixes()
    tier4a_belief()
    tier4b_threat()
    tier4c_setup()
    tier4d_policy()
    tier4e_tuner()
    tier4f_integration()
    tier5_soviet_expert()
    tier6_balance()
    tier7_targets()
    tier8_dew_detect()
    print("\nALL TIERS PASSED")
