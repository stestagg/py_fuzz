#!/usr/bin/env python3
"""Run an lldb analysis session using the lldb Python API."""

import argparse
import subprocess
import sys
import traceback
from pathlib import Path

LAUNCH_TIMEOUT_S = 30
POLL_TIMEOUT_S = 1
MEM_LIMIT_EXEC_PATH = "/pfm/tools/mem_limit_exec"


def import_lldb():
    try:
        import lldb as _lldb
        return _lldb
    except ImportError:
        pass
    lldb_path = subprocess.run(
        ["lldb", "-P"], capture_output=True, text=True
    ).stdout.strip()
    if lldb_path:
        sys.path.insert(0, lldb_path)
    import lldb as _lldb
    return _lldb


def run_command(debugger, cmd: str) -> str:
    import lldb
    ret = lldb.SBCommandReturnObject()
    debugger.GetCommandInterpreter().HandleCommand(cmd, ret)
    return (ret.GetOutput() or "") + (ret.GetError() or "")


def collect_diagnostics(debugger, commands=None) -> str:
    if commands is None:
        commands = ("thread list", "bt all", "register read")
    parts = []
    for cmd in commands:
        parts.append(f"(lldb) {cmd}")
        parts.append(run_command(debugger, cmd))
    return "\n".join(parts)


def analyze_core(lldb, debugger, target, core_path: str, commands=None) -> str:
    process = target.LoadCore(core_path)
    if not process or not process.IsValid():
        return f"error: could not load core {core_path}\n"
    return collect_diagnostics(debugger, commands)


def _is_exec_stop(process, lldb) -> bool:
    for i in range(process.GetNumThreads()):
        if process.GetThreadAtIndex(i).GetStopReason() == lldb.eStopReasonExec:
            return True
    return False


def analyze_crash(lldb, debugger, target, input_path: str, envp=None, commands=None) -> str:
    error = lldb.SBError()
    process = target.Launch(
        debugger.GetListener(),  # listener
        None,            # argv
        envp,            # envp
        input_path,      # stdin_path
        "/dev/stdout",   # stdout_path
        "/dev/stderr",   # stderr_path
        None,            # working_directory
        0,               # launch_flags
        False,           # stop_at_entry
        error,
    )

    if error.Fail():
        return f"error: launch failed: {error}\n"

    running_states = {lldb.eStateAttaching, lldb.eStateLaunching, lldb.eStateRunning}
    listener = debugger.GetListener()
    elapsed = 0
    event = lldb.SBEvent()
    while True:
        state = process.GetState()
        if state in running_states:
            listener.WaitForEvent(POLL_TIMEOUT_S, event)
            elapsed += POLL_TIMEOUT_S
            if elapsed >= LAUNCH_TIMEOUT_S:
                process.Kill()
                return "error: process timed out\n"
        elif state == lldb.eStateStopped and _is_exec_stop(process, lldb):
            process.Continue()
        else:
            break

    state = process.GetState()
    if state == lldb.eStateExited:
        code = process.GetExitStatus()
        return f"process exited cleanly with code {code} — no crash detected\n"

    return collect_diagnostics(debugger, commands)


def run(args) -> str:
    lldb = import_lldb()

    debugger = lldb.SBDebugger.Create()
    debugger.SetAsync(False)

    commands = None
    if args.commands_file:
        commands = [l for l in Path(args.commands_file).read_text().splitlines() if l.strip()]

    if args.crash_input and args.mem_limit_mb > 0:
        launch_binary = MEM_LIMIT_EXEC_PATH
        crash_envp = [
            f"MEM_LIMIT_KB={args.mem_limit_mb * 1024}",
            f"MEM_LIMIT_EXEC={args.target}",
        ]
    else:
        launch_binary = args.target
        crash_envp = None

    target = debugger.CreateTarget(launch_binary)
    if not target or not target.IsValid():
        return f"error: could not create target {launch_binary}\n"

    try:
        if args.core:
            return analyze_core(lldb, debugger, target, args.core, commands)
        else:
            return analyze_crash(lldb, debugger, target, args.crash_input, envp=crash_envp, commands=commands)
    finally:
        lldb.SBDebugger.Destroy(debugger)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    parser.add_argument("--core")
    parser.add_argument("--crash-input")
    parser.add_argument("--output", required=True)
    parser.add_argument("--mem-limit-mb", type=int, default=0)
    parser.add_argument("--commands-file")
    args = parser.parse_args()

    if not args.core and not args.crash_input:
        print("error: one of --core or --crash-input is required", file=sys.stderr)
        sys.exit(1)

    output = ""
    failed = False
    try:
        output = run(args)
    except Exception:
        output = traceback.format_exc()
        print(output, file=sys.stderr)
        failed = True
    finally:
        Path(args.output).write_text(output)

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
