import asyncio
import contextlib
from enum import Enum
from .project import Project
from . import runners
from .paths import root_path
import jinja2
from functools import partial


async def reap_process(proc, timeout: float = 10.0) -> None:
    """Terminate a subprocess and wait for it to exit, escalating to kill."""
    with contextlib.suppress(ProcessLookupError):
        proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()


@contextlib.asynccontextmanager
async def terminate_on_cancel(proc):
    # Cancelling a coroutine that awaits a subprocess abandons the await but
    # leaves the child (pfrun VM / docker container) running; reap it.
    try:
        yield proc
    except asyncio.CancelledError:
        await reap_process(proc)
        raise


class Runner(Enum):
    DOCKER = "docker"
    PFRUN = "pfrun"


PFRUN_IMAGES = [p.name for p in (root_path("pfrun") / "images").iterdir() if p.is_dir()]
DOCKER_IMAGES = [p.name for p in root_path("docker").iterdir() if p.is_dir()]


Image = Enum("Image", {name.upper(): name for name in PFRUN_IMAGES + DOCKER_IMAGES})

SOFILE_BLACKLIST = {
    "_interpreters."
}

def is_blacklisted(sofile) -> bool:
    for b in SOFILE_BLACKLIST:
        if b in str(sofile):
            return True
    return False

def so_files(project):
    dist_dir = project.path("py")
    globs = [
        "lib/python*/lib-dynload/*.so",
        "lib/python*/site-packages/**/*.so",
        # Bundled shared libraries with versioned sonames (e.g. pyarrow's
        # libarrow.so.NNNN / libparquet.so.NNNN under site-packages/pyarrow/).
        "lib/python*/site-packages/**/*.so.*",
    ]
    seen = set()
    so_files = []
    for pattern in globs:
        for p in dist_dir.glob(pattern):
            if is_blacklisted(p) or p in seen:
                continue
            seen.add(p)
            so_files.append(p.relative_to(dist_dir))
    return ":".join(f'/pfm/py/{p}' for p in so_files)


def warmup_imports(project):
    from .packages import warmup_import_names
    return ",".join(warmup_import_names(project))


def load_image_vars(env: Env) -> dict[str, str]:
    runner = env.runner
    image = env.image
    image_root = root_path("pfrun") / "images" / image.value
    vars_file = image_root / "env.txt"
    if not vars_file.exists():
        return {}
    
    template = jinja2.Template(vars_file.read_text())
    rendered = template.render(
        env=env,
        project=env.project,
        so_files=partial(so_files, env.project),
        warmup_imports=partial(warmup_imports, env.project),
    )

    lines = rendered.splitlines()
    vars = {}
    for line in lines:
        if not line.strip() or line.strip().startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        vars[key.strip()] = value.strip()
    return vars


class Env:

    def __init__(self, project, image: Image, runner: Runner=None):
        if isinstance(image, str):
            image = Image(image)
        if runner is None:
            in_pfrun = image.value in PFRUN_IMAGES
            in_docker = image.value in DOCKER_IMAGES
            if in_pfrun and in_docker:
                raise ValueError(f"Image {image} found in both pfrun and docker directories. Please specify runner explicitly.")
            elif in_pfrun:
                runner = Runner.PFRUN
            elif in_docker:
                runner = Runner.DOCKER
            else:
                raise AssertionError(f"Image {image} not found in either pfrun or docker directories.")
        self.project = project
        self.image = image
        self.runner = runner
        self.vars = load_image_vars(self)
        self.setup_commands: list[str] = []

    def __setitem__(self, key, value):
        self.vars[key] = value

    def __getitem__(self, key):
        return self.vars[key]
    
    def __delitem__(self, key):
        del self.vars[key]
    
    @property
    def shell_sets(self) -> list[str]:
        return [f'{e}={v}' for e, v in self.vars.items()]
    
    @property
    def shell_export(self) -> str:
        lines = [f'export {e}={v}' for e, v in self.vars.items()]
        return "\n".join(lines)

    @property
    def setup_script_lines(self) -> list[str]:
        export_lines = [f'export {e}={v}' for e, v in self.vars.items()]
        return export_lines + self.setup_commands

    @property
    def mounts(self):
        mounts = []
        mounts.append((self.project.path("config"), False))
        mounts.append((root_path("src"), False))

        scratch_dir = self.project.path('scratch')
        if not scratch_dir.exists():
            scratch_dir.mkdir()
        mounts.append((scratch_dir, self.image != Image.AFL))

        if self.image == Image.BUILD:
            mounts.append((root_path('helpers'), False))
            mounts.append((root_path("pfrun") / "images" / "build" / "build_scripts", False))
            mounts.append((root_path("tactical-patches"), False))
            mounts.append((self.project.path("py"), True))
            mounts.append((self.project.path("cpython"), True))
            packages_dir = self.project.path("packages")
            packages_dir.mkdir(exist_ok=True)
            mounts.append((packages_dir, True))
            mounts.append((self.project.path('tools'), True))
            mounts.append((root_path("cache"), True))
        
        if self.runner == Runner.PFRUN:
            mounts.append((self.project.path('envs'), False))

        if self.image == Image.AFL:
            mounts.append((self.project.path('py'), False))
            mounts.append((self.project.path('tools'), False))
            mounts.append((root_path('helpers'), False))
            mounts.append((self.project.path('inputs'), False))
            mounts.append((self.project.path('outputs'), True))
            mounts.append((self.project.path('logs'), True))
            mounts.append((self.project.path('cores'), True))
            if self.project.track_inputs:
                input_tracks_dir = self.project.path("input_tracks")
                if not input_tracks_dir.exists():
                    input_tracks_dir.mkdir()
                mounts.append((input_tracks_dir, True))

        if self.image == Image.LLDB:
            mounts.append((self.project.path('py'), False))
            mounts.append((self.project.path("cpython"), False))
            mounts.append((self.project.path('tools'), False))
            mounts.append((root_path('helpers'), False))
            
            mounts.append((self.project.path('inputs'), False))
            mounts.append((self.project.path('outputs'), True))
            mounts.append((self.project.path('logs'), False))
            mounts.append((self.project.path('cores'), False))
            mounts.append((self.project.path('artifacts'), True))
            if self.project.track_inputs:
                mounts.append((self.project.path("input_tracks"), True))

        if self.image == Image.DIST:
            mounts.append((root_path('cache'), True))

        return mounts
    
    async def run(self, cmd: list[str], **kwargs):
        from . import runners
        return await runners.run(self, cmd, **kwargs)
