# NORAD PC Game — Session Handoff Notes

Read this first when picking up work on this project. It captures state,
design decisions, and quirks that are not obvious from the code.

## What this is
A Python/pygame Windows adaptation of NORAD (1977 Mishler board game).
Fully playable: hot seat, solo vs Soviet AI, solo vs American AI, all six
optional rules, AI doctrines per side (picked randomly at start, revealed
at game over). Run: `python norad_game.py` (needs `pip install pygame`).
The user runs it directly from this folder.

## Files
- `norad_game.py` — pygame UI: menu, phases, panel, tracing movement,
  staging, popup unit picker, animations, click-to-continue gates.
- `game_rules.py` — rules engine, no pygame. All rule interpretations are
  marked NOTE:. Board geometry (fan apex, row radii, column angles) is
  embedded as constants measured from the map scan.
- `game_ai.py` — RussianAI (blitz/feint/flank) and AmericanAI
  (fortress/screen/picket/sentinel). AIs only use information a human
  would have (silhouettes/positions, never hidden decoy status).
- `test_norad.py` — the headless regression suite (see Testing). Run it
  after every change: `SDL_VIDEODRIVER=dummy python test_norad.py`.
- `tools/build_grid.py` — regenerates `data/grid.json` (543 squares, 37
  cities) from `NORAD map.jpg`. `tools/extract_units.py` — rebuilds
  `assets/units/` from the counter-sheet scans.
- `grid_verification.png` — labeled overlay the user approved.

## AI doctrines
- Soviet blitz/feint/flank. flank = split the wave between the east and
  west edges: each unit enters the flank aligned with its TARGET's side of
  the map (angular/theta alignment in _entry_key, NOT raw BFS distance -
  the Siberian line reaches far south and used to pull every wave west),
  then hits peripheral/coastal cities on both coasts. Verified east+west
  both threatened (tier2c).
- Real bombers now GRAB the best reachable city and bomb it each turn
  (take_turn movers loop) instead of only bombing when their chosen goal was
  directly reachable - previously (esp. feint, bomber_def=0.45) real bombers
  could drift all the way to row V without bombing. Bomb fires on any
  can_bomb(u) after the move, not only res=="arrived". Verified 0 idle real
  bombers on row V across doctrines; feint avg ~94 pts.
- American fortress/screen/picket/sentinel. sentinel = probabilistic:
  missiles emplace on Omaha/Chicago/Detroit/New York; hold-fire schedule
  25% before turn 5, then 50/75/100% on turns 5/6/7+; fighters roll
  33/66/100% to engage the 1st/2nd/3rd silhouette that closes within 6
  squares; every DETECTED (revealed) decoy adds +12% to all those odds.
  Per-fighter engagement memory on the AI instance (_fseen/_fcount).
  ask_fire() implements the missile schedule; _take_turn_prob() the
  fighters. Doctrines are in the random rotation, revealed at game over.

## Key design decisions (user-confirmed)
- Column naming: 10 (west) … 21 (east); splits at row F (3-digit) and
  row P (4-digit); suffix 1 = west half. Anchorage is H121; Boston O192 /
  Denver P1612 verified.
- City point values from the printed map where it disagrees with the
  rules-PDF appendix: LA 6, Jacksonville 8, Detroit 8 (added). 37 cities.
- 12 US fighters, 5 missiles, 4 decoys. Real backs = explosion scan;
  decoy backs blank. Soviet back sheet scan is MIRRORED vs front. All
  units of a type are pixel-identical canonical tiles.
- Soviet counts (in assets/units/manifest.json): 23 bombers + 8 decoy
  bombers (sov_decoy_1..8; sov_decoy_8 reuses the decoy tile), and 4
  sub-launched missiles = 3 real + 1 decoy (the 4th is flagged decoy at
  load in Game.__init__; sov_missile_5 was removed).
- Soviet entry (STACKED TRAY): during staging the panel shows ONE stack tile
  per unit type via draw_kind_stacks() - real bombers, decoy bombers, real
  missiles, decoy missile - each with an "xN" count and a red "D" badge on
  decoy stacks; empty stacks are hidden. Clicking a stack selects that TYPE
  (self.stage_kind, a kind string), and the selection PERSISTS: you then click
  destinations repeatedly and each click places the next unit of that kind
  until the stack empties (then stage_kind clears). _kind_pool(kind) returns
  the off-board units of that kind for the current phase. Destinations:
  bombers/decoys -> a yellow red-band start slot (the SLOT picks the edge:
  north OR Siberian, both highlighted while a bomber stack is held);
  missiles -> a coastal launch square. Then Done staging and fly each staged
  unit on (row A / westernmost Siberian square is FIRST move square, stop
  B-D). stage_slot_click places bombers; the stage_kind branch in
  click_russian places missiles (enter_unit group="slbm"). min 4/turn still
  enforced by finish_staging. _stage_tray_rects = [(rect, kind)];
  _slot_rects = [(rect, cid, group)].
- DEW Line: when Anchorage+Godthab fall, on-map units unaffected, NO
  staging for the next two turns, then staging is ON row H, row I is the
  first move square, stop J-L. Break shows a "DEW LINE DESTROYED"
  click-to-continue popup (check_dew_break / announce once via
  _dew_announced).
- Game runs past 100 points until the last bomber is spent; winner is
  then Soviet if >=100 else American.
- Movement: trace square-by-square by default (classic jump is a menu
  option). Moved units get a yellow outline. Units face polar north (main
  Soviet force faces south; cuban/US/backs face north).
- Bomber E/W limit: a bomber/decoy may move at most LATERAL_LIMIT (=2)
  squares east/west per turn. After 2 lateral (same-row) steps only
  forward moves are offered. Enforced in russian_step/russian_step_options
  (self._lateral per unit, reset in begin_russian_move) AND in the classic
  legal_russian_dests BFS (4-tuple state carries lateral). Entry moves are
  exempt (Siberian units slide east onto the board). This fixed a hang
  where a unit sidestepped 3+ and could neither stop nor continue.
- Stacking: two Soviet units may share a square only transiently. Stop
  allowed onto an empty square or one holding ONE unit that can still move
  off (resolvable); never onto a 2-unit square or an occupied city.
  _stack_end_ok / _occupant_can_vacate (bounded BFS, no recursion).
  RussianAI runs a per-turn cleanup pass; cuban/slbm entry avoids occupied
  cells. Verified 0 leftover stacks over 175 games.
- No-legal-move rule: a bomber/decoy that must advance but has no legal
  move (direction or stacking) is destroyed at Soviet turn end
  (_destroy_stuck_units); a row-V bomber is exempt (frozen). UI shows a
  "SOVIET UNIT LOST" click-to-continue popup (announce_stuck). ~0/game in
  AI play; mainly a human/edge-congestion safeguard.
- SLBMs: 3 real + 1 decoy; coastal targets only. Placed directly on a
  coastal-adjacent cell (enter_unit, slbm_turn=turn); fire next turn.
  SLBMs keep the staging window open (needs_staging includes offboard
  SLBMs on turn>=2). Re-clicking a just-placed SLBM removes it for
  re-placement (unenter_slbm) - handled EARLY in click_russian so it works
  even while a stack is selected.
  SLBM launch legality ("ocean square one from a coastal city"): grid.json has
  no land/ocean flag, so the valid squares were enumerated by the user and hard
  -coded as SLBM_LAUNCH_CELLS (43 cells). entry_cells("slbm") = those minus
  soviet-occupied. Locked by tier3k. To adjust, edit SLBM_LAUNCH_CELLS.
- Canadian Air Defense (opt["canadian"], forced on by slbm): can_place_us now
  restricts American units to US cities or Godthab (NOT Canadian cities) and
  Canadian units to Canadian cities. AI place_all_units already splits
  us_cities/ca_cities; _fighter_spots "north" picket now drawn from us_cities
  so it never violates the rule. Canadian cities: J152,L142,L171,M192,N191;
  Godthab=G212 (canadian flag False, so admitted for US).
- US/Canada SETUP now uses the SAME stacked tray as the Soviets (no staging):
  us_stack_keys() -> fighter/decoy_fighter/missile (+ ca_ variants when
  Canadian); _us_pool(key) is the unplaced pool; self.us_kind is the selected
  stack (persistent); click_us_setup places the next unit on a legal green
  city and keeps the stack selected; clicking a placed unit with NO stack
  selected returns it. draw_kind_stacks badges decoys by unit.real (a white
  "D" in the tile's bottom-left, works both sides); there is NO Canadian "C"
  badge (removed Jul 8). tray_unit/tray_rect_for are now dead. Locked by tier3j.
- Hover tooltip (draw_hover_tooltip): mouse over any square to see its units as
  ICONS (not a text list). Each icon is the same sprite the map draws via
  self.sprite(u.id, isz, 0, unit_shows_back(u)), so there is no new identity
  leak: US decoys are pixel-identical to fighters, and unrevealed Soviet units
  show the plain front silhouette. A 2px border tags side (blue US/Canada, red
  Soviet); a small header shows the cell id + city name.
- Entry trace: valid STOP squares (rows B-D) are now outlined GREEN (was
  ENTRY_Y yellow) to match normal-move highlighting.
- Cuban units: STAGED on the red band adjacent to row V (the "Optional
  Russian Start Line"), exactly like north/Siberian bands - off-board
  slots (u.staged set, u.cell None), drawn one cell-height SOUTH of row V
  via slot_pos_for(cid,"cuban") (k=(r+155)/r). stage_cells("cuban") =
  row-V CUBAN_ENTRY_COLS anchors = cols 1812,1821,1822,1911,1912 (5 wide,
  1812->1912). The Cuban force is now FIXED: 3 real + 5 decoy bombers are set
  aside from the main pool at construction (_init_cuban_force, cuban_ready
  True from start; setup_cuban() is a no-arg back-compat shim). No composition
  step. cuban_setup uses the SAME stacked tray: two stacks (bombers x3,
  decoys x5), click a stack then click yellow row-V slots; at most CUBAN_MAX
  (=5) may be staged (the 5 slots + a guard enforce it). Click a staged sprite
  to pick it back up (cuban_unstage_click). Confirm Cuban placement ->
  finish_cuban_setup, which RETIRES (alive=False) any unplaced reserve (the 3
  leftovers) so they never score/block, then advances (before US setup).
  During Soviet turns each staged Cuban unit is LAUNCHED
  onto its row-V anchor by clicking its sprite (staged_click ->
  cuban_launch); it holds row V that turn and advances NORTH after. Cuban
  bombers never freeze on row V. needs_staging / the staged-must-launch
  turn-end check consider only north/Siberian. RussianAI: do_setup_phase
  stages them, take_turn launches them (its 1b north-launch loop SKIPS
  stage_group=="cuban"). Rules: place_cuban(->stage_unit) / cuban_launch /
  cuban_to_place / cuban_start_cells / finish_cuban_setup.
- Siberian staging OUTLINES follow the red "OPTIONAL RUSSIAN START LINE" band,
  not polar north. The band is NOT the westmost radial - it is a straight strip
  a few degrees off it, so a per-cell radial anchor drifts out of the band by
  row H. Instead SIB_BAND_LINE holds two points on the band centreline (map-px
  coords, fitted from the scan, ~3.7px residual). _sib_geom(cid) PROJECTS the
  cell's west-edge midpoint onto that line -> (anchor, band tangent T, outward
  normal N = side away from cell centre). slot_pos_for("siberian") puts the
  square's EAST edge at the anchor (= band midline) and extends it outward:
  centre = anchor + N*(half - SIB_BAND_MID), SIB_BAND_MID=0 (>0 nudges the east
  edge inward). The slot-draw loop MUST draw the polygon for BOTH the siberian
  and polar branches (a regression once left it only in the polar `else`, so the
  outline was invisible but still clickable). If the map scan/grid changes,
  re-fit SIB_BAND_LINE. Locked by tier3l (parallel + collinear-anchor + drawn).
- Assigned Targets (opt["targets"]) REVAMPED for the human Soviet: targets are
  no longer picked in the upfront `bomber_targets` phase (that phase is auto-
  skipped for the human in loop(); the AI still uses it via auto_assign_targets).
  Instead, staging a REAL bomber (north/Siberian/Cuban - stage_slot_click) opens
  a target query: self.awaiting_target holds the bomber, click() is locked to the
  map, and click_pick_target(cid) assigns via game_rules.assign_bomber_target.
  One target per bomber; decoys never pick; a city may be shared. SLBMs are
  UNCHANGED (they keep the slbm_targets phase). Targets are locked once a bomber
  begins movement; picking a staged bomber back up (staged_click/cuban_unstage/
  Esc) clears its target and re-queries. Outlines: every targeted city is drawn
  PURPLE during russian+cuban_setup; the bomber currently traced/selected turns
  its target BLUE (back to purple when the move ends); during a target query the
  reachable cities are outlined GREEN (reachable_cities_cached).
  Reachability: game_rules.can_reach(u, from_cell, target) is a multi-turn BFS
  respecting direction (_step_ok), the 2/turn lateral limit, and legal stops
  (_dest_geom_ok), ignoring stacking. assign_bomber_target rejects a city
  unreachable from the bomber's start line (the "can't target what you can't
  reach from staging" rule). During movement the player is BLOCKED (pop-up via
  gate("target", ...)) from stepping/stopping onto a square from which the target
  becomes unreachable - implemented in the UI (_target_step_block), which only
  blocks when a target-preserving alternative exists (so a bomber can never be
  trapped with no legal move; if the target is already lost, moves are allowed).
  Trace step highlights and classic sel_dests both drop blocked squares. Colours
  TARGET_PURPLE/TARGET_BLUE. All covered by tier3m.
- Language: never say "real" in output; say bomber/decoy.

## Panel layout & UI notes (draw_panel, rebuilt every frame)
- "Units entered this turn: N" counts staged (not-yet-flown-on) north/
  Siberian units PLUS entered_this_turn, so it ticks up when a unit is
  staged, not when it moves onto the board (draw_panel, russian phase).
- Top-down: title, turn/points (no "/100"), unrevealed tally, phase info,
  then a FIXED-height pop-up message slot ABOVE the buttons, then the
  action buttons, then the shortcuts panel, then the log.
- Pop-up sits in a fixed slot so buttons/shortcuts below don't jump.
- Shortcuts panel is PINNED at SHORTCUTS_Y (=560), distinct TABS_BG bg,
  pushed lower only if buttons would overlap; clamped up on short windows.
- Buttons: make_buttons() add(label, cb, enabled, bg, gap); tuple is
  (rect,label,cb,enabled,bg) - 5 fields (update every unpack if you touch
  it). Next-entry toggle uses NEXT_BG and an extra gap.
- entry_mode/entry_type_label/toggle_entry_real/entry_real, set_entry,
  do_entry_click, cuban_real/cuban_total are now DEAD (no longer wired to any
  UI - Cuban uses the stacked tray). Kept as harmless helpers; a few tier1
  source asserts still reference their names. Remove together if cleaning up.
- Staging buttons removed in favour of the stacked tray (see Soviet entry);
  during russian staging only "Done staging (N staged)" shows, then "End
  Soviet turn" once moves are done; cuban_setup shows "Confirm Cuban placement
  (n/5 placed)". _stage_tray_rects=[(rect,kind)] (panel stacks) and
  _slot_rects=[(rect,cid,group)] (map slots) drive it; draw_kind_stacks()
  renders the stacks (count + D badge + selection outline).
- Clicking a staged sprite removes/launches it even while an entry group
  is active (click routing checks _staged_rects regardless of entry_group).
- Combat: resolve_american_combat shows ONE banner per square. Step 1: set
  self.banner = combat_outcome(sq) ("Fighter combat at XXX: a bomber/decoy
  destroyed.") with self.banner_hint=False, outline the square (_combat_focus,
  COMBAT_FOCUS) and leave the units on the map; draw + pause 1600ms. Step 2:
  banner_hint=True and wait_click(outcome) leave the units on the map with
  the outcome shown, and resolve_square(sq) removes them ON the continue click
  (not after the delay). The SAME banner KEEPS the outcome text and appends
  "(click to continue)" beneath it (it is not shown as a separate/second
  banner). draw_banner renders banner lines in med/bright and only the
  auto-appended "(click to continue)" hint small/dim. Do NOT use flash() (it
  renders only in the side panel = "no popup"). Locked by tier3i. Missile
  interceptions during Soviet movement now log "Missile combat at {cell}: a
  bomber/decoy destroyed." (_missile_kill) for consistent wording, shown via
  gate("combat"). After AI American movement a click-to-continue popup says
  "...Click to resolve combat." only when there is combat.
- Soviet stacks of 2+ open a pick_unit popup (like US stacks). Game-over
  button is "Exit game" -> self.quit_app (pygame.quit() THEN sys.exit()).
  Calling bare sys.exit() left SDL initialised and hung the window on Windows
  (looked frozen); quit_app matches the QUIT/ESC handlers.

## Testing (test_norad.py, in the project root)
ROOT is now derived from __file__ (was a hardcoded per-session sandbox path
that broke across sessions). Run: `SDL_VIDEODRIVER=dummy python test_norad.py`
(needs pygame; the tiers
cover rules, the stacking rule, no-move destruction, SLBM window, E/W
limit, 42 AI-vs-AI full games across doctrines/options with a no-stack
assertion, sentinel doctrine, flank both-coasts, Cuban staging, and
scripted UI tests incl. Cuban placement, Soviet stack picker, entry-mode
persistence, mid-staging SLBM removal, and tier3l: Siberian band-aligned
staging outlines + icon hover tooltip + clean-exit path). ALWAYS run after changes; add a
tier for any new behavior. The suite asserts on source strings too, so
renames/refactors may require updating an assertion.

## Known quirks / warnings for the assistant
- SESSION Jul 8 (rules/UI batch). All covered by tier3n (+ tier1e rewrite).
  (1) Canadian missile REMOVED from the game (dropped from manifest.json and
  from us_stack_keys); the Canadian force is fighters + decoys only.
  (2) American setup now white-outlines the SELECTED tray stack (draw_kind_stacks
  keys off us_kind in us_setup, stage_kind elsewhere; white vs HILITE yellow).
  (3) Bomber E/W rule changed: a REAL bomber (kind "bomber") may move MORE than
  LATERAL_LIMIT (=2) squares east/west in a turn ONLY IF it bombs a city that
  turn; DECOYS are still hard-capped at 2. russian_step_options relaxes the cap
  for real bombers; legal_russian_dests/can_reach allow the extra lateral only
  onto a bombable city / the assigned target (_can_bomb_at). The "must bomb"
  half is enforced in the UI: after the move+bomb resolves, App.enforce_lateral
  checks game.bomber_exceeded_lateral(u) and, if it did not bomb, aborts the
  move back to the turn-start square with an "ILLEGAL MOVE" gate. move_russian
  now records _lateral so the classic path is checked too; launch_staged zeroes
  _lateral (entry is exempt). AI stays legal by construction (dests are filtered)
  plus a bomb-after-move in the transient-stack cleanup loop.
  (4) If the Soviet turn ends with an on-map sub-launched missile that has not
  moved (game.unmoved_missiles()), end_russian pops a yes/no warning that it
  will be removed. (The engine already removed unmoved attack-window SLBMs.)
  (5) Decoy tray badge: the red-background "D" is gone; a plain white "D" now
  sits in the tile's BOTTOM-LEFT (draw_kind_stacks).
  (6) Cuban decoy (and real) bombers flying north FREEZE when they reach row A
  (end_russian_move / move_russian, mirroring the row-V freeze); _dest_geom_ok
  lets a cuban unit stop on row A with a single-row step.
  (7) Cuban optional rule: unplaced Cuban bombers/decoys are no longer retired -
  finish_cuban_setup sets their group back to "north" so offboard_bombers() (and
  thus Soviet staging) picks them up (works for BOTH north and Siberian slots,
  since group "north" units are eligible for either edge - stage_group records
  the chosen edge). NOTE ON "REVERTS": a cloud sync on this folder was writing
  conflict copies ("game_rules (# Name clash ... #).py") and occasionally
  restored a stale snapshot over a just-saved edit. The user REMOVED the cloud
  sync on Jul 8 and the conflict copies were deleted; if an edit ever seems to
  vanish again, re-read the file and check for new "Name clash" copies.
- SESSION Jul 8b (decoy E/W rule amended). tier1e rewritten; tier3n extended.
  The decoy lateral cap is relaxed like the bomber's: a DECOY may now move MORE
  than 2 squares E/W, but ONLY to end on a city holding a live American missile
  (game.missile_defended(cid) / _lateral_dash_dest_ok). This is a bait play:
  the missile fires (both die via _missile_kill) OR the American holds fire and
  the decoy - which cannot bomb - is exposed and REMOVED (resolve_decoy_dash,
  called at the end of end_russian_move AND move_russian; message "The American
  holds fire ... exposed as a decoy and removed from play", shown via a combat
  gate in trace_click_soviet / classic click_russian / animate_soviet_move when
  the move leaves the unit not-alive). An ILLEGAL decoy dash (>2 E/W ending
  anywhere without a missile) is caught by App.enforce_lateral (now handles both
  kinds; uses game.lateral_exceeded). legal_russian_dests filters decoy >2 E/W
  dests to missile cities, so the AI stays legal. Supersedes the earlier
  "decoys can never move >2 E/W" note.
- SESSION Jul 5 (Assigned Targets revamp): human Soviet now assigns each real
  bomber's target AT STAGING (click a city), not in an upfront phase; purple/blue
  target outlines; movement is blocked (pop-up) when it would make a target
  unreachable; a target can't be chosen if unreachable from the staging area.
  New rules API: can_reach / assign_bomber_target / bomber_start_cell /
  reachable_target_cities / clear_bomber_target. UI: awaiting_target,
  click_pick_target, _target_step_block, TARGET_PURPLE/BLUE. Tier3m added. The
  old bomber_targets phase is auto-skipped for the human (AI still uses it).
- SESSION Jul 5: three UI fixes. (1) Hover tooltip now shows unit ICONS, not a
  text list (draw_hover_tooltip). (2) Game-over "Exit game" -> quit_app so the
  window no longer hangs on Windows. (3) Siberian staging outlines re-aligned to
  the red band via a fitted straight centreline (SIB_BAND_LINE), fixing both a
  polar-north tilt and a row-by-row drift out of the band; a mid-fix regression
  briefly left the outline drawn only in the polar branch (invisible but
  clickable). All covered by tier3l. See the design bullets above for details.
- RECOVERY NOTE (Jul 4): a workspace crash mid-write once truncated
  game_rules.py to 0 bytes. It was restored from Windows "Previous Versions"
  (a day-old copy) and reconciled back to the pre-crash logic by decompiling
  the __pycache__ .pyc (pycdc built from source; disassembly-diff for the
  comprehension-heavy movement methods) and VERIFYING method-by-method
  bytecode parity. Lesson: prefer small bash patches; if a file is lost, the
  .pyc in __pycache__ + the intact test suite are enough to rebuild exactly.
- Siberian entry end zone (in_entry_end_zone): a Siberian-entering unit must
  stop within the 1st-4th COLUMN from the west edge, counted by BASE column index
  (int(col[:2])-10 in 0..3), NOT the row's west_order()[1:4] position - the
  latter miscounts where the columns split (rows A-E 2-digit vs F-O 3-digit).
  Base col 0 (the west edge itself: col "10" in rows A-E, "101"/"102" in rows F+)
  IS allowed: a unit may move straight SOUTH down the west edge (E10 -> H101),
  not only east (E10 -> H121). Fixed Jul 8 (was 1..3, which wrongly rejected
  west-edge south moves). Rows limited to A-I. Covered by tier3o.
- Cuban launch timing: a staged Cuban unit launches (cuban_launch) only during
  the MOVEMENT step, not the staging step - staged_click defers with a message
  while g.needs_staging() is True (a stray click used to send one on a turn
  early). Covered by tier3o.
- The Write/Edit tools (Windows side) corrupt the bash-mount copy with
  trailing/interspersed NULL bytes (seen 2 and 108 nulls). ALWAYS patch
  via bash (python string-replace scripts or heredoc on the mount), verify
  with `ast.parse` + grep, and if you must use Edit, immediately strip
  nulls: `d=open(f,'rb').read().rstrip(b'\x00')`.
- The bash sandbox occasionally hangs on first use; retry after a moment.
  Keep AI-vs-AI sim loops small (timeout 44s) - full games are ~cheap but
  hundreds add up.
- Board: g.board.row_i[cid] gives the 0..21 row index (A=0 .. V=21).
  theta_mid/extremeness give angular (E/W) position. col 21 = east.

## Expert AI (in progress, started Jul 8)
- GOAL: expert-level American and Soviet AIs. Plan agreed: shared BeliefTracker
  + ThreatModel, then per-side policies, then joint Monte-Carlo tuning. American
  side is being built first. Balance baseline (legacy doctrines, 36-game
  tournament): Soviets win ~53%, avg 101 pts; fortress is a broken US doctrine
  (0/9), flank the strongest Soviet. Exclude fortress from the training league.
- FAIRNESS INVARIANT: the expert must use only public info - positions,
  silhouette CLASS (bomber-type vs missile-type), reveals, staging, options -
  NEVER an unrevealed enemy's real/decoy flag. In game_ai_expert.py this is
  enforced: BeliefTracker.observed(u) is the ONLY reader of u.real (gated on
  u.revealed), and tier4a greps that "u.real" appears nowhere else. Note u.kind
  distinguishes bomber vs decoy_bomber (i.e. real vs decoy) so it MUST NOT be
  used to tell them apart - group units by ("bomber","decoy_bomber") only. The
  fair reachability helper (_reachable_cities) deliberately avoids can_reach's
  real-only lateral relaxation so its result can't leak identity.
- PHASE 1 DONE: game_ai_expert.py BeliefTracker. P(real) per hidden Soviet
  silhouette = group prior (Cuban 3/8 vs north 20/23) - behavioural decoy
  evidence (forfeited best-reachable-unguarded value, BETA=0.15/pt) then a
  single global log-odds shift anchoring expected reals to the pool count
  (23R+8D bombers, 3R+1D SLBM) scaled by visible fraction; reveals collapse to
  0/1 and shrink the pool. call update() each American decision; query
  prob_real(u). Covered by tier4a. Smoke calibration over 30 cuban+siberian
  games: 0 crashes, Brier 0.173 (baseline ~0.245); extremes crisp (P<=0.4 -> 0%
  real, P=1.0 -> 100%), mid-high range over/under-confident - to be fixed by
  Phase 5 tuning of BETA/priors, not a bug.
- PHASE 2 DONE: game_ai_expert.py ThreatModel(game, belief). Reachability was
  refactored to a shared module fn reachable_cities(game, u, from_cell, cache)
  (BeliefTracker._reachable_cities now delegates to it). menu(u) = undestroyed
  cities u can still bomb (best first). expected_ceiling() = g.points + greedy
  max-weight assignment of on-board silhouettes to DISTINCT cities weighted
  points*P(real). worst_case_ceiling() = admissible UPPER bound (every non-
  revealed-decoy attacker treated as real, top-R reachable city values; off-
  board units reach anything; +COASTAL for SLBMs). provably_won() = worst_case
  < 100 (WIN_THRESHOLD). Also intercept_value(u) (its greedy-assignment value)
  and city_threat(cid) (sum P(real) of units that can bomb it) for Phases 3-4.
  Covered by tier4b. Smoke over 40 all-options games: 0 crashes, 0 admissibility
  violations (worst_case always >= final score), 0 soundness violations
  (provably_won never fired when Soviets hit 100), min slack 0 (bound is tight).
- DECISIONS: expert exposed as a SELECTABLE DIFFICULTY in the menu (Phase 6),
  not only the random rotation. The ASSIGNED-TARGETS optional rule is NOT used
  with this expert AI - excluded from the tuning and regression leagues (the
  expert never reads the hidden u.target, so it runs without crashing if targets
  is enabled, but is not tuned for it). Full plan saved to EXPERT_AI_PLAN.md.
  (The Phase 1/2 smoke runs used cuban+siberian+slbm+canadian, NOT dew or
  targets - not literally "all options".)
- PHASE 3 DONE: game_ai_expert.ExpertAmericanAI.place_all_units() - interdiction
  setup. _city_priority(cid) folds in options + OBSERVED Cuban staging (public):
  DEW -> Anchorage/Godthab (W_DEW), SLBM -> coastal (W_SLBM), Siberian -> west
  via theta_mid (W_SIB), Cuban -> south via row_i scaled by staged count
  (W_CUBAN * _cuban_pressure). Greedy: 5 missiles on top-priority US cities;
  fighters by facility-location coverage (radius=FIGHTER_REACH=6, diminishing
  returns); decoys BLUFF the highest-priority still-undefended cities; Canadian
  units restricted to Canada. ask_fire/take_turn currently DEFER to a "screen"
  AmericanAI (self._fallback) - Phase 4 replaces them. Not yet wired into the
  menu (Phase 6). Covered by tier4c. Isolated benchmark (expert setup + screen
  turns vs plain screen, 54 league games each): Soviet wins 61% -> 24%, avg pts
  100.4 -> 93.2. Setup alone is a big win before the smart turn policy exists.
- PHASE 4 DONE: ExpertAmericanAI turn/fire policy (fallbacks removed; the AI now
  has its own rng seeded off game.rng for mixed-strategy draws). take_turn:
  b.update() then a fresh ThreatModel; if provably_won -> HOLD all fighters;
  else rank on-board threats by assignment value and intercept the top ones with
  the cheapest reaching fighter, gated by _worth_a_bullet (skip P(real)<P_MIN
  decoys; threshold ENGAGE_BASE*(gap/100), *SCARCE_MULT when <=LOW_BULLETS left).
  ask_fire: endgame override (a bomb that clinches 100 -> fire if P>=P_ENDGAME);
  hard floor P_FIRE_FLOOR (never fire on a near-certain decoy - keep the
  deterrent); else soft/mixed threshold sigmoid((P*val-DETERRENCE)/FIRE_TEMP)
  via self.rng. All params are class attrs = Phase-5 tunables. Covered by tier4d.
  Controlled 3-way (identical seeds, 54 league games): Soviet wins screen/screen
  69% -> expert-setup+screen 35% -> full expert 24% (avg pts 101.1/92.5/91.8).
- PHASE 5 DONE (harness): tools/tune_ai.py - Cross-Entropy Method over the
  continuous tunables (PARAMS list), common random numbers (same doctrine/opts/
  seed list per candidate; seed also drives the expert rng), objective = American
  wins with Soviet-points tiebreak. Writes tools/expert_params.json. Injects a
  vector as ExpertAmericanAI instance attrs. Run: `py tools/tune_ai.py --iters N
  --pop M --seeds K`. Deferred setup upgrades folded in: _city_priority now uses
  observed Cuban COLUMN thetas (CUBAN_THETA_SCALE align falloff), and setup
  placement is randomized among near-best options within SETUP_EPS via _choose
  (SETUP_EPS=0 -> deterministic, used by tests). tier4e covers the harness;
  tier4c pins SETUP_EPS=0 for its asserts and adds a variation check. Baseline
  (untuned defaults) scores 14/18 wins on the harness's small league; a real
  optimization run is an overnight job and its output params still need to be
  ADOPTED (update class defaults or load the json).
- PHASE 6 DONE (expert AI COMPLETE): game_ai.AmericanAI(game, "expert")
  delegates to ExpertAmericanAI via self.expert (lazy import inside __init__ to
  avoid the game_ai<->game_ai_expert cycle; place_all_units/ask_fire/take_turn
  each early-return through it). The dispatch passes load_tuned_params()
  (tools/expert_params.json -> {}); constructing ExpertAmericanAI DIRECTLY does
  NOT load the file (keeps tests file-independent), and only known numeric class
  attrs are accepted. "expert" is NOT in US_STYLES (not in the random rotation).
  Menu: game-option checkbox "expert_us" (Expert American AI); start() pops it
  and sets us_style = "expert" else random legacy doctrine. tier4f covers
  dispatch + menu source wiring + an 18-game benchmark (expert >= screen).
- KNOWN QUIRK (found this session): AI-vs-AI games are reproducible only WITHIN
  one process. board.nbrs are sets of cell-id STRINGS and AI tie-breaks depend
  on set iteration order, which Python's per-process string-hash randomization
  shuffles - so a fixed seed gives identical games within a run but can differ
  across separate `py` launches. The tuner is fine (single process => its CRN
  holds); tier4f uses a big-enough league that the expert's edge dominates. If
  you ever need cross-process reproducibility, set PYTHONHASHSEED or sort nbrs.
- HONEST STATUS (supersedes any "expert is done/strong" claim above): the
  expert AI is fully BUILT and integrated and the suite is green, but its
  PLAYING STRENGTH IS UNVALIDATED. Reproducible fixed-seed benchmarks show it is
  NOT reliably better than the `screen` doctrine and is worse vs blitz. Earlier
  "wins ~76%" was favourable-hash-seed noise. TWO methodology problems, both
  confirmed this session: (a) tuning win-rate vs the weak legacy Soviet doctrines
  optimises against flawed opponents that never punish real weaknesses (a single
  human game exposed fighter-stacking and early-decoy over-commit that 175+ AI
  games missed); (b) games are not reproducible run-to-run even with a fixed
  PYTHONHASHSEED (same game gave 92/92/97 pts) - root cause NOT isolated; must be
  fixed before any quantitative tuning. Treat AI-vs-bot results as REGRESSION
  ONLY, never a quality oracle.
- STABILISED BASELINE (this session, after the above): SETUP_EPS reverted to 0
  (deterministic placement; the randomize-near-best mechanism is kept but off -
  it cost too much defensive quality as written); FRONT_LOAD reverted to a modest
  0.3 (high values ignore early REAL bombers -> lose to blitz; it was noise-tuned
  up to 0.7). ANTI-STACKING kept (fighters/decoys never share a city cell - a
  single bomb takes the whole stack; the Omaha failure from the human game).
  tier4f is now a STRUCTURAL regression (full games complete, setup legal, no
  stacks), not a flaky win-rate assertion. See EXPERT_AI_PLAN.md.
- INTERCEPTION REDESIGN (from a human game where the expert shot first-wave
  north DECOYS, ignored Cuban REALS, never defended an SLBM that took San Diego,
  and shot a decoy that had passed many cities): take_turn is now VALUE-GATED and
  probabilistic. For a threat to a city worth V, engage if P(real) clears a
  threshold that FALLS as V rises (PT_AT5=0.5, PT_SLOPE=0.1 -> V9~0.1, V5~0.5),
  softened into a mixed strategy (ENGAGE_TEMP), lowered as Soviet points near 100
  (URGENCY) and raised while fighters are scarce (SCARCE_BUMP). Missile-defended
  cities are discounted (MISSILE_COVER=0.4) so fighters defend UNDEFENDED cities
  first. _value_at_risk(u) = best (weighted) reachable city for a bomber, or the
  adjacent coastal city for a surfaced SLBM. SLBMs are now interceptable threats
  (they were ignored entirely before): a fighter flies onto the ocean square the
  turn it surfaces (slbm_turn==g.turn). BeliefTracker BETA 0.15->0.20 (stronger
  "passed cities => decoy") and FRONT_LOAD 0.3->0.55 (safe now: value-gating
  still defends undefended jewels vs early reals). Old _worth_a_bullet removed;
  tuner PARAMS updated (P_MIN/ENGAGE_BASE/SCARCE_MULT -> PT_AT5/PT_SLOPE/
  ENGAGE_TEMP/MISSILE_COVER/URGENCY/SCARCE_BUMP). tier4d covers value-gating +
  SLBM interception. Behavioural smoke (12 games, slbm+cuban): 0 crashes, 0 SLBMs
  bombed, interceptions favour reals (~8.4) over decoys (~6.4). These are
  DIRECTIONAL/correctness only (weak-bot, non-reproducible). Exact first-wave
  ignore rate and W_DEW level left for the user's playtesting to calibrate.
- CALIBRATION (from a 2nd human game, expert held Soviets to 110 - "as good as
  expected"): (1) W_DEW 6.0 -> 0.0 - defending the DEW anchors (Anchorage/Godthab,
  5 pts) was stealing a missile from a 9-pt jewel; leave DEW undefended and
  missile the jewels (user: nets +8 pts). tier4c now asserts the 9-cities are
  missiled and the DEW anchors are NOT, even under the DEW rule. (2) First-wave
  (turn-1) north/Siberian bombers were over-intercepted (4/4 decoys shot); added
  _wave_adjust capping their interception at FIRST_WAVE_ENGAGE=0.5, EXCEPT for an
  undefended jewel (v >= JEWEL_VALUE=7). Cuban and later-wave units unaffected
  (Cuban immediate-threat interception was rated "good"). SLBM interception rated
  good, unchanged. tier4d covers both.
- SESSION Jul 9 (3rd human game, more targeted rules):
  (1) MISSILES NOW GO TO THE HIGHEST-VALUE COASTAL CITIES (a missile does double
  duty vs a bomber AND an SLBM that moves onto the city). On the standard map:
  New York + DC (coastal 9s), San Diego + Jacksonville (coastal 8s), and a RANDOM
  coastal 7 (Norfolk/Seattle/San Francisco, rng tiebreak). INLAND jewels (Chicago,
  Omaha) get NO missile - fighters cover them. place_all_units picks missiles from
  COASTAL_CITIES by value; tier4c asserts this.
  (2) SLBM interception rewritten (_slbm_engage_prob): only the highest-value
  adjacent city UNDEFENDED by a missile matters (a missile-city defends itself).
  None -> never intercept. Then by belief P: known decoy (P<=0.02, e.g. 3 reals
  already spent) -> never; known real (P>=0.98, decoy already found) -> always;
  unknown (~0.75) -> 0.1 * that city's point value.
  (3) HARD decoy identification (BeliefTracker._confirmed_decoy, prob_real->0
  forever, used by BOTH ask_fire and interception - fixes "NY missile held, DC
  missile then wasted on the same decoy"). Rule B: a silhouette that ENDS a turn
  on an undestroyed 7/8/9 city (didn't bomb) is a decoy. Rule A: one that passes
  within 4 E/W (via _lateral_gap) south (or north, for Cuban) of an UNDEFENDED
  7/8/9 city is a decoy. Detected in update() from _prev_cell/cur positions.
  tier4a covers rules A/B; tier4d covers the SLBM logic. All DIRECTIONAL only.
- SESSION Jul 9b (4 more rules + a FAIRNESS BUG):
  (A) FAIRNESS LEAK FIXED (important): the interception threat list was built by
  iterating g.soviet_units() (MANIFEST ORDER = real bombers before decoys) and
  stably sorted by value*P - so among tied silhouettes the reals ranked first and
  were picked first when bullets were limited, letting the AI "magically" shoot
  every real in a mixed wave (human-reported: 6R/6D wave, all 6 reals hit, 0
  decoys). Fix: self.rng.shuffle(threats) BEFORE the stable sort. LESSON for the
  fairness invariant: it's not only "don't read u.real" - any ordering that
  correlates with identity (manifest order, dict order) leaks too; randomise
  ties. tier4d has a fairness test (identical-value real+decoy, must pick both).
  (B) SLBM override: a (real) fighter already sitting on an UNDEFENDED threatened
  city always intercepts the adjacent SLBM (P=1) - _slbm_engage_prob.
  (C) UI: clicking a placed SLBM during the MOVEMENT step now has NO effect (the
  removal path is gated to g.needs_staging(); both removal sites in click_russian).
  tier3o covers it.
  (D) Cuban rule-A direction ("moves NORTH past a jewel") was already handled
  (game_ai_expert line ~246, passed = rcur<rc for group cuban); tier4a now tests
  the Cuban direction explicitly.
  (E) RULE C (Cuban timing, in _detect_passed_decoys) - SOFT, not a confirm: a
  Cuban that has had its first full move (g.turn > entered_turn) and did not bomb
  gets P(real) CAPPED at CUBAN_STALL_CAP=0.25 (highly likely, not certain, a
  decoy) UNLESS it can reach an undefended 7/8/9 city on its NEXT move
  (_one_move_cities, fair). Stored in BeliefTracker._prob_cap and applied in
  prob_real as min(p, cap); RE-EVALUATED every update (not permanent), so a real
  Cuban travelling two moves to a jewel is doubted (capped 0.25 on the in-between
  turn) but not written off - once the jewel is one move away the cap lifts. Rules
  A/B stay HARD confirms (P=0). tier4a covers the cap + exemption.
- REAL NEXT STEPS (in order): (1) fix reproducibility so measurement is
  trustworthy; (2) human-game capture -> scenario tests + encode the user's
  winning line as a scripted STRONG Soviet opponent; (3) build the SOVIET expert
  (mirror BeliefTracker/ThreatModel) as a competent adversary - only then is
  automated tuning meaningful.
- SESSION Jul 9c - SOVIET EXPERT BUILT (game_ai_expert.py, bottom):
  UsDefenseBelief - Soviet mirror belief, PUBLIC info only. Key asymmetry: the 5
  US missiles are a distinct ALL-REAL silhouette so their cities are simply KNOWN
  (missile_cities()); real vs decoy FIGHTERS share one silhouette (12R+4D US,
  3R+1D Canadian - SEPARATE pools). prob_real_fighter = hypergeometric marginal
  (unrevealed reals / unrevealed silhouettes; dead-unrevealed stay in the
  denominator). A fighter reveals as real the turn it MOVES (decoys never move),
  so every interception publicly drains the real pool = the flushing arithmetic.
  fighters_remaining()/pressure(cid) drive policy. observed() is the sole hidden-
  flag reader (mirror of BeliefTracker.observed). FAIRNESS: the Soviet section
  never calls movable_fighters (real-only) or references the fighter/decoy kinds
  - tier5 asserts this by source-scanning from "class UsDefenseBelief".
  ExpertSovietAI - principles: missile cities are known so BAIT them with decoys
  (fired missile dies for a decoy); lead waves with decoys where fighters
  cluster to FLUSH reals, escalate real bombers as fighters_remaining drops
  (DECOY_FRAC by FLUSH_HI/MID regime); real bombers route down low-pressure
  lanes and grab the best reachable city (missile cities discounted by HOLD_EV);
  Cuban = 3 real + 2 decoy (user-proven); SLBM decoy baits first, real surfaces
  beside the best missile-free coastal city. All candidate lists SHUFFLED before
  value sorts (the manifest-order fairness leak lesson). Dispatch:
  game_ai.RussianAI(game, "expert") -> self.expert (lazy import); menu option
  "expert_sov"; norad_game start() sov_style. tier5 covers belief + dispatch +
  full expert-vs-expert games. Smoke (6 games) + suite green. Params are class
  attrs = future tunables. NOT yet tuned or human-validated - user will play the
  Soviet expert (solo_us) and give recommendations, same loop as the American.
  BENCHMARK (108 games, ~16 min): Soviet expert beats the American EXPERT 18/18
  (avg 112.6) AND legacy screen 18/18 (avg 108.6). BUT the metric is SATURATED -
  flank also wins 18/18 (avg 112.3), feint 17/18 - i.e. the American defence
  loses to ANY competent Soviet, so the benchmark only confirms the Soviet
  expert is "at least as strong as the best legacy doctrine" (marginally higher
  avg pts than flank); it CANNOT measure how good it really is. Same methodology
  lesson in reverse: to measure an attacker you need a strong DEFENDER, which
  the current American AI is NOT. Real validation = the user playing American.
  Side note: this exposes that the AMERICAN expert is defensively WEAK (loses
  ~100% to strong attackers) - a future American-side improvement track.

## Open/possible next steps
- Hot-seat privac