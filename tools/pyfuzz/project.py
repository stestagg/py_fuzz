from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PROJECTS_DIR = REPO_ROOT / "projects"

DEFAULT_REPO = "python/cpython"


@dataclass
class ProjectConfig:
    env_id: str
    repo: str = DEFAULT_REPO
    pr_id: int | None = None
    branch: str | None = None
    commit: str | None = None
    asan: bool = False
    asan_options: str = "symbolize=0:abort_on_error=1:detect_leaks=0:allocator_may_return_null=1"
    created_at: str | None = None

    @property
    def display_target(self) -> str:
        if self.pr_id is not None:
            return f"PR #{self.pr_id}"
        if self.branch is not None:
            return f"branch:{self.branch}"
        if self.commit is not None:
            return f"commit:{self.commit[:12]}"
        return self.env_id


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
    def name(self) -> str:
        return self.root.name

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


def default_env_id(name: str, pr_id: int | None, branch: str | None = None, commit: str | None = None) -> str:
    if pr_id is not None:
        return f"pr-{pr_id}"
    if branch is not None and branch != "main":
        return f"branch-{branch}"
    if commit is not None:
        return f"commit-{commit[:8]}"
    return name


def validate_name(name: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        raise ValueError("Project name may only contain letters, numbers, dots, underscores, and hyphens")


def validate_target(config: ProjectConfig) -> None:
    targets = sum(1 for x in [config.pr_id, config.branch, config.commit] if x is not None)
    if targets != 1:
        raise ValueError("Exactly one of pr_id, branch, or commit must be set")


def project_path(name: str) -> Path:
    return PROJECTS_DIR / name


def save_project(name: str, config: ProjectConfig) -> Project:
    validate_name(name)
    validate_target(config)
    if config.created_at is None:
        config.created_at = datetime.now(timezone.utc).isoformat()
    root = project_path(name)
    project = Project(root, config)
    project.ensure_layout()
    project.config_path.write_text(json.dumps(asdict(config), indent=2, sort_keys=True) + "\n")
    return project


def load_project(name: str) -> Project:
    root = project_path(name)
    config_path = root / "project.json"
    if not config_path.exists():
        raise FileNotFoundError(f"Unknown project '{name}'. Create it first with ./pyfuzz project {name} create")
    data = json.loads(config_path.read_text())
    data.pop("testcase_dir", None)
    # Migrate old configs that have no branch/commit/pr_id target: default to main
    if data.get("pr_id") is None and data.get("branch") is None and data.get("commit") is None:
        data["branch"] = "main"
    project = Project(root, ProjectConfig(**data))
    project.ensure_layout()
    return project
