from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import click
import odhash
from rich.markup import escape
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer
from textual.widgets import Footer, Header, Static, Tree


@dataclass
class Crash:
    worker_id: str
    name: str
    mtime: float
    crash_path: Path
    analysis_dir: Path


class CrashBrowser(App):
    BINDINGS = [Binding("q", "quit", "Quit"), Binding("r", "refresh", "Refresh")]

    def __init__(self, project_root: Path) -> None:
        super().__init__()
        self.project_root = project_root
        self.crashes: list[Crash] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with ScrollableContainer(id="left"):
                yield Tree("Crashes", id="tree")
            with ScrollableContainer(id="right"):
                yield Static("", id="detail")
        yield Footer()

    def on_mount(self) -> None:
        self.title = f"pyfuzz: {self.project_root.name}"
        self.refresh_data()
        self.query_one("#tree", Tree).focus()

    def category_for(self, crash: Crash) -> str:
        info_path = crash.analysis_dir / "info.txt"
        if info_path.exists():
            text = info_path.read_text(errors="replace")
            m = re.search(r"AddressSanitizer: (\S+)", text)
            if m:
                return m.group(1)
        return "unknown"

    def refresh_data(self) -> None:
        self.crashes = []
        for crash_path in sorted((self.project_root / "outputs").glob("*/crashes/*")):
            if not crash_path.is_file() or crash_path.name == "README.txt":
                continue
            worker_id = crash_path.parts[-3]
            analysis_dir = self.project_root / "analysis" / odhash.hash(crash_path.name)
            self.crashes.append(Crash(worker_id, crash_path.name, crash_path.stat().st_mtime, crash_path, analysis_dir))
        self.crashes.sort(key=lambda crash: crash.mtime, reverse=True)
        tree = self.query_one("#tree", Tree)
        saved_key: tuple[str, str] | None = None
        cursor = tree.cursor_node
        if cursor is not None and isinstance(cursor.data, Crash):
            saved_key = (cursor.data.worker_id, cursor.data.name)
        tree.clear()
        root = tree.root
        categories: dict[str, object] = {}
        for crash in self.crashes:
            category = self.category_for(crash)
            node = categories.get(category)
            if node is None:
                node = root.add(category, data=None)
                categories[category] = node
            node.add(self.label_for(crash), data=crash, allow_expand=False)
        root.expand_all()
        restored = False
        if saved_key is not None:
            for cat_node in root.children:
                for crash_node in cat_node.children:
                    if isinstance(crash_node.data, Crash) and (crash_node.data.worker_id, crash_node.data.name) == saved_key:
                        tree.move_cursor(crash_node)
                        self.show_detail(crash_node.data)
                        restored = True
                        break
                if restored:
                    break
        if not restored:
            self.show_detail(None)

    def label_for(self, crash: Crash) -> str:
        age = int(datetime.now(timezone.utc).timestamp() - crash.mtime)
        return f"{escape(odhash.hash(crash.name))} ({age}s ago)"

    def show_detail(self, crash: Crash | None) -> None:
        detail = self.query_one("#detail", Static)
        if crash is None:
            detail.update("No crash selected")
            return
        lines = [f"worker: {crash.worker_id}", f"crash: {crash.name}", ""]
        input_text = crash.crash_path.read_text(errors="replace")
        lines += ["=== Input ===", input_text, ""]
        info_path = crash.analysis_dir / "info.txt"
        if info_path.exists():
            lines += ["=== Analysis ===", info_path.read_text(errors="replace")[:30000]]
        else:
            lines += ["No analysis available yet. Run ./pyfuzz analyze <project>."]
        detail.update("\n".join(lines))

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted) -> None:
        if isinstance(event.node.data, Crash):
            self.show_detail(event.node.data)

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        if isinstance(event.node.data, Crash):
            self.show_detail(event.node.data)

    def action_refresh(self) -> None:
        self.refresh_data()


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--project-root", required=True, type=click.Path(path_type=Path))
def main(project_root: Path) -> None:
    CrashBrowser(project_root).run()


if __name__ == "__main__":
    main()
