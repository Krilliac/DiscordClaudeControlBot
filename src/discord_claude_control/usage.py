"""
Per-turn usage telemetry: tokens, cache, USD, duration.

Records one JSON line per agent turn into the configured usage log
(sibling of audit.log by default). Schema:

    {
      "ts": 1740000000.123,                  // unix epoch seconds, float
      "model": "claude-opus-4-7",
      "input_tokens": 1234,
      "output_tokens": 567,
      "cache_creation_input_tokens": 0,
      "cache_read_input_tokens": 89000,
      "total_cost_usd": 0.42,                // null in subscription mode
      "duration_ms": 12345,
      "session_id": "..."
    }

`!cost` / `/cost` parse this file to summarize by period.

The writer is fire-and-forget: one append per turn, OSErrors are logged
and swallowed so a missing/locked usage log cannot kill an agent turn.
The reader yields valid lines and skips malformed ones, so a single bad
line never breaks the whole summary.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_VALID_PERIODS: tuple[str, ...] = ("today", "week", "month", "all")


@dataclass(frozen=True)
class UsageRecord:
    ts: float
    model: str
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    total_cost_usd: float | None
    duration_ms: int
    session_id: str


def record_turn(
    path: Path,
    *,
    model: str,
    usage_dict: dict[str, Any] | None,
    total_cost_usd: float | None,
    duration_ms: int,
    session_id: str,
    ts: float | None = None,
) -> None:
    """Append one JSONL entry. Best-effort -- never raises."""
    if ts is None:
        ts = time.time()
    entry: dict[str, Any] = {
        "ts": ts,
        "model": model,
        "input_tokens": _coerce_int(usage_dict, "input_tokens"),
        "output_tokens": _coerce_int(usage_dict, "output_tokens"),
        "cache_creation_input_tokens": _coerce_int(usage_dict, "cache_creation_input_tokens"),
        "cache_read_input_tokens": _coerce_int(usage_dict, "cache_read_input_tokens"),
        "total_cost_usd": total_cost_usd,
        "duration_ms": int(duration_ms),
        "session_id": session_id,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, separators=(",", ":")) + "\n")
    except OSError:
        log.exception("usage.record_turn write failed (path=%s)", path)


def parse_period(args: str) -> str:
    """Empty string -> 'today'. Unknown -> ValueError."""
    s = args.strip().lower()
    if not s:
        return "today"
    if s in _VALID_PERIODS:
        return s
    raise ValueError(f"unknown period: {args!r} (use today | week | month | all)")


def iter_records(path: Path) -> Iterable[UsageRecord]:
    if not path.exists():
        return
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue
                yield UsageRecord(
                    ts=_as_float(obj.get("ts")),
                    model=str(obj.get("model", "")),
                    input_tokens=_as_int(obj.get("input_tokens")),
                    output_tokens=_as_int(obj.get("output_tokens")),
                    cache_creation_input_tokens=_as_int(obj.get("cache_creation_input_tokens")),
                    cache_read_input_tokens=_as_int(obj.get("cache_read_input_tokens")),
                    total_cost_usd=_as_optional_float(obj.get("total_cost_usd")),
                    duration_ms=_as_int(obj.get("duration_ms")),
                    session_id=str(obj.get("session_id", "")),
                )
    except OSError:
        log.exception("usage.iter_records read failed (path=%s)", path)


def summarize(path: Path, period: str, *, now: float | None = None) -> str:
    """Render the `!cost <period>` summary as a markdown code block."""
    if now is None:
        now = time.time()
    cutoff = _cutoff(period, now=now)

    n_turns = 0
    total_in = 0
    total_out = 0
    total_cache_creation = 0
    total_cache_read = 0
    total_usd = 0.0
    any_usd = False
    per_model: dict[str, list[float]] = {}  # model -> [in, out, usd]

    for rec in iter_records(path):
        if rec.ts < cutoff:
            continue
        n_turns += 1
        total_in += rec.input_tokens
        total_out += rec.output_tokens
        total_cache_creation += rec.cache_creation_input_tokens
        total_cache_read += rec.cache_read_input_tokens
        if rec.total_cost_usd is not None:
            total_usd += rec.total_cost_usd
            any_usd = any_usd or rec.total_cost_usd > 0.0
        bucket = per_model.setdefault(rec.model or "<unknown>", [0.0, 0.0, 0.0])
        bucket[0] += rec.input_tokens
        bucket[1] += rec.output_tokens
        bucket[2] += rec.total_cost_usd or 0.0

    if n_turns == 0:
        return f"no turns logged in period `{period}`."

    lines: list[str] = [f"Usage -- {period}", f"turns:  {n_turns}"]
    lines.append(f"input:  {_fmt_tokens(total_in)}")
    lines.append(f"output: {_fmt_tokens(total_out)}")
    if total_cache_creation or total_cache_read:
        lines.append(
            f"cache:  {_fmt_tokens(total_cache_creation)} created, "
            f"{_fmt_tokens(total_cache_read)} read"
        )
    if any_usd:
        lines.append(f"cost:   ${total_usd:.4f}")
    else:
        lines.append("cost:   (subscription mode -- SDK reports $0)")
    if len(per_model) > 1:
        lines.append("")
        lines.append("by model:")
        for model, (tin, tout, usd) in sorted(per_model.items()):
            row = f"  {model}: {_fmt_tokens(int(tin))} in / {_fmt_tokens(int(tout))} out"
            if any_usd:
                row += f" / ${usd:.4f}"
            lines.append(row)
    return "```\n" + "\n".join(lines) + "\n```"


# --------------------------------------------------------------------- #
# internals                                                             #
# --------------------------------------------------------------------- #


def _cutoff(period: str, *, now: float) -> float:
    if period == "all":
        return 0.0
    if period == "today":
        local_midnight = datetime.fromtimestamp(now).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return local_midnight.timestamp()
    if period == "week":
        return now - 7 * 86400.0
    if period == "month":
        return now - 30 * 86400.0
    raise ValueError(f"unsupported period: {period}")


def _coerce_int(d: dict[str, Any] | None, key: str) -> int:
    if not d:
        return 0
    return _as_int(d.get(key))


def _as_int(v: Any) -> int:
    if isinstance(v, bool):
        return 0
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    return 0


def _as_float(v: Any) -> float:
    if isinstance(v, bool):
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    return 0.0


def _as_optional_float(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 10_000:
        return f"{n / 1_000:.1f}k"
    if n >= 1_000:
        return f"{n / 1_000:.2f}k"
    return str(n)
