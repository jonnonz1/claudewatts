#!/usr/bin/env python3
"""
claudewatts — estimate the electrical energy consumed by your Claude Code sessions.

Parses Claude Code transcript JSONL files under ~/.claude/projects/ and converts
token counts into watt-hours using published industry estimates for frontier LLM
inference. Reports five scopes:

  session    the specific transcript you're currently in
  active     every transcript with activity in the last N minutes (parallel sessions)
  repo       every transcript whose cwd is inside a given directory
  today      every message on the local calendar day
  total      cumulative across everything ~/.claude/projects/ has ever recorded

The goal is illustrative, not precise. See docs/METHODOLOGY.md for derivation,
sources, and a worked sanity check. All constants are env-var configurable.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable


# ---------------------------------------------------------------------------
# Energy constants (watt-hours per token)
# ---------------------------------------------------------------------------
# Mid-range defaults, derived from:
#   - Google's 2025 disclosure: median Gemini text prompt = 0.24 Wh
#   - Epoch AI (2025): GPT-4o ≈ 0.3 Wh/query
#   - Simon P. Couch (2026-01): median Claude Code session ≈ 41 Wh
#
# The output:input ratio is 5x. Justification is *structural*: output token
# generation is memory-bandwidth-bound at effective batch ≈ 1 per query
# (each autoregressive step reads the full parameter set), while prefill is
# compute-bound and parallelises across input tokens. Anthropic's 5x pricing
# ratio is corroborating evidence, not the primary argument.
#
# Cache-read discount (0.1x) matches pricing. True energy ratio is unknown;
# it skips prefill compute but still loads KV memory. Real value is plausibly
# in the 0.05–0.3x range. See docs/METHODOLOGY.md.

WH_PER_INPUT_TOKEN = float(os.environ.get("CPM_WH_INPUT", "0.0003"))
WH_PER_OUTPUT_TOKEN = float(os.environ.get("CPM_WH_OUTPUT", "0.0015"))
WH_PER_CACHE_READ_TOKEN = float(os.environ.get("CPM_WH_CACHE_READ", "0.00003"))
WH_PER_CACHE_CREATE_TOKEN = float(os.environ.get("CPM_WH_CACHE_CREATE", "0.0003"))

ACTIVE_WINDOW_MINUTES = int(os.environ.get("CPM_ACTIVE_MINUTES", "10"))


# ---------------------------------------------------------------------------
# Comparison reference points (watt-hours)
# ---------------------------------------------------------------------------
# Ordered smallest to largest. "Best fit" picks the unit with multiplier >= 1
# but smallest, so 450 Wh renders as "4.5 kettle boils" rather than
# "0.0009 village-days".

COMPARISONS: list[tuple[str, float, str]] = [
    ("Google search",    0.3,        "Google searches"),
    ("LED bulb-hour",   10.0,        "LED bulb-hours (10W bulb)"),
    ("phone charge",    15.0,        "phone charges"),
    ("kettle boil",    100.0,        "kettle boils"),
    ("laundry load",   500.0,        "loads of laundry"),
    ("fridge-day",    1500.0,        "days of running a fridge"),
    ("US home-day",  30000.0,        "days of powering a US home"),
    ("village-day", 500000.0,        "days of powering a small village (100 homes, 5 kWh each)"),
]


# ---------------------------------------------------------------------------
# Whimsical comparisons — continuous appliances (avg watts) × time windows.
# Used by random_comparison() to render energy as e.g.
# "powering 4 small villages for a year" or "running 130 toasters for a month".
# Values are mid-range averages, not nameplate peaks.

POWER_USES: list[tuple[str, str, float]] = [
    # (singular, plural, average watts)
    ("LED bulb",                  "LED bulbs",                   10),
    ("laptop",                    "laptops",                     50),
    ("fridge",                    "fridges",                     60),
    ("game console",              "game consoles",               150),
    ("desktop PC",                "desktop PCs",                 200),
    ("UK home",                   "UK homes",                    400),
    ("toaster",                   "toasters",                    800),
    ("microwave",                 "microwaves",                  1000),
    ("US home",                   "US homes",                    1250),
    ("dishwasher",                "dishwashers",                 1500),
    ("space heater",              "space heaters",               1500),
    ("hair dryer",                "hair dryers",                 1800),
    ("electric kettle",           "electric kettles",            2000),
    ("electric oven",             "electric ovens",              2400),
    ("hot tub",                   "hot tubs",                    3000),
    ("EV home charger",           "EV home chargers",            7000),
    ("data centre rack",          "data centre racks",           15000),
    ("small village (100 homes)", "small villages (100 homes)",  125000),
]

TIME_WINDOWS: list[tuple[str, float]] = [
    # (label, hours)
    ("hour",   1),
    ("day",    24),
    ("week",   168),
    ("month",  730),
    ("year",   8760),
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Usage:
    """Token counts aggregated from one or more transcript entries."""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_create_tokens: int = 0
    message_count: int = 0
    session_ids: set[str] = field(default_factory=set)

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.cache_create_tokens += other.cache_create_tokens
        self.message_count += other.message_count
        self.session_ids |= other.session_ids

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_create_tokens
        )

    @property
    def wh(self) -> float:
        """Estimated watt-hours consumed."""
        return (
            self.input_tokens * WH_PER_INPUT_TOKEN
            + self.output_tokens * WH_PER_OUTPUT_TOKEN
            + self.cache_read_tokens * WH_PER_CACHE_READ_TOKEN
            + self.cache_create_tokens * WH_PER_CACHE_CREATE_TOKEN
        )


@dataclass
class Aggregates:
    """All computed scope totals for a single run of the tool."""
    session: Usage = field(default_factory=Usage)
    active: Usage = field(default_factory=Usage)
    repo: Usage = field(default_factory=Usage)
    today: Usage = field(default_factory=Usage)
    total: Usage = field(default_factory=Usage)
    repo_path: Path | None = None


@dataclass
class Event:
    """One assistant message's usage, plus the context we filter on."""
    ts_utc: datetime
    cwd: str
    session_id: str
    transcript_path: Path
    usage: Usage


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def iter_transcript_events(jsonl_path: Path) -> Iterable[Event]:
    """Yield an Event for every assistant message with usage data.

    Silently skips malformed lines — Claude Code occasionally writes partial
    JSON on crash, and we'd rather undercount than raise.
    """
    try:
        f = jsonl_path.open("r", encoding="utf-8")
    except (OSError, FileNotFoundError):
        return
    with f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("type") != "assistant":
                continue
            usage = entry.get("message", {}).get("usage")
            if not usage:
                continue
            ts_raw = entry.get("timestamp")
            if not ts_raw:
                continue
            try:
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            except ValueError:
                continue
            session_id = entry.get("sessionId") or jsonl_path.stem
            yield Event(
                ts_utc=ts,
                cwd=entry.get("cwd", "") or "",
                session_id=session_id,
                transcript_path=jsonl_path,
                usage=Usage(
                    input_tokens=int(usage.get("input_tokens", 0) or 0),
                    output_tokens=int(usage.get("output_tokens", 0) or 0),
                    cache_read_tokens=int(usage.get("cache_read_input_tokens", 0) or 0),
                    cache_create_tokens=int(usage.get("cache_creation_input_tokens", 0) or 0),
                    message_count=1,
                    session_ids={session_id},
                ),
            )


def find_transcripts(projects_dir: Path) -> list[Path]:
    """Return every *.jsonl under the Claude Code projects directory."""
    if not projects_dir.exists():
        return []
    return sorted(projects_dir.rglob("*.jsonl"))


def _is_inside(child: str, parent: Path) -> bool:
    """True if `child` path string is `parent` or a descendant of it."""
    if not child:
        return False
    try:
        c = Path(child).resolve()
        p = parent.resolve()
    except (OSError, RuntimeError):
        return False
    return c == p or p in c.parents


def aggregate(
    projects_dir: Path,
    session_transcript: Path | None = None,
    repo_path: Path | None = None,
    now: datetime | None = None,
    active_window: timedelta | None = None,
) -> Aggregates:
    """Compute all five scope totals in a single pass over the transcripts.

    Every event contributes to `total`. It may additionally contribute to
    `today` (local-day), `active` (recent), `repo` (cwd inside repo_path),
    and `session` (transcript matches session_transcript). Scopes overlap —
    a recent message in the current session counts in every scope at once.
    """
    now = now or datetime.now(timezone.utc).astimezone()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    active_window = active_window or timedelta(minutes=ACTIVE_WINDOW_MINUTES)
    active_cutoff = now - active_window

    agg = Aggregates(repo_path=repo_path.resolve() if repo_path else None)
    session_path = session_transcript.resolve() if session_transcript else None

    for transcript in find_transcripts(projects_dir):
        is_session_file = session_path is not None and transcript.resolve() == session_path
        for event in iter_transcript_events(transcript):
            ts_local = event.ts_utc.astimezone(now.tzinfo)
            agg.total.add(event.usage)
            if ts_local >= today_start:
                agg.today.add(event.usage)
            if ts_local >= active_cutoff:
                agg.active.add(event.usage)
            if repo_path is not None and _is_inside(event.cwd, repo_path):
                agg.repo.add(event.usage)
            if is_session_file:
                agg.session.add(event.usage)
    return agg


# ---------------------------------------------------------------------------
# Formatting — plain text
# ---------------------------------------------------------------------------

def format_energy(wh: float) -> str:
    """Render watt-hours in the most readable SI unit."""
    if wh < 1:
        return f"{wh * 1000:.1f} mWh"
    if wh < 1000:
        return f"{wh:.2f} Wh"
    if wh < 1_000_000:
        return f"{wh / 1000:.2f} kWh"
    return f"{wh / 1_000_000:.2f} MWh"


def best_comparison(wh: float) -> str:
    """Pick the comparison unit whose multiplier is >= 1 but smallest."""
    if wh <= 0:
        return "nothing at all"
    chosen_wh, chosen_plural = COMPARISONS[0][1], COMPARISONS[0][2]
    for _, unit_wh, plural in COMPARISONS:
        if wh / unit_wh >= 1:
            chosen_wh, chosen_plural = unit_wh, plural
        else:
            break
    count = wh / chosen_wh
    if count >= 10:
        return f"{count:,.0f} {chosen_plural}"
    if count >= 1:
        return f"{count:,.1f} {chosen_plural}"
    return f"{count:,.2f} {chosen_plural}"


def random_comparison(wh: float, rng: random.Random | None = None) -> str:
    """Pick a random appliance + time window whose count lands in a feel-able range.

    Returns phrases like "powering 4 small villages for a year" or
    "running 130 toasters for a month". Falls back to best_comparison
    when no candidate produces a count between 0.5 and ~10000.
    """
    if wh <= 0:
        return "nothing at all"
    rng = rng or random.Random()
    candidates: list[tuple[str, str, str, float]] = []
    for singular, plural, watts in POWER_USES:
        for window, hours in TIME_WINDOWS:
            count = wh / (watts * hours)
            if 0.5 <= count <= 9999:
                candidates.append((singular, plural, window, count))
    if not candidates:
        return best_comparison(wh)
    singular, plural, window, count = rng.choice(candidates)
    if count >= 100:
        n = f"{count:,.0f}"
    elif count >= 10:
        n = f"{count:,.1f}"
    else:
        n = f"{count:,.2f}"
    name = singular if 0.95 <= count < 1.5 else plural
    article = "an" if window == "hour" else "a"
    return f"powering {n} {name} for {article} {window}"


def all_comparisons(wh: float) -> list[str]:
    """Render every comparison unit — used in the verbose report."""
    lines = []
    for _, unit_wh, plural in COMPARISONS:
        count = wh / unit_wh
        if count >= 100:
            lines.append(f"  {count:>12,.0f}  {plural}")
        elif count >= 1:
            lines.append(f"  {count:>12,.2f}  {plural}")
        else:
            lines.append(f"  {count:>12,.4f}  {plural}")
    return lines


def render_statusline(agg: Aggregates) -> str:
    """One-line output for the Claude Code statusLine hook."""
    n_active = len(agg.active.session_ids)
    prefix = f"⚡ {n_active}× active" if n_active > 1 else "⚡"
    body = " · ".join([
        f"{format_energy(agg.session.wh)} session",
        f"{format_energy(agg.today.wh)} today",
        f"{format_energy(agg.total.wh)} total",
        f"(≈ {random_comparison(agg.total.wh)})",
    ])
    return f"{prefix} · {body}" if n_active > 1 else f"{prefix} {body}"


def render_report(agg: Aggregates) -> str:
    """Full human-readable report."""
    def section(title: str, usage: Usage, extra: str = "") -> list[str]:
        header = title + (f"  [{extra}]" if extra else "")
        out = [f"\n{header}", "─" * len(header)]
        out.append(f"  Sessions:       {len(usage.session_ids):,}")
        out.append(f"  Messages:       {usage.message_count:,}")
        out.append(f"  Input tokens:   {usage.input_tokens:,}")
        out.append(f"  Output tokens:  {usage.output_tokens:,}")
        out.append(f"  Cache reads:    {usage.cache_read_tokens:,}")
        out.append(f"  Cache creates:  {usage.cache_create_tokens:,}")
        out.append(f"  Total tokens:   {usage.total_tokens:,}")
        out.append(f"  Energy:         {format_energy(usage.wh)}")
        out.append(f"  Equivalent to:")
        out.extend(all_comparisons(usage.wh))
        return out

    lines = ["Claude Code Token Usage — Energy Report", "=" * 39]
    lines += section("Current session", agg.session)
    lines += section(
        f"Active sessions (last {ACTIVE_WINDOW_MINUTES}m)",
        agg.active,
        extra=f"{len(agg.active.session_ids)} session(s)",
    )
    if agg.repo_path is not None:
        lines += section(f"Repo scope: {agg.repo_path}", agg.repo)
    lines += section("Today (local calendar day)", agg.today)
    lines += section("Cumulative (all recorded sessions)", agg.total)
    lines.append("")
    lines.append(f"Headline: your Claude Code usage has consumed roughly")
    lines.append(f"  {format_energy(agg.total.wh)} — about {best_comparison(agg.total.wh)}.")
    lines.append(f"  Or: {random_comparison(agg.total.wh)}.")
    lines.append("")
    lines.append("Energy constants (Wh/token):")
    lines.append(f"  input={WH_PER_INPUT_TOKEN}  output={WH_PER_OUTPUT_TOKEN}")
    lines.append(f"  cache_read={WH_PER_CACHE_READ_TOKEN}  cache_create={WH_PER_CACHE_CREATE_TOKEN}")
    lines.append("Override via CPM_WH_INPUT / CPM_WH_OUTPUT / CPM_WH_CACHE_READ / CPM_WH_CACHE_CREATE.")
    return "\n".join(lines)


def render_json(agg: Aggregates) -> str:
    """Machine-readable output."""
    def usage_dict(u: Usage) -> dict:
        return {
            "sessions": len(u.session_ids),
            "messages": u.message_count,
            "input_tokens": u.input_tokens,
            "output_tokens": u.output_tokens,
            "cache_read_tokens": u.cache_read_tokens,
            "cache_create_tokens": u.cache_create_tokens,
            "total_tokens": u.total_tokens,
            "wh": round(u.wh, 6),
        }
    return json.dumps({
        "session": usage_dict(agg.session),
        "active": usage_dict(agg.active),
        "repo": usage_dict(agg.repo),
        "repo_path": str(agg.repo_path) if agg.repo_path else None,
        "today": usage_dict(agg.today),
        "total": usage_dict(agg.total),
        "comparison": best_comparison(agg.total.wh),
        "random_comparison": random_comparison(agg.total.wh),
        "constants": {
            "wh_per_input_token": WH_PER_INPUT_TOKEN,
            "wh_per_output_token": WH_PER_OUTPUT_TOKEN,
            "wh_per_cache_read_token": WH_PER_CACHE_READ_TOKEN,
            "wh_per_cache_create_token": WH_PER_CACHE_CREATE_TOKEN,
            "active_window_minutes": ACTIVE_WINDOW_MINUTES,
        },
    }, indent=2)


# ---------------------------------------------------------------------------
# Install subcommand
# ---------------------------------------------------------------------------

def install_claude_statusline() -> int:
    """Write a statusLine entry to ~/.claude/settings.json pointing at this script."""
    settings_path = Path.home() / ".claude" / "settings.json"
    script_path = Path(__file__).resolve()
    settings_path.parent.mkdir(parents=True, exist_ok=True)

    data = {}
    if settings_path.exists():
        try:
            data = json.loads(settings_path.read_text())
        except json.JSONDecodeError:
            backup = settings_path.with_suffix(f".json.corrupt-{datetime.now():%Y%m%d-%H%M%S}")
            settings_path.rename(backup)
            print(f"warning: {settings_path} was not valid JSON; moved to {backup}")
            data = {}

    if settings_path.exists():
        backup = settings_path.with_suffix(f".json.backup-{datetime.now():%Y%m%d-%H%M%S}")
        backup.write_text(settings_path.read_text())
        print(f"backed up existing settings to: {backup}")

    data["statusLine"] = {
        "type": "command",
        "command": f"python3 {script_path} statusline",
    }
    settings_path.write_text(json.dumps(data, indent=2) + "\n")
    print(f"wrote statusLine to {settings_path}:")
    print(json.dumps(data["statusLine"], indent=2))
    print()
    print("restart Claude Code to see the meter in your status line.")
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def default_projects_dir() -> Path:
    """Where Claude Code stores its transcripts; overridable for tests."""
    return Path(os.environ.get("CLAUDE_PROJECTS_DIR", Path.home() / ".claude" / "projects"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cwatts",
        description="Estimate electrical energy consumed by Claude Code sessions.",
    )
    sub = parser.add_subparsers(dest="command")

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--session-transcript", type=Path, default=None,
                       help="Path to the current session JSONL file.")
        p.add_argument("--repo", type=Path, default=None,
                       help="Scope repo totals to this directory. `.` for cwd.")
        p.add_argument("--projects-dir", type=Path, default=default_projects_dir(),
                       help="Claude Code projects directory. Default: ~/.claude/projects/")

    p_report = sub.add_parser("report", help="Full human-readable report (default).")
    add_common(p_report)

    p_sl = sub.add_parser("statusline", help="One-line statusLine hook output.")
    add_common(p_sl)

    p_json = sub.add_parser("json", help="Machine-readable JSON.")
    add_common(p_json)

    p_install = sub.add_parser("install", help="Install as a Claude Code statusLine.")
    p_install.add_argument("target", nargs="?", default="claude",
                           choices=["claude"],
                           help="What to install into. Currently only 'claude'.")

    # Optional `town` subcommand: lazy-imported so missing pyxel doesn't break the rest.
    try:
        import town as _town
        _town.add_subparser(sub)
    except ImportError:
        # Register a stub so `cwatts town` still gives a friendly error.
        p_town = sub.add_parser("town", help="(needs pyxel) Cyberpunk city that grows with your Wh.")
        p_town.add_argument("--screenshot", type=Path, metavar="DIR",
                            help="Render every unlock tier as a PNG into DIR and exit.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "report"

    if command == "install":
        return install_claude_statusline()

    if command == "town":
        try:
            import town as _town
        except ImportError:
            print(
                "cwatts town requires pyxel.\n"
                "  pip install 'claudewatts[town]'\n"
                "or\n"
                "  pip install pyxel",
                file=sys.stderr,
            )
            return 1
        projects_dir = getattr(args, "projects_dir", default_projects_dir())
        agg = aggregate(projects_dir, None, None)
        return _town.run(agg.total.wh, screenshot_dir=args.screenshot)

    session_transcript = getattr(args, "session_transcript", None)
    repo_path = getattr(args, "repo", None)
    if repo_path is not None:
        repo_path = repo_path.resolve()
    projects_dir = getattr(args, "projects_dir", default_projects_dir())

    # statusLine hook sends JSON on stdin with transcript_path and cwd
    if command == "statusline" and not sys.stdin.isatty():
        try:
            payload = json.load(sys.stdin)
            if session_transcript is None and payload.get("transcript_path"):
                session_transcript = Path(payload["transcript_path"])
            if repo_path is None and payload.get("cwd"):
                repo_path = Path(payload["cwd"]).resolve()
        except (json.JSONDecodeError, ValueError):
            pass

    agg = aggregate(projects_dir, session_transcript, repo_path)

    if command == "statusline":
        print(render_statusline(agg))
    elif command == "json":
        print(render_json(agg))
    else:
        print(render_report(agg))
    return 0


if __name__ == "__main__":
    sys.exit(main())
