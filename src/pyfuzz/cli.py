import asyncio
from pathlib import Path
import click
import os

from .project import Project
from .env import Env, Image, Runner
from .clean import CleanComponent, clean
from .analysis_query import QueryCommand

def load_project_name_from_file() -> str | None:
    base = Path.cwd()
    while base.parent != base:
        p = base / ".pyfuzz_project"
        if p.exists():
            return p.read_text().strip()
        base = base.parent


@click.group()
@click.option("--project", help="Project name")
@click.pass_context
def cli(ctx, project):
    ctx.ensure_object(dict)
    ctx.obj["project"] = project
    
    prog = os.path.basename(os.getenv("PYTHON_EXECUTABLE", "") or os.getenv("_", "") or "")
    prog = prog or os.path.basename(os.sys.argv[0])
    if prog == 'pfx':
        ctx.obj["project"] = load_project_name_from_file()


@cli.command()
@click.pass_context
def create(ctx):
    click.echo(f"Creating project: {ctx.obj['project']}")
    Project.create(ctx.obj["project"])
    click.echo(f"Project '{ctx.obj['project']}' created successfully.")


async def run_in_env(env, cmd, interactive=False, **kwargs):
    proc = await env.run(cmd, console=True, interactive=interactive, **kwargs)
    await proc.wait()
    return proc


@cli.command("run-cmd")
@click.argument("cmd", nargs=-1)
@click.option("--pfrun", is_flag=True, help="Run using pfrun")
@click.option("--docker", is_flag=True, help="Run using docker")
@click.option("--image", type=click.Choice([e.value for e in Image]), help="Image to use")
@click.pass_context
def run_cmd(ctx, cmd, pfrun, docker, image):
    if pfrun and docker:
        raise click.UsageError("Cannot specify both --pfrun and --docker")
    
    runner = Runner.PFRUN if pfrun else Runner.DOCKER if docker else None
    click.echo(f"Running test command for project: {ctx.obj['project']}")
    project = Project.load(ctx.obj["project"])
    env = Env(project, image, runner)
    asyncio.run(run_in_env(env, list(cmd)))
    click.echo(f"Done")


@cli.command("shell")
@click.option("--pfrun", is_flag=True, help="Run using pfrun")
@click.option("--docker", is_flag=True, help="Run using docker")
@click.option("--image", type=click.Choice([e.value for e in Image]), help="Image to use")
@click.option("-c", "--cmd", default=None, help="Command to run non-interactively instead of opening a shell")
@click.option("--dmesg", default=None, metavar="PATH", help="Write boot/kernel log to this file (pfrun only)")
@click.pass_context
def shell(ctx, pfrun, docker, image, cmd, dmesg):
    if pfrun and docker:
        raise click.UsageError("Cannot specify both --pfrun and --docker")

    runner = Runner.PFRUN if pfrun else Runner.DOCKER if docker else None
    project = Project.load(ctx.obj["project"])
    env = Env(project, image, runner)
    kwargs = {"dmesg_path": dmesg} if dmesg else {}
    if cmd:
        asyncio.run(run_in_env(env, ['/bin/sh', '-c', cmd], interactive=False, **kwargs))
    else:
        asyncio.run(run_in_env(env, ['/bin/sh'], interactive=True, **kwargs))


@cli.command("build")
@click.option('--py', 'build_py', is_flag=True, help="Build Python")
@click.option('--helpers', 'build_helpers', is_flag=True, help="Build Helpers")
@click.pass_context
def build(ctx, build_py, build_helpers):
    if not build_py and not build_helpers:
        build_py = True
        build_helpers = True

    click.echo(f"Building project: {ctx.obj['project']}")
    project = Project.load(ctx.obj["project"])
    if build_py:
        from .build import build_python
        asyncio.run(build_python(project))
    if build_helpers:
        from .build import build_helpers
        asyncio.run(build_helpers(project))
    from .fuzzdict import make_dict
    count = asyncio.run(make_dict(project))
    click.echo(f"Dictionary generation complete with {count} entries")
    click.echo(f"Build complete for project: {ctx.obj['project']}")


@cli.command("clean")
@click.argument("component", type=click.Choice([c.value for c in CleanComponent]), nargs=-1)
@click.pass_context
def clean_cmd(ctx, component):
    click.echo(f"Cleaning project: {ctx.obj['project']}")
    project = Project.load(ctx.obj["project"])
    if not component:
        raise click.UsageError("At least one component must be specified for cleaning")
    clean(project, [CleanComponent(c) for c in component])
    click.echo(f"Clean complete for project: {ctx.obj['project']}")


@cli.command("fuzz")
@click.option('-j', '--instances', default=1, help="Number of fuzzing instances to run in parallel")
@click.option('--no-monitor', is_flag=True, help="Disable automatic log posting")
@click.option('--afl-debug', is_flag=True, help="Set AFL_DEBUG=1 for this run")
@click.option('--no-notify', is_flag=True, help="Disable ntfy.sh notifications")
@click.pass_context
def fuzz(ctx, instances, no_monitor, afl_debug, no_notify):
    from .fuzz import run_fuzz
    from .monitor import monitor_loop
    click.echo(f"Starting fuzzing for project: {ctx.obj['project']} with {instances} instances")
    project = Project.load(ctx.obj["project"])

    async def run_all():
        worker_ids = ['main' if i == 0 else f'w{i}' for i in range(instances)]
        tasks = [asyncio.create_task(run_fuzz(project, i, afl_debug=afl_debug)) for i in range(instances)]

        def _on_done(task, worker_id):
            if task.cancelled():
                click.echo(f"[fuzz] worker {worker_id} cancelled")
            elif task.exception():
                click.echo(f"[fuzz] worker {worker_id} failed: {task.exception()}", err=True)
            else:
                click.echo(f"[fuzz] worker {worker_id} finished")

        for task, wid in zip(tasks, worker_ids):
            task.add_done_callback(lambda t, wid=wid: _on_done(t, wid))

        monitor_task = None if no_monitor else asyncio.create_task(
            monitor_loop(project, get_running_workers=lambda: sum(1 for t in tasks if not t.done()), notify=not no_notify)
        )
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
            for task, wid in zip(tasks, worker_ids):
                if not task.cancelled() and task.exception():
                    raise task.exception()
        except asyncio.CancelledError:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            if monitor_task is not None:
                monitor_task.cancel()
                await asyncio.gather(monitor_task, return_exceptions=True)

    try:
        asyncio.run(run_all())
    except KeyboardInterrupt:
        pass
    click.echo(f"Fuzzing complete for project: {ctx.obj['project']}")


@cli.group("tracks")
@click.pass_context
def tracks(ctx):
    """Commands for working with FUZZ_TRACK_INPUTS data."""
    pass


@tracks.command("reproducer")
@click.argument("worker_id", required=False)
@click.argument("pid", required=False)
@click.argument("out_path", type=click.Path(), required=False)
@click.option("--all", "base", metavar="BASE", default=None,
              help="Generate scripts for all cores and crashes, saving as scratch/reproducers/<BASE>-N.py")
@click.pass_context
def tracks_reproducer(ctx, worker_id, pid, out_path, base):
    """Combine a .log track file into a reproducible Python script.

    With --all=BASE, scans all core and crash artifacts, resolves the pid/worker
    for each (cores from the core symlink, crashes from their meta), finds the
    matching .log file, and writes scratch/reproducers/<BASE>-N.py
    (skipping files that already exist).
    """
    from .trackscript import build_track_script, generate_all_track_scripts
    project = Project.load(ctx.obj["project"])

    if base is not None:
        results = generate_all_track_scripts(project, base)
        if not results:
            click.echo("No cores with matching input tracks found.")
            return
        for out, written in results:
            if written:
                line_count = out.read_text().count("\n")
                click.echo(f"Wrote {out} ({line_count} lines)")
            else:
                click.echo(f"Skipped {out} (already exists)")
        return

    if not worker_id or not pid or not out_path:
        raise click.UsageError(
            "Provide worker_id, pid, and out_path, or use --all=BASE"
        )
    inputs_path = project.path("input_tracks") / f"{worker_id}.log"
    script = build_track_script(inputs_path, worker_id=worker_id, pid=int(pid))
    out = Path(out_path)
    out.write_text(script)
    line_count = script.count("\n")
    click.echo(f"Wrote {out} ({line_count} lines)")


@tracks.command("show")
@click.argument("inputs_file", type=click.Path(exists=True, path_type=Path))
def tracks_show(inputs_file):
    """Parse a .log track file and display each recorded input with a separator."""
    from .trackscript import parse_inputs_file

    inputs = parse_inputs_file(inputs_file)
    if not inputs:
        click.echo("No inputs found in file.")
        return

    CYAN = '\033[36m'
    RESET = '\033[0m'

    for i, raw in enumerate(inputs, 1):
        click.echo(f"{CYAN}# -=-=-=-=-=-=- input {i} -=-=-=-=-=-=-{RESET}")
        null_pos = raw.find(b'\x00')
        content = raw[:null_pos] if null_pos != -1 else raw
        click.echo(content.decode('utf-8', errors='replace'))

    click.echo(f"\n{len(inputs)} input(s) in {inputs_file.name}")


@cli.command("make-dict")
@click.pass_context
def make_dict(ctx):
    from .fuzzdict import make_dict
    click.echo(f"Generating dictionary for project: {ctx.obj['project']}")
    project = Project.load(ctx.obj["project"])
    count = asyncio.run(make_dict(project))
    click.echo(f"Dictionary generation complete with {count} entries for project: {ctx.obj['project']}")


@cli.command("monitor")
@click.option("--interval", type=int, default=30, show_default=True, help="Seconds between log posts.")
@click.option("--once", is_flag=True, help="Post once and exit.")
@click.option("--no-notify", is_flag=True, help="Disable ntfy.sh notifications.")
@click.pass_context
def monitor(ctx, interval, once, no_notify):
    """Periodically post fuzzer progress to the remote log server."""
    from .monitor import monitor_loop
    project = Project.load(ctx.obj["project"])
    try:
        asyncio.run(monitor_loop(project, interval=interval, once=once, notify=not no_notify))
    except KeyboardInterrupt:
        pass


@cli.group("llm")
@click.option("--model", default=None, help="OpenAI model to use for LLM commands.")
@click.pass_context
def llm(ctx, model):
    from .llm import DEFAULT_OPENAI_MODEL, LLMError, create_openai_client

    try:
        ctx.obj["openai_client"] = create_openai_client()
    except LLMError as exc:
        raise click.ClickException(str(exc)) from exc
    ctx.obj["llm_model"] = model or os.environ.get("PYFUZZ_OPENAI_MODEL", DEFAULT_OPENAI_MODEL)


@llm.command("check")
@click.pass_context
def llm_check(ctx):
    from .llm import LLMError, check_openai_connection

    async def run_check():
        return await check_openai_connection(ctx.obj["openai_client"], ctx.obj["llm_model"])

    try:
        result = asyncio.run(run_check())
    except LLMError as exc:
        raise click.ClickException(str(exc)) from exc
    except Exception as exc:
        raise click.ClickException(
            f"OpenAI API check failed ({type(exc).__name__}): {exc}"
        ) from exc

    click.echo(f"OpenAI API connection OK ({ctx.obj['llm_model']}): {result.message}")


@llm.command("classify")
@click.option("--text", "extra_text", default=None, help="Additional guidance for classifying artifacts.")
@click.option("--classes", "classes_text", required=True, help="Comma-separated class labels.")
@click.option("--dest", required=True, help="Filename in each artifact directory for the result.")
@click.option("--force", is_flag=True, help="Overwrite an existing destination file.")
@click.option("--rationale", is_flag=True, help="Ask for a rationale and store JSON instead of just the label.")
@click.argument("artifact_hashes", nargs=-1, required=True)
@click.pass_context
def llm_classify(ctx, extra_text, classes_text, dest, force, rationale, artifact_hashes):
    from .llm import LLMError, classify_artifacts, parse_class_labels

    try:
        labels = parse_class_labels(classes_text)
    except LLMError as exc:
        raise click.UsageError(str(exc)) from exc

    project = Project.load(ctx.obj["project"])

    try:
        results = asyncio.run(
            classify_artifacts(
                ctx.obj["openai_client"],
                project,
                list(artifact_hashes),
                labels,
                dest,
                ctx.obj["llm_model"],
                extra_text=extra_text,
                force=force,
                include_rationale=rationale,
            )
        )
    except LLMError as exc:
        raise click.UsageError(str(exc)) from exc

    failures = []
    for result in results:
        if result.status == "written":
            click.echo(f"{result.artifact_hash}: {result.label} -> {result.dest.name}")
        elif result.status == "skipped":
            click.echo(f"{result.artifact_hash}: skipped ({result.dest.name} exists)")
        else:
            failures.append(result)
            click.echo(f"{result.artifact_hash}: failed: {result.error}", err=True)

    if failures:
        raise click.ClickException(f"{len(failures)} artifact classification(s) failed.")


@llm.command("describe")
@click.option("--prompt", "prompt_text", required=True, help="Prompt to send with each artifact.")
@click.option("--dest", required=True, help="Filename in each artifact directory for the result.")
@click.option("--force", is_flag=True, help="Overwrite an existing destination file.")
@click.option("--max-tokens", default=50, show_default=True, help="Maximum output tokens.")
@click.argument("artifact_hashes", nargs=-1, required=True)
@click.pass_context
def llm_describe(ctx, prompt_text, dest, force, max_tokens, artifact_hashes):
    from .llm import LLMError, describe_artifacts

    project = Project.load(ctx.obj["project"])

    try:
        results = asyncio.run(
            describe_artifacts(
                ctx.obj["openai_client"],
                project,
                list(artifact_hashes),
                prompt_text,
                dest,
                ctx.obj["llm_model"],
                max_tokens=max_tokens,
                force=force,
            )
        )
    except LLMError as exc:
        raise click.UsageError(str(exc)) from exc

    failures = []
    for result in results:
        if result.status == "written":
            preview = (result.text or "")[:80]
            if len(result.text or "") > 80:
                preview += "..."
            click.echo(f"{result.artifact_hash}: {preview} -> {result.dest.name}")
        elif result.status == "skipped":
            click.echo(f"{result.artifact_hash}: skipped ({result.dest.name} exists)")
        else:
            failures.append(result)
            click.echo(f"{result.artifact_hash}: failed: {result.error}", err=True)

    if failures:
        raise click.ClickException(f"{len(failures)} artifact description(s) failed.")


@cli.group("analyze")
@click.pass_context
def analyze(ctx):
    pass


@analyze.command("lldb")
@click.argument("target")
@click.option("--interactive", is_flag=True, default=False, help="Launch an interactive lldb session instead of running automated analysis.")
@click.option("--output", default=None, metavar="PATH", help="Output path under /pfm/ in the VM (e.g. scratch/lldb/out.txt). Required for script mode; defaults to artifacts/<hash>/lldb.txt for hash mode.")
@click.pass_context
def analyze_lldb(ctx, target, interactive, output):
    project = Project.load(ctx.obj["project"])
    if '/' in target:
        from .lldb import run_script_in_lldb
        if not interactive and output is None:
            raise click.UsageError("--output is required for script mode (non-interactive)")
        asyncio.run(run_script_in_lldb(project, Path(target), interactive=interactive, output=output))
    else:
        from .lldb import analyze_core
        asyncio.run(analyze_core(project, target, interactive=interactive, output=output))
        if not interactive:
            click.echo(f"Analysis complete: artifacts/{target}/")


@analyze.command("script")
@click.argument("out_name")
@click.argument("batch_file", type=click.Path(exists=True, path_type=Path))
@click.argument("artifacts", nargs=-1, required=True)
@click.pass_context
def analyze_script(ctx, out_name, batch_file, artifacts):
    from .lldb import analyze_script_artifacts
    project = Project.load(ctx.obj["project"])
    asyncio.run(analyze_script_artifacts(project, out_name, batch_file, list(artifacts)))
    for h in artifacts:
        click.echo(f"Script complete: artifacts/{h}/{out_name}.txt")


@analyze.command("sync")
@click.pass_context
def analyze_sync(ctx):
    from .analysis import sync_artifacts
    project = Project.load(ctx.obj["project"])
    new_count, enriched = asyncio.run(sync_artifacts(project))
    click.echo(f"Synced {new_count} new artifact(s), enriched {enriched} with log metadata")


@analyze.command("link-core")
@click.pass_context
def analyze_link_core(ctx):
    from .analysis import link_cores
    project = Project.load(ctx.obj["project"])
    count = asyncio.run(link_cores(project))
    click.echo(f"Linked {count} core(s) to crashes")


@analyze.command("core")
@click.argument("artifact_hash")
@click.option("-f", "--force", is_flag=True, help="Re-run LLDB and reclassify even if already analyzed.")
@click.pass_context
def analyze_core_cmd(ctx, artifact_hash, force):
    """Run full analysis on a core artifact (LLDB, crash link, input tracking)."""
    from .analysis import analyze_core_artifact
    project = Project.load(ctx.obj["project"])
    asyncio.run(analyze_core_artifact(project, artifact_hash, force=force))
    click.echo(f"Analysis complete: artifacts/{artifact_hash}/")


@analyze.command("query", cls=QueryCommand)
@click.argument("clauses", nargs=-1, metavar="CLAUSE")
@click.pass_context
def analyze_query(ctx, clauses):
    """Query artifacts by filter clauses."""
    from .analysis_query import query_artifacts
    project = Project.load(ctx.obj["project"])
    try:
        results = asyncio.run(query_artifacts(project, list(clauses)))
    except ValueError as e:
        raise click.UsageError(str(e))
    for artifact in results:
        click.echo(artifact.hash)


@analyze.command("stack-fault")
@click.argument("artifact_hashes", nargs=-1)
@click.option("--all", "all_artifacts", is_flag=True, help="Scan all artifacts with lldb.txt.")
@click.option("--write", is_flag=True, help="Write the result into each artifact directory.")
@click.option("--dest", default="stackfault.txt", show_default=True, help="Filename to write with --write.")
@click.option(
    "--min",
    "min_classification",
    type=click.Choice(["unlikely", "possible", "likely"]),
    default="possible",
    show_default=True,
    help="Minimum classification to print.",
)
@click.pass_context
def analyze_stack_fault(ctx, artifact_hashes, all_artifacts, write, dest, min_classification):
    """Heuristically flag LLDB outputs that look like stack-growth segfaults."""
    if not all_artifacts and not artifact_hashes:
        raise click.UsageError("Provide artifact hashes or use --all.")

    from .stackfault import analyze_stack_fault_artifacts

    rank = {"unlikely": 0, "possible": 1, "likely": 2}
    project = Project.load(ctx.obj["project"])
    hashes = None if all_artifacts else list(artifact_hashes)
    results = asyncio.run(
        analyze_stack_fault_artifacts(project, hashes, write=write, dest=dest)
    )

    missing = set(artifact_hashes) - {artifact.hash for artifact, _ in results}
    for artifact_hash in sorted(missing):
        click.echo(f"{artifact_hash}: missing", err=True)

    for artifact, analysis in results:
        if analysis is None:
            continue
        if rank[analysis.classification] < rank[min_classification]:
            continue
        signals = "; ".join(analysis.signals[:3])
        suffix = f" -> {dest}" if write else ""
        click.echo(
            f"{artifact.hash}: {analysis.classification} "
            f"score={analysis.score} {signals}{suffix}"
        )


@cli.command("run-dist")
@click.argument("script", type=click.Path(exists=True, path_type=Path))
@click.option("--ref", "ref", default="main", show_default=True, help="CPython ref to build and run against.")
@click.option("--interactive", is_flag=True, default=False, help="Build or reuse Python, print the command, then open a shell.")
@click.option("--debug", is_flag=True, default=False, help="Build CPython with --with-pydebug.")
@click.option("--env", "env_vars", multiple=True, metavar="KEY=VALUE", help="Environment variable to pass to the script.")
@click.option("--configure-args", default="", help="Extra arguments to pass to CPython ./configure.")
@click.option("-M", "--mem", "mem", default=None, type=int, help="Memory limit in MB (0 or negative = unlimited; default: use configured vm_mem).")
@click.pass_context
def run_dist_cmd(ctx, script, ref, interactive, debug, env_vars, configure_args, mem):
    from .dist import run_dist
    project = Project.load(ctx.obj["project"])
    try:
        rc = asyncio.run(
            run_dist(
                project,
                script,
                ref=ref,
                interactive=interactive,
                debug=debug,
                env_vars=env_vars,
                configure_args=configure_args,
                mem=mem,
            )
        )
    except ValueError as e:
        raise click.UsageError(str(e))
    except KeyboardInterrupt:
        raise SystemExit(130)
    raise SystemExit(rc)


@cli.command("bisect")
@click.argument("script", type=click.Path(exists=True, path_type=Path))
@click.option("--ccache", is_flag=True, default=False, help="Wrap compiler with ccache (local to this run)")
@click.option("--configure-args", default="", help="Extra arguments to pass to ./configure, on top of the project's py_debug/py_configure_extra_args settings")
@click.option("-m", "--mem-limit", type=int, default=None, help="Memory limit in MB applied as ulimit when running test script")
@click.option("--log", is_flag=True, default=False, help="Write per-commit build/run output to /pfm/scratch/bisect-logs/<short_hash>.txt")
@click.pass_context
def bisect_cmd(ctx, script, ccache, configure_args, mem_limit, log):
    from .bisect import run_bisect
    project = Project.load(ctx.obj["project"])
    try:
        asyncio.run(run_bisect(project, script, ccache=ccache, configure_args=configure_args, mem_limit=mem_limit, log=log))
    except KeyboardInterrupt:
        pass


from .inputs import inputs_group
cli.add_command(inputs_group)


def _project_defaults() -> dict:
    import dataclasses
    defaults = {}
    for f in dataclasses.fields(Project):
        if f.name.startswith('_'):
            continue
        if f.default is not dataclasses.MISSING:
            defaults[f.name] = f.default
        elif f.default_factory is not dataclasses.MISSING:
            defaults[f.name] = f.default_factory()
    return defaults


def _project_public_dict(project) -> dict:
    from dataclasses import asdict
    return {k: v for k, v in asdict(project).items() if not k.startswith('_')}


@cli.command("edit")
@click.pass_context
def edit_config(ctx):
    """Edit the project configuration in $EDITOR."""
    import json
    import subprocess
    import tempfile
    from json_repair import repair_json

    project = Project.load(ctx.obj["project"])
    defaults = _project_defaults()
    config = _project_public_dict(project)

    editor = os.environ.get('EDITOR', 'vi')
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(config, f, indent=2, sort_keys=True)
        f.write('\n')
        tmp_path = f.name

    try:
        subprocess.run([editor, tmp_path], check=True)
        raw = Path(tmp_path).read_text()
    finally:
        os.unlink(tmp_path)

    repaired = repair_json(raw)
    if not repaired:
        raise click.ClickException("Editor produced invalid JSON that could not be repaired.")
    data = json.loads(repaired)

    stripped = {k: v for k, v in data.items() if k not in defaults or v != defaults[k]}
    project.config_path.write_text(json.dumps(stripped, indent=2, sort_keys=True) + '\n')
    click.echo(f"Config saved for project '{project.name}'.")


_FUZZ_SCRIPT_TEMPLATE = '''\
# Fuzz driver for the `fuzz_script` harness.
#
# This script is compiled once and run on every fuzzing iteration. The raw
# AFL-mutated input is available as `FUZZ_INPUT` (a `bytes` object).
#
# Set the project `harness` option to "fuzz_script" to use this.

# Example: fuzz a decompressor.
# import zlib
# try:
#     zlib.decompress(FUZZ_INPUT)
# except zlib.error:
#     pass
'''


@cli.command("edit-script")
@click.pass_context
def edit_script(ctx):
    """Edit the fuzz_script harness script (config/fuzz_script.py) in $EDITOR."""
    import subprocess

    project = Project.load(ctx.obj["project"])
    script_path = project.path("config", "fuzz_script.py")
    if not script_path.exists():
        script_path.write_text(_FUZZ_SCRIPT_TEMPLATE)

    editor = os.environ.get('EDITOR', 'vi')
    subprocess.run([editor, str(script_path)], check=True)
    click.echo(f"Saved {script_path}")


@cli.command("show-config")
@click.pass_context
def show_config(ctx):
    """Display the current project configuration, one setting per line."""
    import json

    project = Project.load(ctx.obj["project"])
    defaults = _project_defaults()
    config = _project_public_dict(project)

    CYAN = '\033[36m'
    YELLOW = '\033[33m'
    DIM = '\033[2m'
    RESET = '\033[0m'

    for key in sorted(config):
        val = config[key]
        is_default = key in defaults and val == defaults[key]
        val_str = json.dumps(val)
        if is_default:
            click.echo(f"{DIM}{CYAN}{key}{RESET}{DIM}: {val_str}{RESET}")
        else:
            click.echo(f"{CYAN}{key}{RESET}: {YELLOW}{val_str}{RESET}")


@cli.command("set-default")
@click.pass_context
def set_default(ctx):
    project_name = ctx.obj["project"]
    if project_name is None:
        raise click.UsageError("No project specified. Use --project <name>")
    Project.load(project_name)  # validate it exists
    p = Path.cwd() / ".pyfuzz_project"
    p.write_text(project_name + "\n")
    click.echo(f"Default project set to '{project_name}' in {p}")


@cli.command("summary")
@click.pass_context
def summary(ctx):
    from .summary import summarize_fuzzing
    click.echo(f"Summarizing fuzzing results for project: {ctx.obj['project']}")
    project = Project.load(ctx.obj["project"])
    summary = summarize_fuzzing(project)
    for k, v in summary.items():
        click.echo(f"{k}: {v}")


@cli.command("ui")
@click.option("--host", default="127.0.0.1", show_default=True, help="Host to bind both UI servers.")
@click.option("--backend-port", default=8767, show_default=True, help="Websocket backend port.")
@click.option("--vite-port", default=5174, show_default=True, help="Vite frontend port.")
@click.option("--no-open", is_flag=True, help="Do not open a browser window.")
@click.pass_context
def ui(ctx, host, backend_port, vite_port, no_open):
    from ui.backend.launcher import run_ui

    raise SystemExit(
        run_ui(
            project_name=ctx.obj["project"],
            host=host,
            backend_port=backend_port,
            vite_port=vite_port,
            open_browser=not no_open,
        )
    )
