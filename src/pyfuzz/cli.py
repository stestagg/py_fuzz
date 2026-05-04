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

    click.echo(f"Hello, PyFuzz: {ctx.obj['project']}!")


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
    click.echo(f"Loaded project: {project}")
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
    click.echo(f"Loaded project: {project}")
    env = Env(project, image, runner)
    asyncio.run(run_in_env(env, ['/bin/sh'], interactive=True))
    click.echo(f"Done")


@cli.command("build")
@click.pass_context
def build(ctx):
    click.echo(f"Building project: {ctx.obj['project']}")
    project = Project.load(ctx.obj["project"])
    click.echo(f"Loaded project: {project}")
    from .pybuild import build_python
    asyncio.run(build_python(project))
    click.echo(f"Build complete for project: {ctx.obj['project']}")


@cli.command("clean")
@click.argument("component", type=click.Choice([c.value for c in CleanComponent]), nargs=-1)
@click.pass_context
def clean_cmd(ctx, component):
    click.echo(f"Cleaning project: {ctx.obj['project']}")
    project = Project.load(ctx.obj["project"])
    click.echo(f"Loaded project: {project}")
    if not component:
        raise click.UsageError("At least one component must be specified for cleaning")
    clean(project, [CleanComponent(c) for c in component])
    click.echo(f"Clean complete for project: {ctx.obj['project']}")