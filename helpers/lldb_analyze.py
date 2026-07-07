#!/usr/bin/env python3
"""Run an lldb analysis session using the lldb Python API."""

import argparse
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path

RUN_TIMEOUT_S = 60
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


_FAULT_ADDR_RE = re.compile(r"fault address=0x([0-9a-fA-F]+)")


def run_command(debugger, cmd: str) -> str:
    import lldb
    ret = lldb.SBCommandReturnObject()
    debugger.GetCommandInterpreter().HandleCommand(cmd, ret)
    return (ret.GetOutput() or "") + (ret.GetError() or "")


def collect_diagnostics(debugger, commands=None) -> str:
    if commands is None:
        commands = ("thread list", "bt all", "register read", "memory region $sp")
    parts = []
    for cmd in commands:
        parts.append(f"(lldb) {cmd}")
        parts.append(run_command(debugger, cmd))

    # If the stop reason names a fault address, look up its region too. This
    # works against a loaded core (no live process required) and tells us
    # whether the fault landed in the unmapped hole just below the stack
    # region (stack-growth fault) or in the unmapped hole starting at 0x0
    # (NULL/small-offset dereference, unrelated to the stack).
    match = _FAULT_ADDR_RE.search("\n".join(parts))
    if match is not None:
        fault_cmd = f"memory region 0x{match.group(1)}"
        parts.append(f"(lldb) {fault_cmd}")
        parts.append(run_command(debugger, fault_cmd))

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


def _launch_and_wait_for_crash(lldb, debugger, target, input_path: str = None, argv=None, envp=None):
    """Launch the target and return the process once it crashes or exits.

    Handles the exec stop transparently (same as analyze_crash). Returns
    (process, error_str) where error_str is non-None on failure/timeout.
    Pass input_path to feed a file as stdin (crash-input mode).
    Pass argv to launch with command-line arguments and /dev/null stdin (script mode).
    """
    stdin_path = input_path if input_path else "/dev/null"
    error = lldb.SBError()
    print("Launching process")
    process = target.Launch(
        debugger.GetListener(),
        argv,            # argv
        envp,            # envp
        stdin_path,      # stdin_path
        "/dev/stdout",   # stdout_path
        "/dev/stderr",   # stderr_path
        None,            # working_directory
        0,               # launch_flags
        False,           # stop_at_entry
        error,
    )

    if error.Fail():
        return None, f"error: launch failed: {error}\n"

    listener = debugger.GetListener()
    event = lldb.SBEvent()
    deadline = time.monotonic() + RUN_TIMEOUT_S

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            process.Kill()
            return process, "error: process timed out\n"

        got_event = listener.WaitForEvent(max(1, int(remaining)), event)

        if not got_event:
            continue

        if not lldb.SBProcess.EventIsProcessEvent(event):
            continue

        state = lldb.SBProcess.GetStateFromEvent(event)

        if state == lldb.eStateStopped:
            if _is_exec_stop(process, lldb):
                print("Process stopped at exec, continuing")
                process.Continue()
            else:
                break
        elif state in (lldb.eStateExited, lldb.eStateCrashed, lldb.eStateDetached):
            break

    return process, None


def analyze_crash(lldb, debugger, target, input_path: str, envp=None, commands=None) -> str:
    process, err = _launch_and_wait_for_crash(lldb, debugger, target, input_path, envp)
    if err:
        return err

    state = process.GetState()
    if state == lldb.eStateExited:
        code = process.GetExitStatus()
        return f"process exited cleanly with code {code} — no crash detected\n"

    print("Collecting diagnostics")
    return collect_diagnostics(debugger, commands)


def interactive_crash(lldb, debugger, target, input_path: str, envp=None) -> None:
    """Launch the target, wait for the crash, then start an interactive session."""
    process, err = _launch_and_wait_for_crash(lldb, debugger, target, input_path, envp)
    if err:
        print(err, file=sys.stderr)
        return

    state = process.GetState()
    if state == lldb.eStateExited:
        code = process.GetExitStatus()
        print(f"process exited cleanly with code {code} — no crash detected")
        return

    print("Process stopped. Entering interactive LLDB session (type 'q' to quit).")
    _run_interactive_session(lldb, debugger)


def analyze_script(lldb, debugger, target, script_path: str, envp=None, commands=None) -> str:
    process, err = _launch_and_wait_for_crash(lldb, debugger, target, argv=[script_path], envp=envp)
    if err:
        return err

    state = process.GetState()
    if state == lldb.eStateExited:
        code = process.GetExitStatus()
        return f"process exited cleanly with code {code} — no crash detected\n"

    print("Collecting diagnostics")
    return collect_diagnostics(debugger, commands)


def interactive_script(lldb, debugger, target, script_path: str, envp=None) -> None:
    process, err = _launch_and_wait_for_crash(lldb, debugger, target, argv=[script_path], envp=envp)
    if err:
        print(err, file=sys.stderr)
        return

    state = process.GetState()
    if state == lldb.eStateExited:
        code = process.GetExitStatus()
        print(f"process exited cleanly with code {code} — no crash detected")
        return

    print("Process stopped. Entering interactive LLDB session (type 'q' to quit).")
    _run_interactive_session(lldb, debugger)


def interactive_core(lldb, debugger, target, core_path: str) -> None:
    """Load the core file then start an interactive session."""
    process = target.LoadCore(core_path)
    if not process or not process.IsValid():
        print(f"error: could not load core {core_path}", file=sys.stderr)
        return

    print("Core loaded. Entering interactive LLDB session (type 'q' to quit).")
    _run_interactive_session(lldb, debugger)


def _run_interactive_session(lldb, debugger) -> None:
    debugger.SetAsync(False)
    options = lldb.SBCommandInterpreterRunOptions()
    debugger.RunCommandInterpreter(True, False, options, 0, False, False)


def run(args) -> str:
    lldb = import_lldb()
    print("Creating lldb session")
    debugger = lldb.SBDebugger.Create()
    debugger.SetAsync(True)

    commands = None
    if args.commands_file:
        commands = [l for l in Path(args.commands_file).read_text().splitlines() if l.strip()]

    if (args.crash_input or args.script_path) and args.mem_limit_mb > 0:
        print("Have mem limit, using mem_limit_exec")
        launch_binary = MEM_LIMIT_EXEC_PATH
        crash_envp = [
            f"MEM_LIMIT_MB={args.mem_limit_mb}",
            f"MEM_LIMIT_EXEC={args.target}",
        ]
    else:
        launch_binary = args.target
        crash_envp = None

    print("Creating target")
    target = debugger.CreateTarget(launch_binary)
    if not target or not target.IsValid():
        return f"error: could not create target {launch_binary}\n"

    try:
        if args.core:
            return analyze_core(lldb, debugger, target, args.core, commands)
        elif args.script_path:
            return analyze_script(lldb, debugger, target, args.script_path, envp=crash_envp, commands=commands)
        else:
            return analyze_crash(lldb, debugger, target, args.crash_input, envp=crash_envp, commands=commands)
    finally:
        lldb.SBDebugger.Destroy(debugger)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    parser.add_argument("--core")
    parser.add_argument("--crash-input")
    parser.add_argument("--script-path")
    parser.add_argument("--output")
    parser.add_argument("--mem-limit-mb", type=int, default=0)
    parser.add_argument("--commands-file")
    parser.add_argument("--interactive", action="store_true",
                        help="Wait for crash then drop into an interactive LLDB session")
    args = parser.parse_args()

    if not args.core and not args.crash_input and not args.script_path:
        print("error: one of --core, --crash-input, or --script-path is required", file=sys.stderr)
        sys.exit(1)

    if not args.interactive and not args.output:
        print("error: --output is required in non-interactive mode", file=sys.stderr)
        sys.exit(1)

    if args.interactive:
        lldb = import_lldb()
        debugger = lldb.SBDebugger.Create()
        debugger.SetAsync(True)

        if (args.crash_input or args.script_path) and args.mem_limit_mb > 0:
            launch_binary = MEM_LIMIT_EXEC_PATH
            crash_envp = [
                f"MEM_LIMIT_MB={args.mem_limit_mb}",
                f"MEM_LIMIT_EXEC={args.target}",
            ]
        else:
            launch_binary = args.target
            crash_envp = None

        print(f"Creating target {launch_binary}")
        target = debugger.CreateTarget(launch_binary)
        if not target or not target.IsValid():
            print(f"error: could not create target {launch_binary}", file=sys.stderr)
            sys.exit(1)

        try:
            if args.core:
                interactive_core(lldb, debugger, target, args.core)
            elif args.script_path:
                interactive_script(lldb, debugger, target, args.script_path, envp=crash_envp)
            else:
                interactive_crash(lldb, debugger, target, args.crash_input, envp=crash_envp)
        finally:
            lldb.SBDebugger.Destroy(debugger)
        return

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
