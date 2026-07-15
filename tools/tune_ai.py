"""Tune the expert American AI's parameters by self-play against the legacy
Soviet doctrines, using the Cross-Entropy Method (CEM).

Each candidate is a vector of the ExpertAmericanAI tunables (the class-attribute
knobs). A candidate is scored by playing the whole league - the three Soviet
doctrines x a set of option combinations x seeds - with COMMON RANDOM NUMBERS
(the same (doctrine, options, seed) list for every candidate, and the seed also
drives the expert's own RNG), so candidates are compared on identical games.

Objective: maximise American wins; ties broken by minimising total Soviet points.

The assigned-targets rule is never used, and fortress (a Soviet-favouring US
doctrine) is irrelevant here since the American side is the expert.

Run (short demo):   python tools/tune_ai.py --iters 2 --pop 8 --seeds 1
Full-ish overnight: python tools/tune_ai.py --iters 8 --pop 16 --seeds 3
Writes the best vector to tools/expert_params.json.
"""
import argparse
import json
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import game_ai
import game_ai_expert as ex
import game_rules

# (name, low, high) - continuous tunables. FIGHTER_REACH / LOW_BULLETS are
# structural and left fixed.
PARAMS = [
    ("W_SLBM", 0.0, 6.0),
    ("W_SIB", 0.0, 6.0),
    ("W_CUBAN", 0.0, 3.0),
    ("CUBAN_THETA_SCALE", 5.0, 60.0),
    # SETUP_EPS is deliberately NOT tuned: it trades expected value for
    # unpredictability against a HUMAN, which a fixed-AI league can't reward
    # (the tuner would just drive it to 0). It is pinned at its class default.
    ("PT_AT5", 0.2, 0.8),
    ("PT_SLOPE", 0.0, 0.25),
    ("ENGAGE_TEMP", 0.05, 0.6),
    ("MISSILE_COVER", 0.0, 1.0),
    ("URGENCY", 0.0, 0.8),
    ("SCARCE_BUMP", 0.0, 0.4),
    ("P_ENDGAME", 0.05, 0.6),
    ("P_FIRE_FLOOR", 0.0, 0.4),
    ("DETERRENCE", 0.0, 8.0),
    ("FIRE_TEMP", 0.5, 5.0),
]
OPTSETS = [
    {},
    {"dew": True},
    {"cuban": True},
    {"siberian": True},
    {"slbm": True, "canadian": True},
    {"dew": True, "cuban": True, "siberian": True, "slbm": True,
     "canadian": True},
]


def defaults():
    return {name: float(getattr(ex.ExpertAmericanAI, name))
            for name, _lo, _hi in PARAMS}


def make_league(seeds):
    return [(rs, opts, s)
            for rs in game_ai.RUS_STYLES
            for opts in OPTSETS
            for s in range(seeds)]


def run_game(params, rs, opts, seed):
    g = game_rules.Game(ROOT, opts, rng=random.Random(seed))
    r = game_ai.RussianAI(g, rs)
    us = ex.ExpertAmericanAI(g)
    for k, v in params.items():
        setattr(us, k, v)
    guard = 0
    while g.phase != "over" and guard < 300:
        guard += 1
        ph = g.phase
        if ph in ("cuban_setup", "slbm_targets", "bomber_targets"):
            r.do_setup_phase()
        elif ph == "us_setup":
            us.place_all_units()
        elif ph == "russian":
            r.take_turn(us.ask_fire)
        elif ph == "american":
            us.take_turn()
            for sq, _fs, _tg in g.fighter_combat_preview():
                g.resolve_square(sq)
            g.finish_american_turn()
    return g.winner, g.points


def evaluate(params, league):
    wins = 0
    sov_pts = 0
    for rs, opts, seed in league:
        w, p = run_game(params, rs, opts, seed)
        wins += (w != "soviet")
        sov_pts += p
    return wins, sov_pts


def score(wins, sov_pts):
    # wins dominate; total Soviet points break ties (fewer is better)
    return wins - 0.001 * sov_pts


def clip(v, lo, hi):
    return max(lo, min(hi, v))


def cem(iters, pop, elite_frac, seeds, orng):
    league = make_league(seeds)
    n_games = len(league)
    mean = defaults()
    std = {name: (hi - lo) * 0.30 for name, lo, hi in PARAMS}
    base = dict(mean)
    bw, bp = evaluate(base, league)
    best = (score(bw, bp), dict(base), bw, bp)
    print(f"league: {n_games} games/candidate | baseline "
          f"{bw}/{n_games} wins, {bp} Soviet pts")
    n_elite = max(2, int(pop * elite_frac))
    for it in range(iters):
        cands = []
        for _ in range(pop):
            v = {name: clip(orng.gauss(mean[name], std[name]), lo, hi)
                 for name, lo, hi in PARAMS}
            w, p = evaluate(v, league)
            cands.append((score(w, p), v, w, p))
        cands.sort(key=lambda t: -t[0])
        if cands[0][0] > best[0]:
            best = cands[0]
        elite = cands[:n_elite]
        for name, lo, hi in PARAMS:
            vals = [e[1][name] for e in elite]
            mean[name] = sum(vals) / len(vals)
            var = sum((x - mean[name]) ** 2 for x in vals) / len(vals)
            std[name] = max((hi - lo) * 0.05, var ** 0.5)
        top = cands[0]
        print(f"iter {it + 1}/{iters}: best-in-pop {top[2]}/{n_games} wins, "
              f"{top[3]} pts (score {top[0]:.3f}) | overall best "
              f"{best[2]}/{n_games}")
    return best, n_games


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iters", type=int, default=4)
    ap.add_argument("--pop", type=int, default=12)
    ap.add_argument("--elite", type=float, default=0.34)
    ap.add_argument("--seeds", type=int, default=2)
    ap.add_argument("--seed", type=int, default=0, help="optimizer RNG seed")
    ap.add_argument("--out", default=os.path.join("tools", "expert_params.json"))
    args = ap.parse_args()

    orng = random.Random(args.seed)
    (sc, params, wins, pts), n_games = cem(
        args.iters, args.pop, args.elite, args.seeds, orng)
    print(f"\nBEST: {wins}/{n_games} American wins, {pts} Soviet pts "
          f"(score {sc:.3f})")
    for name, _lo, _hi in PARAMS:
        print(f"    {name} = {params[name]:.3f}")
    with open(os.path.join(ROOT, args.out), "w") as f:
        json.dump({"score": sc, "wins": wins, "games": n_games,
                   "soviet_pts": pts, "params": params}, f, indent=2)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
