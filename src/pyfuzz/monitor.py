import asyncio
import json
import math
import socket
import sys
import uuid
import urllib.request
from datetime import datetime, timezone

from .project import Project
from .summary import summarize_fuzzing

_LOG_URL = "https://logs.offd.es/logs/fuzz/logs"
_LOG_SECRET = "XRAEtZ4E8qDNdDr7DOf2Wunj9shgZXSj"
_NTFY_URL = "https://ntfy.sh/ss-pyfuzz-notifications-lemon-koala"
MONITOR_INTERVAL = 30


def _magnitude_tier(n: int) -> int:
    if n <= 0:
        return -1
    return int(math.log10(n))


def _post_ntfy(message: str) -> None:
    req = urllib.request.Request(
        _NTFY_URL,
        data=message.encode("utf-8"),
        headers={"Title": "pyfuzz"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception as e:
        print(f"[monitor] warning: ntfy post failed: {e}", file=sys.stderr)


def _post_log(log_data: dict, session_id: str) -> None:
    payload = json.dumps([{
        "log_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "device": socket.gethostname(),
        "session": session_id,
        "log": log_data,
    }]).encode()
    req = urllib.request.Request(
        _LOG_URL,
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": _LOG_SECRET},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status not in range(200, 300):
                print(f"[monitor] warning: server returned {resp.status}", file=sys.stderr)
    except Exception as e:
        print(f"[monitor] warning: post failed: {e}", file=sys.stderr)


async def monitor_loop(
    project: Project,
    interval: int = MONITOR_INTERVAL,
    once: bool = False,
    get_running_workers=None,
    notify: bool = True,
) -> None:
    session_id = str(uuid.uuid4())
    print(f"[monitor] watching {project.name} (interval={interval}s, session={session_id[:8]})")
    crash_tier: int | None = None
    core_tier: int | None = None
    while True:
        try:
            stats = summarize_fuzzing(project)
        except Exception:
            stats = {"project": project.name}
        if get_running_workers is not None:
            stats["running_workers"] = get_running_workers()
        await asyncio.to_thread(_post_log, stats, session_id)
        parts = (
            f"crashes={stats.get('crashes', 0)} cores={stats.get('core_dumps', 0)}"
            f" execs={stats.get('execs_done', 0)} hangs={stats.get('saved_hangs', 0)}"
            f" tmouts={stats.get('total_tmout', 0)}"
        )
        if "running_workers" in stats:
            parts += f" workers={stats['running_workers']}"
        print(f"[monitor] posted: {parts}")
        if notify:
            cur_crash = _magnitude_tier(stats.get("crashes", 0))
            cur_core = _magnitude_tier(stats.get("core_dumps", 0))
            if crash_tier is None:
                crash_tier = cur_crash
                core_tier = cur_core
            else:
                if cur_crash > crash_tier:
                    crash_tier = cur_crash
                    await asyncio.to_thread(_post_ntfy, f"crashes reached {stats['crashes']}")
                if cur_core > core_tier:
                    core_tier = cur_core
                    await asyncio.to_thread(_post_ntfy, f"core dumps reached {stats['core_dumps']}")
        if once:
            break
        await asyncio.sleep(interval)
