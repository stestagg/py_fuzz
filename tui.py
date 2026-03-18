#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["textual>=8.1.1"]
# ///
"""Interactive two-pane TUI for browsing py-fuzz crash files and their analysis."""

from __future__ import annotations

import json
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime, timezone

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static
from textual.containers import Horizontal, ScrollableContainer

SCRIPT_DIR = Path(__file__).parent.resolve()


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Crash:
    pr_id: str
    worker_id: str
    name: str
    mtime: float
    crash_path: Path
    analysis_dir: Path
    is_gdb_analyzed: bool
    is_llm_analyzed: bool


def discover_crashes() -> list[Crash]:
    crashes: list[Crash] = []
    for crash_path in (SCRIPT_DIR / "output").glob("*/*/crashes/*"):
        if not crash_path.is_file():
            continue
        if crash_path.name == "README.txt":
            continue
        parts = crash_path.parts
        pr_id = parts[-4]
        worker_id = parts[-3]
        name = crash_path.name
        analysis_dir = SCRIPT_DIR / "analysis" / pr_id / f"{worker_id}-{name}"
        crashes.append(Crash(
            pr_id=pr_id,
            worker_id=worker_id,
            name=name,
            mtime=crash_path.stat().st_mtime,
            crash_path=crash_path,
            analysis_dir=analysis_dir,
            is_gdb_analyzed=(analysis_dir / "info.txt").exists(),
            is_llm_analyzed=(analysis_dir / "llm_summary.json").exists(),
        ))
    crashes.sort(key=lambda c: c.mtime, reverse=True)
    return crashes


def relative_age(mtime: float) -> str:
    age = datetime.now(timezone.utc).timestamp() - mtime
    if age < 60:
        return f"{int(age)}s ago"
    if age < 3600:
        return f"{int(age / 60)}m ago"
    if age < 86400:
        return f"{int(age / 3600)}h ago"
    return f"{int(age / 86400)}d ago"


def crash_label(crash: Crash) -> str:
    if crash.is_llm_analyzed:
        indicator = "[[L]"
    elif crash.is_gdb_analyzed:
        indicator = "[[✓]"
    else:
        indicator = "[[ ]"
    return (
        f"[cyan bold]{crash.pr_id}[/] "
        f"{indicator} {crash.worker_id} {crash.name[:20]} {relative_age(crash.mtime)}"
    )


def render_detail(crash: Crash | None, analyzing: str = "") -> str:
    if crash is None:
        return "No crash selected."

    if analyzing == "gdb":
        return f"Analyzing {crash.name}...\n\nRunning GDB via Docker. This may take a minute."

    lines: list[str] = []

    if crash.is_llm_analyzed:
        lines.append("=== LLM Analysis ===")
        try:
            data = json.loads((crash.analysis_dir / "llm_summary.json").read_text())
            for key in ("is_cpython_error", "error_category", "one_line_summary", "short_summary"):
                if key in data:
                    lines.append(f"{key}: {data[key]}")
        except Exception as e:
            lines.append(f"(error reading llm_summary.json: {e})")
        lines.append("")

    if crash.is_gdb_analyzed:
        input_path = crash.analysis_dir / "input"
        info_path = crash.analysis_dir / "info.txt"

        lines.append("=== Input ===")
        if input_path.exists():
            lines.append(input_path.read_text(errors="replace"))
        else:
            lines.append("(input file missing)")
        lines.append("")

        lines.append("=== GDB Output ===")
        if info_path.exists():
            lines.append(info_path.read_text(errors="replace"))
        else:
            lines.append("(info.txt missing)")

        if not crash.is_llm_analyzed:
            lines.append("")
            lines.append("[Press Enter to run LLM analysis]")
    else:
        lines.append("=== Input (raw crash file) ===")
        try:
            lines.append(crash.crash_path.read_text(errors="replace"))
        except Exception as e:
            lines.append(f"(error reading crash file: {e})")
        lines.append("")
        lines.append("[Press Enter to run GDB analysis via analyze.py]")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Textual app
# ---------------------------------------------------------------------------

class AnalysisDone(Message):
    def __init__(self, pr_id: str) -> None:
        super().__init__()
        self.pr_id = pr_id


class LLMAnalysisDone(Message):
    def __init__(self, crash_key: str) -> None:
        super().__init__()
        self.crash_key = crash_key


class CrashBrowserApp(App):
    CSS = """
    #left-pane {
        width: 35%;
        border: solid $primary;
        height: 100%;
    }
    #right-pane {
        width: 65%;
        border: solid $primary;
        height: 100%;
        padding: 0 1;
    }
    #detail {
        width: 100%;
    }
    Horizontal {
        height: 1fr;
    }
    #llm-status {
        height: 1;
        background: $accent;
        color: $text;
        padding: 0 1;
        display: none;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("ctrl+c", "quit", "Quit", show=False),
        Binding("r", "refresh", "Refresh"),
        Binding("enter", "analyze", "Analyze"),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._crashes: list[Crash] = []
        self._index_map: list[int | None] = []  # list pos -> crash index, or None for headers
        self._analyzing: set[str] = set()      # crash keys: GDB in progress
        self._llm_analyzing: set[str] = set()  # crash keys: LLM in progress

    def _crash_key(self, crash: Crash) -> str:
        return f"{crash.pr_id}/{crash.worker_id}/{crash.name}"

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with ScrollableContainer(id="left-pane"):
                yield ListView(id="crash-list")
            with ScrollableContainer(id="right-pane"):
                yield Static("", id="detail")
        yield Static("", id="llm-status")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "py-fuzz crash browser"
        self._load_crashes()
        self.set_interval(5.0, self._refresh_crashes)

    def _get_category(self, crash: Crash) -> str | None:
        if not crash.is_llm_analyzed:
            return None
        try:
            data = json.loads((crash.analysis_dir / "llm_summary.json").read_text())
            return data.get("error_category") or "unknown"
        except Exception:
            return None

    def _build_list(self) -> tuple[list[ListItem], list[int | None]]:
        unanalyzed = [i for i, c in enumerate(self._crashes) if not c.is_llm_analyzed]
        categorized: dict[str, list[int]] = {}
        for i, crash in enumerate(self._crashes):
            if crash.is_llm_analyzed:
                cat = self._get_category(crash) or "unknown"
                categorized.setdefault(cat, []).append(i)

        items: list[ListItem] = []
        index_map: list[int | None] = []

        if unanalyzed:
            items.append(ListItem(Label("[bold dim]Unanalyzed[/]"), disabled=True))
            index_map.append(None)
            for ci in unanalyzed:
                items.append(ListItem(Label(crash_label(self._crashes[ci]))))
                index_map.append(ci)

        for cat in sorted(categorized.keys()):
            items.append(ListItem(Label(f"[bold dim]{cat}[/]"), disabled=True))
            index_map.append(None)
            for ci in categorized[cat]:
                items.append(ListItem(Label(crash_label(self._crashes[ci]))))
                index_map.append(ci)

        return items, index_map

    def _selected_key(self) -> str | None:
        """Return the crash key for the currently selected list item, if any."""
        lv = self.query_one("#crash-list", ListView)
        li = lv.index
        if li is None or li >= len(self._index_map):
            return None
        ci = self._index_map[li]
        return self._crash_key(self._crashes[ci]) if ci is not None else None

    def _list_idx_for_key(self, key: str) -> int | None:
        for li, ci in enumerate(self._index_map):
            if ci is not None and self._crash_key(self._crashes[ci]) == key:
                return li
        return None

    def _first_crash_list_idx(self) -> int | None:
        for li, ci in enumerate(self._index_map):
            if ci is not None:
                return li
        return None

    def _load_crashes(self) -> None:
        prev_key = self._selected_key()
        self._crashes = discover_crashes()
        items, self._index_map = self._build_list()
        lv = self.query_one("#crash-list", ListView)
        lv.clear()
        for item in items:
            lv.append(item)
        new_li = (self._list_idx_for_key(prev_key) if prev_key else None) \
                 or self._first_crash_list_idx()
        if new_li is not None:
            lv.index = new_li
        self._update_detail(new_li)

    def _refresh_crashes(self) -> None:
        new_crashes = discover_crashes()
        new_keys = [self._crash_key(c) for c in new_crashes]
        old_keys = [self._crash_key(c) for c in self._crashes]
        if new_keys == old_keys:
            # Update labels in-place (relative age changes); headers stay put
            self._crashes = new_crashes
            lv = self.query_one("#crash-list", ListView)
            list_items = lv.query(ListItem)
            for li, ci in enumerate(self._index_map):
                if ci is not None and li < len(list_items):
                    list_items[li].query_one(Label).update(crash_label(self._crashes[ci]))
            return
        self._load_crashes()

    def _update_detail(self, list_idx: int | None = None) -> None:
        lv = self.query_one("#crash-list", ListView)
        detail = self.query_one("#detail", Static)
        if list_idx is None:
            list_idx = lv.index
        if list_idx is None or list_idx >= len(self._index_map):
            detail.update("No crash selected.")
            return
        crash_idx = self._index_map[list_idx]
        if crash_idx is None:
            detail.update("No crash selected.")
            return
        crash = self._crashes[crash_idx]
        key = self._crash_key(crash)
        if key in self._analyzing:
            analyzing = "gdb"
        elif key in self._llm_analyzing:
            analyzing = "llm"
        else:
            analyzing = ""
        detail.update(render_detail(crash, analyzing=analyzing))

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        self._update_detail()

    def on_key(self, event) -> None:
        if event.key == "enter":
            self.action_analyze()
            event.stop()

    def action_analyze(self) -> None:
        lv = self.query_one("#crash-list", ListView)
        li = lv.index
        if li is None or li >= len(self._index_map):
            return
        ci = self._index_map[li]
        if ci is None:
            return
        crash = self._crashes[ci]
        key = self._crash_key(crash)

        if not crash.is_gdb_analyzed and key not in self._analyzing:
            # Trigger GDB analysis
            self._analyzing.add(key)
            self._update_detail()

            def run_gdb() -> None:
                subprocess.run(
                    ["python", str(SCRIPT_DIR / "analyze.py"), crash.pr_id],
                    cwd=SCRIPT_DIR,
                )
                self.post_message(AnalysisDone(crash.pr_id))

            threading.Thread(target=run_gdb, daemon=True).start()

        elif crash.is_gdb_analyzed and not crash.is_llm_analyzed and key not in self._llm_analyzing:
            # Trigger LLM analysis
            self._llm_analyzing.add(key)
            status = self.query_one("#llm-status", Static)
            active = len(self._llm_analyzing)
            status.update(f" Analyzing {crash.name}…" + (f" ({active} running)" if active > 1 else ""))
            status.display = True
            self._update_detail()

            def run_llm() -> None:
                subprocess.run(
                    ["uv", "run", str(SCRIPT_DIR / "pr_handler" / "analyze_crashes.py"),
                     str(crash.analysis_dir)],
                    cwd=SCRIPT_DIR,
                )
                self.post_message(LLMAnalysisDone(key))

            threading.Thread(target=run_llm, daemon=True).start()

    def on_analysis_done(self, event: AnalysisDone) -> None:
        self._analyzing = {
            k for k in self._analyzing if not k.startswith(f"{event.pr_id}/")
        }
        self._load_crashes()

    def on_llm_analysis_done(self, event: LLMAnalysisDone) -> None:
        self._llm_analyzing.discard(event.crash_key)
        status = self.query_one("#llm-status", Static)
        if self._llm_analyzing:
            status.update(f" Analyzing… ({len(self._llm_analyzing)} running)")
        else:
            status.display = False
        self._load_crashes()

    def action_refresh(self) -> None:
        self._refresh_crashes()
        self.notify("Crash list refreshed", timeout=2)

    def action_cursor_up(self) -> None:
        self.query_one("#crash-list", ListView).action_cursor_up()

    def action_cursor_down(self) -> None:
        self.query_one("#crash-list", ListView).action_cursor_down()


if __name__ == "__main__":
    CrashBrowserApp().run()
