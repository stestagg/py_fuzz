
from pathlib import Path
from dataclasses import asdict, dataclass
import json
from . import paths

DEFAULT_REPO = "python/cpython"

@dataclass
class Project:
    repo: str = DEFAULT_REPO
    pr_id: int | None = None
    branch: str | None = None
    commit: str | None = None
    asan: bool = False
    warmup_imports: str = ""
    created_at: str | None = None
    vm_mem: int = 2048
    ncpu: int = 1
    fuzz_timeout_ms: int = 5000
    fuzz_mem_limit: int = 512
    fuzz_peg: bool = False
    py_configure_extra_args: str = ""
    py_debug: bool = False
    cmplog: bool = False
    fuzz_env: tuple[str] = ()
    track_inputs: bool = False

    _name: str = None

    @property
    def clone_ref(self) -> tuple[str, str]:
        if self.pr_id is not None:
            return ('branch', f"pull/{self.pr_id}/head")
        if self.branch is not None:
            return ('branch', self.branch)
        if self.commit is not None:
            return ('commit', self.commit)
        return ('branch', "main")

    @property
    def name(self) -> str:
        return self._name

    @property
    def config_path(self) -> Path:
        return paths.project_path(self.name, "config", "project.json")

    def save(self):
        self.config_path.write_text(json.dumps(asdict(self), indent=2, sort_keys=True) + "\n")
        return self

    @classmethod
    def load(cls, name: str) -> Project:
        assert name is not None, "Project name must be provided"
        root = paths.project_path(name)
        if not root.exists():
            raise FileNotFoundError(f"Unknown project '{name}'")
        config_path = root / "config" / "project.json"
        if not config_path.exists():
            raise FileNotFoundError(f"No project.json found in project path: {root}")
        data = json.loads(config_path.read_text())
        
        project = cls(**data)
        project._name = name
        return project
    
    @classmethod
    def create(cls, name: str) -> Project:
        root = paths.project_path(name)
        if root.exists():
            raise FileExistsError(f"Project already exists: {name}")
        config_path = root / "config" / "project.json"
        config_path.parent.mkdir(parents=True)
        config_path.write_text("{}")
        skel_dirs = ['py', 'cpython', 'inputs', 'outputs', 'cores', 'artifacts', 'logs', 'tools', 'envs']
        for d in skel_dirs:
            (root / d).mkdir()
        return cls.load(name)
    
    @classmethod
    def projects(cls) -> set[str]:
        return {p.parent.parent.name for p in paths.PROJECT_ROOT.glob("*/config/project.json")}
    
    def path(self, *parts: str) -> Path:
        return paths.project_path(self.name, *parts)

    @property
    def fuzz_target(self) -> str:
        return "/pfm/tools/fuzz_peg" if self.fuzz_peg else "/pfm/tools/fuzz_python"
    
    @property
    def actual_fuzz_mem_limit(self) -> int:
        return 0 if self.asan else self.fuzz_mem_limit
    
    @property
    def actual_vm_mem(self) -> int:
        return self.vm_mem if not self.asan else self.vm_mem * 3