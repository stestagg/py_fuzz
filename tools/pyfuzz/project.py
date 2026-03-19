from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PROJECTS_DIR = REPO_ROOT / "projects"


@dataclass
class ProjectConfig:
    name: str
    env_id: str
    pr_id: int | None = None
    asan: bool = False

    @property
    def display_target(self) -> str:
        return f"PR #{self.pr_id}" if self.pr_id is not None else self.env_id


class Project:
    def __init__(self, root: Path, config: ProjectConfig) -> None:
        self.root = root
        self.config = config
        self.source_dir = root / "cpython"
        self.dist_dir = root / "dist"
        self.outputs_dir = root / "outputs"
        self.cores_dir = root / "cores"
        self.analysis_dir = root / "analysis"
        self.logs_dir = root / "logs"
        self.reports_dir = root / "report"
        self.inputs_dir = root / "inputs"
        self.trace_file = root / "dlopen_files.txt"

    @property
    def harness_path(self) -> Path:
        return self.dist_dir / "fuzz_python"

    @property
    def cmplog_path(self) -> Path:
        return self.dist_dir / "fuzz_python_cmplog"

    @property
    def config_path(self) -> Path:
        return self.root / "project.json"

    def ensure_layout(self) -> None:
        for path in [self.root, self.dist_dir, self.outputs_dir, self.cores_dir, self.analysis_dir, self.logs_dir, self.reports_dir, self.inputs_dir]:
            path.mkdir(parents=True, exist_ok=True)


def default_env_id(name: str, pr_id: int | None) -> str:
    return f"pr-{pr_id}" if pr_id is not None else name


def validate_name(name: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        raise ValueError("Project name may only contain letters, numbers, dots, underscores, and hyphens")


def project_path(name: str) -> Path:
    return PROJECTS_DIR / name


def save_project(config: ProjectConfig) -> Project:
    validate_name(config.name)
    root = project_path(config.name)
    project = Project(root, config)
    project.ensure_layout()
    project.config_path.write_text(json.dumps(asdict(config), indent=2, sort_keys=True) + "\n")
    return project


def load_project(name: str) -> Project:
    root = project_path(name)
    config_path = root / "project.json"
    if not config_path.exists():
        raise FileNotFoundError(f"Unknown project '{name}'. Create it first with ./pyfuzz create {name}")
    data = json.loads(config_path.read_text())
    data.pop("testcase_dir", None)
    project = Project(root, ProjectConfig(**data))
    project.ensure_layout()
    return project
