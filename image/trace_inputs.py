from __future__ import annotations

import os
import subprocess
from pathlib import Path


def main() -> int:
    project = Path(os.environ["PROJECT_ROOT"])
    testcases = Path(os.environ["TESTCASES_DIR"])
    harness = project / "dist" / "fuzz_python"
    trace_so = project / "dist" / "trace_dlopen.so"
    output_file = project / "dlopen_files.txt"
    env = dict(os.environ)
    env["PYTHONHOME"] = str(project / "dist" / "install")
    env["DLOPEN_LOG"] = str(output_file)
    output_file.write_text("")
    count = 0
    for testcase in sorted(path for path in testcases.rglob("*") if path.is_file()):
        with testcase.open("rb") as handle:
            subprocess.run([str(harness)], stdin=handle, env={**env, "LD_PRELOAD": str(trace_so)}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        count += 1
    unique_lines = sorted({line for line in output_file.read_text().splitlines() if line.strip()})
    output_file.write_text("\n".join(unique_lines) + ("\n" if unique_lines else ""))
    print(f"==> {count} inputs replayed; {len(unique_lines)} unique dlopen paths recorded in {output_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
