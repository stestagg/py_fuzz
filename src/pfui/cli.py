from __future__ import annotations

import argparse
import asyncio
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

from pyfuzz.project import Project

from .app import serve
from .project_service import find_default_project


WEB_DIR = Path(__file__).resolve().parent / "web"


def browser_host(host: str) -> str:
    return "localhost" if host in {"0.0.0.0", "::"} else host


def wait_for_http(url: str, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=0.75):
                return
        except (OSError, urllib.error.URLError) as exc:
            error = exc
            time.sleep(0.2)
    raise RuntimeError(f"Timed out waiting for {url}: {error}")


def terminate(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def resolve_initial_project(explicit: str | None) -> tuple[str | None, str | None]:
    if explicit:
        Project.load(explicit)
        return explicit, None
    default = find_default_project(Path.cwd())
    if default is None:
        return None, None
    try:
        Project.load(default)
    except FileNotFoundError:
        return None, f"Default project '{default}' does not exist; choose another project."
    return default, None


def run_dev(args: argparse.Namespace) -> int:
    backend_command = [
        sys.executable,
        "-m",
        "pfui",
        "--host",
        args.host,
        "--port",
        str(args.port),
        "--no-dev",
        "--no-open",
    ]
    if args.project:
        backend_command.extend(["--project", args.project])
    environment = os.environ.copy()
    environment["PFUI_BACKEND_URL"] = f"http://{browser_host(args.host)}:{args.port}"
    vite_command = [
        "pnpm",
        "exec",
        "vite",
        "--host",
        args.host,
        "--port",
        str(args.vite_port),
    ]
    backend = subprocess.Popen(backend_command, cwd=Path.cwd())
    frontend = subprocess.Popen(vite_command, cwd=WEB_DIR, env=environment)
    backend_url = f"http://{browser_host(args.host)}:{args.port}/health"
    frontend_url = f"http://{browser_host(args.host)}:{args.vite_port}/"
    try:
        wait_for_http(backend_url)
        wait_for_http(frontend_url)
        print(f"PFUI development server: {frontend_url}")
        if not args.no_open:
            webbrowser.open(frontend_url)
        while backend.poll() is None and frontend.poll() is None:
            time.sleep(0.25)
        return backend.returncode if backend.returncode is not None else frontend.returncode or 0
    except KeyboardInterrupt:
        return 130
    finally:
        terminate(frontend)
        terminate(backend)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the cross-project pyfuzz UI.")
    parser.add_argument("--project", help="Project to select initially.")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1).")
    parser.add_argument("--port", type=int, default=8767, help="Server port (default: 8767).")
    parser.add_argument("--no-open", action="store_true", help="Do not open a browser window.")
    dev_group = parser.add_mutually_exclusive_group()
    dev_group.add_argument("--dev", dest="dev", action="store_true", help="Run the frontend through Vite with HMR (default).")
    dev_group.add_argument("--no-dev", dest="dev", action="store_false", help="Serve the built static frontend instead of Vite.")
    parser.set_defaults(dev=True)
    parser.add_argument("--vite-port", type=int, default=5174, help="Vite port for dev mode (default: 5174).")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    try:
        initial_project, warning = resolve_initial_project(args.project)
    except FileNotFoundError as exc:
        raise SystemExit(str(exc)) from exc
    if args.dev:
        raise SystemExit(run_dev(args))
    url = f"http://{browser_host(args.host)}:{args.port}/"
    if initial_project:
        from urllib.parse import quote

        url += f"?project={quote(initial_project)}"

    def started() -> None:
        print(f"PFUI: {url}")
        if warning:
            print(f"Warning: {warning}")
        if not args.no_open:
            webbrowser.open(url)

    async def run() -> None:
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        installed_signals: list[signal.Signals] = []

        def request_stop() -> None:
            if not stop_event.is_set():
                print("\nStopping PFUI...")
                stop_event.set()

        for handled_signal in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(handled_signal, request_stop)
                installed_signals.append(handled_signal)
            except NotImplementedError:
                pass
        try:
            await serve(
                args.host,
                args.port,
                initial_project=initial_project,
                default_warning=warning,
                on_started=started,
                stop_event=stop_event,
            )
        finally:
            for handled_signal in installed_signals:
                loop.remove_signal_handler(handled_signal)

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
