from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

import click


def command_text(cmd: list[str]) -> str:
    return shlex.join(str(part) for part in cmd)


def step(message: str) -> None:
    click.echo(click.style(f"==> {message}", fg="cyan", bold=True))


def success(message: str) -> None:
    click.echo(click.style(message, fg="green"))


def warn(message: str) -> None:
    click.echo(click.style(message, fg="yellow"), err=True)


def detail(label: str, value: str) -> None:
    click.echo(f"  {click.style(label, fg='blue')}: {value}")


def run(cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    click.echo(click.style(f"$ {command_text(cmd)}", fg="bright_black"))
    return subprocess.run(cmd, cwd=cwd, env=env, check=check)
