import asyncio
from pathlib import Path
import click
import os

from .project import Project
from .env import Env, Image, Runner
from .clean import CleanComponent, clean

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


async def run_in_env(env, cmd, interactive=False):
    proc = await env.run(cmd, console=True, interactive=interactive)
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
@click.pass_context
def shell(ctx, pfrun, docker, image):
    if pfrun and docker:
        raise click.UsageError("Cannot specify both --pfrun and --docker")
    
    runner = Runner.PFRUN if pfrun else Runner.DOCKER if docker else None
    click.echo(f"Running test command for project: {ctx.obj['project']}")
    project = Project.load(ctx.obj["project"])
    env = Env(project, image, runner)
    asyncio.run(run_in_env(env, ['/bin/sh'], interactive=True))
    click.echo(f"Done")


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
@click.pass_context
def fuzz(ctx, instances, no_monitor):
    from .fuzz import run_fuzz
    from .monitor import monitor_loop
    click.echo(f"Starting fuzzing for project: {ctx.obj['project']} with {instances} instances")
    project = Project.load(ctx.obj["project"])

    async def run_all():
        tasks = [asyncio.create_task(run_fuzz(project, i)) for i in range(instances)]
        monitor_task = None if no_monitor else asyncio.create_task(
            monitor_loop(project, get_running_workers=lambda: sum(1 for t in tasks if not t.done()))
        )
        try:
            await asyncio.gather(*tasks)
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



@cli.command("monitor")
@click.option("--interval", type=int, default=30, show_default=True, help="Seconds between log posts.")
@click.option("--once", is_flag=True, help="Post once and exit.")
@click.pass_context
def monitor(ctx, interval, once):
    """Periodically post fuzzer progress to the remote log server."""
    from .monitor import monitor_loop
    project = Project.load(ctx.obj["project"])
    try:
        asyncio.run(monitor_loop(project, interval=interval, once=once))
    except KeyboardInterrupt:
        pass


@cli.group("analyze")
@click.pass_context
def analyze(ctx):
    pass


@analyze.command("lldb")
@click.argument("hash")
@click.pass_context
def analyze_lldb(ctx, hash):
    from .lldb import analyze_core
    project = Project.load(ctx.obj["project"])
    asyncio.run(analyze_core(project, hash))
    click.echo(f"Analysis complete: artifacts/{hash}/")


@analyze.command("sync")
@click.pass_context
def analyze_sync(ctx):
    from .analysis import sync_artifacts
    project = Project.load(ctx.obj["project"])
    count = asyncio.run(sync_artifacts(project))
    click.echo(f"Synced {count} new artifact(s)")


@analyze.command("link-core")
@click.pass_context
def analyze_link_core(ctx):
    from .analysis import link_cores
    project = Project.load(ctx.obj["project"])
    count = asyncio.run(link_cores(project))
    click.echo(f"Linked {count} core(s) to crashes")


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
@click.option("--backend-port", default=8765, show_default=True, help="Websocket backend port.")
@click.option("--vite-port", default=5173, show_default=True, help="Vite frontend port.")
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
