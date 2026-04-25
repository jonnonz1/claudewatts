"""
claudewatts.town — a tiny cyberpunk city that grows with your Claude Code Wh.

Lazy-loads pyxel so the rest of cwatts stays stdlib-only. Run via `cwatts town`.

The town is a *pure function of cumulative watt-hours*: the same Wh always
produces the same scene. No save file, no state, no cheating.
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    import pyxel
except ImportError:
    pyxel = None


# ───────────────────────────────────────────────────────────────────────────
# Cyberpunk palette — overrides pyxel's 16-colour default at startup.
# Indexed by integer; referenced everywhere via NAMES below.
# ───────────────────────────────────────────────────────────────────────────

PALETTE = [
    0x0a0014,  # 0  void           — deepest sky
    0x140033,  # 1  midnight        — high atmosphere
    0x2a0050,  # 2  deep purple     — low atmosphere
    0x1f1f3a,  # 3  blue-grey       — ground shadow
    0x3a2d4d,  # 4  shadow purple   — building shadow side
    0x5c4d6e,  # 5  dim purple-grey — building lit side
    0x8a8a9e,  # 6  dim grey        — building roof
    0xe0e0ff,  # 7  ghost white     — moon, stars
    0xff1a8c,  # 8  neon pink       — primary signage
    0xff5500,  # 9  amber           — sodium streetlights
    0xffcc00,  # 10 sodium yellow   — windows (warm)
    0x00ff66,  # 11 acid green      — terminals, rain
    0x00ddff,  # 12 electric cyan   — windows (cold), holograms
    0x6633ff,  # 13 violet neon     — accents
    0xff66cc,  # 14 soft pink       — neon glow halo
    0xfff0aa,  # 15 warm white      — bright windows
]

VOID, MIDNIGHT, PURPLE = 0, 1, 2
GROUND_A, SHADOW, LIT = 3, 4, 5
ROOF, GHOST = 6, 7
PINK, AMBER, SODIUM = 8, 9, 10
ACID, CYAN, VIOLET = 11, 12, 13
SOFT_PINK, WARM_WHITE = 14, 15


# ───────────────────────────────────────────────────────────────────────────
# Tech tree — Wh threshold → unlocked feature.
# Order matters; everything whose threshold is met is drawn, in order.
# ───────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Unlock:
    wh: float
    name: str
    blurb: str


UNLOCKS: list[Unlock] = [
    Unlock(0,            "empty plot",       "An empty grid waits in the rain."),
    Unlock(50,            "first lamp",      "A solitary lamp post flickers on."),
    Unlock(200,           "dirt path",       "A path materialises in the dust."),
    Unlock(500,           "shack",           "Someone's set up a shack."),
    Unlock(1_500,         "second shack",    "A neighbour arrives. Welcome, neighbour."),
    Unlock(5_000,         "row of cubes",    "A row of cube-houses with neon over the doors."),
    Unlock(15_000,        "parked bikes",    "Hover-bikes line the kerb."),
    Unlock(50_000,        "first tower",     "A four-storey tower goes up."),
    Unlock(150_000,       "traffic",         "Traffic flows along the main road."),
    Unlock(500_000,       "megabuilding",    "A megabuilding pierces the smog."),
    Unlock(1_500_000,     "acid rain",       "Acid rain falls in slow neon stripes."),
    Unlock(5_000_000,     "arcology",        "An arcology — a city in a single building."),
    Unlock(50_000_000,    "sky platforms",   "Floating platforms drift above the city."),
]


def unlocked_count(wh: float) -> int:
    """Return the number of UNLOCKS satisfied at this energy total."""
    n = 0
    for u in UNLOCKS:
        if wh >= u.wh:
            n += 1
        else:
            break
    return n


def current_unlock(wh: float) -> Unlock:
    """The most-recently-unlocked tier."""
    return UNLOCKS[max(0, unlocked_count(wh) - 1)]


def next_unlock(wh: float) -> Unlock | None:
    """The next tier the user is working toward, or None at the cap."""
    n = unlocked_count(wh)
    return UNLOCKS[n] if n < len(UNLOCKS) else None


# ───────────────────────────────────────────────────────────────────────────
# Isometric projection — grid coords → screen pixels.
# Tile is 24px wide × 12px tall (2:1, classic iso).
# ───────────────────────────────────────────────────────────────────────────

WIDTH, HEIGHT = 256, 192
TILE_W, TILE_H = 24, 12
GRID = 6
ORIGIN_X = WIDTH // 2
ORIGIN_Y = 70


def iso(gx: float, gy: float) -> tuple[int, int]:
    """Grid (gx, gy) → screen (sx, sy). The tile centre lands on (sx, sy)."""
    sx = ORIGIN_X + int((gx - gy) * TILE_W)
    sy = ORIGIN_Y + int((gx + gy) * (TILE_H // 2))
    return sx, sy


def diamond(cx: int, cy: int, col: int) -> None:
    """Filled isometric tile diamond centred at (cx, cy)."""
    tw, th = TILE_W, TILE_H // 2
    pyxel.tri(cx - tw, cy, cx, cy - th, cx + tw, cy, col)
    pyxel.tri(cx - tw, cy, cx + tw, cy, cx, cy + th, col)


def diamond_outline(cx: int, cy: int, col: int) -> None:
    tw, th = TILE_W, TILE_H // 2
    pyxel.line(cx - tw, cy, cx, cy - th, col)
    pyxel.line(cx, cy - th, cx + tw, cy, col)
    pyxel.line(cx + tw, cy, cx, cy + th, col)
    pyxel.line(cx, cy + th, cx - tw, cy, col)


def box(gx: int, gy: int, height: int, top_col: int, left_col: int, right_col: int) -> tuple[int, int, int, int]:
    """Draw a 1×1 footprint isometric box `height` px tall.
    Returns (cx, cy, top_y, base_y) so callers can decorate the faces.
    """
    cx, cy = iso(gx, gy)
    tw, th = TILE_W, TILE_H // 2
    top_y = cy - height
    # Top diamond
    pyxel.tri(cx - tw, top_y, cx, top_y - th, cx + tw, top_y, top_col)
    pyxel.tri(cx - tw, top_y, cx + tw, top_y, cx, top_y + th, top_col)
    # Left face (parallelogram = 2 triangles)
    pyxel.tri(cx - tw, top_y, cx, top_y + th, cx, cy + th, left_col)
    pyxel.tri(cx - tw, top_y, cx, cy + th, cx - tw, cy, left_col)
    # Right face
    pyxel.tri(cx, top_y + th, cx + tw, top_y, cx + tw, cy, right_col)
    pyxel.tri(cx, top_y + th, cx + tw, cy, cx, cy + th, right_col)
    # Vertical edges for definition
    pyxel.line(cx - tw, top_y, cx - tw, cy, VOID)
    pyxel.line(cx + tw, top_y, cx + tw, cy, VOID)
    pyxel.line(cx, top_y + th, cx, cy + th, VOID)
    return cx, cy, top_y, cy


def windows_on_face(cx: int, cy: int, top_y: int, base_y: int, side: str, frame: int) -> None:
    """Sprinkle a few lit windows onto a building face."""
    tw = TILE_W
    height = base_y - top_y
    rows = max(1, height // 8)
    cols = 2
    for r in range(rows):
        for c in range(cols):
            # Deterministic on/off + colour from position (no flicker spam).
            seed = (cx * 13 + r * 7 + c * 31 + (1 if side == "L" else 2) * 5)
            if seed % 5 == 0:
                continue  # window dark
            col = WARM_WHITE if seed % 3 == 0 else (CYAN if seed % 2 == 0 else SODIUM)
            # Subtle slow flicker on a few windows.
            if seed % 11 == 0 and (frame // 8 + seed) % 7 == 0:
                col = VOID
            wy = top_y + 3 + r * 8
            if side == "L":
                wx = cx - tw + 4 + c * 8
            else:
                wx = cx + 3 + c * 8
            pyxel.rect(wx, wy, 3, 4, col)


# ───────────────────────────────────────────────────────────────────────────
# Building catalogue — (min_unlock_index, drawer_callable).
# Each drawer takes (frame:int) and renders its piece. Drawers reference
# fixed grid coords so the layout is stable as the city grows.
# ───────────────────────────────────────────────────────────────────────────

def draw_lamp(frame: int) -> None:
    cx, cy = iso(2, 5)
    pyxel.line(cx, cy - 16, cx, cy, ROOF)
    glow = AMBER if (frame // 6) % 8 != 0 else SODIUM
    pyxel.circ(cx, cy - 18, 2, glow)
    pyxel.pset(cx, cy - 18, WARM_WHITE)


def draw_dirt_path(_: int) -> None:
    # A path along the diagonal, drawn as recoloured tiles.
    for i in range(GRID):
        cx, cy = iso(i, GRID // 2)
        diamond(cx, cy, SHADOW)
        diamond_outline(cx, cy, GROUND_A)


def draw_shack(frame: int) -> None:
    cx, cy, ty, by = box(1, 4, 18, ROOF, SHADOW, LIT)
    windows_on_face(cx, cy, ty, by, "L", frame)
    windows_on_face(cx, cy, ty, by, "R", frame)


def draw_second_shack(frame: int) -> None:
    cx, cy, ty, by = box(4, 1, 16, ROOF, SHADOW, LIT)
    windows_on_face(cx, cy, ty, by, "L", frame)
    windows_on_face(cx, cy, ty, by, "R", frame)


def draw_cube_row(frame: int) -> None:
    for i, gx in enumerate([0, 1, 2]):
        cx, cy, ty, by = box(gx, 1, 14 + (i % 2) * 4, ROOF, SHADOW, LIT)
        windows_on_face(cx, cy, ty, by, "L", frame)
        windows_on_face(cx, cy, ty, by, "R", frame)
    # Neon signs above doors.
    sx, sy = iso(1, 1)
    pyxel.text(sx - 8, sy - 28, "EAT", PINK)
    sx, sy = iso(2, 1)
    pyxel.text(sx - 8, sy - 32, "OPEN", CYAN)


def draw_bikes(_: int) -> None:
    # Tiny hover-bike sprites near the path.
    for gx in [0, 2, 4]:
        cx, cy = iso(gx, GRID // 2)
        pyxel.rect(cx - 4, cy - 3, 8, 2, PINK)
        pyxel.rect(cx - 5, cy - 1, 10, 1, ACID)  # hover glow


def draw_tower(frame: int) -> None:
    cx, cy, ty, by = box(4, 4, 60, ROOF, SHADOW, LIT)
    windows_on_face(cx, cy, ty, by, "L", frame)
    windows_on_face(cx, cy, ty, by, "R", frame)
    # Antenna with blinking light.
    pyxel.line(cx, ty - 6, cx, ty - 14, ROOF)
    if (frame // 10) % 2 == 0:
        pyxel.pset(cx, ty - 14, PINK)


def draw_traffic(frame: int) -> None:
    # A bright dot moving along the path each frame.
    t = (frame % 120) / 120
    gx = t * (GRID - 1)
    cx, cy = iso(gx, GRID // 2)
    pyxel.rect(cx - 1, cy - 2, 3, 2, AMBER)
    pyxel.pset(cx + 2, cy - 1, WARM_WHITE)


def draw_mega(frame: int) -> None:
    cx, cy, ty, by = box(0, 0, 100, ROOF, SHADOW, LIT)
    windows_on_face(cx, cy, ty, by, "L", frame)
    windows_on_face(cx, cy, ty, by, "R", frame)
    # Hologram bobbing on the roof.
    bob = int(math.sin(frame / 12) * 2)
    pyxel.text(cx - 12, ty - 14 + bob, "MEGA", CYAN)
    pyxel.line(cx - 12, ty - 6 + bob, cx + 12, ty - 6 + bob, CYAN)


def draw_rain(frame: int) -> None:
    # Acid rain — diagonal neon streaks, deterministic per frame.
    for i in range(40):
        seed = (i * 53 + frame) % 1000
        x = (seed * 7) % WIDTH
        y = (seed * 11 + frame * 3) % HEIGHT
        pyxel.line(x, y, x - 2, y + 4, ACID)


def draw_arcology(frame: int) -> None:
    cx, cy, ty, by = box(5, 5, 140, ROOF, SHADOW, LIT)
    windows_on_face(cx, cy, ty, by, "L", frame)
    windows_on_face(cx, cy, ty, by, "R", frame)
    # Apex pyramid hint.
    pyxel.tri(cx - 8, ty, cx + 8, ty, cx, ty - 12, VIOLET)


def draw_sky_platforms(frame: int) -> None:
    drift = int(math.sin(frame / 30) * 4)
    for i, (px, py) in enumerate([(40, 22), (180, 18), (110, 8)]):
        x = px + drift * (1 if i % 2 == 0 else -1)
        pyxel.rect(x, py, 24, 3, VIOLET)
        pyxel.line(x + 2, py + 3, x + 2, py + 6, VIOLET)
        pyxel.line(x + 20, py + 3, x + 20, py + 6, VIOLET)
        if (frame // 12) % 2 == 0:
            pyxel.pset(x + 12, py - 1, PINK)


# index in UNLOCKS → drawer
DRAWERS: list = [
    None,                # 0 empty plot
    draw_lamp,           # 1
    draw_dirt_path,      # 2
    draw_shack,          # 3
    draw_second_shack,   # 4
    draw_cube_row,       # 5
    draw_bikes,          # 6
    draw_tower,          # 7
    draw_traffic,        # 8
    draw_mega,           # 9
    draw_rain,           # 10
    draw_arcology,       # 11
    draw_sky_platforms,  # 12
]


# ───────────────────────────────────────────────────────────────────────────
# The pyxel App.
# ───────────────────────────────────────────────────────────────────────────

class TownApp:
    """Runs the pyxel main loop; renders the town for `total_wh`."""

    def __init__(self, total_wh: float):
        self.total_wh = total_wh
        self.unlocked = unlocked_count(total_wh)
        self.frame = 0

        pyxel.init(WIDTH, HEIGHT, title="claudewatts // town", fps=30, quit_key=pyxel.KEY_Q)
        for i, hex_col in enumerate(PALETTE):
            pyxel.colors[i] = hex_col
        pyxel.run(self.update, self.draw)

    def update(self) -> None:
        self.frame += 1

    def draw(self) -> None:
        pyxel.cls(VOID)
        self._draw_sky()
        self._draw_ground()
        self._draw_features()
        if self.unlocked > 10:  # rain layer goes on top of buildings
            draw_rain(self.frame)
        self._draw_hud()

    def _draw_sky(self) -> None:
        # Vertical gradient: midnight at top → deep purple at horizon.
        for y in range(0, 30):
            pyxel.line(0, y, WIDTH, y, MIDNIGHT)
        for y in range(30, 50):
            pyxel.line(0, y, WIDTH, y, PURPLE)
        # Stars (deterministic positions, gentle twinkle).
        for i in range(20):
            x = (i * 71 + 13) % WIDTH
            y = (i * 37 + 5) % 28
            if (self.frame // 24 + i) % 7 != 0:
                pyxel.pset(x, y, GHOST if i % 3 else SOFT_PINK)
        # Moon.
        pyxel.circ(WIDTH - 32, 22, 6, GHOST)
        pyxel.circ(WIDTH - 30, 21, 5, MIDNIGHT)
        # Distant skyline silhouette.
        for x in range(0, WIDTH, 12):
            h = 6 + ((x * 17) % 9)
            pyxel.rect(x, 50 - h, 10, h, VOID)

    def _draw_ground(self) -> None:
        for gy in range(GRID):
            for gx in range(GRID):
                cx, cy = iso(gx, gy)
                col = GROUND_A if (gx + gy) % 2 == 0 else SHADOW
                diamond(cx, cy, col)
                diamond_outline(cx, cy, MIDNIGHT)

    def _draw_features(self) -> None:
        for i in range(1, min(self.unlocked, len(DRAWERS))):
            drawer = DRAWERS[i]
            if drawer is draw_rain:  # drawn last, see draw()
                continue
            if drawer:
                drawer(self.frame)

    def _draw_hud(self) -> None:
        # Bottom strip.
        pyxel.rect(0, HEIGHT - 18, WIDTH, 18, MIDNIGHT)
        pyxel.line(0, HEIGHT - 18, WIDTH, HEIGHT - 18, PINK)
        pyxel.text(4, HEIGHT - 14, f"{format_wh(self.total_wh)}", CYAN)
        cur = current_unlock(self.total_wh)
        pyxel.text(4, HEIGHT - 6, cur.name.upper(), WARM_WHITE)
        nxt = next_unlock(self.total_wh)
        if nxt:
            need = nxt.wh - self.total_wh
            label = f"NEXT: {nxt.name} in {format_wh(need)}"
            pyxel.text(WIDTH - len(label) * 4 - 4, HEIGHT - 6, label, AMBER)
        else:
            pyxel.text(WIDTH - 56, HEIGHT - 6, "MAX TIER REACHED", ACID)


class ScreenshotSession(TownApp):
    """Single pyxel.run() pass that snaps every tier in sequence then quits.

    Pyxel can only be initialised once per process, and screenshots must be
    taken from inside the run loop after a frame has been drawn — so we
    drive the whole sweep through one update/draw cycle.
    """

    FRAMES_PER_TIER = 3  # let a couple of frames render before snapping

    def __init__(self, out_dir: Path, tiers: list[float] | None = None):
        self.out_dir = out_dir
        self.tiers = tiers if tiers is not None else [u.wh for u in UNLOCKS]
        self.tier_idx = 0
        self.frame_in_tier = 0
        self.total_wh = self.tiers[0]
        self.unlocked = unlocked_count(self.total_wh)
        self.frame = 0
        self.done = False

        out_dir.mkdir(parents=True, exist_ok=True)
        pyxel.init(WIDTH, HEIGHT, title="cwatts town // screenshot", fps=30)
        for i, hex_col in enumerate(PALETTE):
            pyxel.colors[i] = hex_col
        pyxel.run(self.update, self.draw)

    def update(self) -> None:
        self.frame += 1
        self.frame_in_tier += 1
        if self.done:
            pyxel.quit()
            return
        if self.frame_in_tier >= self.FRAMES_PER_TIER:
            slug = tier_slug(self.total_wh)
            stem = str(self.out_dir / f"town-{slug}")
            pyxel.screenshot(stem, scale=4)
            print(f"  wrote town-{slug}.png  ({format_wh(self.total_wh)})")
            self.tier_idx += 1
            if self.tier_idx >= len(self.tiers):
                self.done = True
                return
            self.total_wh = self.tiers[self.tier_idx]
            self.unlocked = unlocked_count(self.total_wh)
            self.frame_in_tier = 0

    # draw() inherited from TownApp; reads self.unlocked / self.total_wh / self.frame


# ───────────────────────────────────────────────────────────────────────────
# Helpers shared with the CLI.
# ───────────────────────────────────────────────────────────────────────────

def format_wh(wh: float) -> str:
    if wh < 1:
        return f"{wh * 1000:.0f}MWH" if False else f"{wh * 1000:.0f}mWh"
    if wh < 1000:
        return f"{wh:.0f}Wh"
    if wh < 1_000_000:
        return f"{wh / 1000:.1f}kWh"
    return f"{wh / 1_000_000:.1f}MWh"


def parse_wh(s: str) -> float:
    """Parse a Wh value with optional unit suffix. '500' / '5kwh' / '5MWh' / '0.5gwh'."""
    s = s.strip().lower().replace(",", "").replace(" ", "")
    multipliers = {"gwh": 1e9, "mwh": 1e6, "kwh": 1e3, "wh": 1.0}
    for suffix, mul in multipliers.items():
        if s.endswith(suffix):
            return float(s[: -len(suffix)]) * mul
    return float(s)


def tier_slug(wh: float) -> str:
    """Filename slug for a screenshot at this Wh — e.g. '5kwh', '500wh', '5mwh'."""
    if wh < 1000:
        return f"{int(wh)}wh"
    if wh < 1_000_000:
        return f"{int(wh / 1000)}kwh"
    return f"{int(wh / 1_000_000)}mwh"


# ───────────────────────────────────────────────────────────────────────────
# CLI entry — invoked from `cwatts town` (see claudewatts.py).
# ───────────────────────────────────────────────────────────────────────────

SCREENSHOT_TIERS = [u.wh for u in UNLOCKS]


def run(total_wh: float, screenshot_dir: Path | None = None) -> int:
    """Launch the town for the given energy total. Pyxel must be importable."""
    if pyxel is None:
        print(
            "cwatts town requires pyxel.\n"
            "  pip install 'claudewatts[town]'\n"
            "or\n"
            "  pip install pyxel",
            file=sys.stderr,
        )
        return 1

    if screenshot_dir is not None:
        ScreenshotSession(screenshot_dir, tiers=SCREENSHOT_TIERS)
        print(f"\nwrote {len(SCREENSHOT_TIERS)} screenshots to {screenshot_dir}")
        return 0

    TownApp(total_wh)
    return 0


def add_subparser(sub) -> None:
    """Wire up `cwatts town` from the main CLI."""
    p = sub.add_parser(
        "town",
        help="Open the cyberpunk city that grows with your cumulative Wh.",
    )
    p.add_argument(
        "--screenshot",
        type=Path,
        metavar="DIR",
        help="Headless: render every unlock tier as a PNG into DIR and exit.",
    )
    p.add_argument(
        "--wh",
        type=parse_wh,
        metavar="N",
        help="Inject a fake Wh total instead of reading transcripts. "
             "Accepts plain number ('5000') or suffix ('50kwh', '5mwh'). "
             "Useful for previewing tiers and capturing screenshots.",
    )
