from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .console import run, step
from .project import REPO_ROOT

IMAGE_DIR = REPO_ROOT / "image"
BUILD_IMAGE = "py-fuzz:build"
RUN_IMAGE = "py-fuzz:run"
DEBUG_IMAGE = "py-fuzz:debug"


def ensure_tool(name: str) -> None:
    if shutil.which(name) is None:
        raise click.ClickException(f"Missing required tool: {name}")


def ensure_image(tag: str, dockerfile_name: str, *, force: bool = False) -> None:
    ensure_tool("docker")
    inspect = subprocess.run(["docker", "image", "inspect", tag], cwd=REPO_ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if force or inspect.returncode != 0:
        step(f"Building Docker image {tag} from image/{dockerfile_name}")
        run(["docker", "build", "-t", tag, "-f", str(IMAGE_DIR / dockerfile_name), str(REPO_ROOT)])


def docker_env(base: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    if base:
        env.update(base)
    return env

