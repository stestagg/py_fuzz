
from .. import env

def run(project_env, cmd: list[str], **kwargs):
        if project_env.runner == env.Runner.PFRUN:
            from . import pfrun
            return pfrun.pf_run(project_env, cmd, **kwargs)
        else:
            from . import docker
            return docker.docker_run(project_env, cmd, **kwargs)