import shutil
import click
from .paths import PYFUZZ_ROOT
from .project import Project


def list_inputs(project: Project) -> list[str]:
    inputs_dir = project.path("inputs")
    return sorted(d.name for d in inputs_dir.iterdir() if d.is_dir())


def add_inputs(project: Project, names: list[str]) -> None:
    inputs_dir = project.path("inputs")
    for name in names:
        src = PYFUZZ_ROOT / "testcases" / name
        if not src.exists():
            raise FileNotFoundError(f"Testcase not found: {src}")
        dst = inputs_dir / name
        if dst.exists():
            raise FileExistsError(f"Input already exists: {name}")
        shutil.copytree(src, dst)


def remove_inputs(project: Project, names: list[str]) -> None:
    inputs_dir = project.path("inputs")
    for name in names:
        dst = inputs_dir / name
        if not dst.exists():
            raise FileNotFoundError(f"Input not found: {name}")
        shutil.rmtree(dst)


def tree_inputs(project: Project) -> str:
    inputs_dir = project.path("inputs")
    lines = [str(inputs_dir)]

    def _walk(path, prefix):
        entries = sorted(path.iterdir(), key=lambda e: (e.is_file(), e.name))
        for i, entry in enumerate(entries):
            connector = "└── " if i == len(entries) - 1 else "├── "
            lines.append(f"{prefix}{connector}{entry.name}")
            if entry.is_dir():
                extension = "    " if i == len(entries) - 1 else "│   "
                _walk(entry, prefix + extension)

    _walk(inputs_dir, "")
    return "\n".join(lines)


@click.group("inputs")
@click.pass_context
def inputs_group(ctx):
    pass


@inputs_group.command("tree")
@click.pass_context
def inputs_tree(ctx):
    project = Project.load(ctx.obj["project"])
    click.echo(tree_inputs(project))


@inputs_group.command("list")
@click.pass_context
def inputs_list(ctx):
    project = Project.load(ctx.obj["project"])
    for name in list_inputs(project):
        click.echo(name)


@inputs_group.command("add")
@click.argument("names", nargs=-1, required=True)
@click.pass_context
def inputs_add(ctx, names):
    project = Project.load(ctx.obj["project"])
    add_inputs(project, list(names))
    for name in names:
        click.echo(f"Added: {name}")


@inputs_group.command("rm")
@click.argument("names", nargs=-1, required=True)
@click.pass_context
def inputs_rm(ctx, names):
    project = Project.load(ctx.obj["project"])
    remove_inputs(project, list(names))
    for name in names:
        click.echo(f"Removed: {name}")
