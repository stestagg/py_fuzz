import json
import os
import asyncio
from pathlib import Path
from enum import Enum
from typing import Literal

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


def _classification_response_model(labels: list[str], include_rationale: bool) -> type[BaseModel]:
    label_enum = Enum(
        "ClassificationLabel",
        {f"LABEL_{i}": label for i, label in enumerate(labels)},
    )
    fields = {
        "label": (
            label_enum,
            Field(..., description="Exactly one of the provided class labels."),
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
    labels_text = "\n".join(f"- {label}" for label in labels)
    extra = (extra_text or "").strip()
    if extra:
        extra = f"\nAdditional classification guidance:\n{extra}\n"
    rationale_instruction = (
        " Include a concise rationale."
        if include_rationale
        else " Return only the selected label field."
    )

    return [
        {
            "role": "system",
            "content": (
                "You classify pyfuzz CPython crash artifacts. Choose exactly one "
                "of the provided class labels. Base the choice on the artifact "
                "metadata, crash input, LLDB output, and any additional analysis. "
                "Do not copy long artifact excerpts." + rationale_instruction
            ),
        },
        {
            "role": "user",
            "content": (
                f"Class labels:\n{labels_text}\n"
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
    artifact = get_artifact(project, artifact_hash)
    dest_path = artifact.dir / dest
    if dest_path.exists() and not force:
        return LLMClassificationResult(artifact_hash, dest_path, "skipped")

    artifact_view = render_artifact_llm_view(
        project,
        artifact_hash,
        require_lldb=True,
        exclude_filenames={dest},
    )
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
    tmp_path = artifact.dir / f".{dest}.tmp-{os.getpid()}"
    if include_rationale:
        tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    else:
        tmp_path.write_text(str(payload["label"]) + "\n")
    tmp_path.replace(dest_path)
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
