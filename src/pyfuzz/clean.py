from enum import Enum
import click

from .project import Project
from .paths import project_path
import shutil

class CleanComponent(Enum):
    ALL = "all"
    OUTPUTS = "outputs"
    BUILD = "build"
    ANALYSIS = "analysis"

def clean_dir(name):
    # Removes everything in the given directory, but not the directory itself
    click.echo(f"Cleaning directory: {name}")
    if name.exists() and name.is_dir():
        for item in name.iterdir():
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()

def clean_component(project: Project, component: CleanComponent):
    if component == CleanComponent.OUTPUTS:
        clean_dir(project_path(project, "outputs"))
        clean_dir(project_path(project, "cores"))
        clean_dir(project_path(project, "logs"))
        clean_dir(project_path(project, "input_tracks"))
        clean_component(project, CleanComponent.ANALYSIS)
    elif component == CleanComponent.BUILD:
        clean_dir(project_path(project, "cpython"))
        clean_dir(project_path(project, "py"))
        clean_dir(project_path(project, "tools"))
        clean_dir(project_path(project, "envs"))
    elif component == CleanComponent.ANALYSIS:
        clean_dir(project_path(project, "artifacts"))
    elif component == CleanComponent.ALL:
        for comp in CleanComponent:
            if comp != CleanComponent.ALL:
                clean_component(project, comp)

def clean(project: Project, components: list[CleanComponent]):
    for component in components:
        clean_component(project, component)