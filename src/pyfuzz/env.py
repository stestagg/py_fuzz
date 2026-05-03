from enum import Enum
from .project import Project
from . import runners
from .paths import root_path
import jinja2

class Runner(Enum):
    DOCKER = "docker"
    PFRUN = "pfrun"


PFRUN_IMAGES = [p.name for p in root_path("pfrun").iterdir() if p.is_dir()]
DOCKER_IMAGES = [p.name for p in root_path("docker").iterdir() if p.is_dir()]


Image = Enum("Image", {name.upper(): name for name in PFRUN_IMAGES + DOCKER_IMAGES})


def load_image_vars(env: Env) -> dict[str, str]:
    runner = env.runner
    image = env.image
    image_root = root_path(runner.value) / image.value
    vars_file = image_root / "env.txt"
    if not vars_file.exists():
        return {}
    
    template = jinja2.Template(vars_file.read_text())
    rendered = template.render(env=env, project=env.project)

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
    def mounts(self):
        mounts = []
        mounts.append((self.project.path("config"), False))
        mounts.append((root_path("src"), False))

        if self.image == Image.BUILD:
            mounts.append((self.project.path("py"), True))
            mounts.append((self.project.path("cpython"), True))
            mounts.append((root_path("cache"), True))

        # mounts.append((self.project.path("py"), False))
        return mounts
    
    async def run(self, cmd: list[str], **kwargs):
        from . import runners
        return await runners.run(self, cmd, **kwargs)