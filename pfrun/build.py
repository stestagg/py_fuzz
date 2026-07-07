#!/usr/bin/env -S uv run --script
# /// script
# dependencies = ["click"]
# ///

import shutil
import subprocess
import sys
from pathlib import Path

import click

ROOT = Path(__file__).parent.resolve()
REPO_ROOT = ROOT.parent
PYMUTATE = REPO_ROOT / "pymutate"
IMAGES = ROOT / "images"
PACMAN_CACHE = ROOT / ".pacman-cache"
CARGO_CACHE = ROOT / ".cargo-cache"
PFRUN_BUILD = ROOT / ".pfrun-build"
RUNNER_PROJ = ROOT / "runner" / "pfrun.xcodeproj"
BINARY_OUT = ROOT / "pfrun"

IMAGE_ORDER = ["base", "afl", "build", "lldb"]


def run(*args, **kwargs):
    print(f"+ {' '.join(str(a) for a in args[0])}")
    subprocess.run(*args, check=True, **kwargs)


def _require(path: Path, label: str, suggestion: str):
    if not path.exists():
        click.echo(f"Missing {label}: {path}", err=True)
        click.echo(f"Build it first with: ./build.py image {suggestion}", err=True)
        sys.exit(1)


def build_runner():
    run([
        "xcodebuild",
        "-project", RUNNER_PROJ,
        "-scheme", "pfrun",
        "-configuration", "Release",
        "-derivedDataPath", PFRUN_BUILD,
        "build",
    ])
    built = PFRUN_BUILD / "Build" / "Products" / "Release" / "pfrun"
    shutil.copy2(built, BINARY_OUT)
    print(f"Runner binary written to {BINARY_OUT}")


def _build_base_docker(extra_run_args: list[str]):
    img = IMAGES / "base"
    PACMAN_CACHE.mkdir(exist_ok=True)
    tag = "py_fuzz_linux_base"
    run(["docker", "build", "-t", tag, str(img)])
    run([
        "docker", "run", "--rm",
        "--cap-add", "SYS_ADMIN",
        "-v", f"{img / 'build'}:/build",
        "-v", f"{img}:/out",
        "-v", f"{PACMAN_CACHE}:/var/cache/pacman/pkg",
        tag, *extra_run_args,
    ])


def build_image_kernel(name: str):
    """Build vmlinux+initram for name: compile for base, copy from base for others."""
    img = IMAGES / name
    img.mkdir(exist_ok=True)

    if name == "base":
        _build_base_docker(["kernel"])
    else:
        base = IMAGES / "base"
        _require(base / "vmlinux", "base vmlinux", "kernel base")
        _require(base / "initram", "base initram", "kernel base")
        shutil.copy2(base / "vmlinux", img / "vmlinux")
        shutil.copy2(base / "initram", img / "initram")
        print(f"Copied kernel artifacts from base to {name}")


def build_image_fs(name: str):
    """Build fs.img for the given image."""
    img = IMAGES / name
    img.mkdir(exist_ok=True)
    PACMAN_CACHE.mkdir(exist_ok=True)

    tag = f"py_fuzz_linux_{name}"
    run(["docker", "build", "-t", tag, str(img)])

    docker_run_cmd = ["docker", "run", "--rm"]

    if name == "base":
        CARGO_CACHE.mkdir(exist_ok=True)
        docker_run_cmd += [
            "--cap-add", "SYS_ADMIN",
            "-v", f"{img / 'build'}:/build",
            "-v", f"{img}:/out",
            "-v", f"{PYMUTATE}:/pymutate:ro",
            "-v", f"{PACMAN_CACHE}:/var/cache/pacman/pkg",
            "-v", f"{CARGO_CACHE}:/cargo-cache",
            tag, "fs",
        ]
    elif name == "afl":
        base = IMAGES / "base"
        _require(base / "fs.img", "base fs.img", "fs base")
        docker_run_cmd += [
            "--cap-add", "SYS_ADMIN",
            "-v", f"{base / 'fs.img'}:/base/fs.img:ro",
            "-v", f"{img}:/out",
            "-v", f"{PACMAN_CACHE}:/var/cache/pacman/pkg",
            tag,
        ]
    elif name == "build":
        afl = IMAGES / "afl"
        _require(afl / "fs.img", "afl fs.img", "fs afl")
        docker_run_cmd += [
            "--privileged",
            "-v", f"{afl / 'fs.img'}:/base/fs.img:ro",
            "-v", f"{img}:/out",
            "-v", f"{PACMAN_CACHE}:/var/cache/pacman/pkg",
            tag,
        ]
    elif name == "lldb":
        base = IMAGES / "base"
        _require(base / "fs.img", "base fs.img", "fs base")
        docker_run_cmd += [
            "--cap-add", "SYS_ADMIN",
            "-v", f"{base / 'fs.img'}:/base/fs.img:ro",
            "-v", f"{img}:/out",
            "-v", f"{PACMAN_CACHE}:/var/cache/pacman/pkg",
            tag,
        ]

    run(docker_run_cmd)


def build_image(name: str, part: str):
    if part in ("kernel", "all"):
        build_image_kernel(name)
    if part in ("fs", "all"):
        build_image_fs(name)


@click.group()
def cli():
    pass


@cli.command()
def runner():
    """Build the Swift pfrun binary via xcodebuild."""
    build_runner()


@cli.command()
@click.argument("part", type=click.Choice(["kernel", "fs", "all"]))
@click.argument("image", type=click.Choice(IMAGE_ORDER + ["all"]))
def image(part, image):
    """Build kernel (vmlinux+initram), fs (root filesystem), or both for one or all images.

    PART is kernel, fs, or all. IMAGE is one of base/afl/build/lldb or all.

    Examples:\n
      ./build.py image all base       # full base build\n
      ./build.py image fs all         # rebuild root filesystem for every image\n
      ./build.py image kernel base    # recompile kernel only
    """
    names = IMAGE_ORDER if image == "all" else [image]
    for name in names:
        build_image(name, part)


@cli.command(name="all")
def build_all():
    """Build the runner and all images (kernel + fs)."""
    build_runner()
    for name in IMAGE_ORDER:
        build_image(name, "all")


if __name__ == "__main__":
    cli()
