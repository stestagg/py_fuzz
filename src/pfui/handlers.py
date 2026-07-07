from __future__ import annotations

import asyncio
import os
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from pyfuzz.analysis import analyze_artifact, get_artifact, is_artifact_analyzed, list_artifacts, sync_artifacts
from pyfuzz.build import build_helpers, build_python
from pyfuzz.build_progress import median_total
from pyfuzz.clean import CleanComponent, clean
from pyfuzz.fuzz import run_fuzz
from pyfuzz.fuzzdict import make_dict
from pyfuzz.lldb import analyze_core
from pyfuzz.llm import (
    DEFAULT_OPENAI_MODEL,
    LLMError,
    classify_artifacts,
    create_openai_client,
    describe_artifact,
    validate_artifact_result_filename,
)
from pyfuzz.monitor import monitor_loop
from pyfuzz.project import Project

from .artifact_service import (
    artifact_detail,
    artifact_list_payload,
    contained_artifact_file,
    validate_local_filename,
)
from .input_service import delete_input_file, input_file_payload, input_tree, update_input_file
from .project_service import create_project, list_projects, project_snapshot, summary_payload, update_project_config
from .protocol import EmptyParams, ProtocolError, RequestContext, Router
from .tasks import ProgressReporter
from .trend_service import load_trend


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ArtifactListParams(Params):
    group_specs: list[str] = Field(default_factory=list, alias="groupSpecs")


class ArtifactParams(Params):
    hash: str = Field(min_length=1)


class ArtifactFileParams(ArtifactParams):
    filename: str = Field(min_length=1)


class AskLlmParams(ArtifactParams):
    prompt: str = Field(min_length=1)
    dest: str = Field(min_length=1)
    filenames: list[str]


class ClassifyClass(Params):
    name: str = Field(min_length=1)
    description: str = ""


class ClassifyParams(Params):
    dest: str = Field(min_length=1)
    # In free-class mode the caller supplies no fixed classes and the LLM output
    # is not constrained to a predefined list.
    free: bool = False
    classes: list[ClassifyClass] = Field(default_factory=list)
    extra_text: str = Field(default="", alias="extraText")


class TaskStartParams(Params):
    action: str
    params: dict[str, Any] = Field(default_factory=dict)


class TaskStopParams(Params):
    task_id: str = Field(alias="taskId", min_length=1)


class ProjectCreateParams(Params):
    name: str = Field(min_length=1, max_length=100)


class ProjectConfigParams(Params):
    config: str = Field(min_length=1, max_length=262_144)


class InputPathParams(Params):
    path: str = Field(min_length=1, max_length=4096)


class InputUpdateParams(InputPathParams):
    content: str = Field(max_length=4 * 1024 * 1024)


router = Router()


@router.handler("projects.list", EmptyParams, requires_project=False)
async def projects_list(context: RequestContext, project: Project | None, params: EmptyParams) -> Any:
    return {"projects": list_projects()}


@router.handler("project.create", ProjectCreateParams, requires_project=False)
async def project_create(context: RequestContext, project: Project | None, params: ProjectCreateParams) -> Any:
    try:
        created = await asyncio.to_thread(create_project, params.name)
    except FileExistsError as exc:
        raise ProtocolError("conflict", str(exc)) from exc
    except ValueError as exc:
        raise ProtocolError("bad_request", str(exc)) from exc
    return {"project": project_snapshot(created), "projects": list_projects()}


@router.handler("project.get")
async def project_get(context: RequestContext, project: Project | None, params: EmptyParams) -> Any:
    assert project is not None
    return {"project": project_snapshot(project)}


@router.handler("project.updateConfig", ProjectConfigParams)
async def project_update_config(context: RequestContext, project: Project | None, params: ProjectConfigParams) -> Any:
    assert project is not None
    try:
        updated = await asyncio.to_thread(update_project_config, project, params.config)
    except ValueError as exc:
        raise ProtocolError("bad_request", str(exc)) from exc
    return {"project": project_snapshot(updated)}


@router.handler("inputs.list")
async def inputs_list(context: RequestContext, project: Project | None, params: EmptyParams) -> Any:
    assert project is not None
    return {"tree": await asyncio.to_thread(input_tree, project)}


@router.handler("input.read", InputPathParams)
async def input_read(context: RequestContext, project: Project | None, params: InputPathParams) -> Any:
    assert project is not None
    return await asyncio.to_thread(input_file_payload, project, params.path)


@router.handler("input.update", InputUpdateParams)
async def input_update(context: RequestContext, project: Project | None, params: InputUpdateParams) -> Any:
    assert project is not None
    return await asyncio.to_thread(update_input_file, project, params.path, params.content)


@router.handler("input.delete", InputPathParams)
async def input_delete(context: RequestContext, project: Project | None, params: InputPathParams) -> Any:
    assert project is not None
    return {"tree": await asyncio.to_thread(delete_input_file, project, params.path)}


@router.handler("summary.get")
async def summary_get(context: RequestContext, project: Project | None, params: EmptyParams) -> Any:
    assert project is not None
    return {"summary": await asyncio.to_thread(summary_payload, project)}


@router.handler("trend.get")
async def trend_get(context: RequestContext, project: Project | None, params: EmptyParams) -> Any:
    assert project is not None
    return {"points": await asyncio.to_thread(load_trend, project)}


@router.handler("artifacts.list", ArtifactListParams)
async def artifacts_list(context: RequestContext, project: Project | None, params: ArtifactListParams) -> Any:
    assert project is not None
    return await artifact_list_payload(project, params.group_specs)


@router.handler("artifacts.sync")
async def artifacts_sync(context: RequestContext, project: Project | None, params: EmptyParams) -> Any:
    assert project is not None
    return {"created": await sync_artifacts(project)}


@router.handler("artifact.get", ArtifactParams)
async def artifact_get(context: RequestContext, project: Project | None, params: ArtifactParams) -> Any:
    assert project is not None
    return await asyncio.to_thread(artifact_detail, project, params.hash)


@router.handler("artifact.file", ArtifactFileParams)
async def artifact_file(context: RequestContext, project: Project | None, params: ArtifactFileParams) -> Any:
    assert project is not None
    artifact = get_artifact(project, params.hash)
    path = contained_artifact_file(artifact, params.filename)
    return {"content": await asyncio.to_thread(path.read_text, "utf-8", "replace")}


@router.handler("artifact.runLldb", ArtifactParams)
async def artifact_run_lldb(context: RequestContext, project: Project | None, params: ArtifactParams) -> Any:
    assert project is not None
    await context.tasks.run_tracked(
        f"lldb {params.hash[:8]}",
        "lldb",
        project.name,
        analyze_core(project, params.hash),
    )
    return await asyncio.to_thread(artifact_detail, project, params.hash)


@router.handler("artifact.analyze", ArtifactParams)
async def artifact_analyze(context: RequestContext, project: Project | None, params: ArtifactParams) -> Any:
    assert project is not None
    await context.tasks.run_tracked(
        f"analyze {params.hash[:8]}",
        "analyze",
        project.name,
        analyze_artifact(project, params.hash),
    )
    return await asyncio.to_thread(artifact_detail, project, params.hash)


@router.handler("artifacts.analyze")
async def artifacts_analyze(context: RequestContext, project: Project | None, params: EmptyParams) -> Any:
    assert project is not None

    async def analyze_all() -> int:
        # Cores and crashes are both artifacts; analyze every one that has not
        # been analyzed yet, regardless of type. analyze_artifact dispatches to
        # the right per-type analysis internally.
        pending = [a for a in await list_artifacts(project) if not is_artifact_analyzed(a)]
        for artifact in pending:
            await analyze_artifact(project, artifact.hash)
        return len(pending)

    # Analyzing every artifact can take far longer than a request round-trip, so
    # we start the work as a tracked background task and return immediately. The
    # UI follows progress via tasks.changed events rather than blocking on the
    # reply.
    context.tasks.start(
        "analyze artifacts",
        "analyze-all",
        project.name,
        analyze_all(),
        exclusive_key=f"analyze-all:{project.name}",
    )
    return {"started": True}


@router.handler("artifact.askLlm", AskLlmParams)
async def artifact_ask_llm(context: RequestContext, project: Project | None, params: AskLlmParams) -> Any:
    assert project is not None
    prompt = params.prompt.strip()
    if not prompt:
        raise ProtocolError("bad_request", "Prompt cannot be empty")
    destination = validate_artifact_result_filename(params.dest.strip())
    validate_local_filename(destination, "Response filename")
    if destination.endswith(".marker"):
        raise ProtocolError("bad_request", "Response filename cannot end with .marker")

    artifact = get_artifact(project, params.hash)
    available = {
        path.name
        for path in artifact.dir.iterdir()
        if not path.name.endswith(".marker") and (path.is_file() or path.is_symlink())
    }
    unknown = sorted(set(params.filenames) - available)
    if unknown:
        raise ProtocolError("bad_request", f"Unknown artifact filename: {unknown[0]}")
    if (artifact.dir / destination).exists():
        raise ProtocolError("conflict", f"Artifact file already exists: {destination}")

    async def ask() -> None:
        await describe_artifact(
            create_openai_client(),
            project,
            params.hash,
            prompt,
            destination,
            os.environ.get("PYFUZZ_OPENAI_MODEL", DEFAULT_OPENAI_MODEL),
            include_filenames=set(params.filenames),
        )

    try:
        await context.tasks.run_tracked(
            f"ask LLM {params.hash[:8]}",
            "ask-llm",
            project.name,
            ask(),
            exclusive_key=f"ask-llm:{project.name}:{params.hash}",
        )
    except ValueError as exc:
        raise ProtocolError("conflict", str(exc)) from exc
    return await asyncio.to_thread(artifact_detail, project, params.hash)


@router.handler("artifacts.classify", ClassifyParams)
async def artifacts_classify(context: RequestContext, project: Project | None, params: ClassifyParams) -> Any:
    assert project is not None

    try:
        destination = validate_artifact_result_filename(params.dest.strip())
    except LLMError as exc:
        raise ProtocolError("bad_request", str(exc)) from exc
    validate_local_filename(destination, "Output filename")
    if destination.endswith(".marker"):
        raise ProtocolError("bad_request", "Output filename cannot end with .marker")

    # Free-class mode passes empty labels; classify_artifacts then lets the LLM
    # produce an unconstrained tag-style label instead of picking from a list.
    if params.free:
        labels: list[str] = []
    else:
        labels = [item.name.strip() for item in params.classes]
        if not labels:
            raise ProtocolError("bad_request", "Provide at least one class")
        if any(not label for label in labels):
            raise ProtocolError("bad_request", "Class labels cannot be empty")
        if len(set(labels)) != len(labels):
            raise ProtocolError("bad_request", "Class labels must be unique")

    # Fold the per-class descriptions and any freeform prompt text into a single
    # guidance block; the labels written to disk stay clean names.
    described = [(item.name.strip(), item.description.strip()) for item in params.classes if item.description.strip()]
    guidance: list[str] = []
    if described:
        guidance.append("Class descriptions:\n" + "\n".join(f"- {name}: {desc}" for name, desc in described))
    if params.extra_text.strip():
        guidance.append(params.extra_text.strip())
    extra_text = "\n\n".join(guidance) or None

    model = os.environ.get("PYFUZZ_OPENAI_MODEL", DEFAULT_OPENAI_MODEL)

    async def classify_all() -> int:
        # Only classify artifacts that do not already have the destination file,
        # mirroring the CLI's skip-existing behaviour without wasting API calls.
        pending = [
            artifact.hash
            for artifact in await list_artifacts(project)
            if not (get_artifact(project, artifact.hash).dir / destination).exists()
        ]
        if not pending:
            return 0
        await classify_artifacts(
            create_openai_client(),
            project,
            pending,
            labels,
            destination,
            model,
            extra_text=extra_text,
        )
        return len(pending)

    # Classifying every artifact can outlast a request round-trip, so run it as a
    # tracked background task and let the UI follow progress via tasks.changed.
    context.tasks.start(
        "classify artifacts",
        "classify-all",
        project.name,
        classify_all(),
        exclusive_key=f"classify-all:{project.name}",
    )
    return {"started": True}


async def _fuzz_action(project: Project, instances: int, afl_debug: bool, monitor: bool) -> None:
    workers = [asyncio.create_task(run_fuzz(project, index, afl_debug=afl_debug)) for index in range(instances)]
    monitor_task = None
    if monitor:
        monitor_task = asyncio.create_task(
            monitor_loop(project, get_running_workers=lambda: sum(not task.done() for task in workers))
        )
    try:
        await asyncio.gather(*workers)
    except asyncio.CancelledError:
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        raise
    finally:
        if monitor_task is not None:
            monitor_task.cancel()
            await asyncio.gather(monitor_task, return_exceptions=True)


async def _build_action(project: Project, target: str, on_progress=None) -> None:
    stages = [s for s, keys in (("py", {"all", "py"}), ("helpers", {"all", "helpers"})) if target in keys]

    # Combine the stages into one continuous 0..1 bar, weighted by their
    # historical durations (fall back to py-dominant weights without history).
    totals = {s: median_total(s) for s in stages}
    if len(stages) > 1 and all(totals.values()):
        grand = sum(totals.values())
        weights = {s: totals[s] / grand for s in stages}
    else:
        default = {"py": 0.9, "helpers": 0.1}
        norm = sum(default[s] for s in stages)
        weights = {s: default[s] / norm for s in stages}
    offsets: dict[str, float] = {}
    acc = 0.0
    for s in stages:
        offsets[s] = acc
        acc += weights[s]

    def wrap(stage: str):
        if on_progress is None:
            return None
        later = sum(totals[s] or 0.0 for s in stages[stages.index(stage) + 1:])
        return lambda progress, eta, phase: on_progress(
            min(1.0, offsets[stage] + progress * weights[stage]), eta + later, phase
        )

    if "py" in stages:
        await build_python(project, wrap("py"))
    if "helpers" in stages:
        await build_helpers(project, wrap("helpers"))
    await make_dict(project)


@router.handler("tasks.list", EmptyParams, requires_project=False)
async def tasks_list(context: RequestContext, project: Project | None, params: EmptyParams) -> Any:
    return {"tasks": context.tasks.snapshot()}


@router.handler("task.stop", TaskStopParams, requires_project=False)
async def task_stop(context: RequestContext, project: Project | None, params: TaskStopParams) -> Any:
    try:
        return await context.tasks.stop(params.task_id)
    except ValueError as exc:
        raise ProtocolError("not_found", str(exc)) from exc


@router.handler("task.start", TaskStartParams)
async def task_start(context: RequestContext, project: Project | None, request: TaskStartParams) -> Any:
    assert project is not None
    values = request.params
    if request.action == "fuzz":
        instances = int(values.get("instances") or 10)
        if not 1 <= instances <= 128:
            raise ProtocolError("bad_request", "instances must be between 1 and 128")
        try:
            tracked = context.tasks.start(
                f"fuzz (-j {instances})",
                "fuzz",
                project.name,
                _fuzz_action(project, instances, bool(values.get("aflDebug")), bool(values.get("monitor", True))),
                exclusive_key=f"fuzz:{project.name}",
            )
        except ValueError as exc:
            raise ProtocolError("conflict", str(exc)) from exc
    elif request.action == "build":
        target = str(values.get("target") or "all")
        if target not in {"all", "py", "helpers"}:
            raise ProtocolError("bad_request", f"Unknown build target: {target}")
        if context.tasks.running("fuzz", project.name):
            raise ProtocolError("conflict", "Cannot build while fuzzing is running on this project")
        reporter = ProgressReporter()
        tracked = context.tasks.start(
            f"build: {target}", "build", project.name,
            _build_action(project, target, reporter.emit),
            progress_reporter=reporter,
        )
    elif request.action == "clean":
        raw_components = values.get("components")
        if not isinstance(raw_components, list) or not raw_components:
            raise ProtocolError("bad_request", "clean requires at least one component")
        try:
            components = [CleanComponent(str(value)) for value in raw_components]
        except ValueError as exc:
            raise ProtocolError("bad_request", str(exc)) from exc
        if context.tasks.running("fuzz", project.name):
            raise ProtocolError("conflict", "Cannot clean while fuzzing is running on this project")
        label = "+".join(component.value for component in components)
        tracked = context.tasks.start(
            f"clean: {label}",
            "clean",
            project.name,
            asyncio.to_thread(clean, project, components),
            thread_backed=True,
        )
    else:
        raise ProtocolError("bad_request", f"Unknown action: {request.action}")
    return {"taskId": tracked.id}
