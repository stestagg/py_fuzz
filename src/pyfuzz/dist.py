import json
import re
import shutil
from pathlib import Path

from .env import Env, Image
from .paths import root_path
from .project import Project


RESERVED_ENV_PREFIX = "DIST_"
RESERVED_ENV_KEYS = {
    "PYTHON",
}
ENV_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def parse_env_assignment(assignment: str) -> tuple[str, str]:
    if "=" not in assignment:
        raise ValueError(f"Environment value must be KEY=VALUE: {assignment!r}")
    key, value = assignment.split("=", 1)
    if not ENV_KEY_RE.fullmatch(key):
        raise ValueError(f"Environment key must be a shell-style name: {key!r}")
    if "\n" in value or "\0" in value:
        raise ValueError(f"Environment value may not contain newlines or NUL bytes: {key}")
    if key.startswith(RESERVED_ENV_PREFIX) or key in RESERVED_ENV_KEYS:
        raise ValueError(f"Environment key is reserved for run-dist: {key}")
    return key, value


async def run_dist(
    project: Project,
    script_path: Path,
    *,
    ref: str = "main",
    interactive: bool = False,
    debug: bool = False,
    env_vars: tuple[str, ...] = (),
    configure_args: str = "",
) -> int:
    script_name = script_path.name
    dist_script_dir = project.path("dist_script")
    dist_script_dir.mkdir(parents=True, exist_ok=True)
    dest = dist_script_dir / script_name
    if script_path.resolve() != dest.resolve():
        shutil.copy2(script_path, dest)

    root_path("cache", "dist-builds").mkdir(parents=True, exist_ok=True)

    env = Env(project, Image.DIST)
    env["DIST_SCRIPT_NAME"] = script_name
    env["DIST_REF"] = ref
    env["DIST_DEBUG"] = "1" if debug else "0"
    env["DIST_INTERACTIVE"] = "1" if interactive else "0"
    env["DIST_CONFIGURE_ARGS"] = configure_args
    env["DIST_JOBS"] = str(project.ncpu)
    script_env = {}
    for assignment in env_vars:
        key, value = parse_env_assignment(assignment)
        script_env[key] = value
    env["DIST_SCRIPT_ENV_JSON"] = json.dumps(script_env, sort_keys=True, separators=(",", ":"))

    proc = await env.run([], console=True, interactive=interactive)
    await proc.wait()
    return proc.returncode or 0
