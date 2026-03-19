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

from rich.markup import escape
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.widgets import Footer, Header, Static, Tree
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


def crash_leaf_label(crash: Crash) -> str:
    if crash.is_llm_analyzed:
        indicator = "[L]"
    elif crash.is_gdb_analyzed:
        indicator = "[✓]"
    else:
        indicator = "[ ]"
    return f"{indicator} {escape(crash.worker_id)} {escape(crash.name[:20])} {relative_age(crash.mtime)}"


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
            gdb_output = info_path.read_text(errors="replace")
            if len(gdb_output) > 30_000:
                half = 15_000
                gdb_output = gdb_output[:half] + "\n[.. content skipped ..]\n" + gdb_output[-half:]
            lines.append(gdb_output)
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
    def __init__(self, crash_key: str) -> None:
        super().__init__()
        self.crash_key = crash_key


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
        Binding("a", "analyze", "Analyze"),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("left", "collapse_node", "Collapse", show=False),
        Binding("right", "expand_node", "Expand", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._crashes: list[Crash] = []
        self._analyzing: set[str] = set()
        self._llm_analyzing: set[str] = set()
        self._gdb_queue: list[Crash] = []
        self._gdb_running: int = 0
        self._gdb_max_concurrent: int = 3
        # None means "expand all" (initial state); after first build, tracks which nodes are open
        self._expanded_prs: set[str] | None = None   # pr_id strings
        self._expanded_cats: set[str] | None = None  # "pr_id/cat" strings

    def _crash_key(self, crash: Crash) -> str:
        return f"{crash.pr_id}/{crash.worker_id}/{crash.name}"

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with ScrollableContainer(id="left-pane"):
                yield Tree("Crashes", id="crash-tree")
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

    def _save_tree_state(self) -> None:
        """Capture current expand/collapse state before rebuilding."""
        tree = self.query_one("#crash-tree", Tree)
        self._expanded_prs = set()
        self._expanded_cats = set()
        for pr_node in tree.root.children:
            if not isinstance(pr_node.data, str):
                continue
            pr_id = pr_node.data
            if not pr_node.is_collapsed:
                self._expanded_prs.add(pr_id)
            for cat_node in pr_node.children:
                if isinstance(cat_node.data, str) and not cat_node.is_collapsed:
                    self._expanded_cats.add(cat_node.data)

    def _build_tree(self) -> None:
        tree = self.query_one("#crash-tree", Tree)
        tree.clear()

        # Group by pr_id
        by_pr: dict[str, list[int]] = {}
        for i, crash in enumerate(self._crashes):
            by_pr.setdefault(crash.pr_id, []).append(i)

        for pr_id in sorted(by_pr.keys()):
            expand_pr = self._expanded_prs is None or pr_id in self._expanded_prs
            pr_node = tree.root.add(f"[cyan bold]{pr_id}[/]", data=pr_id, expand=expand_pr)

            # Group by category within this pr
            by_cat: dict[str, list[int]] = {}
            for ci in by_pr[pr_id]:
                crash = self._crashes[ci]
                cat = self._get_category(crash) if crash.is_llm_analyzed else None
                cat = cat or "unanalyzed"
                by_cat.setdefault(cat, []).append(ci)

            for cat in sorted(by_cat.keys()):
                cat_key = f"{pr_id}/{cat}"
                expand_cat = self._expanded_cats is None or cat_key in self._expanded_cats
                cat_node = pr_node.add(f"[dim]{escape(cat)}[/]", data=cat_key, expand=expand_cat)
                for ci in by_cat[cat]:
                    crash = self._crashes[ci]
                    cat_node.add_leaf(crash_leaf_label(crash), data=ci)

        tree.root.expand()

    def _get_selected_crash(self) -> Crash | None:
        tree = self.query_one("#crash-tree", Tree)
        node = tree.cursor_node
        if node is None or not isinstance(node.data, int):
            return None
        return self._crashes[node.data]

    def _selected_key(self) -> str | None:
        crash = self._get_selected_crash()
        return self._crash_key(crash) if crash else None

    def _find_node_for_key(self, key: str):
        tree = self.query_one("#crash-tree", Tree)

        def search(node):
            if isinstance(node.data, int) and self._crash_key(self._crashes[node.data]) == key:
                return node
            for child in node.children:
                result = search(child)
                if result is not None:
                    return result
            return None

        return search(tree.root)

    def _first_crash_node(self):
        tree = self.query_one("#crash-tree", Tree)

        def search(node):
            if isinstance(node.data, int):
                return node
            for child in node.children:
                result = search(child)
                if result is not None:
                    return result
            return None

        return search(tree.root)

    def _load_crashes(self) -> None:
        prev_key = self._selected_key()
        if self._crashes:  # save state only after initial population
            self._save_tree_state()
        self._crashes = discover_crashes()
        self._build_tree()
        target = (
            self._find_node_for_key(prev_key) if prev_key else None
        ) or self._first_crash_node()
        if target is not None:
            # Defer until after the tree finishes processing the rebuild;
            # otherwise tree.clear()'s async cursor reset fires after our move.
            self.call_after_refresh(self.query_one("#crash-tree", Tree).move_cursor, target)
        self._update_detail()

    def _refresh_crashes(self) -> None:
        self._load_crashes()

    def _update_detail(self) -> None:
        detail = self.query_one("#detail", Static)
        crash = self._get_selected_crash()
        if crash is None:
            detail.update("No crash selected.")
            return
        key = self._crash_key(crash)
        if key in self._analyzing:
            analyzing = "gdb"
        elif key in self._llm_analyzing:
            analyzing = "llm"
        else:
            analyzing = ""
        detail.update(Text(render_detail(crash, analyzing=analyzing)))

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted) -> None:
        self._update_detail()

    def _get_crash_leaves(self, node) -> list[Crash]:
        """Return all crashes for leaf nodes under (and including) the given node."""
        if isinstance(node.data, int):
            return [self._crashes[node.data]]
        results = []
        for child in node.children:
            results.extend(self._get_crash_leaves(child))
        return results

    def _start_gdb(self, crash: Crash) -> None:
        key = self._crash_key(crash)
        self._analyzing.add(key)
        self._gdb_running += 1
        self._update_detail()

        def run_gdb() -> None:
            subprocess.run(
                [str(SCRIPT_DIR / "analyze.py"), crash.pr_id,
                 "--worker", crash.worker_id, "--crash", crash.name],
                cwd=SCRIPT_DIR,
            )
            self.call_from_thread(self.post_message, AnalysisDone(key))

        threading.Thread(target=run_gdb, daemon=True).start()

    def _drain_gdb_queue(self) -> None:
        while self._gdb_queue and self._gdb_running < self._gdb_max_concurrent:
            crash = self._gdb_queue.pop(0)
            key = self._crash_key(crash)
            if key not in self._analyzing:
                self._start_gdb(crash)

    def action_analyze(self) -> None:
        tree = self.query_one("#crash-tree", Tree)
        node = tree.cursor_node
        if node is None:
            return

        for crash in self._get_crash_leaves(node):
            key = self._crash_key(crash)
            if not crash.is_gdb_analyzed and key not in self._analyzing:
                if crash not in self._gdb_queue:
                    self._gdb_queue.append(crash)
            elif crash.is_gdb_analyzed and not crash.is_llm_analyzed and key not in self._llm_analyzing:
                self._llm_analyzing.add(key)
                status = self.query_one("#llm-status", Static)
                active = len(self._llm_analyzing)
                status.update(f" Analyzing {escape(crash.name)}…" + (f" ({active} running)" if active > 1 else ""))
                status.display = True
                self._update_detail()

                def run_llm(c: Crash = crash, k: str = key) -> None:
                    subprocess.run(
                        ["uv", "run", str(SCRIPT_DIR / "pr_handler" / "analyze_crashes.py"),
                         str(c.analysis_dir)],
                        cwd=SCRIPT_DIR,
                    )
                    self.call_from_thread(self.post_message, LLMAnalysisDone(k))

                threading.Thread(target=run_llm, daemon=True).start()

        self._drain_gdb_queue()

    def on_analysis_done(self, event: AnalysisDone) -> None:
        self._analyzing.discard(event.crash_key)
        self._gdb_running = max(0, self._gdb_running - 1)
        self._drain_gdb_queue()
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
        self.query_one("#crash-tree", Tree).action_cursor_up()

    def action_cursor_down(self) -> None:
        self.query_one("#crash-tree", Tree).action_cursor_down()

    def action_collapse_node(self) -> None:
        tree = self.query_one("#crash-tree", Tree)
        node = tree.cursor_node
        if node is None:
            return
        if not node.is_collapsed and node.allow_expand:
            node.collapse()
        elif node.parent and node.parent != tree.root:
            tree.move_cursor(node.parent)
            node.parent.collapse()

    def action_expand_node(self) -> None:
        tree = self.query_one("#crash-tree", Tree)
        node = tree.cursor_node
        if node is None:
            return
        if node.is_collapsed:
            node.expand()
        elif node.children:
            tree.move_cursor(node.children[0])


if __name__ == "__main__":
    CrashBrowserApp().run()
