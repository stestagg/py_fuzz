from pathlib import Path
from . import project

PYFUZZ_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = PYFUZZ_ROOT / "projects"

def root_path(*parts: str) -> Path:
    return PYFUZZ_ROOT / Path(*parts)

def project_path(project_name: str, *parts: str) -> Path:
    if isinstance(project_name, project.Project):
        project_name = project_name.name
    return PROJECT_ROOT / project_name / Path(*parts)

