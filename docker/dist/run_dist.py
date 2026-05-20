#!/usr/bin/env python3
"""Build or reuse a stock CPython checkout, then run a supplied script."""

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path


CPYTHON_DIR = Path("/src/cpython")
DIST_SCRIPT_DIR = Path("/pfm/dist_script")
DIST_BUILDS_DIR = Path("/pfm/cache/dist-builds")
BUILDER_ID = "archlinux-cpython-dist-v1"


def fail(message: str, code: int = 2) -> None:
    print(f"run-dist: {message}", file=sys.stderr)
    raise SystemExit(code)


def run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    capture: bool = False,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    printable = shlex.join(str(part) for part in cmd)
    if cwd:
        print(f"+ cd {cwd} && {printable}", flush=True)
    else:
        print(f"+ {printable}", flush=True)
    return subprocess.run(
        cmd,
        cwd=cwd,
        check=True,
        capture_output=capture,
        text=True if capture else None,
        env=env,
    )


def refresh_cpython_refs() -> None:
    run(
        [
            "git",
            "fetch",
            "--tags",
            "--prune",
            "origin",
            "+refs/heads/*:refs/remotes/origin/*",
        ],
        cwd=CPYTHON_DIR,
    )


def resolve_ref(ref: str) -> str:
    candidates = []
    if not ref.startswith("origin/"):
        candidates.append(f"origin/{ref}")
    candidates.append(ref)

    for candidate in candidates:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", f"{candidate}^{{commit}}"],
            cwd=CPYTHON_DIR,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    fail(f"CPython ref not found after fetch: {ref}")


def parse_configure_args(raw: str) -> list[str]:
    try:
        args = shlex.split(raw)
    except ValueError as exc:
        fail(f"invalid --configure-args: {exc}")
    for arg in args:
        if arg == "--prefix" or arg.startswith("--prefix="):
            fail("--configure-args may not override --prefix")
    return args


def build_spec(ref: str, commit: str, debug: bool, configure_args: list[str]) -> dict:
    configure = ["--prefix=<dist-build>", *configure_args]
    if debug:
        configure.append("--with-pydebug")
    return {
        "builder": BUILDER_ID,
        "ref": ref,
        "commit": commit,
        "debug": debug,
        "configure_args": configure,
    }


def spec_hash(spec: dict) -> str:
    payload = json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()[:24]


def build_is_complete(build_root: Path, spec: dict) -> bool:
    stamp = build_root / ".complete"
    python = build_root / "bin" / "python3"
    manifest = build_root / "manifest.json"
    if not stamp.exists() or not python.exists() or not manifest.exists():
        return False
    try:
        existing = json.loads(manifest.read_text())
    except json.JSONDecodeError:
        return False
    return existing == spec


def prepare_source(commit: str) -> None:
    run(["git", "reset", "--hard"], cwd=CPYTHON_DIR)
    run(["git", "clean", "-xfd"], cwd=CPYTHON_DIR)
    run(["git", "checkout", "--detach", commit], cwd=CPYTHON_DIR)


def build_python(build_root: Path, spec: dict, configure_args: list[str], debug: bool) -> None:
    if build_root.exists():
        shutil.rmtree(build_root)
    build_root.mkdir(parents=True)

    prepare_source(spec["commit"])

    configure = ["./configure", f"--prefix={build_root}", *configure_args]
    if debug:
        configure.append("--with-pydebug")

    run(configure, cwd=CPYTHON_DIR)
    jobs = os.environ.get("DIST_JOBS", "1")
    run(["make", "-j", jobs], cwd=CPYTHON_DIR)
    run(["make", "install"], cwd=CPYTHON_DIR)

    (build_root / "manifest.json").write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n")
    (build_root / ".complete").write_text("ok\n")


def ensure_build(ref: str, debug: bool, configure_args: list[str]) -> tuple[Path, str, dict]:
    refresh_cpython_refs()
    commit = resolve_ref(ref)
    spec = build_spec(ref, commit, debug, configure_args)
    key = spec_hash(spec)
    build_root = DIST_BUILDS_DIR / key
    print(f"run-dist: resolved {ref} -> {commit}", flush=True)
    print(f"run-dist: build cache {build_root}", flush=True)

    if build_is_complete(build_root, spec):
        print("run-dist: reusing cached build", flush=True)
    else:
        print("run-dist: building CPython", flush=True)
        build_python(build_root, spec, configure_args, debug)
    return build_root, key, spec


def find_python(build_root: Path) -> Path:
    python = build_root / "bin" / "python3"
    if python.exists():
        return python
    candidates = sorted((build_root / "bin").glob("python3.*"))
    if candidates:
        return candidates[0]
    fail(f"built Python not found under {build_root / 'bin'}")


def load_script_env() -> dict[str, str]:
    raw = os.environ.get("DIST_SCRIPT_ENV_JSON", "{}")
    try:
        env = json.loads(raw)
    except json.JSONDecodeError as exc:
        fail(f"invalid DIST_SCRIPT_ENV_JSON: {exc}")
    if not isinstance(env, dict):
        fail("DIST_SCRIPT_ENV_JSON must be an object")
    return {str(key): str(value) for key, value in env.items()}


def interactive_shell(
    command: list[str],
    build_root: Path,
    script_path: Path,
    key: str,
    script_env: dict[str, str],
) -> None:
    env = os.environ.copy()
    env.update(script_env)
    env["DIST_BUILD_HASH"] = key
    env["DIST_BUILD_ROOT"] = str(build_root)
    env["DIST_SCRIPT"] = str(script_path)
    env["DIST_COMMAND"] = shlex.join(str(part) for part in command)
    env["PYTHON"] = str(command[0])
    env["PATH"] = f"{build_root / 'bin'}:{env.get('PATH', '')}"

    print()
    print(f"run-dist: would run: {env['DIST_COMMAND']}")
    print("run-dist: dropping to interactive shell")
    print()
    os.chdir(CPYTHON_DIR)
    os.execvpe("/bin/bash", ["bash"], env)


def main() -> int:
    script_name = os.environ.get("DIST_SCRIPT_NAME", "").strip()
    if not script_name:
        fail("DIST_SCRIPT_NAME is not set")
    script_path = DIST_SCRIPT_DIR / script_name
    if not script_path.exists():
        fail(f"script not found: {script_path}")

    ref = os.environ.get("DIST_REF", "main").strip() or "main"
    debug = os.environ.get("DIST_DEBUG", "0") == "1"
    configure_args = parse_configure_args(os.environ.get("DIST_CONFIGURE_ARGS", ""))

    build_root, key, _spec = ensure_build(ref, debug, configure_args)
    python = find_python(build_root)
    command = [str(python), str(script_path)]
    script_env = load_script_env()

    if os.environ.get("DIST_INTERACTIVE", "0") == "1":
        interactive_shell(command, build_root, script_path, key, script_env)
        return 0

    print(f"run-dist: running: {shlex.join(command)}", flush=True)
    env = os.environ.copy()
    env.update(script_env)
    return subprocess.run(command, env=env).returncode


if __name__ == "__main__":
    raise SystemExit(main())
