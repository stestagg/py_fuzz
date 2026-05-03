from .project import Project
from .env import Env, Image


async def build_python(project: Project):
    env = Env(project, image=Image.BUILD)
    proc = await env.run(["/src/build.sh", project.clone_ref], console=True, vm_mem=8192)
    await proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"Python build failed with return code {proc.returncode}")
    