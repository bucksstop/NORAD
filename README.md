# NORAD — Strategic Game of Air Warfare (PC version)

Complete playable game: hot seat, solo vs Soviet AI, or solo vs American
AI, with all optional rules (DEW Line, Siberian placement, Cuban-based
units, Soviet sub-launched missiles, Canadian Air Defense, Assigned
Targets).

## Run
`pip install pygame` then `python norad_game.py`

## Files
- `norad_game.py` — the game UI (menus, phases, animation)
- `game_rules.py` — rules engine (no UI)
- `game_ai.py` — simple computer opponents
- `tools/` — asset/board build scripts
- `assets/units/`, `data/grid.json`, `NORAD map.jpg` — game data

## Controls
| Action | Input |
|---|---|
| Select / trace / place / buttons | Left click |
| End a traced move | Click the unit itself |
| Abort / restart a move | Click a moved unit, or Esc mid-trace |
| Zoom / pan | Wheel / right-drag or arrows |
| Show current player's unit backs | Hold TAB |
| Hide all units (bare map) | Hold C |
| Rules summary (full-screen) | R (press again to close) |

## Movement
Default: trace the path square by square; the unit follows each click.
Click the unit to stop. Units that already moved can be clicked to abort
and re-trace. The menu's "Classic movement" option restores
click-the-destination movement (the unit jumps along an auto-path).

Soviet entry is a move: the first square is on row A (from the north) or
the westernmost square of the row (from Siberia); the unit then traces
south/east and must stop in rows B-D (2nd-4th square from the west edge
for Siberian entries). Aborting an entry returns the unit to the pool.

Combat results and city bombings pause the game until you click.

## Win conditions
- Soviets win at 100+ points of destroyed cities.
- Americans win when every real Soviet attacker has been destroyed or
  expended (bombed-out, stuck on row V, missile window passed, or unable
  to ever reach a remaining city).

## Notes / interpretations
- SLBM force is 3 real + 1 decoy; SLBM targets must be coastal cities.
- Point values from the printed map where it disagrees with the rules
  appendix (LA 6, Jacksonville 8, Detroit 8 added).
- Main Soviet force faces south on the board; Cuban units face north.
- DEW Line broken: entry at rows H–L; units north of H get extra
  movement to represent starting from row H.
