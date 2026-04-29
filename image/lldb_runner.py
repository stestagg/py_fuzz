#!/usr/bin/env python3
"""
Run a process or load a core under the lldb Python API and print crash info.

Usage:
  lldb_runner.py crash --harness H --stdin-file S [--env K=V ...] [--continue-after-exec]
  lldb_runner.py core  --harness H --core-file C
"""
import argparse
import signal as _signal
import sys
import lldb


def _wait_for_stop(listener, timeout: int = 60):
    """Block until the process reaches a terminal state; return that state."""
    
    while True:
        event = lldb.SBEvent()
        if not listener.WaitForEvent(timeout, event):
            sys.exit("error: timed out waiting for process event")
        if not lldb.SBProcess.EventIsProcessEvent(event):
            continue
        state = lldb.SBProcess.GetStateFromEvent(event)
        if state in (lldb.eStateStopped, lldb.eStateCrashed, lldb.eStateExited):
            return state


def _signal_stop_desc(thread) -> str:
    if thread.GetStopReason() == lldb.eStopReasonSignal:
        signo = thread.GetStopReasonDataAtIndex(0)
        try:
            name = _signal.Signals(signo).name
        except ValueError:
            name = f"SIG{signo}"
        return f"signal {name}"
    return thread.GetStopDescription(256)


def _print_backtraces(process) -> None:
    for i in range(process.GetNumThreads()):
        thread = process.GetThreadAtIndex(i)
        stop_desc = _signal_stop_desc(thread)
        print(f"\nThread {i + 1} \"{thread.GetName() or ''}\" stop reason = {stop_desc}")
        for j, frame in enumerate(thread):
            print(f"  frame #{j}: {frame}")


def _print_registers(process) -> None:
    thread = process.GetSelectedThread()
    frame = thread.GetSelectedFrame()
    print("\nRegisters:")
    for regset in frame.GetRegisters():
        print(f"  {regset.GetName()}:")
        for reg in regset:
            print(f"    {reg.GetName()} = {reg.GetValue() or '<unavailable>'}")


def run_crash(harness: str, stdin_file: str, stdout_file: str, stderr_file: str,
              env: list[str], continue_after_exec: bool) -> None:

    debugger = lldb.SBDebugger.Create()
    debugger.SetAsync(True)
    # debugger.EnableLog("lldb", ["host", "process"])

    target = debugger.CreateTargetWithFileAndArch(harness, lldb.LLDB_ARCH_DEFAULT)
    if not target:
        sys.exit(f"error: failed to create target for {harness}")

    listener = lldb.SBListener("pyfuzz")
    error = lldb.SBError()
    process = target.Launch(
        listener,
        None,           # argv (harness takes no extra args)
        env,
        stdin_file,
        stdout_file,
        stderr_file,
        None,           # working directory
        0,              # launch flags
        False,          # stop at entry
        error,
    )

    if error.Fail():
        sys.exit(f"error: failed to launch: {error}")
    if not process or not process.IsValid():
        sys.exit("error: invalid process after launch")

    state = _wait_for_stop(listener)

    if continue_after_exec and state == lldb.eStateStopped:
        thread = process.GetSelectedThread()
        if thread.GetStopReason() == lldb.eStopReasonExec:
            # mem_limit_exec execs the harness; continue past the exec stop.
            process.Continue()
            state = _wait_for_stop(listener)

    pid = process.GetProcessID()

    if state == lldb.eStateExited:
        print(f"Process {pid} exited with status = {process.GetExitStatus()}")
        sys.exit(1)

    if state in (lldb.eStateCrashed, lldb.eStateStopped):
        print(f"Process {pid} stopped")
        _print_backtraces(process)
        _print_registers(process)
        sys.exit(0)

    print(f"Process {pid} ended in unexpected state = {state}")
    sys.exit(1)


def run_core(harness: str, core_file: str) -> None:
    import lldb

    debugger = lldb.SBDebugger.Create()

    target = debugger.CreateTargetWithFileAndArch(harness, lldb.LLDB_ARCH_DEFAULT)
    if not target:
        sys.exit(f"error: failed to create target for {harness}")

    error = lldb.SBError()
    process = target.LoadCore(core_file, error)
    if not process or not process.IsValid():
        sys.exit(f"error: failed to load core {core_file}: {error}")

    pid = process.GetProcessID()
    print(f"Process {pid} stopped")
    print()
    for i in range(process.GetNumThreads()):
        thread = process.GetThreadAtIndex(i)
        marker = "* " if thread == process.GetSelectedThread() else "  "
        print(f"{marker}thread #{i + 1}: tid={thread.GetThreadID()}, name={thread.GetName() or '?'}")

    _print_backtraces(process)


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="mode", required=True)

    cp = sub.add_parser("crash")
    cp.add_argument("--harness", required=True)
    cp.add_argument("--stdin-file", required=True)
    cp.add_argument("--stdout-file", required=True)
    cp.add_argument("--stderr-file", required=True)
    cp.add_argument("--env", action="append", default=[])
    cp.add_argument("--continue-after-exec", action="store_true")

    kp = sub.add_parser("core")
    kp.add_argument("--harness", required=True)
    kp.add_argument("--core-file", required=True)

    args = parser.parse_args()

    if args.mode == "crash":
        run_crash(
            harness=args.harness,
            stdin_file=args.stdin_file,
            stdout_file=args.stdout_file,
            stderr_file=args.stderr_file,
            env=args.env,
            continue_after_exec=args.continue_after_exec,
        )
    else:
        run_core(harness=args.harness, core_file=args.core_file)


if __name__ == "__main__":
    main()
