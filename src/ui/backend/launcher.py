from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


UI_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = UI_DIR.parents[1]
BACKEND_SCRIPT = UI_DIR / "backend" / "server.py"


def browser_host(host: str) -> str:
    if host in {"0.0.0.0", "::"}:
        return "localhost"
    return host


def wait_for_http(url: str, *, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=0.75):
                return
        except (OSError, urllib.error.URLError) as exc:
            last_error = exc
            time.sleep(0.25)
    if last_error is not None:
        raise RuntimeError(f"Timed out waiting for {url}: {last_error}")
    raise RuntimeError(f"Timed out waiting for {url}")


def terminate(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def run_ui(
    *,
    project_name: str | None,
    host: str,
    backend_port: int,
    vite_port: int,
    open_browser: bool,
) -> int:
    backend_cmd = [
        sys.executable,
        str(BACKEND_SCRIPT),
        "--host",
        host,
        "--port",
        str(backend_port),
    ]
    if project_name:
        backend_cmd.extend(["--project", project_name])

    env = os.environ.copy()
    env["VITE_PYFUZZ_WS_URL"] = f"ws://{browser_host(host)}:{backend_port}/ws"

    vite_cmd = [
        "pnpm",
        "exec",
        "vite",
        "--host",
        host,
        "--port",
        str(vite_port),
    ]

    backend = subprocess.Popen(backend_cmd, cwd=REPO_ROOT)
    vite = subprocess.Popen(vite_cmd, cwd=UI_DIR, env=env)

    frontend_url = f"http://{browser_host(host)}:{vite_port}/"
    backend_url = f"http://{browser_host(host)}:{backend_port}/health"

    try:
        wait_for_http(backend_url)
        wait_for_http(frontend_url)
        print(f"pyfuzz UI: {frontend_url}")
        if open_browser:
            webbrowser.open(frontend_url)

        while True:
            backend_code = backend.poll()
            vite_code = vite.poll()
            if backend_code is not None:
                terminate(vite)
                return backend_code
            if vite_code is not None:
                terminate(backend)
                return vite_code
            time.sleep(0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        terminate(vite)
        terminate(backend)

