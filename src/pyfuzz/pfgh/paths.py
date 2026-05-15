from ..paths import *

GH_ROOT = root_path("gh")

def gh_path(*parts: str) -> Path:
    return GH_ROOT / Path(*parts)
