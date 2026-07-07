"""Estimate build progress by matching build output against a timeline learned
from previous builds.

The build runs ``make -j1 install`` (serial, single-threaded) plus a fixed
configure/freeze/install sequence, so the *number of output lines emitted so far*
is a stable, deterministic proxy for how far along the build is — and it stays
stable under ccache (the Makefile echoes each compile recipe line whether or not
the cache hits, only the wall-time per line changes). We therefore record, per
successful build, a downsampled curve of ``elapsed / total`` against
``lines_emitted / total_lines`` plus the totals, in a global history file. On a
live build we map the current line count onto that averaged curve for a percent,
and scale the remaining-time estimate by the observed pace (absorbing ccache
warm/cold variance). Phase markers are used only to label the current step.
"""

from __future__ import annotations

import json
import re
import statistics
import time
from datetime import datetime, timezone

from .paths import root_path

HISTORY_PATH = root_path("build-timing.json")
MAX_RUNS = 10
GRID_N = 50          # resolution of the stored/averaged progress curve
SAMPLE_EVERY = 20    # record a (line, elapsed) sample every N lines
_EMIT_STEP = 0.01    # only surface a reading when progress moves >= 1%

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[mK]")

# Ordered phase markers — used only for the human-readable label, not the
# numeric estimate. Ranked so the label only ever advances.
_PHASES: list[tuple[int, str, re.Pattern[str]]] = [
    (0, "Configuring", re.compile(r"\+ ?\./configure\b")),
    (1, "Compiling", re.compile(r"\+ ?make -j1 install\b")),
    (2, "Freezing modules", re.compile(r"Python/frozen_modules/")),
    (3, "Installing", re.compile(r"Creating directory /pfm/py\b")),
    (4, "Finalizing", re.compile(r"verinfo --write-git-info")),
]


def _match_phase(line: str) -> tuple[int, str] | None:
    for rank, label, rx in _PHASES:
        if rx.search(line):
            return rank, label
    return None


def _read_history() -> dict:
    try:
        return json.loads(HISTORY_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _runs(target: str) -> list[dict]:
    runs = _read_history().get(target, {}).get("runs", [])
    return [
        r for r in runs
        if r.get("total_s") and r.get("total_lines") and len(r.get("curve") or []) == GRID_N + 1
    ]


def median_total(target: str) -> float | None:
    """Median total build duration for a target, or None with no usable history."""
    runs = _runs(target)
    return statistics.median(r["total_s"] for r in runs) if runs else None


def _load_aggregate(target: str):
    runs = _runs(target)
    if not runs:
        return None, None, None
    total_s = statistics.median(r["total_s"] for r in runs)
    total_lines = statistics.median(r["total_lines"] for r in runs)
    curve = [statistics.median(r["curve"][i] for r in runs) for i in range(GRID_N + 1)]
    return total_s, total_lines, curve


def _interp(xs: list[float], ys: list[float], x: float) -> float:
    """Linear interpolation over ascending xs."""
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    lo, hi = 0, len(xs) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if xs[mid] <= x:
            lo = mid
        else:
            hi = mid
    span = xs[hi] - xs[lo]
    frac = (x - xs[lo]) / span if span else 0.0
    return ys[lo] + frac * (ys[hi] - ys[lo])


def _curve_at(curve: list[float], frac: float) -> float:
    pos = max(0.0, min(1.0, frac)) * GRID_N
    lo = int(pos)
    if lo >= GRID_N:
        return curve[GRID_N]
    return curve[lo] + (pos - lo) * (curve[lo + 1] - curve[lo])


def _append_run(target: str, total_s: float, total_lines: int, curve: list[float]) -> None:
    data = _read_history()
    runs = data.setdefault(target, {}).setdefault("runs", [])
    runs.append({
        "at": datetime.now(timezone.utc).isoformat(),
        "total_s": round(total_s, 2),
        "total_lines": total_lines,
        "curve": [round(v, 4) for v in curve],
    })
    del runs[:-MAX_RUNS]
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = HISTORY_PATH.with_name(HISTORY_PATH.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(HISTORY_PATH)


class BuildProgressEstimator:
    """Feed build output lines; get (progress, eta_seconds, phase) readings."""

    def __init__(self, target: str, default_phase: str | None = None) -> None:
        self.target = target
        self._start = time.monotonic()
        self._line = 0
        self._samples: list[tuple[int, float]] = [(0, 0.0)]
        self._phase = default_phase
        self._phase_rank = -1
        self._last_progress = 0.0
        self._emitted = -1.0
        self._emitted_phase = object()  # sentinel: force first emit
        self._total_s, self._total_lines, self._curve = _load_aggregate(target)

    def feed(self, line: str) -> tuple[float, float, str] | None:
        self._line += 1
        now = time.monotonic() - self._start
        if self._line % SAMPLE_EVERY == 0:
            self._samples.append((self._line, now))

        info = _match_phase(_ANSI_RE.sub("", line))
        if info is not None and info[0] > self._phase_rank:
            self._phase_rank, self._phase = info

        # Cold start (no usable history): keep the indeterminate UI spinner.
        if not self._curve or not self._total_lines or not self._total_s:
            return None

        frac = self._line / self._total_lines
        progress = max(self._last_progress, _curve_at(self._curve, frac))
        self._last_progress = progress

        expected = progress * self._total_s
        pace = now / expected if expected > 0 else 1.0
        pace = min(4.0, max(0.25, pace))  # clamp so early jitter can't blow up ETA
        eta = max(0.0, (self._total_s - expected) * pace)

        if progress - self._emitted >= _EMIT_STEP or self._phase != self._emitted_phase:
            self._emitted = progress
            self._emitted_phase = self._phase
            return progress, eta, self._phase or ""
        return None

    def finish(self, success: bool) -> None:
        # Only successful, complete runs are recorded; a partial timeline would
        # skew the curve and totals.
        if not success or self._line < 5:
            return
        total_s = time.monotonic() - self._start
        total_lines = self._line
        if self._samples[-1][0] != total_lines:
            self._samples.append((total_lines, total_s))
        xs = [s[0] for s in self._samples]
        ys = [s[1] for s in self._samples]
        curve = [
            min(1.0, _interp(xs, ys, (i / GRID_N) * total_lines) / total_s) if total_s > 0 else i / GRID_N
            for i in range(GRID_N + 1)
        ]
        _append_run(self.target, total_s, total_lines, curve)
