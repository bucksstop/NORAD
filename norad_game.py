"""NORAD - Strategic Game of Air Warfare (1977 Mishler edition).

Full game: two-player hot seat, solo vs Soviet AI, or solo vs American AI,
with all optional rules selectable at the start menu.

Movement (default): trace the path - click each square in turn and the
unit follows; click the unit itself to stop. Click a unit that has already
moved to abort and re-trace its move. A menu option restores classic
movement (click the destination, the unit jumps).

Controls:
  Left click        select / trace / place units, press buttons
  Mouse wheel       zoom (centered on cursor)
  Right-drag        pan map
  Arrow keys        pan map
  TAB (hold)        show backs of the current player's units
  C (hold)          hide all units (view the bare map)
  Esc               abort move / cancel selection / quit from game over
  R                 toggle a full-screen rules summary

Requires: pygame  (pip install pygame)
Run:      python norad_game.py
"""
import asyncio
import inspect
import json
import math
import os
import random
import sys

import pygame

import game_ai
import game_rules

ROOT = os.path.dirname(os.path.abspath(__file__))
MAP_FILE = os.path.join(ROOT, "NORAD map.jpg")
UNITS_DIR = os.path.join(ROOT, "assets", "units")

PANEL_W = 270
BG = (24, 28, 34)
PANEL_BG = (38, 44, 52)
BTN_BG = (60, 70, 84)
BTN_HOT = (90, 104, 122)
HILITE = (255, 220, 0)
OKGREEN = (80, 220, 120)
ERRRED = (235, 80, 80)
ENTRY_Y = (255, 200, 60)
# Centreline of the red "OPTIONAL RUSSIAN START LINE" band: two points in
# map-image pixel coords, fitted from the scan. The band is a straight strip
# (~3.7px max residual) running a few degrees off the westmost radial, so
# anchoring to each cell's own west edge drifts out of the band lower down.
SIB_BAND_LINE = ((896.7, 266.2), (-85.2, 2008.6))
SIB_BAND_MID = 0.0    # px: >0 nudges the outline east edge inward from the band midline
WHITE = (230, 230, 230)
GREY = (170, 170, 170)
TABS_BG = (70, 52, 82)
NEXT_BG = (120, 100, 48)      # Next-entry toggle button
SHORTCUTS_Y = 560            # pinned top of the shortcuts panel
COMBAT_FOCUS = (255, 140, 40)
TARGET_PURPLE = (185, 90, 225)   # assigned-target city outline
TARGET_BLUE = (90, 150, 255)     # target of the bomber being moved

AI_STEP_MS = 550          # per-square AI movement speed
COMBAT_SHOW_MS = 1300     # reveal time per combat square

# Compact rules summary shown by the in-game "R" overlay: three columns of
# (kind, text) pairs, kind is "header" | "body" | "spacer". Body text is
# wrapped to the column's actual pixel width at render time (draw_rules), so
# these stay as whole sentences rather than hand-broken lines.
RULES_COLUMNS = [
    [
        ("header", "OBJECTIVE"),
        ("body", "Soviets win at 100+ points from bombed cities."),
        ("body", "Americans win by preventing the Soviets from reaching "
                 "100 points."),
        ("spacer", ""),
        ("header", "SETUP (in order)"),
        ("body", "1. Cuban unit staging (optional rule): if enabled, "
                 "the Soviet stages its Cuban force on row V first."),
        ("body", "2. American Setup: places missiles + fighters on "
                 "city squares - one missile max per city, unlimited "
                 "real/decoy fighters per city."),
        ("body", "3. Soviet staging: North (and, if enabled, Siberian) "
                 "forces stage next, before movement begins."),
    ],
    [
        ("header", "MOVEMENT"),
        ("body", "Trace mode: click each square in turn; click the "
                 "unit itself to stop."),
        ("body", "Classic mode: click destination, unit auto-paths "
                 "(menu option)."),
        ("body", "Soviet entry: first square is row A (north) or the "
                 "west edge (Siberia)."),
        ("body", "A unit must advance 2+ rows/turn unless bombing or "
                 "reaching row V (Cuban: row A)."),
        ("body", "Units may move at most 2 squares east/west per turn, "
                 "unless the extra lateral move ends the turn by "
                 "bombing a city."),
        ("body", "Movement allowance: Soviet bomber 4 squares; "
                 "American/Canadian fighter 6 squares; SLBM 1 square."),
        ("spacer", ""),
        ("header", "COMBAT"),
        ("body", "US missiles may fire on a Soviet unit entering "
                 "their city (hold or fire)."),
        ("body", "Fighters intercept the Soviet unit in the same "
                 "square."),
        ("body", "Bombing a city scores its points."),
    ],
    [
        ("header", "OPTIONAL RULES"),
        ("body", "DEW Line: North/Siberian decoys crossing row H may "
                 "be exposed & removed: 50% while both Anchorage and "
                 "Godthab stand, 25% with one, 0% with neither."),
        ("body", "Siberian: extra Soviet entry route, east band."),
        ("body", "Cuban: sets aside 3 real + 5 decoy bombers from the "
                 "main force; up to 5 of them may stage via row V."),
        ("body", "SLBM: 3 real + 1 decoy sub-launched missile, "
                 "coastal cities only."),
        ("body", "Canadian AD: adds 3 real + 1 decoy Canadian fighter; "
                 "must be setup in Canadian cities."),
        ("body", "Play Balance: +1 US missile decoy, -1 Soviet decoy "
                 "bomber (8 -> 7)."),
        ("body", "Assigned Targets: each Soviet attacker may bomb "
                 "only its pre-assigned city."),
    ],
]
RULES_FOOTER = ("Left click: select/trace/place/buttons   |   Wheel/right-"
                "drag/arrows: zoom & pan   |   TAB: show unit backs   |   "
                "C: hide units   |   Esc: abort/cancel   |   R: close")


def fatal(msg):
    print(msg)
    try:
        import tkinter.messagebox
        import tkinter
        r = tkinter.Tk()
        r.withdraw()
        tkinter.messagebox.showerror("NORAD", msg)
    except Exception:
        pass
    sys.exit(1)


def _web_window():
    """The browser window object when running under pygbag/WebAssembly, else
    None on the desktop. pygbag exposes it as platform.window; some builds use
    the pyodide-style js.window. Either gives innerWidth/innerHeight so the
    display can be sized to the real page instead of a small fixed default."""
    for modname in ("platform", "js"):
        try:
            mod = __import__(modname)
            win = getattr(mod, "window", None)
            if win is not None and hasattr(win, "innerWidth"):
                return win
        except Exception:
            pass
    return None


class View:
    def __init__(self, map_size, screen_rect):
        self.mw, self.mh = map_size
        self.rect = screen_rect
        self.min_zoom = min(screen_rect.w / self.mw, screen_rect.h / self.mh)
        self.zoom = self.min_zoom
        self.ox = screen_rect.x + (screen_rect.w - self.mw * self.zoom) / 2
        self.oy = screen_rect.y + (screen_rect.h - self.mh * self.zoom) / 2

    def to_screen(self, mx, my):
        return mx * self.zoom + self.ox, my * self.zoom + self.oy

    def to_map(self, sx, sy):
        return (sx - self.ox) / self.zoom, (sy - self.oy) / self.zoom

    def zoom_at(self, sx, sy, factor):
        mx, my = self.to_map(sx, sy)
        self.zoom = max(self.min_zoom, min(6.0, self.zoom * factor))
        self.ox = sx - mx * self.zoom
        self.oy = sy - my * self.zoom
        self.clamp()

    def pan(self, dx, dy):
        self.ox += dx
        self.oy += dy
        self.clamp()

    def clamp(self):
        r = self.rect
        w, h = self.mw * self.zoom, self.mh * self.zoom
        self.ox = (r.x + (r.w - w) / 2) if w <= r.w else \
            min(r.x, max(r.x + r.w - w, self.ox))
        self.oy = (r.y + (r.h - h) / 2) if h <= r.h else \
            min(r.y, max(r.y + r.h - h, self.oy))


class App:
    def __init__(self):
        pygame.init()
        pygame.display.set_caption("NORAD - Strategic Game of Air Warfare")
        # In the browser (pygbag) the SDL "desktop" size is a small fixed
        # default, which leaves the map cramped and the page half empty. When
        # a browser window is present, size the display to it (and track it so
        # loop() can follow live resizes); otherwise use the desktop sizing.
        self.web_window = _web_window()
        if self.web_window is not None:
            # Our custom pygbag template sizes the framebuffer to the browser
            # window (config.fb_width/height) so the game fills the page at the
            # window's own aspect - no letterbox margins, no distortion. Lay the
            # game out at exactly that framebuffer size.
            try:
                cfg = self.web_window.config
                size = (max(640, int(cfg.fb_width)), max(480, int(cfg.fb_height)))
            except Exception:
                size = (1280, 720)
        else:
            info = pygame.display.Info()
            size = (max(800, min(1500, info.current_w - 60)),
                    max(600, min(950, info.current_h - 90)))
        self.screen = pygame.display.set_mode(size, pygame.RESIZABLE)
        self.clock = pygame.time.Clock()
        self.font = pygame.font.SysFont("segoeui,arial", 15)
        self.small = pygame.font.SysFont("segoeui,arial", 13)
        self.rules_font = pygame.font.SysFont("segoeui,arial", 12)
        self.med = pygame.font.SysFont("segoeui,arial", 18, bold=True)
        self.big = pygame.font.SysFont("segoeui,arial", 28, bold=True)
        # larger fonts used only on the full-screen rules overlay (readability)
        self.rules_title = pygame.font.SysFont("segoeui,arial", 23, bold=True)
        self.rules_hdr = pygame.font.SysFont("segoeui,arial", 18, bold=True)
        self.rules_body = pygame.font.SysFont("segoeui,arial", 15)
        if not os.path.exists(MAP_FILE):
            fatal(f'Map file not found: "{MAP_FILE}"')
        self.map_img = pygame.image.load(MAP_FILE).convert()
        with open(os.path.join(UNITS_DIR, "manifest.json")) as f:
            manifest = json.load(f)
        self.front = {}
        self.back = {}
        _cache = {}
        for m in manifest:
            self.front[m["id"]] = pygame.image.load(
                os.path.join(UNITS_DIR, m["file"])).convert()
            bf = m["back"]
            if bf not in _cache:
                _cache[bf] = pygame.image.load(
                    os.path.join(UNITS_DIR, bf)).convert()
            self.back[m["id"]] = _cache[bf]
        self._sprites = {}
        self.reset_ui_state()

    def reset_ui_state(self):
        self.game = None
        self.mode = None
        self.classic_move = False
        self.trace = None            # unit being traced
        self.sel_unit = None         # classic-mode selection
        self.sel_dests = {}
        self.entry_group = None
        self.tray_unit = None
        self.stage_kind = None       # selected unit type in the staging tray
        self.awaiting_target = None  # real bomber awaiting a target pick
        self._reach_key = None       # cache key for target reachability
        self._reach_set = set()
        self._blocked_dests = set()  # classic dests blocked by the target rule
        self.us_kind = None          # selected US unit type during us_setup
        self._stage_tray_rects = []
        self.peek = False
        self.hide_units = False
        self.reveal_all = False
        self.panning = False
        self.msg = ""
        self.msg_color = OKGREEN
        self.msg_until = 0
        self.buttons = []
        self.turn_problems = []      # shown only after a failed End Turn
        self.banner = None           # click-to-continue banner text
        self.banner_hint = True      # append "(click to continue)" to banner
        self.title_override = None   # (text, color) during combat/bombing
        self.cuban_real = 0
        self.cuban_total = 0
        self.entry_real = True       # next entered unit: real or decoy
        self.entry_mode = None       # None | "bomber" | "missile"
        self._dew_announced = False  # DEW-break popup shown yet?
        self._combat_focus = None    # square being resolved in combat
        self.rules_open = False      # full-screen rules summary (toggle: R)
        self._scaled = None
        self._scaled_size = None

    # ------------------------------------------------------------- layout
    def make_view(self):
        w, h = self.screen.get_size()
        self.map_rect = pygame.Rect(PANEL_W, 0, w - PANEL_W, h)
        old = getattr(self, "view", None)
        self.view = View(self.map_img.get_size(), self.map_rect)
        if old:
            self.view.zoom = max(self.view.min_zoom, old.zoom)
            self.view.clamp()

    # ------------------------------------------------------------- helpers
    def human_turn(self):
        g = self.game
        if g.phase in ("cuban_setup", "slbm_targets", "bomber_targets",
                       "russian"):
            return self.human_sov
        if g.phase in ("us_setup", "american"):
            return self.human_us
        return True

    def side_on_turn(self):
        return ("soviet" if self.game.phase in
                ("cuban_setup", "slbm_targets", "bomber_targets", "russian")
                else "us")

    def flash(self, text, color=OKGREEN):
        self.msg, self.msg_color = text, color
        self.msg_until = pygame.time.get_ticks() + 4000

    def pump_quit(self):
        for e in pygame.event.get((pygame.QUIT,)):
            pygame.quit()
            sys.exit()

    def view_event(self, e):
        """Zoom/pan handling that stays available during animations."""
        if e.type == pygame.MOUSEWHEEL:
            mx, my = pygame.mouse.get_pos()
            if self.map_rect.collidepoint(mx, my):
                self.view.zoom_at(mx, my, 1.15 if e.y > 0 else 1 / 1.15)
            return True
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 3:
            self.panning = True
            return True
        if e.type == pygame.MOUSEBUTTONUP and e.button == 3:
            self.panning = False
            return True
        if e.type == pygame.MOUSEMOTION and self.panning:
            self.view.pan(*e.rel)
            return True
        if e.type == pygame.KEYDOWN and e.key in (
                pygame.K_LEFT, pygame.K_RIGHT, pygame.K_UP, pygame.K_DOWN):
            dx = {pygame.K_LEFT: 60, pygame.K_RIGHT: -60}.get(e.key, 0)
            dy = {pygame.K_UP: 60, pygame.K_DOWN: -60}.get(e.key, 0)
            self.view.pan(dx, dy)
            return True
        return False

    async def pause(self, ms):
        end = pygame.time.get_ticks() + ms
        while pygame.time.get_ticks() < end:
            for e in pygame.event.get():
                if e.type == pygame.QUIT:
                    pygame.quit()
                    sys.exit()
                self.view_event(e)
            self.draw()
            self.clock.tick(60)
            await asyncio.sleep(0)

    def ensure_visible(self, cid):
        """Zoom the map out until the given square is on screen."""
        if cid not in self.game.board.cells:
            return
        v = self.view
        inner = self.map_rect.inflate(-60, -60)
        for _ in range(12):
            sx, sy = v.to_screen(*self.game.board.cells[cid]["center"])
            if inner.collidepoint(sx, sy) or v.zoom <= v.min_zoom + 1e-6:
                return
            v.zoom_at(self.map_rect.centerx, self.map_rect.centery,
                      1 / 1.3)

    async def wait_click(self, text):
        """Block until the player clicks (or presses Space/Enter)."""
        self.banner = text
        while True:
            self.draw()
            for e in pygame.event.get():
                if e.type == pygame.QUIT:
                    pygame.quit()
                    sys.exit()
                if self.view_event(e):
                    continue
                if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                    self.banner = None
                    return
                if e.type == pygame.KEYDOWN and e.key in (
                        pygame.K_SPACE, pygame.K_RETURN, pygame.K_ESCAPE):
                    self.banner = None
                    return
            self.clock.tick(60)
            await asyncio.sleep(0)

    def entry_type_label(self):
        if not self.entry_mode:              # nothing chosen yet
            return ""
        noun = "MISSILE" if self.entry_mode == "missile" else "BOMBER"
        return noun if self.entry_real else f"{noun} DECOY"

    def unit_label(self, u):
        base = {"fighter": "Fighter", "missile": "Missile",
                "decoy_fighter": "Fighter", "bomber": "Bomber",
                "decoy_bomber": "Decoy", "decoy_missile": "Decoy",
                "us_decoy_missile": "Missile"}.get(u.kind, u.kind)
        if u.canadian:
            base = "Can. " + base
        return base

    async def pick_unit(self, cid, stack, title):
        """Popup: choose one unit from a stack. Returns the unit or None."""
        g = self.game
        tile, pad = 64, 14
        cols = min(4, len(stack))
        rows = (len(stack) + cols - 1) // cols
        bw = cols * (tile + pad) + pad + 24
        bh = rows * (tile + pad + 34) + 70
        w, h = self.screen.get_size()
        bx = PANEL_W + (w - PANEL_W) // 2 - bw // 2
        by = h // 2 - bh // 2
        rects = []
        for i, u in enumerate(stack):
            r, c = divmod(i, cols)
            rects.append((pygame.Rect(bx + 18 + c * (tile + pad),
                                      by + 44 + r * (tile + pad + 34),
                                      tile, tile), u))
        while True:
            self.draw()
            scr = self.screen
            box = pygame.Surface((bw, bh))
            box.fill((8, 8, 12))
            box.set_alpha(248)
            scr.blit(box, (bx, by))
            pygame.draw.rect(scr, WHITE, (bx, by, bw, bh), 2)
            scr.blit(self.med.render(title, True, (255, 255, 255)),
                     (bx + 18, by + 12))
            for r, u in rects:
                img = pygame.transform.smoothscale(self.front[u.id],
                                                   (tile, tile))
                scr.blit(img, r)
                pygame.draw.rect(scr, GREY, r, 1)
                if u.kind == "decoy_fighter":
                    scr.blit(self.font.render("D", True, ERRRED),
                             (r.x + 4, r.bottom - 20))
                if u.kind == "missile":
                    tag = "fixed"
                elif u.moved_turn == self.game.turn:
                    tag = "moved"
                elif u.kind == "decoy_fighter":
                    tag = ""
                else:
                    tag = "ready"
                lab = self.small.render(self.unit_label(u), True,
                                        (255, 255, 255))
                scr.blit(lab, (r.x, r.bottom + 3))
                if tag:
                    scr.blit(self.small.render(tag, True, GREY),
                             (r.x, r.bottom + 18))
            hint = self.small.render("Esc / click outside to cancel", True,
                                     GREY)
            scr.blit(hint, (bx + 18, by + bh - 22))
            pygame.display.flip()
            for e in pygame.event.get():
                if e.type == pygame.QUIT:
                    pygame.quit()
                    sys.exit()
                if e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
                    return None
                if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                    for r, u in rects:
                        if r.collidepoint(e.pos):
                            return u
                    return None
            self.clock.tick(60)
            await asyncio.sleep(0)

    async def gate(self, kind, msg):
        """Click-to-continue with a COMBAT/BOMBING/DEW panel title."""
        titles = {"combat": ("COMBAT", (110, 170, 255)),
                  "bomb": ("BOMBING", (235, 90, 90)),
                  "dew": ("DEW LINE DESTROYED", ENTRY_Y),
                  "lost": ("SOVIET UNIT LOST", (235, 90, 90)),
                  "illegal": ("ILLEGAL MOVE", (235, 90, 90)),
                  "target": ("ASSIGNED TARGET", TARGET_PURPLE)}
        self.title_override = titles.get(kind, ("BOMBING", (235, 90, 90)))
        await self.wait_click(msg)
        self.title_override = None

    async def announce_stuck(self):
        """Pop up an explanation for any Soviet unit destroyed for having no
        legal move (set by the rules engine at the end of a Soviet turn)."""
        msgs = getattr(self.game, "_stuck_msgs", [])
        if msgs:
            self.game._stuck_msgs = []
            await self.gate("lost", " ".join(msgs))

    async def check_dew_break(self):
        """Show a one-time click-to-continue popup when the DEW Line falls."""
        g = self.game
        if (g.opt["dew"] and g.dew_break_turn is not None
                and not self._dew_announced):
            self._dew_announced = True
            msg = next((e for e in reversed(g.log)
                        if e.startswith("The DEW Line is broken")),
                       "The DEW Line is broken! Soviet staging halts for two "
                       "turns; afterwards units stage on the row-H line.")
            await self.gate("dew", msg)

    async def ask_yesno(self, lines):
        while True:
            self.draw(modal=lines)
            for e in pygame.event.get():
                if e.type == pygame.QUIT:
                    pygame.quit()
                    sys.exit()
                if e.type == pygame.KEYDOWN:
                    if e.key in (pygame.K_y, pygame.K_RETURN):
                        return True
                    if e.key in (pygame.K_n, pygame.K_ESCAPE):
                        return False
                if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                    yes, no = self._modal_rects(lines)
                    if yes.collidepoint(e.pos):
                        return True
                    if no.collidepoint(e.pos):
                        return False
            self.clock.tick(60)
            await asyncio.sleep(0)

    def _modal_rects(self, lines):
        w, h = self.screen.get_size()
        bw = max(360, max(self.font.size(t)[0] for t in lines) + 60)
        bh = 90 + 22 * len(lines)
        bx, by = (w + PANEL_W) // 2 - bw // 2, h // 2 - bh // 2
        yes = pygame.Rect(bx + bw // 2 - 130, by + bh - 48, 110, 32)
        no = pygame.Rect(bx + bw // 2 + 20, by + bh - 48, 110, 32)
        return yes, no

    # ------------------------------------------------------------- menu
    async def menu(self):
        modes = [("Two Player", "hotseat"),
                 ("Solo - you play the American side", "solo_us"),
                 ("Solo - you play the Soviet side", "solo_sov")]
        opts = [("dew", "The DEW Line"),
                ("siberian", "Siberian-based placement"),
                ("cuban", "Cuban-based units"),
                ("slbm", "Soviet sub-launched missiles (adds Canadian AD)"),
                ("canadian", "Canadian Air Defense"),
                ("balance", "Play Balance (+1 US missile decoy, -1 Soviet decoy)"),
                ("targets", "Assigned targets (AI opponents only)")]
        game_opts = [
            ("classic_move", "Classic movement (click destination - unit jumps)"),
        ]
        ai_styles = [
            ("standard", "Standard AI Opponent"),
            ("expert", "Expert AI Opponent"),
        ]
        is_web = self.web_window is not None   # hot seat is disabled online
        mode_i = 1 if is_web else 0
        # All optional rules default ON except Assigned Targets.
        checks = {k: (k != "targets") for k, _ in opts}
        for k, _ in game_opts:
            checks[k] = False
        ai_style = None      # "standard" | "expert" - neither pre-selected;
                             # required in solo modes before START GAME works

        while True:
            w, h = self.screen.get_size()
            mode_name = modes[mode_i][1]
            scr = self.screen
            scr.fill(BG)
            t = self.big.render("NORAD - Strategic Game of Air Warfare",
                                True, WHITE)
            scr.blit(t, (w // 2 - t.get_width() // 2, 40))
            rects = []
            rule_rect = pygame.Rect(w - 232, 16, 214, 30)
            pygame.draw.rect(scr, TABS_BG, rule_rect, border_radius=6)
            rl = self.font.render("View NORAD Rulebook (PDF)", True, WHITE)
            scr.blit(rl, (rule_rect.centerx - rl.get_width() // 2,
                          rule_rect.centery - rl.get_height() // 2))
            y = 120
            # --- Game mode ---
            scr.blit(self.font.render("Game mode:", True, GREY),
                     (w // 2 - 260, y - 24))
            for i, (label, key) in enumerate(modes):
                disabled = is_web and key == "hotseat"
                r = pygame.Rect(w // 2 - 260, y, 520, 32)
                if disabled:
                    pygame.draw.rect(scr, (30, 30, 34), r, border_radius=6)
                elif i == mode_i:
                    pygame.draw.rect(scr, (46, 108, 74), r, border_radius=6)
                    pygame.draw.rect(scr, OKGREEN, r, 2, border_radius=6)
                else:
                    pygame.draw.rect(scr, BTN_BG, r, border_radius=6)
                lab = label + ("   (not available online)" if disabled else "")
                scr.blit(self.font.render(lab, True, GREY if disabled else WHITE),
                         (r.x + 12, r.y + 6))
                if not disabled:
                    rects.append(("mode", i, r))
                y += 40
            y += 14
            # --- AI opponent (below game mode, above optional rules) ---
            hotseat = mode_name == "hotseat"
            scr.blit(self.font.render("AI opponent (solo modes only):", True,
                                      GREY), (w // 2 - 260, y - 4))
            y += 20
            for k, label in ai_styles:
                r = pygame.Rect(w // 2 - 260, y, 520, 28)
                pygame.draw.rect(scr, (30, 30, 34) if hotseat else BTN_BG, r,
                                 border_radius=6)
                circ = (r.x + 16, r.y + 14)
                pygame.draw.circle(scr, GREY if hotseat else WHITE, circ, 8, 2)
                if ai_style == k and not hotseat:
                    pygame.draw.circle(scr, OKGREEN, circ, 5)
                lab = f"{label}  (N/A - hot seat, no AI)" if hotseat else label
                scr.blit(self.font.render(lab, True, GREY if hotseat else WHITE),
                         (r.x + 34, r.y + 4))
                if not hotseat:
                    rects.append(("ai_style", k, r))
                y += 34
            y += 14
            # --- Optional rules ---
            scr.blit(self.font.render(
                "Optional rules (click to toggle):", True, GREY),
                (w // 2 - 260, y - 4))
            y += 20
            for k, label in opts:
                r = pygame.Rect(w // 2 - 260, y, 520, 28)
                on = checks[k] or (k == "canadian" and checks["slbm"])
                pygame.draw.rect(scr, BTN_BG, r, border_radius=6)
                box = pygame.Rect(r.x + 8, r.y + 6, 16, 16)
                pygame.draw.rect(scr, WHITE, box, 2)
                if on:
                    pygame.draw.rect(scr, OKGREEN, box.inflate(-6, -6))
                scr.blit(self.font.render(label, True, WHITE),
                         (r.x + 34, r.y + 4))
                rects.append(("opt", k, r))
                y += 34
            y += 14
            # --- Game options ---
            scr.blit(self.font.render("Game options:", True, GREY),
                     (w // 2 - 260, y - 4))
            y += 20
            for k, label in game_opts:
                r = pygame.Rect(w // 2 - 260, y, 520, 28)
                pygame.draw.rect(scr, BTN_BG, r, border_radius=6)
                box = pygame.Rect(r.x + 8, r.y + 6, 16, 16)
                pygame.draw.rect(scr, WHITE, box, 2)
                if checks[k]:
                    pygame.draw.rect(scr, OKGREEN, box.inflate(-6, -6))
                scr.blit(self.font.render(label, True, WHITE),
                         (r.x + 34, r.y + 4))
                rects.append(("opt", k, r))
                y += 34
            y += 6
            need_ai_choice = not hotseat and ai_style is None
            start = pygame.Rect(w // 2 - 90, y, 180, 44)
            pygame.draw.rect(scr, (45, 70, 55) if need_ai_choice
                             else (60, 130, 80), start, border_radius=8)
            t = self.font.render("START GAME", True,
                                 GREY if need_ai_choice else WHITE)
            scr.blit(t, (start.centerx - t.get_width() // 2,
                         start.centery - t.get_height() // 2))
            if need_ai_choice:
                hint = self.small.render(
                    "Select an AI Opponent style above to continue.",
                    True, ERRRED)
                scr.blit(hint, (start.centerx - hint.get_width() // 2,
                                start.bottom + 8))
            pygame.display.flip()
            for e in pygame.event.get():
                if e.type == pygame.QUIT:
                    pygame.quit()
                    sys.exit()
                if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                    if rule_rect.collidepoint(e.pos):
                        self.open_rulebook()
                    for kind, val, r in rects:
                        if r.collidepoint(e.pos):
                            if kind == "mode":
                                mode_i = val
                            elif kind == "ai_style":
                                ai_style = val
                            else:
                                checks[val] = not checks[val]
                    if start.collidepoint(e.pos) and not need_ai_choice:
                        if checks["slbm"]:
                            checks["canadian"] = True
                        mode_name = modes[mode_i][1]
                        checks["expert_us"] = (mode_name == "solo_sov"
                                               and ai_style == "expert")
                        checks["expert_sov"] = (mode_name == "solo_us"
                                                and ai_style == "expert")
                        return mode_name, checks
            self.clock.tick(60)
            await asyncio.sleep(0)

    # ------------------------------------------------------------- game
    async def start(self):
        mode, checks = await self.menu()
        self.mode = mode
        self.classic_move = checks.pop("classic_move", False)
        expert_us = checks.pop("expert_us", False)
        expert_sov = checks.pop("expert_sov", False)
        self.human_us = mode in ("hotseat", "solo_us")
        self.human_sov = mode in ("hotseat", "solo_sov")
        self.game = game_rules.Game(ROOT, checks)
        sov_style = ("expert" if expert_sov
                     else random.choice(list(game_ai.RUS_STYLES)))
        self.rus_ai = game_ai.RussianAI(self.game, style=sov_style)
        us_style = ("expert" if expert_us
                    else random.choice(list(game_ai.US_STYLES)))
        self.us_ai = game_ai.AmericanAI(self.game, style=us_style)
        self.make_view()
        v = self.view
        v.zoom = max(v.min_zoom, self.map_rect.w / v.mw)
        v.ox, v.oy = float(self.map_rect.x), float(self.map_rect.y)
        v.clamp()
        self.flash("Game started. " + self.phase_hint())
        await self.loop()

    async def _fire_cb(self, cids):
        """Resolve the American 'fire your missile?' decision for any US
        missiles sitting on `cids`, returning a plain (non-blocking) callback
        for the rules engine. When the American side is AI the engine uses the
        AI's own decision; when it is human we ask now - awaiting the modal -
        and hand back the cached answers, so the synchronous rules step itself
        never has to block for input (which a browser cannot do)."""
        if not self.human_us:
            return self.us_ai.ask_fire
        g = self.game
        answers = {}
        for cid in cids:
            for m in [x for x in g.at(cid, "us") if x.kind == "missile"]:
                city = g.board.city(m.cell)
                name = city["name"] if city else m.cell
                answers[m.id] = await self.ask_yesno(
                    ["AMERICAN PLAYER:",
                     f"A Soviet unit has entered {name} ({m.cell}),",
                     "which is defended by your missile.",
                     "Fire the missile? (Both units are removed.)"])
        return lambda m, _u: answers.get(m.id, False)

    def _passive_fire(self, m, u):
        """Non-blocking ask_fire passed to the AI's take_turn. The UI always
        supplies a mover, so the engine resolves any human fire prompt through
        the mover (_fire_cb) and never calls this; it exists only so the AI's
        headless fallback path has something synchronous to call."""
        return False

    def phase_hint(self):
        g = self.game
        return {
            "cuban_setup": "Soviet: choose your Cuban-based force, then place it on the yellow start-line squares.",
            "slbm_targets": "Soviet: assign target cities to your "
                            "sub-launched missiles.",
            "us_setup": "American: place all your units on city squares.",
            "bomber_targets": "Soviet: assign a target city to each bomber.",
            "russian": f"Turn {g.turn}: Soviet {'staging' if g.needs_staging() else 'movement'}.",
            "american": f"Turn {g.turn}: American movement.",
            "over": "Game over.",
        }.get(g.phase, "")

    async def loop(self):
        while True:
            g = self.game
            if (g.phase == "bomber_targets" and self.human_sov
                    and g.opt["targets"]):
                g.next_phase()      # human assigns targets while staging, later
                continue
            if g.phase != "over" and not self.human_turn():
                await self.run_ai_phase()
                continue
            for e in pygame.event.get():
                await self.handle(e)
            self.draw()
            self.clock.tick(60)
            await asyncio.sleep(0)

    # ------------------------------------------------------------- AI
    async def animate_soviet_move(self, u, path):
        g = self.game
        if not u.entering and not g.begin_russian_move(u):
            return "arrived"
        self.ensure_visible(u.cell)
        if path:
            self.ensure_visible(path[-1])
        for cid in path:
            self.ensure_visible(cid)
            ask = await self._fire_cb([cid])
            res = g.russian_step(u, cid, ask)
            self.draw()
            await self.pause(AI_STEP_MS)
            if res == "dead":
                # A DEW-exposed decoy stays on the board (blank back showing)
                # through the gate, then is removed when the player continues.
                await self.gate("combat", g.log[-1])
                if getattr(u, "dew_exposed", False):
                    u.alive = False
                return "dead"
        g.end_russian_move(u)
        if not u.alive:                 # decoy dashed onto a held-fire missile
            await self.gate("combat", g.log[-1])
            return "dead"
        return "arrived"

    async def animate_fighter_move(self, u, path):
        g = self.game
        if not g.begin_fighter_move(u):
            return "arrived"
        self.ensure_visible(u.cell)
        if path:
            self.ensure_visible(path[-1])
        for cid in path:
            self.ensure_visible(cid)
            g.fighter_step(u, cid)
            self.draw()
            await self.pause(AI_STEP_MS)
        g.end_fighter_move(u)
        return "arrived"

    async def run_ai_phase(self):
        g = self.game
        self.draw()
        await self.pause(350)
        if g.phase in ("cuban_setup", "slbm_targets", "bomber_targets"):
            self.rus_ai.do_setup_phase()
            self.flash(self.phase_hint())
        elif g.phase == "us_setup":
            self.us_ai.place_all_units()
            self.flash("The American AI has placed its units. "
                       + self.phase_hint())
        elif g.phase == "russian":
            async def rus_event(kind=""):
                if kind == "bombed":
                    await self.gate("bomb", g.log[-1])
                    await self.check_dew_break()
                else:
                    await self.pause(200)
            await self.rus_ai.take_turn(self._passive_fire,
                                        mover=self.animate_soviet_move,
                                        on_event=rus_event)
            await self.announce_stuck()
            self.flash(self.phase_hint())
        elif g.phase == "american":
            async def us_event(*_):
                await self.pause(150)
            await self.us_ai.take_turn(mover=self.animate_fighter_move,
                                       on_event=us_event)
            has_combat = any(t for _sq, _fs, t
                             in g.fighter_combat_preview())
            await self.wait_click("American movement complete. Click to resolve "
                                  "combat." if has_combat
                                  else "American movement complete.")
            await self.resolve_american_combat()
            self.flash(self.phase_hint())
        self.trace = None
        self.sel_unit = None
        self.sel_dests = {}
        self.turn_problems = []

    async def resolve_american_combat(self):
        """Reveal and resolve each battle square in order, slowly."""
        g = self.game
        self.title_override = ("COMBAT", (110, 170, 255))
        combats = g.fighter_combat_preview()
        for sq, fighters, target in combats:
            for f in fighters:
                f.revealed = True
            if target:
                target.revealed = True
            self._combat_focus = sq
            self.ensure_visible(sq)
            # 1) show the outcome banner with the units still on the map and
            #    the square outlined; hold for the delay (no click prompt yet).
            outcome = g.combat_outcome(sq)
            self.banner = outcome
            self.banner_hint = False
            self.draw()
            await self.pause(1600)
            # 2) keep the SAME outcome text, add "(click to continue)"
            #    beneath it, and leave the units on the map until the player
            #    clicks - the units are removed ON the continue click.
            self.banner_hint = True
            await self.wait_click(outcome)
            g.resolve_square(sq)
        self._combat_focus = None
        self.title_override = None
        self.banner_hint = True
        g.finish_american_turn()

    # ------------------------------------------------------------- events
    async def handle(self, e):
        g = self.game
        if e.type == pygame.QUIT:
            pygame.quit()
            sys.exit()
        elif e.type == pygame.VIDEORESIZE:
            self.make_view()
        elif (e.type == pygame.KEYDOWN and e.key == pygame.K_r
              and not (e.mod & (pygame.KMOD_CTRL | pygame.KMOD_ALT
                                | pygame.KMOD_META | pygame.KMOD_SHIFT))):
            # ONLY an unmodified R toggles the rules; any modifier combo (e.g.
            # the browser's Ctrl+Shift+R hard-reload) passes through instead of
            # opening the rules overlay
            self.rules_open = not self.rules_open
        elif self.rules_open:
            return   # swallow all other input while the rules screen is up
        elif e.type == pygame.KEYDOWN:
            if e.key == pygame.K_ESCAPE:
                if self.awaiting_target is not None:
                    u = self.awaiting_target
                    self.awaiting_target = None
                    if u.staged is not None and not u.entered:
                        g.unstage_unit(u)
                    g.clear_bomber_target(u)
                    self.flash("Target pick cancelled - the bomber went back "
                               "to the tray.")
                    return
                if g.phase == "over":
                    pygame.quit()
                    sys.exit()
                self.msg = ""
                if self.trace:
                    g.abort_russian_move(self.trace) \
                        if self.trace.side == "soviet" \
                        else g.abort_fighter_move(self.trace)
                    self.flash("Move aborted.")
                if self.sel_unit is not None and \
                        getattr(self.sel_unit, "entering", False):
                    g.abort_russian_move(self.sel_unit)
                    self.flash("Entry aborted - unit returned to the pool.")
                self.trace = None
                self.sel_unit = None
                self.sel_dests = {}
                self.entry_group = None
                self.tray_unit = None
                self.stage_kind = None
                self.us_kind = None
                self.banner_hint = True
            elif e.key == pygame.K_TAB:
                self.peek = True
            elif e.key == pygame.K_c:
                self.hide_units = True
            step = 60
            if e.key == pygame.K_LEFT:
                self.view.pan(step, 0)
            elif e.key == pygame.K_RIGHT:
                self.view.pan(-step, 0)
            elif e.key == pygame.K_UP:
                self.view.pan(0, step)
            elif e.key == pygame.K_DOWN:
                self.view.pan(0, -step)
        elif e.type == pygame.KEYUP:
            if e.key == pygame.K_TAB:
                self.peek = False
            elif e.key == pygame.K_c:
                self.hide_units = False
        elif e.type == pygame.MOUSEWHEEL:
            mx, my = pygame.mouse.get_pos()
            if self.map_rect.collidepoint(mx, my):
                self.view.zoom_at(mx, my, 1.15 if e.y > 0 else 1 / 1.15)
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 3:
            self.panning = True
        elif e.type == pygame.MOUSEBUTTONUP and e.button == 3:
            self.panning = False
        elif e.type == pygame.MOUSEMOTION and self.panning:
            self.view.pan(*e.rel)
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            await self.click(*e.pos)

    async def click(self, sx, sy):
        self.msg = ""            # a new input replaces the old message
        if self.awaiting_target is not None:
            if sx >= PANEL_W:
                mx, my = self.view.to_map(sx, sy)
                self.click_pick_target(self.cell_at(mx, my))
            else:
                self.flash("First click this bomber's target city on the "
                           "map (green = reachable).", ERRRED)
            return
        for rect, label, cb, enabled, _bg in self.buttons:
            if rect.collidepoint(sx, sy):
                if enabled:
                    r = cb()
                    if inspect.isawaitable(r):
                        await r
                return
        if self.human_sov and self.game.phase in ("russian", "cuban_setup"):
            if self.stage_kind is not None:
                for r, cid, grp in getattr(self, "_slot_rects", []):
                    if r.collidepoint(sx, sy):
                        self.stage_slot_click(cid, grp)
                        return
            for r, u in getattr(self, "_staged_rects", []):
                if r.collidepoint(sx, sy):
                    if (self.game.phase == "cuban_setup"
                            and u.stage_group == "cuban"):
                        self.cuban_unstage_click(u)
                    else:
                        self.staged_click(u)
                    return
        if sx < PANEL_W:
            self.panel_click(sx, sy)
            return
        mx, my = self.view.to_map(sx, sy)
        cid = self.cell_at(mx, my)
        await self.map_click(cid)

    def cell_at(self, mx, my):
        for cid, cell in self.game.board.cells.items():
            if self.point_in_poly(mx, my, cell["poly"]):
                return cid
        return None

    @staticmethod
    def point_in_poly(x, y, poly):
        inside = False
        j = len(poly) - 1
        for i in range(len(poly)):
            xi, yi = poly[i]
            xj, yj = poly[j]
            if (yi > y) != (yj > y) and \
                    x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                inside = not inside
            j = i
        return inside

    # ------------------------------------------------------------- clicks
    def _us_pool(self, key):
        """Unplaced US units of a stack key: 'fighter' / 'decoy_fighter' /
        'missile' and their 'ca_' (Canadian) variants."""
        g = self.game
        ca = key.startswith("ca_")
        kind = key[3:] if ca else key
        return [u for u in g.us_placement_units()
                if u.kind == kind and bool(u.canadian) == ca]

    def us_stack_keys(self):
        keys = ["fighter", "decoy_fighter", "missile"]
        if self.game.opt["balance"]:
            keys.append("us_decoy_missile")     # Play Balance: the missile decoy
        if self.game.opt["canadian"]:
            # The Canadian force is fighters + decoys only (no missile).
            keys += ["ca_fighter", "ca_decoy_fighter"]
        return keys

    def panel_click(self, sx, sy):
        g = self.game
        if g.phase == "us_setup" and self.human_us:
            for r, k in getattr(self, "_stage_tray_rects", []):
                if r.collidepoint(sx, sy):
                    self.us_kind = None if k == self.us_kind else k
                    self.flash("Click legal (green) cities to place from this "
                               "stack." if self.us_kind else "Stack deselected.")
                    return
        elif (self.human_sov and (
                (g.phase == "russian" and g.needs_staging())
                or g.phase == "cuban_setup")):
            for r, k in getattr(self, "_stage_tray_rects", []):
                if r.collidepoint(sx, sy):
                    self.stage_kind = None if k == self.stage_kind else k
                    if self.stage_kind is None:
                        self.flash("Stack deselected.")
                    elif self.stage_kind in ("missile", "decoy_missile"):
                        self.flash("Click coastal squares to launch missiles "
                                   "(the stack stays selected).")
                    else:
                        self.flash("Click start slots to place from this "
                                   "stack (it stays selected).")
                    return

    async def map_click(self, cid):
        if self.awaiting_target is not None:
            self.click_pick_target(cid)
            return
        if cid is None:
            self.flash("Outside the playing area.", ERRRED)
            return
        g = self.game
        if g.phase == "us_setup" and self.human_us:
            self.click_us_setup(cid)
        elif g.phase in ("slbm_targets", "bomber_targets") and self.human_sov:
            self.click_assign(cid)
        elif g.phase == "russian" and self.human_sov:
            await self.click_russian(cid)
        elif g.phase == "american" and self.human_us:
            await self.click_american(cid)

    def click_us_setup(self, cid):
        g = self.game
        if self.us_kind:
            pool = self._us_pool(self.us_kind)
            if not pool:
                self.us_kind = None
                return
            ok, why = g.place_us(pool[0], cid)
            if not ok:
                self.flash(why, ERRRED)
                return
            self.flash(f"Placed at {g.board.city(cid)['name']} ({cid}). "
                       "Place more, or pick another stack.")
            if not self._us_pool(self.us_kind):
                self.us_kind = None
            return
        # no stack selected: click a placed unit to return it to its stack
        placed = [u for u in g.us_units() if u.cell == cid]
        if placed:
            g.unplace_us(placed[-1])
            self.flash(f"Returned a unit from {cid} to its stack.")

    def click_assign(self, cid):
        g = self.game
        if not g.board.is_city(cid):
            self.flash("Click a city square.", ERRRED)
            return
        todo = g.assignable_bombers()
        if todo:
            u = todo[0]
            ok, why = g.assign_target(u, cid)
            if not ok:
                self.flash(why, ERRRED)
                return
            self.flash(f"{u.id} assigned to {g.board.city(cid)['name']}.")
            if g.phase not in ("slbm_targets", "bomber_targets"):
                self.flash(self.phase_hint())

    # ---------------- Soviet turn clicks
    async def click_russian(self, cid):
        g = self.game
        # Clicking a sub-launched missile placed this turn removes it so it can
        # be set down elsewhere - but ONLY during the staging step. Once staging
        # is done (the movement step) a placed SLBM is committed: clicking it
        # does nothing.
        if cid is not None and not self.trace and g.needs_staging():
            slbm_here = [u for u in g.at(cid, "soviet")
                         if u.kind in ("missile", "decoy_missile")
                         and getattr(u, "slbm_turn", None) == g.turn]
            if slbm_here:
                g.unenter_slbm(slbm_here[0])
                self.flash("Sub-launched missile removed - pick it from the "
                           "tray to place it again.")
                return
        if self.stage_kind is not None:
            if self.stage_kind in ("missile", "decoy_missile"):
                pool = self._kind_pool(self.stage_kind)
                if not pool:
                    self.stage_kind = None
                    return
                if cid in g.entry_cells("slbm"):
                    g.enter_unit(pool[0], cid, "slbm")
                    self.turn_problems = []
                    if not self._kind_pool(self.stage_kind):
                        self.stage_kind = None
                    self.flash(f"Sub-launched missile placed at {cid}.")
                else:
                    self.flash("Click a highlighted coastal square.", ERRRED)
            else:
                self.flash("Click a highlighted start slot (near the edge) "
                           "to stage from this stack.", ERRRED)
            return
        if self.entry_group:
            self.do_entry_click(cid)
            return
        if self.trace:
            await self.trace_click_soviet(cid)
            return
        if (self.classic_move and self.sel_unit
                and cid in self._blocked_dests):
            await self.gate("target", self._target_block_msg(self.sel_unit))
            return
        if self.classic_move and self.sel_unit and cid in self.sel_dests:
            u = self.sel_unit
            path = self.sel_dests[cid]
            self.sel_unit = None
            self.sel_dests = {}
            if u.entering:
                for step in path:
                    ask = await self._fire_cb([step])
                    if g.russian_step(u, step, ask) == "dead":
                        self.flash(g.log[-1], ERRRED)
                        await self.gate("combat", g.log[-1])
                        return
                g.end_russian_move(u)
                await self.maybe_bomb(u)
                return
            ask = await self._fire_cb(path)
            res = g.move_russian(u, path, ask)
            if res == "dead":
                self.flash(g.log[-1], ERRRED)
                await self.gate("combat", g.log[-1])
            elif not u.alive:           # decoy dashed onto a held-fire missile
                await self.gate("combat", g.log[-1])
            else:
                await self.maybe_bomb(u)
                await self.enforce_lateral(u)
            return
        stack = [u for u in g.at(cid, "soviet")]
        if not stack:
            self.sel_unit = None
            self.sel_dests = {}
            return
        if len(stack) > 1:
            u = await self.pick_unit(cid, stack, f"Select a unit at {cid}")
            if u is None:
                return
        else:
            u = stack[-1]
        if u.frozen:
            self.flash("This unit can no longer move.", ERRRED)
            return
        if u.moved_turn == g.turn:
            if u.move_start is None:
                if (u.kind in ("missile", "decoy_missile")
                        and getattr(u, "slbm_turn", None) == g.turn):
                    if not g.needs_staging():        # committed once staging ends
                        return                       # no effect during movement
                    g.unenter_slbm(u)
                    self.entry_group = None
                    self.flash("Sub-launched missile removed - use 'Place "
                               "sub-launched missile' to set it down again.")
                    return
                self.flash("This unit entered the board this turn.", ERRRED)
                return
            entry = (u.move_start == game_rules.OFFBOARD)
            q = ("Abort its entry and return to starting location?"
                 if entry
                 else "Abort that move and start it again?")
            if await self.ask_yesno(["SOVIET PLAYER:",
                                     "This unit has already moved this turn.",
                                     q]):
                g.abort_russian_move(u)
                if u.cell is None:
                    self.flash("Unit returned to its staging slot.")
                else:
                    self.select_soviet(u)
            return
        self.select_soviet(u)

    def select_soviet(self, u):
        g = self.game
        if u.entering:                  # resume an interrupted entry
            if self.classic_move:
                self.sel_unit = u
                self.sel_dests = g.legal_entry_dests(u)
            else:
                self.trace = u
            self.flash("Finish this unit's entry move.")
            return
        if self.classic_move:
            dests = g.legal_russian_dests(u)
            if not dests:
                self.flash("This unit has no legal move.", ERRRED)
                return
            self._blocked_dests = {d for d in dests
                                   if self._target_step_block(u, d, dests)}
            dests = {d: p for d, p in dests.items()
                     if d not in self._blocked_dests}
            self.sel_unit = u
            self.sel_dests = dests
            self.flash("Green = legal destinations. Esc to cancel.")
        else:
            if not g.begin_russian_move(u):
                self.flash("This unit cannot move now.", ERRRED)
                return
            self.trace = u
            self.flash("Trace the path square by square. Click the unit "
                       "to stop; Esc aborts.")

    async def trace_click_soviet(self, cid):
        g = self.game
        u = self.trace
        if cid == u.cell:
            if u.cell == u.move_start:
                g.abort_russian_move(u)
                self.trace = None
                self.flash("Move cancelled.")
                return
            ok, why = g.end_russian_move(u)
            if not ok:
                self.flash(why, ERRRED)
                return
            self.trace = None
            if not u.alive:              # decoy dashed onto a held-fire missile
                await self.gate("combat", g.log[-1])
                return
            await self.maybe_bomb(u)
            await self.enforce_lateral(u)
            return
        opts = g.russian_step_options(u)
        if cid in opts:
            if self._target_step_block(u, cid, opts):
                await self.gate("target", self._target_block_msg(u))
                return
            ask = await self._fire_cb([cid])
            res = g.russian_step(u, cid, ask)
            if res == "dead":
                self.trace = None
                self.flash(g.log[-1], ERRRED)
                await self.gate("combat", g.log[-1])
            elif not g.russian_step_options(u):
                self.flash("No movement left - click the unit to stop "
                           "(or Esc to abort).", HILITE)
        else:
            self.flash("Click an adjacent square (or the unit to stop).",
                       ERRRED)

    async def enforce_lateral(self, u):
        """Undo an ILLEGAL >2 east/west move. A real bomber may exceed the
        lateral limit only if it bombs a city that turn; a decoy only if it
        ends on a missile-defended city (that legal decoy dash is already
        resolved in the engine - the decoy would have been removed - so if a
        decoy reaches here still alive its dash ended somewhere illegal). Call
        after the move (and any bombing/dash) is resolved. Returns True if it
        undid an illegal move."""
        g = self.game
        if (u.side != "soviet" or not u.alive or u.entering
                or not g.lateral_exceeded(u)):
            return False
        lim = game_rules.LATERAL_LIMIT
        if u.kind == "bomber":
            if u.frozen and u.cell in g.destroyed:
                return False             # it bombed - the move was legal
            u.frozen = False             # undo any row-V freeze from this move
            why = (f"A bomber may only move more than {lim} squares east or "
                   "west in a turn if it bombs a city that turn.")
        else:                            # decoy_bomber
            why = (f"A decoy may only move more than {lim} squares east or west "
                   "in a turn to reach a city defended by an American missile.")
        g.abort_russian_move(u)
        self.trace = None
        self.sel_unit = None
        self.sel_dests = {}
        await self.gate("illegal", why + " This move is illegal - the unit has "
                        "been returned to where it started the turn.")
        return True

    async def maybe_bomb(self, u):
        g = self.game
        if g.can_bomb(u):
            city = g.board.city(u.cell)
            if self.human_sov:
                if await self.ask_yesno(
                        ["SOVIET PLAYER:",
                         f"Your unit has stopped at {city['name']} "
                         f"({city['points']} points).",
                         "Bomb the city?"]):
                    g.bomb(u)
                    await self.gate("bomb", g.log[-1])
                    await self.check_dew_break()
            else:
                g.bomb(u)
                await self.gate("bomb", g.log[-1])
                await self.check_dew_break()

    def staged_click(self, u):
        g = self.game
        if self.trace:
            self.flash("Finish or abort the current move first.", ERRRED)
            return
        if u.stage_group == "cuban":
            # Cuban units launch during the MOVEMENT step, not while staging the
            # north/Siberian wave - otherwise a stray click sends one onto the
            # board a turn early.
            if g.needs_staging():
                self.flash("Finish staging first - Cuban-based units launch "
                           "during movement.", ERRRED)
                return
            ok, why = g.cuban_launch(u)
            if not ok:
                self.flash(why, ERRRED)
                return
            self.flash(f"Cuban-based unit entered at {u.cell}; it advances "
                       "north on following turns.")
            return
        if g.needs_staging():
            had = g.opt["targets"] and u.real and u.target
            g.unstage_unit(u)
            g.clear_bomber_target(u)
            self.flash("Unit returned to the off-board pool."
                       + (" Its target was cleared." if had else ""))
            return
        ok, why = g.launch_staged(u)
        if not ok:
            self.flash(why, ERRRED)
            return
        if self.classic_move:
            dests = g.legal_entry_dests(u)
            if not dests:
                g.abort_russian_move(u)
                self.flash("No legal way to finish that entry now.", ERRRED)
                return
            self.sel_unit = u
            self.sel_dests = dests
            self.flash(f"On the board at {u.cell}. Green = squares where "
                       "the entry may stop.")
        else:
            self.trace = u
            self.flash(f"On the board at {u.cell}. Trace the rest of the "
                       "entry; click the unit to stop.")

    def do_entry_click(self, cid):
        g = self.game
        cells = g.entry_cells(self.entry_group)
        if cid not in cells:
            self.flash("Not a legal entry square.", ERRRED)
            return
        pools = {"north": g.offboard_bombers, "siberian": g.offboard_bombers,
                 "cuban": g.offboard_cuban, "slbm": g.offboard_slbms}
        group = self.entry_group
        pool = pools[group]()
        if not pool:
            self.entry_group = None
            return
        cand = [u for u in pool if u.real == self.entry_real]
        if not cand:
            kind = "bombers" if self.entry_real else "decoys"
            if group == "slbm" and self.entry_real:
                kind = "missiles"
            self.flash(f"No {kind} left in that force - use the "
                       "'Next entry' button to switch.", ERRRED)
            return
        if (self.entry_real and g.opt["targets"]
                and group in ("north", "siberian")):
            dmap = game_ai.bfs_dist(g.board, cid)
            cand.sort(key=lambda u: dmap.get(u.target, 999))
        u = cand[0]
        self.turn_problems = []
        if group in ("north", "siberian"):      # place on the red band
            ok, why = g.stage_unit(u, cid, group)
            if not ok:
                self.flash(why, ERRRED)
                return
            self.flash("Unit staged. Stage more, or press 'Done staging'.")
            if not (g.stage_cells(group)
                    and [x for x in pools[group]()
                         if x.real == self.entry_real]):
                self.entry_group = None
            return
        g.enter_unit(u, cid, group)
        self.entry_group = None
        self.flash(f"Unit entered at {cid}.")

    def _kind_pool(self, kind):
        """Off-board units of a given kind available in the current context."""
        g = self.game
        if g.phase == "cuban_setup":
            return [u for u in g.cuban_to_place() if u.kind == kind]
        if kind in ("bomber", "decoy_bomber"):
            return [u for u in g.offboard_bombers() if u.kind == kind]
        if kind in ("missile", "decoy_missile"):
            return [u for u in g.offboard_slbms() if u.kind == kind]
        return []

    def stage_slot_click(self, cid, group):
        """Place the next unit of the selected stack onto a start slot. The
        stack stays selected so several can be placed in a row."""
        g = self.game
        kind = self.stage_kind
        if kind is None:
            return
        if kind in ("missile", "decoy_missile"):
            self.flash("Missiles launch onto a coastal square, not a start "
                       "slot.", ERRRED)
            return
        pool = self._kind_pool(kind)
        if not pool:
            self.stage_kind = None
            self.flash("None of that type left.", ERRRED)
            return
        if group == "cuban" and g.cuban_staged_count() >= g.CUBAN_MAX:
            self.flash(f"Only {g.CUBAN_MAX} Cuban units may be placed.", ERRRED)
            return
        u = pool[0]
        ok, why = g.stage_unit(u, cid, group)
        if not ok:
            self.flash(why, ERRRED)
            return
        self.turn_problems = []
        if not self._kind_pool(kind):          # this stack is now empty
            self.stage_kind = None
        if (g.opt["targets"] and u.real
                and u.kind in ("bomber", "decoy_bomber")):
            self.awaiting_target = u
            self.flash("Select this bomber's target city on the map "
                       "(green = reachable).")
        else:
            self.flash("Unit staged. Place more from the stack, or pick "
                       "another.")

    def _target_step_block(self, u, cid, opts):
        """Should moving `u` onto `cid` be blocked because it would make its
        assigned target unreachable? Only blocks when a target-preserving
        alternative exists, so a bomber is never trapped with no legal move."""
        g = self.game
        if not (g.opt["targets"] and u.side == "soviet" and u.real
                and u.target and u.kind in ("bomber", "decoy_bomber")):
            return False
        if g.can_reach(u, cid, u.target):
            return False
        return any(g.can_reach(u, o, u.target) for o in opts if o != cid)

    def _target_block_msg(self, u):
        name = self.game.board.city(u.target)["name"]
        return (f"That move would leave this bomber unable to reach its "
                f"assigned target, {name} ({u.target}). The move is blocked - "
                "choose a square that keeps the target reachable.")

    def reachable_cities_cached(self, u):
        """Cities u can still reach from its start line, cached by unit+start
        so the per-frame highlight during target selection is cheap."""
        g = self.game
        key = (id(u), g.bomber_start_cell(u), len(g.destroyed))
        if self._reach_key != key:
            self._reach_key = key
            self._reach_set = set(g.reachable_target_cities(u))
        return self._reach_set

    def click_pick_target(self, cid):
        """Assign the pending real bomber's one target city. Rejects
        non-cities and cities it cannot reach from its start line."""
        g = self.game
        u = self.awaiting_target
        if u is None:
            return
        if cid is None or not g.board.is_city(cid):
            self.flash("Click a city square for this bomber's target.", ERRRED)
            return
        ok, why = g.assign_bomber_target(u, cid)
        if not ok:
            self.flash(why, ERRRED)
            return
        self.awaiting_target = None
        self.flash(f"Bomber targeted on {g.board.city(cid)['name']} ({cid}). "
                   "Purple marks its target.")

    # ---------------- American turn clicks
    async def click_american(self, cid):
        g = self.game
        if self.trace:
            self.trace_click_fighter(cid)
            return
        if self.classic_move and self.sel_unit and cid in self.sel_dests:
            u = self.sel_unit
            self.sel_unit = None
            self.sel_dests = {}
            g.move_fighter(u, cid)
            self.flash(f"Fighter committed to {cid}. (Click it again to "
                       "abort and re-move.)")
            return
        here = g.at(cid, "us")
        moved_here = [u for u in here
                      if u.kind == "fighter" and u.moved_turn == g.turn
                      and u.move_start is not None]
        movable = [u for u in here if u in g.movable_fighters()]
        if len(here) > 1 and (movable or moved_here):
            u = await self.pick_unit(cid, here, f"Select a unit at {cid}")
            if u is None:
                return
            if u in movable:
                self.select_fighter(u)
            elif u in moved_here:
                if await self.ask_yesno(["AMERICAN PLAYER:",
                                         "This fighter has already moved this "
                                         "turn.",
                                         "Abort that move and start it "
                                         "again?"]):
                    g.abort_fighter_move(u)
                    self.select_fighter(u)
            else:
                self.flash("That unit cannot move (missiles and decoys "
                           "never move).", ERRRED)
            return
        if movable:
            self.select_fighter(movable[-1])
        elif moved_here:
            u = moved_here[-1]
            if await self.ask_yesno(["AMERICAN PLAYER:",
                                     "This fighter has already moved this "
                                     "turn.",
                                     "Abort that move and start it again?"]):
                g.abort_fighter_move(u)
                self.select_fighter(u)
        else:
            if here:
                self.flash("No movable fighter here (missiles and decoys "
                           "never move).", ERRRED)
            self.sel_unit = None
            self.sel_dests = {}

    def select_fighter(self, u):
        g = self.game
        if self.classic_move:
            self.sel_unit = u
            self.sel_dests = g.legal_fighter_dests(u)
            self.flash("Green = reachable squares. Moving a fighter "
                       "commits it for this turn. Esc cancels.")
        else:
            if not g.begin_fighter_move(u):
                self.flash("This fighter cannot move.", ERRRED)
                return
            self.trace = u
            self.flash("Trace the fighter's path. Click it to stop; "
                       "Esc aborts. Moving commits it this turn.")

    def trace_click_fighter(self, cid):
        g = self.game
        u = self.trace
        if cid == u.cell:
            if u.cell == u.move_start:
                g.abort_fighter_move(u)
                self.trace = None
                self.flash("Move cancelled.")
                return
            ok, why = g.end_fighter_move(u)
            if not ok:
                self.flash(why, ERRRED)
                return
            self.trace = None
            self.flash("Fighter committed. (Click it again to abort "
                       "and re-move before ending the turn.)")
            return
        opts = g.fighter_step_options(u)
        if cid in opts:
            g.fighter_step(u, cid)
            if not g.fighter_step_options(u):
                self.flash("No movement left - click the fighter to stop "
                           "(or Esc to abort).", HILITE)
        else:
            self.flash("Click an adjacent square (or the fighter to stop).",
                       ERRRED)

    # ------------------------------------------------------------- buttons
    def make_buttons(self, y):
        g = self.game
        self.buttons = []
        x, w = 14, PANEL_W - 28

        def add(label, cb, enabled=True, bg=None, gap=0):
            nonlocal y
            nlines = len(self.wrap(label, w - 16, self.small))
            bh = max(30, nlines * 16 + 10)
            r = pygame.Rect(x, y, w, bh)
            self.buttons.append((r, label, cb, enabled, bg))
            y += bh + 6 + gap

        # Rulebook reference - always available, on every phase.
        add("NORAD Rulebook (PDF)", self.open_rulebook, bg=TABS_BG, gap=4)

        if g.phase == "over":
            add("Reveal all units", self.toggle_reveal)
            add("Exit game", self.quit_app)
            return y
        if not self.human_turn():
            return y
        if g.phase == "cuban_setup":
            add(f"Confirm Cuban placement "
                f"({g.cuban_staged_count()}/{g.CUBAN_MAX} placed)",
                self.confirm_cuban_placement)
        elif g.phase in ("slbm_targets", "bomber_targets"):
            add("Auto-assign remaining", self.do_auto_assign)
        elif g.phase == "us_setup":
            left = len(g.us_placement_units())
            add(f"Confirm setup ({left} left)", self.confirm_us_setup,
                enabled=(left == 0))
        elif g.phase == "russian":
            # Units are staged from the panel tray (click a unit, then a start
            # slot); no mode buttons are needed here anymore.
            if g.needs_staging():
                add(f"Done staging ({len(g.staged_units())} staged)",
                    self.done_staging)
            elif not g.russian_turn_problems():
                add("End Soviet turn", self.end_russian)
        elif g.phase == "american":
            add("End American turn (resolve combat)", self.end_american)
        return y

    def done_staging(self):
        ok, why = self.game.finish_staging()
        if not ok:
            self.flash(why, ERRRED)
        else:
            self.entry_group = None
            self.stage_kind = None
            self.flash("Staging complete. Click each staged unit to fly "
                       "it onto the board, then move your other units.")

    def toggle_entry_real(self):
        self.entry_real = not self.entry_real
        self.flash("Entering " + ("bombers." if self.entry_real
                                  else "decoys."))

    def set_entry(self, group):
        self.entry_group = group
        if group in ("north", "siberian"):
            self.entry_mode = "bomber"
        elif group == "slbm":
            self.entry_mode = "missile"
        self.trace = None
        self.sel_unit = None
        self.sel_dests = {}
        self.flash("Yellow = legal entry squares. Esc to cancel.")

    def confirm_cuban_placement(self):
        self.stage_kind = None
        self.game.finish_cuban_setup()
        self.flash(self.phase_hint())

    def cuban_unstage_click(self, u):
        self.game.unplace_cuban(u)
        self.game.clear_bomber_target(u)
        self.flash("Cuban unit picked back up.")

    def do_auto_assign(self):
        self.game.auto_assign_targets()
        self.flash(self.phase_hint())

    def confirm_us_setup(self):
        self.game.finish_us_setup()
        self.flash(self.phase_hint())

    async def end_russian(self):
        if self.trace:
            self.flash("Finish or abort the current move first.", ERRRED)
            return
        unmoved = self.game.unmoved_missiles()
        if unmoved:
            n = len(unmoved)
            noun = ("A sub-launched missile has" if n == 1
                    else f"{n} sub-launched missiles have")
            if not await self.ask_yesno(
                    ["SOVIET PLAYER:",
                     f"{noun} not moved this turn.",
                     "Any missile left unmoved will be removed from the map.",
                     "End the turn anyway?"]):
                self.flash("Move your sub-launched missile(s) before ending "
                           "the turn.", ERRRED)
                return
        problems = self.game.end_russian_turn()
        if problems:
            self.turn_problems = problems
            self.flash(problems[0][0], ERRRED)
        else:
            self.turn_problems = []
            self.sel_unit = None
            self.sel_dests = {}
            self.entry_group = None
            await self.announce_stuck()
            self.flash(self.phase_hint())

    async def end_american(self):
        if self.trace:
            self.flash("Finish or abort the current move first.", ERRRED)
            return
        self.sel_unit = None
        self.sel_dests = {}
        await self.resolve_american_combat()
        self.flash(self.phase_hint())

    def toggle_reveal(self):
        self.reveal_all = not self.reveal_all

    def quit_app(self):
        pygame.quit()
        sys.exit()

    def open_rulebook(self):
        """Show the rulebook PDF: a new browser tab in the web build, the OS
        default PDF viewer on the desktop. The file ships next to the page
        online (docs/) and in the project root for the desktop game."""
        if self.web_window is not None:
            try:
                self.web_window.open("NORAD%20Rulebook.pdf", "_blank")
            except Exception:
                self.flash("Could not open the rulebook.", ERRRED)
            return
        path = os.path.join(ROOT, "NORAD Rulebook.pdf")
        if not os.path.exists(path):
            self.flash("Rulebook PDF not found.", ERRRED)
            return
        try:
            os.startfile(path)                    # Windows default viewer
        except AttributeError:                    # non-Windows fallback
            import webbrowser
            webbrowser.open("file://" + path)
        except Exception:
            self.flash("Could not open the rulebook.", ERRRED)

    # ------------------------------------------------------------- drawing
    def sprite(self, uid, size, rot, back, done=False):
        img = self.back[uid] if back else self.front[uid]
        key = (img, size, round(rot, 1), done)
        got = self._sprites.get(key)
        if got is None:
            tile = pygame.Surface((size, size), pygame.SRCALPHA)
            tile.blit(pygame.transform.smoothscale(img, (size, size)),
                      (0, 0))
            pygame.draw.rect(tile, HILITE if done else (30, 30, 30),
                             pygame.Rect(0, 0, size, size), 2 if done else 1)
            if abs(rot % 360) > 0.05:
                tile = pygame.transform.rotate(tile, rot)
            if len(self._sprites) > 900:
                self._sprites.clear()
            self._sprites[key] = tile
            got = tile
        return got

    def _sib_geom(self, cid):
        """Geometry for a Siberian start cell's staging outline, in map coords:
        (anchor, along-band tangent, outward/west normal). The band is a
        straight strip (SIB_BAND_LINE) a few degrees off the westmost radial,
        so anchoring to the cell's own west edge would drift out of the band
        lower down; instead we project the cell's west-edge midpoint onto the
        band centreline and anchor there, so every row sits the same depth in."""
        b = self.game.board
        ax, ay = b.apex
        poly = b.cells[cid]["poly"]
        th = sorted(poly, key=lambda p: math.atan2(p[1] - ay, p[0] - ax))
        p1, p2 = th[-2], th[-1]              # two westmost (max-theta) corners
        emid = ((p1[0] + p2[0]) / 2.0, (p1[1] + p2[1]) / 2.0)
        (x0, y0), (x1, y1) = SIB_BAND_LINE
        dx, dy = x1 - x0, y1 - y0
        dl = math.hypot(dx, dy) or 1.0
        tx, ty = dx / dl, dy / dl           # tangent, along the band
        s = (emid[0] - x0) * tx + (emid[1] - y0) * ty
        anchor = (x0 + tx * s, y0 + ty * s)  # emid projected onto the band line
        nx, ny = -ty, tx                    # perpendicular
        ccx, ccy = b.cells[cid]["center"]   # outward = the side away from
        if ((anchor[0] + nx - ccx) ** 2 + (anchor[1] + ny - ccy) ** 2
                < (anchor[0] - nx - ccx) ** 2 + (anchor[1] - ny - ccy) ** 2):
            nx, ny = -nx, -ny               # ...the cell centre (into the band)
        return anchor, (tx, ty), (nx, ny)

    def slot_pos_for(self, cid, group):
        """Map-pixel position of a red-band staging slot."""
        b = self.game.board
        cx, cy = b.cells[cid]["center"]
        if group == "north" and self.game.dew_stage_active():
            return cx, cy                    # the H square IS the line
        ax, ay = b.apex
        dx, dy = cx - ax, cy - ay
        r = math.hypot(dx, dy)
        if group == "north":
            k = (r - 145.0) / r              # one cell outward (north)
            return ax + dx * k, ay + dy * k
        if group == "cuban":
            k = (r + 155.0) / r              # one cell outward (south of V)
            return ax + dx * k, ay + dy * k
        # siberian: hug the red 'OPTIONAL RUSSIAN START LINE' band - the
        # square's east edge sits at the band midline, parallel to the band
        # (not polar north, which the fan geometry would otherwise impose).
        anchor, _t, (nx, ny) = self._sib_geom(cid)
        size_m = max(14, min(64, int(40 * self.view.zoom
                                     / self.view.min_zoom * 0.5)))
        off = size_m / 2.0 / self.view.zoom - SIB_BAND_MID
        return anchor[0] + nx * off, anchor[1] + ny * off

    def slot_pos(self, u):
        return self.slot_pos_for(u.staged, u.stage_group)

    def north_rot(self, cid):
        b = self.game.board
        cx, cy = b.cells[cid]["center"]
        theta = math.degrees(math.atan2(cy - b.apex[1], cx - b.apex[0]))
        return 90.0 - theta

    def unit_rot(self, u, cid, back=False):
        """US and Cuban/SLBM units face grid north; the main Soviet force
        (north/Siberian entries) faces south - its direction of attack.
        Any unit showing its back (mushroom clouds on destroyed cities,
        TAB peeks, reveals) is oriented to grid north."""
        rot = self.north_rot(cid)
        if (not back and u.side == "soviet"
                and u.kind in ("bomber", "decoy_bomber")
                and u.group != "cuban"):
            rot += 180.0
        return rot

    def unit_shows_back(self, u):
        if self.reveal_all or u.revealed:
            return True
        if not self.peek:
            return False
        side = self.side_on_turn()
        human = self.human_sov if side == "soviet" else self.human_us
        return human and u.side == side

    def draw(self, modal=None):
        scr = self.screen
        scr.fill(BG)
        self.draw_map(scr)
        self.draw_panel(scr)
        self.draw_hover_tooltip(scr)
        if self.game and self.game.phase == "over":
            self.draw_over(scr)
        if self.banner:
            self.draw_banner(scr)
        if modal:
            self.draw_modal(scr, modal)
        if self.rules_open:
            self.draw_rules(scr)
        pygame.display.flip()

    def draw_map(self, scr):
        g = self.game
        v = self.view
        scr.set_clip(self.map_rect)
        w = int(self.map_img.get_width() * v.zoom)
        h = int(self.map_img.get_height() * v.zoom)
        if self._scaled_size != (w, h):
            self._scaled = pygame.transform.smoothscale(self.map_img,
                                                        (w, h))
            self._scaled_size = (w, h)
        scr.blit(self._scaled, (v.ox, v.oy))

        def outline(cid, color, width=2):
            pts = [v.to_screen(*p) for p in g.board.cells[cid]["poly"]]
            pygame.draw.polygon(scr, color, pts, width)

        if g.phase == "us_setup" and self.us_kind and self._us_pool(self.us_kind):
            rep_u = self._us_pool(self.us_kind)[0]
            for cid in g.board.cells:
                if g.board.is_city(cid) and g.can_place_us(rep_u, cid)[0]:
                    outline(cid, OKGREEN)
        if g.phase in ("slbm_targets", "bomber_targets") and self.human_sov:
            for cid in g.board.cells:
                if g.board.is_city(cid) and (
                        g.phase != "slbm_targets"
                        or cid in game_rules.COASTAL_CITIES):
                    outline(cid, OKGREEN)
        # Highlight staging slots: Cuban setup uses entry_group; Soviet
        # staging highlights slots for whatever unit is picked from the tray.
        self._slot_rects = []
        slot_specs = []          # red-band square slots: (group, [cells])
        if g.phase == "cuban_setup" and self.stage_kind is not None:
            slot_specs.append(("cuban", g.stage_cells("cuban")))
        elif g.phase == "russian" and self.stage_kind is not None:
            if self.stage_kind in ("missile", "decoy_missile"):
                for cid in g.entry_cells("slbm"):   # coastal launch cells
                    outline(cid, ENTRY_Y)
            else:
                for grp in (["north"]
                            + (["siberian"] if g.opt["siberian"] else [])):
                    slot_specs.append((grp, g.stage_cells(grp)))
        if slot_specs:
            size_m = max(14, min(64, int(40 * v.zoom / v.min_zoom * 0.5)))
            ax, ay = g.board.apex
            for grp, cells in slot_specs:
                for cid in cells:
                    mx, my = self.slot_pos_for(cid, grp)
                    sx0, sy0 = v.to_screen(mx, my)
                    if grp == "north" and g.dew_stage_active():
                        outline(cid, ENTRY_Y)      # slot is the H square itself
                    else:
                        if grp == "siberian":
                            # orient to the red start-line band, not polar north
                            _e, (tx, ty), (ux, uy) = self._sib_geom(cid)
                        else:
                            dx, dy = mx - ax, my - ay  # orient square to polar north
                            rlen = math.hypot(dx, dy) or 1.0
                            ux, uy = dx / rlen, dy / rlen      # radial (N-S)
                            tx, ty = -uy, ux                   # tangential (E-W)
                        half = size_m / 2.0 / v.zoom
                        corners = [
                            v.to_screen(mx + sr * half * ux + st * half * tx,
                                        my + sr * half * uy + st * half * ty)
                            for sr, st in ((1, 1), (1, -1), (-1, -1), (-1, 1))]
                        pygame.draw.polygon(scr, ENTRY_Y, corners, 2)
                    rr = pygame.Rect(0, 0, size_m, size_m)
                    rr.center = (int(sx0), int(sy0))
                    self._slot_rects.append((rr, cid, grp))
        if self.trace:
            u = self.trace
            opts = (g.russian_step_options(u) if u.side == "soviet"
                    else g.fighter_step_options(u))
            for cid in opts:
                if u.side == "soviet" and self._target_step_block(u, cid, opts):
                    continue
                outline(cid, OKGREEN)
            if u.side == "soviet" and u.entering:
                # valid stop squares (rows B-D) shown green, like normal moves
                for cid in g.legal_entry_dests(u):
                    outline(cid, OKGREEN)
            outline(u.cell, HILITE, 3)
            if (u.move_start and u.move_start != u.cell
                    and u.move_start in g.board.cells):
                outline(u.move_start, GREY, 2)
        for cid in self.sel_dests:
            outline(cid, OKGREEN)
        if self.sel_unit and self.sel_unit.cell:
            outline(self.sel_unit.cell, HILITE, 3)
        for cid in g.destroyed:
            outline(cid, ERRRED, 3)
        if self._combat_focus and self._combat_focus in g.board.cells:
            outline(self._combat_focus, COMBAT_FOCUS, 4)
        # Assigned-targets: purple on every targeted city; the bomber being
        # moved (or whose target is being picked) shows blue instead.
        if (g.opt["targets"] and self.human_sov
                and g.phase in ("russian", "cuban_setup")):
            sel = self.trace or self.sel_unit
            tcol = {}
            for u in g.soviet_units():
                if not (u.alive and u.real and u.target
                        and u.kind in ("bomber", "decoy_bomber")):
                    continue
                if u.target in g.destroyed:
                    continue
                col = TARGET_BLUE if (sel is u) else TARGET_PURPLE
                if tcol.get(u.target) != TARGET_BLUE:
                    tcol[u.target] = col
            for cid, col in tcol.items():
                outline(cid, col, 3)
        # While choosing a target: green = cities this bomber can still reach.
        if self.awaiting_target is not None:
            for cid in self.reachable_cities_cached(self.awaiting_target):
                outline(cid, OKGREEN)
        if self.turn_problems:
            shade = pygame.Surface(scr.get_size(), pygame.SRCALPHA)
            for _msg, pcell in self.turn_problems:
                if pcell and pcell in g.board.cells:
                    pts = [v.to_screen(*p)
                           for p in g.board.cells[pcell]["poly"]]
                    pygame.draw.polygon(shade, (235, 60, 60, 95), pts)
            scr.blit(shade, (0, 0))

        # staged Soviet units on the red start line
        self._staged_rects = []
        if not self.hide_units:
            size_s = max(14, min(64, int(40 * v.zoom / v.min_zoom * 0.5)))
            for u in g.staged_units():
                mx, my = self.slot_pos(u)
                sx, sy = v.to_screen(mx, my)
                theta = math.degrees(math.atan2(my - g.board.apex[1],
                                                mx - g.board.apex[0]))
                back = self.unit_shows_back(u)
                face_south = not back and u.stage_group != "cuban"
                rot = (90.0 - theta) + (180.0 if face_south else 0.0)
                img = self.sprite(u.id, size_s, rot, back)
                r = img.get_rect(center=(sx, sy))
                scr.blit(img, r)
                self._staged_rects.append((r, u))

        if not self.hide_units:
            by_cell = {}
            for u in g.units:
                if u.alive and u.cell:
                    by_cell.setdefault(u.cell, []).append(u)
            size = max(14, int(40 * v.zoom / v.min_zoom * 0.5))
            size = min(size, 64)
            for cid, stack in by_cell.items():
                cx, cy = g.board.cells[cid]["center"]
                sx, sy = v.to_screen(cx, cy)
                n = len(stack)
                for i, u in enumerate(stack):
                    back = self.unit_shows_back(u)
                    done = (u.moved_turn == g.turn and not u.entering
                            and not u.revealed
                            and ((u.side == "soviet"
                                  and g.phase == "russian")
                                 or (u.side == "us"
                                     and g.phase == "american")))
                    img = self.sprite(u.id, size,
                                      self.unit_rot(u, cid, back), back,
                                      done)
                    off = (i - (n - 1) / 2) * size * 0.25
                    scr.blit(img, img.get_rect(center=(sx + off, sy - off)))
        scr.set_clip(None)

    # panel ------------------------------------------------------------
    def tray_rect_for(self, u):
        units = self.game.us_placement_units()
        if u not in units:
            return None
        i = units.index(u)
        s, gap, x0 = 44, 6, 14
        y0 = getattr(self, "_tray_y0", 110)
        per = (PANEL_W - 2 * x0) // (s + gap)
        r, c = divmod(i, per)
        return pygame.Rect(x0 + c * (s + gap), y0 + r * (s + gap), s, s)

    def draw_kind_stacks(self, scr, y, kinds):
        """Draw one stack tile per unit type. `kinds` is a list of
        (kind, pool); empty pools are skipped. Each tile shows the sprite, an
        'xN' count, and a white 'D' in the bottom-left corner for decoy types;
        the selected stack is outlined (white in American setup, yellow in
        Soviet staging). Fills self._stage_tray_rects with (rect, kind) and
        returns the y below the tiles."""
        s, gap, x0, pad, labelh = 50, 18, 14, 8, 18
        per = max(1, (PANEL_W - 2 * x0) // (s + gap))
        shown = [(k, pool) for k, pool in kinds if pool]
        rects, rows = [], 0
        rowh = pad + s + labelh
        for i, (k, pool) in enumerate(shown):
            r, c = divmod(i, per)
            rows = r + 1
            rect = pygame.Rect(x0 + c * (s + gap),
                               y + r * (rowh + gap) + pad, s, s)
            img = pygame.transform.smoothscale(self.front[pool[0].id], (s, s))
            n = len(pool)
            for d in range(min(3, n) - 1, 0, -1):     # stacked-counter look
                scr.blit(img, (rect.x + 3 * d, rect.y - 3 * d))
            scr.blit(img, rect)
            if not pool[0].real:
                # White 'D' in the bottom-left corner marks a decoy stack
                # (no coloured background).
                scr.blit(self.small.render("D", True, (255, 255, 255)),
                         (rect.left + 2, rect.bottom - 17))
            scr.blit(self.font.render(f"x{n}", True, WHITE),
                     (rect.x, rect.bottom + 1))
            # Highlight the selected stack: white during American setup,
            # yellow during Soviet staging/Cuban setup.
            selected = (self.us_kind if self.game.phase == "us_setup"
                        else self.stage_kind)
            if selected == k:
                col = WHITE if self.game.phase == "us_setup" else HILITE
                pygame.draw.rect(scr, col, rect.inflate(8, 8), 3)
            rects.append((rect, k))
        self._stage_tray_rects = rects
        return y + rows * (rowh + gap) + 6

    def draw_panel(self, scr):
        g = self.game
        h = scr.get_height()
        pygame.draw.rect(scr, PANEL_BG, (0, 0, PANEL_W, h))
        y = 10
        # ---- title
        if self.title_override:
            title, tcol = self.title_override
        else:
            side = self.side_on_turn()
            tcol = (235, 90, 90) if side == "soviet" else (110, 170, 255)
            if g.phase == "russian":
                title = ("SOVIET STAGING" if g.needs_staging()
                         else "SOVIET MOVEMENT")
            elif g.phase == "american":
                title = "AMERICAN MOVEMENT"
            elif g.phase == "us_setup":
                title = "AMERICAN SETUP"
            elif g.phase == "cuban_setup":
                title = "CUBAN SETUP"
            elif g.phase == "over":
                title, tcol = "GAME OVER", WHITE
            else:
                title = "SOVIET STAGING"
        scr.blit(self.big.render(title, True, tcol), (14, y))
        y += 42
        scr.blit(self.med.render(
            f"Turn {max(1, g.turn)}   Soviet points: {g.points}",
            True, WHITE), (14, y))
        y += 28
        unb = sum(1 for u in g.soviet_units()
                  if u.kind == "bomber" and not u.revealed)
        und = sum(1 for u in g.soviet_units()
                  if u.kind == "decoy_bomber" and not u.revealed)
        scr.blit(self.font.render(
            f"Unrevealed: {unb} bombers, {und} decoys", True, WHITE),
            (14, y))
        y += 24
        if not self.human_turn():
            scr.blit(self.font.render("(computer moving...)", True, GREY),
                     (14, y))
            y += 22

        # ---- phase-specific info
        if g.phase == "us_setup" and self.human_us:
            for line in self.wrap(
                    "Click a stack, then click green cities to place. "
                    "Click a placed unit (no stack picked) to return it.",
                    PANEL_W - 28, self.small):
                scr.blit(self.small.render(line, True, ENTRY_Y), (14, y))
                y += 16
            self._stage_tray_rects = []
            kinds = [(k, self._us_pool(k)) for k in self.us_stack_keys()]
            y = self.draw_kind_stacks(scr, y + 2, kinds) + 4
        elif g.phase == "cuban_setup" and self.human_sov:
            for line in self.wrap(
                    "Cuban force: 3 bombers + 5 decoys. Click a stack, then "
                    "place up to 5 on the yellow start-line slots.",
                    PANEL_W - 28, self.small):
                scr.blit(self.small.render(line, True, ENTRY_Y), (14, y))
                y += 16
            scr.blit(self.font.render(
                f"Placed: {g.cuban_staged_count()}/{g.CUBAN_MAX}",
                True, WHITE), (14, y))
            y += 24
            y = self.draw_kind_stacks(scr, y, [
                ("bomber", self._kind_pool("bomber")),
                ("decoy_bomber", self._kind_pool("decoy_bomber"))])
        elif g.phase in ("slbm_targets", "bomber_targets") \
                and self.human_sov:
            todo = g.assignable_bombers()
            if todo:
                scr.blit(self.font.render(
                    f"Assign: {todo[0].id}  ({len(todo)} left)", True,
                    WHITE), (14, y))
                y += 22
                scr.blit(self.small.render("Click a city square.", True,
                                           GREY), (14, y))
                y += 20
        elif g.phase == "russian" and self.human_sov:
            # Count staged (not yet flown-on) north/Siberian units too, so the
            # tally updates the moment a unit is staged, not when it moves on.
            staged_now = sum(1 for u in g.staged_units()
                             if u.stage_group in ("north", "siberian"))
            scr.blit(self.font.render(
                f"Units entered this turn: "
                f"{g.entered_this_turn + staged_now}", True,
                WHITE), (14, y))
            y += 24
            self._stage_tray_rects = []
            if g.needs_staging():
                for line in self.wrap(
                        "Click a stack, then click start slots (bombers) or "
                        "coastal squares (missiles) to place them:",
                        PANEL_W - 28, self.small):
                    scr.blit(self.small.render(line, True, ENTRY_Y), (14, y))
                    y += 16
                kinds = [("bomber", self._kind_pool("bomber")),
                         ("decoy_bomber", self._kind_pool("decoy_bomber"))]
                if g.opt["slbm"]:
                    kinds += [
                        ("missile", self._kind_pool("missile")),
                        ("decoy_missile", self._kind_pool("decoy_missile"))]
                y = self.draw_kind_stacks(scr, y + 2, kinds)
                y += 4
            n_cuban = sum(1 for u in g.staged_units()
                          if u.stage_group == "cuban")
            if n_cuban:
                for line in self.wrap(
                        f"{n_cuban} Cuban unit(s) on the start line - click "
                        "one to send it onto the board.",
                        PANEL_W - 28, self.small):
                    scr.blit(self.small.render(line, True, ENTRY_Y), (14, y))
                    y += 16
                y += 4
            if not g.needs_staging() and g.russian_turn_problems():
                for line in self.wrap(
                        "Finish all required moves to end the turn.",
                        PANEL_W - 28, self.small):
                    scr.blit(self.small.render(line, True, HILITE), (14, y))
                    y += 16
                y += 4
            if self.trace:
                left = g._steps_left.get(self.trace.id, 0)
                scr.blit(self.font.render(
                    f"Moves left: {left} - click unit to stop", True,
                    HILITE), (14, y))
                y += 22
            for msg, _cell in self.turn_problems[:3]:
                for line in self.wrap(msg, PANEL_W - 28, self.small):
                    scr.blit(self.small.render(line, True, ERRRED), (14, y))
                    y += 17
        elif g.phase == "american" and self.human_us and self.trace:
            left = g._steps_left.get(self.trace.id, 0)
            scr.blit(self.font.render(
                f"Moves left: {left} - click fighter to stop", True,
                HILITE), (14, y))
            y += 24

        # ---- pop-up message ABOVE the buttons, in a fixed-height slot so
        #      items below stay put whether or not a message is showing
        y += 6
        popup_h = 16 * 4 + 14         # reserve room for up to 4 lines
        if self.msg:
            mlines = self.wrap(self.msg, PANEL_W - 44, self.small)[:4]
            bh = 16 * len(mlines) + 14
            r = pygame.Rect(8, y, PANEL_W - 16, bh)
            box = pygame.Surface((r.w, r.h))
            box.fill((5, 5, 8))
            box.set_alpha(245)
            scr.blit(box, r)
            pygame.draw.rect(scr, self.msg_color, r, 2)
            yy = y + 7
            for line in mlines:
                scr.blit(self.small.render(line, True, (255, 255, 255)),
                         (16, yy))
                yy += 16
        y += popup_h + 8

        # ---- action buttons (wrap long labels)
        y = self.make_buttons(y)
        for r, label, cb, enabled, bg in self.buttons:
            base = (bg if bg else BTN_HOT) if enabled else BTN_BG
            pygame.draw.rect(scr, base, r, border_radius=6)
            blines = self.wrap(label, r.w - 16, self.small)
            ty = r.y + (r.h - len(blines) * 16) // 2 + 1
            for ln in blines:
                scr.blit(self.small.render(
                    ln, True, WHITE if enabled else GREY), (r.x + 8, ty))
                ty += 16

        # ---- shortcuts panel: pinned to a fixed low position so it does not
        #      jump as messages/buttons above it change; pushed down only if
        #      the buttons would otherwise overlap it
        shortcut_src = [
            "Shortcuts",
            "TAB - show unit backside",
            "C - hide units/show map",
            "Mouse wheel - zoom",
            "Mouse right click - drag map",
            "R - rules",
        ]
        hint_lines = []
        for s0 in shortcut_src:
            hint_lines.extend(self.wrap(s0, PANEL_W - 32, self.small))
        hint_h = len(hint_lines) * 16 + 10
        y = max(y + 6, min(SHORTCUTS_Y, h - hint_h - 60))
        pygame.draw.rect(scr, TABS_BG,
                         pygame.Rect(8, y, PANEL_W - 16, hint_h),
                         border_radius=5)
        ty = y + 5
        for ln in hint_lines:
            scr.blit(self.small.render(ln, True, (255, 255, 255)),
                     (16, ty))
            ty += 16
        y += hint_h + 8

        # ---- log fills whatever space remains
        scr.blit(self.font.render("Log:", True, GREY), (14, y))
        y += 20
        max_lines = max(0, (h - y - 8) // 16)
        lines = []
        for entry in g.log[-10:]:
            lines.extend(self.wrap(entry, PANEL_W - 24, self.small))
        for seg in lines[-max_lines:] if max_lines else []:
            scr.blit(self.small.render(seg, True, WHITE), (12, y))
            y += 16

    def wrap(self, text, width, font):
        words = text.split()
        lines, cur = [], ""
        for w in words:
            t = (cur + " " + w).strip()
            if font.size(t)[0] <= width:
                cur = t
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        return lines

    def draw_status(self, scr):
        if self.msg and pygame.time.get_ticks() < self.msg_until:
            t = self.font.render(self.msg, True, (255, 255, 255))
            bg = pygame.Surface((t.get_width() + 24, t.get_height() + 12))
            bg.fill((5, 5, 8))
            bg.set_alpha(245)
            x = PANEL_W + 12
            y = scr.get_height() - t.get_height() - 26
            r = bg.get_rect(topleft=(x - 12, y - 6))
            scr.blit(bg, r)
            pygame.draw.rect(scr, self.msg_color, r, 2)
            scr.blit(t, (x, y))

    def draw_hover_tooltip(self, scr):
        """Hover a square to examine the units in it, shown as ICONS rather than
        a text list. No identity leak: each icon is the same sprite the map
        draws - enemy decoys are pixel-identical to their silhouette and hidden
        Soviet units show the plain front silhouette. A thin border tags side
        (blue = US/Canada, red = Soviet)."""
        g = self.game
        if not g or g.phase == "over" or self.banner or self.hide_units:
            return
        mx, my = pygame.mouse.get_pos()
        if mx < PANEL_W or not self.map_rect.collidepoint(mx, my):
            return
        cid = self.cell_at(*self.view.to_map(mx, my))
        if not cid:
            return
        us = list(g.at(cid, "us"))
        sov = list(g.at(cid, "soviet"))
        if not us and not sov:
            return
        head = cid + (f"  {g.board.city(cid)['name']}"
                      if g.board.is_city(cid) else "")
        units = [("us", u) for u in us] + [("sov", u) for u in sov]
        isz, pad, gap, hh = 44, 6, 6, 18
        cols = max(1, min(len(units), 6))
        rows = (len(units) + cols - 1) // cols
        w = max(self.small.size(head)[0] + 12,
                cols * isz + (cols - 1) * gap + 2 * pad)
        h = hh + rows * isz + (rows - 1) * gap + 2 * pad
        bx = min(mx + 16, scr.get_width() - w - 4)
        by = min(my + 16, scr.get_height() - h - 4)
        box = pygame.Surface((w, h)); box.fill((10, 10, 14)); box.set_alpha(242)
        scr.blit(box, (bx, by))
        pygame.draw.rect(scr, HILITE, pygame.Rect(bx, by, w, h), 1)
        scr.blit(self.small.render(head, True, (255, 255, 255)),
                 (bx + 6, by + 3))
        y0 = by + hh + pad
        for i, (side, u) in enumerate(units):
            r, c = divmod(i, cols)
            ix = bx + pad + c * (isz + gap)
            iy = y0 + r * (isz + gap)
            back = self.unit_shows_back(u)          # same reveal rules as the map
            scr.blit(self.sprite(u.id, isz, 0.0, back), (ix, iy))
            tag = (110, 170, 255) if side == "us" else (235, 90, 90)
            pygame.draw.rect(scr, tag, pygame.Rect(ix, iy, isz, isz), 2)

    def draw_banner(self, scr):
        hint = getattr(self, "banner_hint", True)
        lines = self.wrap(self.banner, 560, self.med)
        show = list(lines)
        if hint:
            show.append("(click to continue)")
        bw = max(self.med.size(t)[0] for t in show) + 48
        bh = 28 * len(show) + 24
        cx = PANEL_W + (scr.get_width() - PANEL_W) // 2
        box = pygame.Surface((bw, bh))
        box.fill((5, 5, 8))
        box.set_alpha(252)
        r = box.get_rect(center=(cx, 60 + bh // 2))
        scr.blit(box, r)
        pygame.draw.rect(scr, HILITE, r, 2)
        for i, t in enumerate(show):
            is_hint = hint and i == len(show) - 1
            font = self.font if is_hint else self.med
            col = (215, 215, 215) if is_hint else (255, 255, 255)
            surf = font.render(t, True, col)
            scr.blit(surf, (r.x + 24, r.y + 12 + 28 * i))

    def draw_modal(self, scr, lines):
        yes, no = self._modal_rects(lines)
        w, h = self.screen.get_size()
        bw = max(360, max(self.font.size(t)[0] for t in lines) + 60)
        bh = 90 + 22 * len(lines)
        bx, by = (w + PANEL_W) // 2 - bw // 2, h // 2 - bh // 2
        box = pygame.Surface((bw, bh))
        box.fill((15, 15, 20))
        box.set_alpha(235)
        scr.blit(box, (bx, by))
        pygame.draw.rect(scr, WHITE, (bx, by, bw, bh), 2)
        for i, t in enumerate(lines):
            scr.blit(self.font.render(t, True, WHITE),
                     (bx + 24, by + 16 + 22 * i))
        for r, lab in ((yes, "Yes (Y)"), (no, "No (N)")):
            pygame.draw.rect(scr, BTN_HOT, r, border_radius=6)
            t = self.font.render(lab, True, WHITE)
            scr.blit(t, (r.centerx - t.get_width() // 2,
                         r.centery - t.get_height() // 2))

    def draw_rules(self, scr):
        """Full-screen rules summary, toggled by pressing R. Sized to fit with
        no scrolling: a fixed set of short lines across three columns plus a
        one-line control footer."""
        w, h = scr.get_size()
        margin = 14
        box = pygame.Rect(margin, margin, w - 2 * margin, h - 2 * margin)
        dim = pygame.Surface((w, h))
        dim.fill((0, 0, 0))
        dim.set_alpha(190)
        scr.blit(dim, (0, 0))
        panel = pygame.Surface((box.w, box.h))
        panel.fill((16, 18, 24))
        panel.set_alpha(250)
        scr.blit(panel, box.topleft)
        pygame.draw.rect(scr, HILITE, box, 2)

        title = self.rules_title.render(
            "NORAD - RULES SUMMARY  (press R to close)", True, WHITE)
        scr.blit(title, (box.centerx - title.get_width() // 2, box.y + 8))

        rf = self.rules_body
        line_h = rf.get_height() + 2
        footer_lines = self.wrap(RULES_FOOTER, box.w - 40, rf)
        footer_h = line_h * len(footer_lines) + 10
        roster_h = 92 if self.game else 0
        top_y = box.y + 8 + title.get_height() + 8
        cols_rect = pygame.Rect(box.x + 20, top_y, box.w - 40,
                                box.bottom - footer_h - roster_h - top_y - 16)
        gap = 24
        ncols = len(RULES_COLUMNS)
        col_w = (cols_rect.w - gap * (ncols - 1)) // ncols
        for i, col in enumerate(RULES_COLUMNS):
            cx = cols_rect.x + i * (col_w + gap)
            cy = cols_rect.y
            for kind, text in col:
                if kind == "header":
                    cy += 4
                    surf = self.rules_hdr.render(text, True, HILITE)
                    scr.blit(surf, (cx, cy))
                    cy += surf.get_height() + 3
                    pygame.draw.line(scr, (90, 90, 90), (cx, cy - 2),
                                     (cx + col_w, cy - 2), 1)
                elif kind == "spacer":
                    cy += 6
                else:
                    for line in self.wrap(text, col_w, rf):
                        surf = rf.render(line, True, (225, 225, 225))
                        scr.blit(surf, (cx, cy))
                        cy += surf.get_height() + 1
                    cy += 4

        if self.game:
            roster_y = cols_rect.bottom + 8
            pygame.draw.line(scr, (90, 90, 90), (box.x + 16, roster_y),
                             (box.right - 16, roster_y), 1)
            self.draw_unit_roster(scr, pygame.Rect(
                box.x + 20, roster_y + 6, box.w - 40, roster_h - 10))

        pygame.draw.line(scr, (90, 90, 90), (box.x + 16, box.bottom - footer_h),
                         (box.right - 16, box.bottom - footer_h), 1)
        fy = box.bottom - footer_h + 8
        for line in footer_lines:
            s = rf.render(line, True, GREY)
            scr.blit(s, (box.centerx - s.get_width() // 2, fy))
            fy += line_h

    def _unit_roster_entries(self):
        """(sprite_id, label, count-text) for each visually distinct unit
        silhouette present in the current game - real and decoy counterparts
        of a kind share one silhouette (that's the point: you can't tell them
        apart on sight), so their counts are combined on one line."""
        g = self.game
        units = g.units
        entries = []

        def rep(side, kinds, canadian=None):
            for u in units:
                if (u.side == side and u.kind in kinds
                        and (canadian is None or bool(u.canadian) == canadian)):
                    return u.id
            return None

        def count(side, kind, canadian=None):
            return sum(1 for u in units if u.side == side and u.kind == kind
                      and (canadian is None or bool(u.canadian) == canadian))

        uid = rep("soviet", ("bomber", "decoy_bomber"))
        if uid:
            entries.append((uid, "Soviet Bomber",
                            f'{count("soviet", "bomber")}R/'
                            f'{count("soviet", "decoy_bomber")}D'))
        if g.opt["slbm"]:
            uid = rep("soviet", ("missile", "decoy_missile"))
            if uid:
                entries.append((uid, "Soviet SLBM",
                                f'{count("soviet", "missile")}R/'
                                f'{count("soviet", "decoy_missile")}D'))
        uid = rep("us", ("fighter", "decoy_fighter"), canadian=False)
        if uid:
            entries.append((uid, "US Fighter",
                            f'{count("us", "fighter", False)}R/'
                            f'{count("us", "decoy_fighter", False)}D'))
        if g.opt["canadian"]:
            uid = rep("us", ("fighter", "decoy_fighter"), canadian=True)
            if uid:
                entries.append((uid, "Canadian Fighter",
                                f'{count("us", "fighter", True)}R/'
                                f'{count("us", "decoy_fighter", True)}D'))
        uid = rep("us", ("missile", "us_decoy_missile"))
        if uid:
            nreal = count("us", "missile")
            ndec = count("us", "us_decoy_missile")
            sub = f"{nreal}R" + (f"/{ndec}D" if ndec else "")
            entries.append((uid, "US Missile", sub))
        return entries

    def draw_unit_roster(self, scr, rect):
        entries = self._unit_roster_entries()
        if not entries:
            return
        rf = self.rules_body
        icon_s = 68
        slot_w = rect.w // len(entries)
        for i, (uid, label, sub) in enumerate(entries):
            cx = rect.x + slot_w * i + slot_w // 2
            img = pygame.transform.smoothscale(self.front[uid],
                                               (icon_s, icon_s))
            irect = img.get_rect(midtop=(cx, rect.y))
            scr.blit(img, irect)
            lab = rf.render(f"{label} {sub}", True, HILITE)
            scr.blit(lab, (cx - lab.get_width() // 2, irect.bottom + 3))

    def draw_over(self, scr):
        g = self.game
        who = {"soviet": "SOVIET PLAYER WINS",
               "american": "AMERICAN PLAYER WINS"}.get(g.winner, "GAME OVER")
        wcol = (235, 90, 90) if g.winner == "soviet" else (110, 170, 255)
        lines = [f"Soviet points: {g.points}.  esc - exit game, or use "
                 "'Reveal all units'."]
        if not self.human_sov:
            lines.append(f"Soviet AI doctrine was: {self.rus_ai.style}")
        if not self.human_us:
            lines.append(f"American AI doctrine was: {self.us_ai.style}")
        t = self.big.render(who, True, wcol)
        surfs = [self.font.render(x, True, (255, 255, 255)) for x in lines]
        bw = max([t.get_width()] + [s.get_width() for s in surfs]) + 60
        bh = 70 + 24 * len(surfs)
        cx = PANEL_W + (scr.get_width() - PANEL_W) // 2
        box = pygame.Surface((bw, bh))
        box.fill((0, 0, 0))
        box.set_alpha(215)
        scr.blit(box, box.get_rect(center=(cx, 60 + bh // 2)))
        scr.blit(t, t.get_rect(center=(cx, 85)))
        yy = 112
        for s in surfs:
            scr.blit(s, s.get_rect(center=(cx, yy)))
            yy += 24


async def main():
    # pygbag (the web/WebAssembly build) runs this coroutine as the program's
    # entry point. On the desktop it is driven by asyncio.run() below. The whole
    # game loop is async so the browser build can yield a frame at a time.
    await App().start()


if __name__ == "__main__":
    asyncio.run(main())
