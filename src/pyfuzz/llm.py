import json
import os
import asyncio
from pathlib import Path
from enum import Enum
from typing import Any, Literal

from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field, create_model

from .analysis import get_artifact, render_artifact_llm_view
from .project import Project


OPENAI_KEY_PATH = Path("~/.openai_key").expanduser()
DEFAULT_OPENAI_MODEL = "gpt-5.4"
CLASSIFY_CONCURRENCY = 100
RESERVED_ARTIFACT_FILENAMES = {"core", "input.txt", "lldb.txt", "meta.json"}


class LLMError(RuntimeError):
    pass


class LLMCheckResult(BaseModel):
    status: Literal["ok"]
    message: Literal["pyfuzz-ok"]


class LLMClassificationBase(BaseModel):
    model_config = ConfigDict(use_enum_values=True)


class LLMClassificationResult:
    def __init__(
        self,
        artifact_hash: str,
        dest: Path,
        status: Literal["written", "skipped", "failed"],
        label: str | None = None,
        error: str | None = None,
    ):
        self.artifact_hash = artifact_hash
        self.dest = dest
        self.status = status
        self.label = label
        self.error = error


class LLMDescribeResult:
    def __init__(
        self,
        artifact_hash: str,
        dest: Path,
        status: Literal["written", "skipped", "failed"],
        text: str | None = None,
        error: str | None = None,
    ):
        self.artifact_hash = artifact_hash
        self.dest = dest
        self.status = status
        self.text = text
        self.error = error


def load_openai_api_key() -> str:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if api_key:
        return api_key

    try:
        api_key = OPENAI_KEY_PATH.read_text().strip()
    except FileNotFoundError as exc:
        raise LLMError(
            f"OpenAI API key not found. Set OPENAI_API_KEY or create {OPENAI_KEY_PATH}."
        ) from exc

    if not api_key:
        raise LLMError(f"OpenAI API key file is empty: {OPENAI_KEY_PATH}")
    return api_key


def create_openai_client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=load_openai_api_key())


async def check_openai_connection(client: AsyncOpenAI, model: str) -> LLMCheckResult:
    response = await client.responses.parse(
        model=model,
        input=[
            {
                "role": "system",
                "content": "Return the requested structured health-check response.",
            },
            {
                "role": "user",
                "content": "Confirm the pyfuzz OpenAI connection is working.",
            },
        ],
        text_format=LLMCheckResult,
        max_output_tokens=64,
    )

    parsed = response.output_parsed
    if parsed is None:
        raise LLMError("OpenAI API check completed without a parsed response.")
    return parsed


def parse_class_labels(classes: str) -> list[str]:
    labels = [label.strip() for label in classes.split(",")]
    labels = [label for label in labels if label]
    if not labels:
        raise LLMError("At least one class label must be provided.")
    if len(set(labels)) != len(labels):
        raise LLMError("Class labels must be unique.")
    return labels


def validate_artifact_result_filename(dest: str) -> str:
    path = Path(dest)
    if path.is_absolute() or len(path.parts) != 1 or path.name != dest:
        raise LLMError("--dest must be a filename, not a path.")
    if dest in {"", ".", ".."}:
        raise LLMError("--dest must be a non-empty filename.")
    if dest in RESERVED_ARTIFACT_FILENAMES:
        raise LLMError(f"--dest may not overwrite artifact source file {dest!r}.")
    return dest


async def _load_artifact_for_llm(
    project: Project,
    artifact_hash: str,
    dest: str,
    force: bool,
    include_filenames: set[str] | None = None,
) -> tuple[Path, str | None]:
    """Returns (dest_path, artifact_view). artifact_view is None when the result should be skipped."""
    artifact = get_artifact(project, artifact_hash)
    dest_path = artifact.dir / dest
    if dest_path.exists() and not force:
        return dest_path, None
    artifact_view = render_artifact_llm_view(
        project,
        artifact_hash,
        require_lldb=True,
        exclude_filenames={dest},
        include_filenames=include_filenames,
    )
    return dest_path, artifact_view


def _atomic_write(dest_path: Path, content: str) -> None:
    tmp_path = dest_path.parent / f".{dest_path.name}.tmp-{os.getpid()}"
    tmp_path.write_text(content)
    tmp_path.replace(dest_path)


def _classification_response_model(labels: list[str], include_rationale: bool) -> type[BaseModel]:
    if labels:
        label_type: Any = Enum(
            "ClassificationLabel",
            {f"LABEL_{i}": label for i, label in enumerate(labels)},
        )
        label_field = Field(..., description="Exactly one of the provided class labels.")
    else:
        # Free-class mode: the model invents a short tag-style label instead of
        # picking from a fixed enum.
        label_type = str
        label_field = Field(..., description="A short tag-style label naming this artifact's class.")
    fields = {
        "label": (
            label_type,
            label_field,
        ),
    }
    if include_rationale:
        fields["rationale"] = (
            str,
            Field(..., description="A concise reason for the selected label."),
        )

    return create_model(
        "ArtifactClassificationResponse",
        __base__=LLMClassificationBase,
        **fields,
    )


def _classification_prompt(
    artifact_view: str,
    labels: list[str],
    extra_text: str | None,
    include_rationale: bool,
) -> list[dict[str, str]]:
    extra = (extra_text or "").strip()
    if extra:
        extra = f"\nAdditional classification guidance:\n{extra}\n"
    rationale_instruction = (
        " Include a concise rationale."
        if include_rationale
        else " Return only the selected label field."
    )

    if labels:
        labels_text = "\n".join(f"- {label}" for label in labels)
        task_instruction = "Choose exactly one of the provided class labels."
        labels_block = f"Class labels:\n{labels_text}\n"
    else:
        # Free-class mode: no fixed list, so ask for an invented tag-style label.
        task_instruction = "Assign a single concise tag-style label naming this artifact's class."
        labels_block = ""

    return [
        {
            "role": "system",
            "content": (
                "You classify pyfuzz CPython crash artifacts. " + task_instruction + " Base the "
                "choice on the artifact metadata, crash input, LLDB output, and any additional "
                "analysis. Do not copy long artifact excerpts." + rationale_instruction
            ),
        },
        {
            "role": "user",
            "content": (
                f"{labels_block}"
                f"{extra}\n"
                "Artifact view:\n"
                f"{artifact_view}"
            ),
        },
    ]


async def classify_artifact(
    client: AsyncOpenAI,
    project: Project,
    artifact_hash: str,
    labels: list[str],
    dest: str,
    model: str,
    extra_text: str | None = None,
    force: bool = False,
    include_rationale: bool = False,
) -> LLMClassificationResult:
    dest_path, artifact_view = await _load_artifact_for_llm(project, artifact_hash, dest, force)
    if artifact_view is None:
        return LLMClassificationResult(artifact_hash, dest_path, "skipped")

    response_model = _classification_response_model(labels, include_rationale)
    response = await client.responses.parse(
        model=model,
        input=_classification_prompt(artifact_view, labels, extra_text, include_rationale),
        text_format=response_model,
        max_output_tokens=512 if include_rationale else 64,
    )

    parsed = response.output_parsed
    if parsed is None:
        raise LLMError(f"OpenAI classification for {artifact_hash} returned no parsed output.")

    payload = parsed.model_dump(mode="json")
    if include_rationale:
        _atomic_write(dest_path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    else:
        _atomic_write(dest_path, str(payload["label"]) + "\n")
    return LLMClassificationResult(
        artifact_hash,
        dest_path,
        "written",
        label=str(payload["label"]),
    )


async def classify_artifacts(
    client: AsyncOpenAI,
    project: Project,
    artifact_hashes: list[str],
    labels: list[str],
    dest: str,
    model: str,
    extra_text: str | None = None,
    force: bool = False,
    include_rationale: bool = False,
    concurrency: int = CLASSIFY_CONCURRENCY,
) -> list[LLMClassificationResult]:
    dest = validate_artifact_result_filename(dest)
    sem = asyncio.Semaphore(concurrency)

    async def run_one(artifact_hash: str) -> LLMClassificationResult:
        async with sem:
            try:
                return await classify_artifact(
                    client,
                    project,
                    artifact_hash,
                    labels,
                    dest,
                    model,
                    extra_text=extra_text,
                    force=force,
                    include_rationale=include_rationale,
                )
            except Exception as exc:
                artifact_dir = project.path("artifacts", artifact_hash)
                return LLMClassificationResult(
                    artifact_hash,
                    artifact_dir / dest,
                    "failed",
                    error=f"{type(exc).__name__}: {exc}",
                )

    return await asyncio.gather(*(run_one(hash) for hash in artifact_hashes))


def _describe_prompt(artifact_view: str, prompt: str) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "You are analyzing crash artifacts produced by a CPython fuzzer. "
                "Each artifact includes metadata about the crash, the input that triggered it, "
                "LLDB debugger output, and optional additional analysis. "
                "Be concise and focus on what the evidence actually shows."
            ),
        },
        {
            "role": "user",
            "content": f"{prompt}\n\nArtifact view:\n{artifact_view}",
        },
    ]


async def describe_artifact(
    client: AsyncOpenAI,
    project: Project,
    artifact_hash: str,
    prompt: str,
    dest: str,
    model: str,
    max_tokens: int = 50,
    force: bool = False,
    include_filenames: set[str] | None = None,
) -> LLMDescribeResult:
    dest_path, artifact_view = await _load_artifact_for_llm(
        project,
        artifact_hash,
        dest,
        force,
        include_filenames=include_filenames,
    )
    if artifact_view is None:
        return LLMDescribeResult(artifact_hash, dest_path, "skipped")

    response = await client.responses.create(
        model=model,
        input=_describe_prompt(artifact_view, prompt),
        max_output_tokens=max_tokens,
    )
    text = response.output_text.strip()
    _atomic_write(dest_path, text + "\n")
    return LLMDescribeResult(artifact_hash, dest_path, "written", text=text)


async def describe_artifacts(
    client: AsyncOpenAI,
    project: Project,
    artifact_hashes: list[str],
    prompt: str,
    dest: str,
    model: str,
    max_tokens: int = 50,
    force: bool = False,
    concurrency: int = CLASSIFY_CONCURRENCY,
) -> list[LLMDescribeResult]:
    dest = validate_artifact_result_filename(dest)
    sem = asyncio.Semaphore(concurrency)

    async def run_one(artifact_hash: str) -> LLMDescribeResult:
        async with sem:
            try:
                return await describe_artifact(
                    client,
                    project,
                    artifact_hash,
                    prompt,
                    dest,
                    model,
                    max_tokens=max_tokens,
                    force=force,
                )
            except Exception as exc:
                artifact_dir = project.path("artifacts", artifact_hash)
                return LLMDescribeResult(
                    artifact_hash,
                    artifact_dir / dest,
                    "failed",
                    error=f"{type(exc).__name__}: {exc}",
                )

    return await asyncio.gather(*(run_one(hash) for hash in artifact_hashes))
