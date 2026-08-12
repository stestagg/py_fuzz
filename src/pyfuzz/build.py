import asyncio
import configparser
import hashlib
import json
import platform
import re
import urllib.request
from pathlib import Path
from typing import Callable

from .project import Project
from .env import Env, Image, terminate_on_cancel
from .paths import root_path
from .build_progress import BuildProgressEstimator
from .packages import resolve_packages, write_pymutate_name_file, REGISTRY

ProgressCallback = Callable[[float, float, str], None]

# Build backends + pandas' pure-Python runtime deps, installed offline into the
# debug interpreter inside the VM. All are universal (py3-none-any) wheels except
# where noted below. Kept as an explicit closure (installed with --no-deps) so the
# host download never pulls a host-platform wheel for a transitive dependency.
TOOLING_WHEELS = (
    "pip", "setuptools", "wheel", "packaging", "pyproject-metadata",
    "pyproject-hooks", "meson-python", "meson", "build", "versioneer",
    "python-dateutil", "six", "pytz", "tzdata",
)
TOOLING_SDISTS = ("Cython",)          # no universal wheel; compiled offline in-VM
# Prebuilt binaries; need the VM's platform tag. cmake drives both stages of the
# pyarrow build (Arrow C++ core, then the bindings).
TOOLING_PLATFORM_WHEELS = ("ninja", "cmake")


def _vm_manylinux_platform() -> str:
    # The pfrun VM is Linux matching the host CPU arch (Apple Silicon -> aarch64).
    arch = "aarch64" if platform.machine() in ("arm64", "aarch64") else "x86_64"
    return f"manylinux2014_{arch}"


async def _git(*args, cwd=None):
    proc = await asyncio.create_subprocess_exec("git", *args, cwd=cwd)
    await proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed (exit {proc.returncode})")


async def _pip_download(dest, args: list[str]):
    # Driven via `uv run --with pip` so it works regardless of whether the ambient
    # interpreter has pip; runs from the repo root (has pyproject for uv).
    cmd = ["uv", "run", "--quiet", "--with", "pip", "--",
           "python", "-m", "pip", "download", "--dest", str(dest), *args]
    proc = await asyncio.create_subprocess_exec(*cmd, cwd=root_path())
    await proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"pip download failed (exit {proc.returncode}): {' '.join(args)}")


async def ensure_package_tooling(project: Project):
    """Fetch the offline build toolchain into projects/<name>/packages/.wheels.

    The VM is offline, so — like the source checkout — the build backends
    (pip/meson-python/Cython/ninja/...) and pandas' pure-Python runtime deps are
    downloaded host-side and installed with `pip --no-index --find-links` in-VM.
    """
    wheels_dir = project.path("packages", ".wheels")
    wheels_dir.mkdir(parents=True, exist_ok=True)
    if list(wheels_dir.glob("*.whl")):
        return  # already populated; delete .wheels to refresh
    await _pip_download(wheels_dir, ["--no-deps", *TOOLING_WHEELS])
    await _pip_download(wheels_dir, ["--no-deps", "--no-binary=:all:", *TOOLING_SDISTS])
    await _pip_download(
        wheels_dir,
        ["--no-deps", "--only-binary=:all:", "--platform", _vm_manylinux_platform(), *TOOLING_PLATFORM_WHEELS],
    )


async def ensure_cpython_checkout(project: Project):
    cpython_dir = project.path("cpython")
    if (cpython_dir / ".git").exists():
        return
    repo_url = f"https://github.com/{project.repo}.git"
    kind, ref = project.clone_ref
    if kind == "branch":
        await _git("clone", "--depth", "1", "--branch", ref, repo_url, str(cpython_dir))
    else:
        await _git("clone", "--no-checkout", "--filter=blob:none", repo_url, str(cpython_dir))
        await _git("fetch", "--depth", "1", "origin", ref, cwd=cpython_dir)
        await _git("checkout", "FETCH_HEAD", cwd=cpython_dir)


def meson_wrap_downloads(wrap_text: str) -> list[tuple[str, str, str | None]]:
    """Parse a meson .wrap file into (url, cache_filename, sha256) downloads.

    Only [wrap-file] wraps fetch archives; both the source_* and patch_* triples
    can name one. Entries without a URL (e.g. patch_directory wraps) yield nothing.
    """
    cfg = configparser.ConfigParser()
    cfg.read_string(wrap_text)
    if not cfg.has_section("wrap-file"):
        return []
    section = cfg["wrap-file"]
    downloads = []
    for prefix in ("source", "patch"):
        url = section.get(f"{prefix}_url")
        filename = section.get(f"{prefix}_filename")
        if url and filename:
            downloads.append((url, filename, section.get(f"{prefix}_hash")))
    return downloads


async def _prefetch_meson_wraps(pkg_dir: Path):
    """Populate subprojects/packagecache so meson never downloads in the VM.

    pandas' meson build fetches wrap-file subprojects (fast_float, xsimd) at
    configure time; the VM is offline, so fetch the archives host-side. Meson
    checks subprojects/packagecache/<filename> first and uses it when the sha256
    matches source_hash, skipping the network entirely.
    """
    subprojects = pkg_dir / "subprojects"
    if not subprojects.is_dir():
        return
    cache_dir = subprojects / "packagecache"
    for wrap_path in sorted(subprojects.glob("*.wrap")):
        for url, filename, sha256 in meson_wrap_downloads(wrap_path.read_text()):
            dest = cache_dir / filename
            if dest.is_file() and (
                sha256 is None or hashlib.sha256(dest.read_bytes()).hexdigest() == sha256
            ):
                continue
            cache_dir.mkdir(parents=True, exist_ok=True)
            data = await asyncio.to_thread(lambda u=url: urllib.request.urlopen(u).read())
            if sha256 is not None and hashlib.sha256(data).hexdigest() != sha256:
                raise RuntimeError(
                    f"{wrap_path.name}: downloaded {url} does not match {filename}'s pinned sha256"
                )
            dest.write_bytes(data)


# Path, relative to the Arrow checkout, of Apache's own offline-download helper.
# It fetches every bundled third-party tarball (pinned in cpp/thirdparty/versions.txt)
# and prints the `export ARROW_<DEP>_URL=<dir>/<file>` lines Arrow's CMake honours.
ARROW_DOWNLOAD_SCRIPT = "cpp/thirdparty/download_dependencies.sh"
# Mount point of projects/<name>/packages inside the offline build VM (see _common.sh).
VM_PACKAGES_ROOT = "/pfm/packages"


def _arrow_thirdparty_env(script_stdout: str, host_prefix: str, vm_prefix: str) -> str:
    """Translate download_dependencies.sh output into a VM-sourced env file.

    The helper prints host paths (it downloaded into a host dir); rewrite that prefix
    to the dir's mount point inside the offline VM and force BUNDLED resolution so the
    in-VM CMake configure never reaches for the network.
    """
    body = script_stdout.replace(host_prefix, vm_prefix)
    return (
        "# Generated by pyfuzz ensure_arrow_thirdparty; sourced by pyarrow.sh.\n"
        "export ARROW_DEPENDENCY_SOURCE=BUNDLED\n"
        f"{body}"
    )


async def ensure_arrow_thirdparty(project: Project, pkg_dir: Path):
    """Prefetch Arrow's bundled C++ deps host-side for the offline VM build.

    Parquet pulls in Thrift plus the compression codecs, which Arrow otherwise
    downloads at CMake-configure time. Run Apache's own download helper here (network
    is available on the host), then leave an env file the recipe sources so the in-VM
    configure resolves every dep from the local cache.
    """
    script = pkg_dir / ARROW_DOWNLOAD_SCRIPT
    if not script.exists():
        raise RuntimeError(
            f"Arrow download helper missing: {script} (unexpected apache/arrow layout)"
        )
    cache_dir = pkg_dir / ".arrow_thirdparty"
    env_file = cache_dir / "env.sh"
    if env_file.exists():
        return  # already prefetched; delete .arrow_thirdparty to refresh
    cache_dir.mkdir(parents=True, exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        "bash", str(script), str(cache_dir),
        cwd=pkg_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=None,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"{ARROW_DOWNLOAD_SCRIPT} failed (exit {proc.returncode})")
    vm_cache = f"{VM_PACKAGES_ROOT}/{pkg_dir.name}/.arrow_thirdparty"
    env_file.write_text(
        _arrow_thirdparty_env(stdout.decode(), str(cache_dir), vm_cache)
    )


async def ensure_package_checkout(project: Project, name: str, ref: str):
    """Clone a package's source into projects/<name>/packages/<name> on the host.

    The build VM has no network, so — exactly like ensure_cpython_checkout — the
    checkout happens here and is mounted in; the in-VM recipe only builds.
    """
    spec = REGISTRY[name]
    pkg_dir = project.path("packages", name)
    repo_url = f"https://github.com/{spec.repo}.git"
    if not (pkg_dir / ".git").exists():
        pkg_dir.parent.mkdir(parents=True, exist_ok=True)
        await _git("clone", "--no-checkout", "--filter=blob:none", repo_url, str(pkg_dir))
    # Fetch + checkout the requested ref (branch/tag/commit are all reachable this way).
    await _git("fetch", "--depth", "1", "origin", ref, cwd=pkg_dir)
    await _git("checkout", "--force", "FETCH_HEAD", cwd=pkg_dir)
    await _git("submodule", "update", "--init", "--recursive", cwd=pkg_dir)
    # Meson wrap-file subprojects are the third download mechanism (after the git
    # clone and submodules); cache their archives host-side for the offline VM.
    await _prefetch_meson_wraps(pkg_dir)
    # Arrow (pyarrow) resolves its bundled C++ deps via a fourth mechanism: CMake
    # ExternalProject downloads. Prefetch those host-side too.
    if name == "pyarrow":
        await ensure_arrow_thirdparty(project, pkg_dir)


async def _pump(stream, log_file, feed):
    """Read a piped stream line-by-line: tee each line to the log and the estimator."""
    buf = b""
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            break
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            log_file.write(line + b"\n")
            feed(line.decode("utf-8", "replace"))
    if buf:
        log_file.write(buf)
        feed(buf.decode("utf-8", "replace"))


def _tail(log_path, n: int = 40) -> str:
    try:
        lines = log_path.read_bytes().decode("utf-8", "replace").splitlines()
        return "\n".join(lines[-n:])
    except Exception as exc:
        return f"(could not read {log_path}: {exc})"


async def _build_run(env, script, log_path, target, on_progress: ProgressCallback | None, default_phase=None):
    estimator = BuildProgressEstimator(target, default_phase)

    def feed(line: str) -> None:
        reading = estimator.feed(line)
        if reading is not None and on_progress is not None:
            on_progress(*reading)

    log_path.parent.mkdir(parents=True, exist_ok=True)
    proc = await env.run([script], vm_mem=8192)
    success = False
    with open(log_path, "wb", buffering=0) as log_file:
        try:
            async with terminate_on_cancel(proc):
                await asyncio.gather(
                    _pump(proc.stdout, log_file, feed),
                    _pump(proc.stderr, log_file, feed),
                    proc.wait(),
                )
            success = proc.returncode == 0
        finally:
            estimator.finish(success)
    if proc.returncode != 0:
        raise RuntimeError(
            f"Build script {script} failed with return code {proc.returncode}\n"
            f"Last lines of {log_path}:\n{_tail(log_path)}"
        )
    # pfrun returns success after a guest command failure, recording the guest
    # status in its output instead.  Surface that failure before checking for
    # individual build artifacts, which would otherwise hide the real cause.
    log = log_path.read_text(errors="replace")
    statuses = re.findall(r"PFM_CMD_STATUS=(\d+)", log)
    if statuses and int(statuses[-1]) != 0:
        raise RuntimeError(
            f"Build script {script} failed in pfrun with status {statuses[-1]}\n"
            f"Last lines of {log_path}:\n{_tail(log_path)}"
        )


async def build_python(project: Project, on_progress: ProgressCallback | None = None):
    await ensure_cpython_checkout(project)
    env = Env(project, image=Image.BUILD)

    patches_dir = root_path("tactical-patches")
    patches_json_path = project.path("config", "patches.json")

    existing = {}
    if patches_json_path.exists():
        existing = json.loads(patches_json_path.read_text())

    all_patches = sorted(
        p.name for p in patches_dir.iterdir()
        if p.suffix in (".diff", ".patch")
    )

    skip_patches = [name for name in all_patches if existing.get(name) == "no"]
    env["PY_FUZZ_SKIP_PATCHES"] = ":".join(skip_patches)

    await _build_run(env, "/pfm/build_scripts/build.sh", project.path("logs", "build.log"), "py", on_progress)
    # pfrun always exits 0; verify the expected output actually exists
    if not any(project.path("py").glob("bin/python3*-config")):
        raise RuntimeError("Python build failed: python3-config not found in py/bin/")
    if not project.path("py", ".git-version-info").is_file():
        raise RuntimeError("Python build failed: Git version metadata was not generated")

    updated = {name: existing.get(name, "yes") for name in all_patches}
    patches_json_path.write_text(json.dumps(updated, indent=2, sort_keys=True) + "\n")


async def build_packages(project: Project, on_progress: ProgressCallback | None = None):
    plan = resolve_packages(project.packages)
    if not plan:
        return
    # Fetch every package's source and the build toolchain host-side (VM is offline).
    for name, ref in plan:
        await ensure_package_checkout(project, name, ref)
    await ensure_package_tooling(project)
    env = Env(project, image=Image.BUILD)
    # Colon-separated: env vars are exported unquoted into the VM, so a space
    # separator would truncate the list (see PY_FUZZ_SKIP_PATCHES for precedent).
    env["PF_PACKAGES"] = ":".join(name for name, _ref in plan)
    for name, _ref in plan:
        env[f"PF_PKG_PROFILE_{name}"] = REGISTRY[name].profile
    log_path = project.path("logs", "build_packages.log")
    await _build_run(
        env,
        "/pfm/build_scripts/build_packages.sh",
        log_path,
        "packages",
        on_progress,
        default_phase="Building packages",
    )
    # pfrun always exits 0 even when the in-VM build fails, so a non-zero status is
    # invisible to _build_run; verify each configured package actually installed
    # into the interpreter and fail loudly (stopping the whole build) if not.
    missing = [name for name, _ref in plan if not _package_installed(project, name)]
    if missing:
        raise RuntimeError(
            f"Package build failed: {', '.join(missing)} did not install into the "
            f"interpreter. Last lines of {log_path}:\n{_tail(log_path)}"
        )
    write_pymutate_name_file(project)


def _package_installed(project: Project, name: str) -> bool:
    """Whether a built package landed in the interpreter's site-packages."""
    spec = REGISTRY[name]
    for site_packages in project.path("py").glob("lib/python*/site-packages"):
        # A dist-info proves pip completed the install; the import dir/module is the
        # thing warmup imports and AFL_PRELOAD actually need.
        if list(site_packages.glob(f"{name}-*.dist-info")):
            return True
        for imp in spec.imports:
            if (site_packages / imp).exists() or list(site_packages.glob(f"{imp}.*")):
                return True
    return False


async def build_helpers(project: Project, on_progress: ProgressCallback | None = None):
    env = Env(project, image=Image.BUILD)
    await _build_run(env, "/pfm/build_scripts/build_helpers.sh", project.path("logs", "build_helpers.log"), "helpers", on_progress, default_phase="Building helpers")
    # pfrun always exits 0; verify at least one fuzz helper binary was produced
    tool_name = project.harness
    if not (project.path("tools") / tool_name).exists():
        raise RuntimeError(f"Helper build failed: {tool_name} not found in tools/")
