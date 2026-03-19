#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["click"]
# ///

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import click

SCRIPT_DIR = Path(__file__).parent.resolve()


def parse_duration(s):
    m = re.fullmatch(r'(\d+)h', s)
    if m:
        return int(m.group(1)) * 3600
    m = re.fullmatch(r'(\d+)m', s)
    if m:
        return int(m.group(1)) * 60
    m = re.fullmatch(r'(\d+)s?', s)
    if m:
        return int(m.group(1))
    raise click.BadParameter("use e.g. 30m, 1h, 3600 or 3600s", param_hint="'-T'")


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.argument("pr_id", required=False)
@click.option("-o", "--output", metavar="DIR", envvar="OUTPUT_DIR",
              help="Override output directory [env: OUTPUT_DIR]")
@click.option("-j", "--jobs", default=1, metavar="N", show_default=True,
              help="Number of AFL++ workers; main attaches to terminal, secondary workers log to output/workerN.log")
@click.option("-T", "--timeout", metavar="DUR",
              help="Stop fuzzing after DUR (e.g. 30m, 1h, 3600s)")
def cli(pr_id, output, jobs, timeout):
    """Fuzz CPython using AFL++.

    With no PR_ID, fuzzes dist/main/. With a PR_ID, fuzzes dist/<PR_ID>/.
    Build artifacts must already exist in dist/ — run build.sh first.

    \b
    Examples:
      run.py                         fuzz dist/main/, single worker
      run.py -j4                     4 AFL++ workers
      run.py 132345                  fuzz dist/132345/
      run.py -j4 -T 1h 132345       4 workers, 1-hour session, specific PR
    """
    if jobs < 1:
        raise click.BadParameter("must be a positive integer", param_hint="'-j'")

    if pr_id and not re.fullmatch(r'\d+', pr_id):
        raise click.BadParameter(f"must be a number, got: {pr_id}", param_hint="'PR_ID'")

    if not shutil.which('afl-fuzz'):
        raise click.ClickException("Missing required tool: afl-fuzz")

    dist_id = pr_id or 'main'
    dist_dir = Path(os.environ.get('DIST_DIR') or f'dist/{dist_id}')
    output_dir = Path(output or f'output/{dist_id}')

    harness = dist_dir / 'fuzz_python'
    if not harness.exists():
        raise click.ClickException(
            f"{harness} not found — run build.sh {pr_id or ''} first".strip()
        )

    output_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env['PYTHONHOME'] = str(dist_dir / 'install')

    # Coredump setup — Linux only; core_pattern write requires root + --privileged.
    # In Docker, entrypoint.sh handles this before dropping to the fuzzer user.
    core_pattern_path = Path('/proc/sys/kernel/core_pattern')
    if core_pattern_path.exists():
        if os.geteuid() == 0:
            import resource
            cores_dir = output_dir / 'cores'
            cores_dir.mkdir(parents=True, exist_ok=True)
            resource.setrlimit(resource.RLIMIT_CORE, (resource.RLIM_INFINITY, resource.RLIM_INFINITY))
            helper = SCRIPT_DIR / 'helpers' / 'coredump_helper.sh'
            core_pattern_path.write_text(f'|{helper} {cores_dir} %p %e %t\n')
            click.echo(f"==> Coredumps enabled -> {cores_dir}/")

        shim = dist_dir / 'nocorelimit.so'
        if shim.exists():
            existing = env.get('LD_PRELOAD', '')
            env['LD_PRELOAD'] = f"{shim}:{existing}" if existing else str(shim)

    harness_cmplog = dist_dir / 'fuzz_python_cmplog'
    testcases_dir = os.environ.get('TESTCASES_DIR', 'testcases')
    dict_file = os.environ.get('DICT_FILE', 'dicts/python.dict')

    timeout_args = []
    if timeout:
        secs = parse_duration(timeout)
        click.echo(f"==> Session timeout: {timeout} ({secs}s)")
        timeout_args = ['-V', str(secs)]

    resuming = (output_dir / 'main').exists()
    if resuming:
        click.echo("==> Resuming previous session (-i -)")
    input_arg = '-' if resuming else testcases_dir

    afl_common = [
        'afl-fuzz',
        '-i', input_arg,
        '-o', str(output_dir),
        '-t', '5000',
        '-m', '512',
        '-x', dict_file,
        *timeout_args,
    ]

    secondary_procs = []

    if jobs > 1:
        click.echo(f"==> Launching {jobs} AFL++ workers (1 main + {jobs - 1} secondary)...")
        for i in range(1, jobs):
            log_path = output_dir / f'worker{i}.log'
            cmd = [*afl_common, '-S', f'worker{i}', '--', str(harness)]
            log_file = open(log_path, 'w')
            p = subprocess.Popen(cmd, env=env, cwd=SCRIPT_DIR, stdout=log_file, stderr=log_file)
            secondary_procs.append((p, log_file))
            click.echo(f"    worker{i} pid={p.pid}  log={log_path}")

    main_cmd = [*afl_common, '-M', 'main']
    if harness_cmplog.exists():
        main_cmd += ['-c', str(harness_cmplog)]
    main_cmd += ['--', str(harness)]

    ret = 0
    try:
        result = subprocess.run(main_cmd, env=env, cwd=SCRIPT_DIR)
        ret = result.returncode
    except KeyboardInterrupt:
        pass
    finally:
        for p, log_file in secondary_procs:
            try:
                p.terminate()
            except ProcessLookupError:
                pass
        for p, log_file in secondary_procs:
            p.wait()
            log_file.close()

    sys.exit(ret)


if __name__ == '__main__':
    cli()
