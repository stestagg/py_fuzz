#!/usr/bin/env python3
"""Interactive CPython crash bisect lifecycle manager."""

import os
import re
import subprocess
import sys
from pathlib import Path

import questionary
from questionary import Style
from rich.console import Console
from rich.panel import Panel

CPYTHON_DIR = Path("/src/cpython")
BISECT_SCRIPT = Path("/bisect.sh")
LOG_DIR = Path("/pfm/scratch/bisect-logs")
MAX_TAG_SUGGESTIONS = 50

console = Console()

STYLE = Style([
    ("answer", "fg:#00ff87 bold"),
    ("question", "bold"),
    ("pointer", "fg:#ff9d00 bold"),
    ("highlighted", "fg:#ff9d00 bold"),
    ("selected", "fg:#00ff87"),
])


def run_git(*args: str, capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=CPYTHON_DIR,
        capture_output=capture,
        text=True if capture else None,
    )


def validate_ref(ref: str) -> bool:
    result = run_git("rev-parse", "--verify", ref)
    if result.returncode != 0:
        console.print(f"[red]Ref '{ref}' not found in git history.[/red]")
    return result.returncode == 0


def get_tags() -> list[str]:
    result = run_git("tag", "--sort=-version:refname")
    if result.returncode != 0:
        return []
    tags = [t for t in result.stdout.strip().splitlines() if t]
    return tags[:MAX_TAG_SUGGESTIONS]


def test_ref(ref: str) -> bool | None:
    """Returns True=good (no crash), False=bad (crash), None=build failed."""
    console.print(f"[yellow]Checking out {ref}...[/yellow]")
    co = run_git("checkout", ref)
    if co.returncode != 0:
        console.print(f"[red]Failed to checkout '{ref}'.[/red]")
        return None

    console.print(f"[yellow]Building and testing {ref} (this will take a while)...[/yellow]")
    result = subprocess.run([str(BISECT_SCRIPT)], cwd=CPYTHON_DIR)

    if result.returncode == 125:
        console.print(f"[yellow]'{ref}' failed to build — skipping.[/yellow]")
        return None
    elif result.returncode == 1:
        console.print(f"[red]✗ '{ref}' CRASHES (bad)[/red]")
        return False
    else:
        console.print(f"[green]✓ '{ref}' is clean (good)[/green]")
        return True


def ask_bad_ref() -> str:
    while True:
        ref = questionary.text(
            "Bad ref (commit/tag where crash is present):",
            default="main",
            style=STYLE,
        ).ask()
        if ref is None:
            sys.exit(0)
        ref = ref.strip()
        if validate_ref(ref):
            return ref


def ask_knows_good_ref() -> bool:
    answer = questionary.confirm(
        "Do you have a known good ref (where the crash doesn't happen)?",
        default=False,
        style=STYLE,
    ).ask()
    if answer is None:
        sys.exit(0)
    return answer


def ask_good_ref_direct() -> str:
    while True:
        ref = questionary.text(
            "Good ref (commit/tag where crash is absent):",
            style=STYLE,
        ).ask()
        if ref is None:
            sys.exit(0)
        ref = ref.strip()
        if validate_ref(ref):
            return ref


def discover_good_ref() -> str:
    tags = get_tags()
    console.print(f"\n[blue]Loaded {len(tags)} recent tags for autocomplete.[/blue]")
    console.print("[dim]Keep guessing older refs until we find one without the crash.[/dim]\n")

    while True:
        ref = questionary.autocomplete(
            "Guess a tag/ref that predates the bug:",
            choices=tags,
            style=STYLE,
            validate=lambda x: bool(x.strip()) or "Please enter a ref",
        ).ask()
        if ref is None:
            sys.exit(0)
        ref = ref.strip()

        if not validate_ref(ref):
            continue

        result = test_ref(ref)
        if result is True:
            console.print(f"\n[green]Found good ref: {ref}[/green]")
            return ref
        elif result is False:
            console.print("[yellow]That ref crashes too — try an older one.[/yellow]\n")
        else:
            console.print("[yellow]Build failed — try a different ref.[/yellow]\n")


def run_bisect(bad_ref: str, good_ref: str) -> None:
    console.print(
        Panel(
            f"  Bad:  [red]{bad_ref}[/red]\n  Good: [green]{good_ref}[/green]",
            title="[bold]Starting bisect[/bold]",
            border_style="yellow",
        )
    )

    run_git("bisect", "reset")
    run_git("bisect", "start")
    run_git("bisect", "bad", bad_ref)
    run_git("bisect", "good", good_ref)

    console.print("[blue]Running bisect\n")

    subprocess.run(
        ["git", "bisect", "run", str(BISECT_SCRIPT)],
        cwd=CPYTHON_DIR,
    )


def show_result() -> None:
    log = run_git("bisect", "log")
    if log.returncode != 0 or not log.stdout.strip():
        return

    # Extract the first-bad commit line from the log
    first_bad = None
    for line in log.stdout.splitlines():
        if "first bad commit" in line:
            m = re.search(r"\[([0-9a-f]+)\]", line)
            if m:
                first_bad = m.group(1)
            break

    body = log.stdout.strip()
    if first_bad:
        detail = run_git("show", "--no-patch", "--format=%H%n%an%n%s", first_bad)
        if detail.returncode == 0:
            parts = detail.stdout.strip().splitlines()
            if len(parts) >= 3:
                body = f"[bold red]First bad commit:[/bold red] {parts[0]}\n[dim]{parts[1]}[/dim]\n{parts[2]}\n\n[dim]{log.stdout.strip()}[/dim]"

    console.print(Panel(body, title="[bold green]Bisect Result[/bold green]", border_style="green"))


def cleanup() -> None:
    console.print("\n[blue]Resetting bisect state...[/blue]")
    run_git("bisect", "reset")


def main() -> None:
    script_name = os.environ.get("SCRIPT_NAME", "").strip()
    if not script_name:
        console.print("[red]Error: SCRIPT_NAME environment variable is not set.[/red]")
        sys.exit(1)

    script_path = Path(f"/pfm/scratch/bisect/{script_name}.py")
    if not script_path.exists():
        console.print(f"[red]Error: Script not found at {script_path}[/red]")
        sys.exit(1)

    if os.environ.get("BISECT_LOG"):
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        if any(LOG_DIR.iterdir()):
            console.print(f"[red]Error: Log directory {LOG_DIR} is not empty.[/red]")
            sys.exit(1)
        console.print(f"[blue]Logging per-commit output to {LOG_DIR}/<short_hash>.txt[/blue]")

    console.print(
        Panel(
            f"[bold]CPython Crash Bisection[/bold]\nScript: [cyan]{script_name}.py[/cyan]",
            border_style="blue",
        )
    )

    bad_ref = ask_bad_ref()
    console.print(f"[red]Bad ref set: {bad_ref}[/red]\n")

    confirm = questionary.confirm(
        f"Confirm crash on '{bad_ref}' by running the bisect script now?",
        default=True,
        style=STYLE,
    ).ask()
    if confirm is None:
        sys.exit(0)
    if confirm:
        result = test_ref(bad_ref)
        if result is True:
            console.print(f"[yellow]Warning: '{bad_ref}' did NOT crash — double-check your bad ref.[/yellow]\n")
        elif result is None:
            console.print(f"[yellow]Warning: '{bad_ref}' failed to build — proceed with caution.[/yellow]\n")
        else:
            console.print(f"[green]Crash confirmed on '{bad_ref}'.[/green]\n")

    if ask_knows_good_ref():
        good_ref = ask_good_ref_direct()
    else:
        good_ref = discover_good_ref()

    console.print(f"[green]Good ref set: {good_ref}[/green]\n")

    try:
        run_bisect(bad_ref, good_ref)
        show_result()
    finally:
        cleanup()


if __name__ == "__main__":
    main()
