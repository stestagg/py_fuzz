from __future__ import annotations

import json
import textwrap
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Sequence

from tools.pyfuzz.project import DEFAULT_REPO, PROJECTS_DIR, ProjectConfig, default_env_id, project_path, save_project

from .models import FinalRankingEntry, GroupAssessment, PullRequestCandidate

MAX_BODY_CHARS = 800
MAX_COMMENT_CHARS = 260
MAX_FILES_IN_PROMPT = 40
MAX_FILES_IN_RANKING_PROMPT = 12


def chunked(items: Sequence[PullRequestCandidate], size: int) -> Iterator[list[PullRequestCandidate]]:
    for index in range(0, len(items), size):
        yield list(items[index:index + size])


def existing_pr_numbers(projects_dir: Path = PROJECTS_DIR) -> set[int]:
    if not projects_dir.exists():
        return set()
    results: set[int] = set()
    for entry in projects_dir.iterdir():
        if not entry.is_dir():
            continue
        if not entry.name.startswith("pr-"):
            continue
        suffix = entry.name[3:]
        if suffix.isdigit():
            results.add(int(suffix))
    return results


def collect_candidates(candidates: Iterable[PullRequestCandidate], *, limit: int) -> list[PullRequestCandidate]:
    results: list[PullRequestCandidate] = []
    for candidate in candidates:
        results.append(candidate)
        if len(results) >= limit:
            break
    return results


def _compact_text(text: str, *, limit: int) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rstrip() + "..."


def format_group_prompt(group: Sequence[PullRequestCandidate]) -> str:
    sections: list[str] = []
    for list_index, candidate in enumerate(group, start=1):
        comment_lines = [
            f"- {comment.source} by {comment.author} at {comment.created_at}: "
            f"{_compact_text(comment.body, limit=MAX_COMMENT_CHARS)}"
            for comment in candidate.comments
        ]
        if not comment_lines:
            comment_lines = ["- none"]
        file_list = ", ".join(candidate.changed_files[:MAX_FILES_IN_PROMPT])
        if len(candidate.changed_files) > MAX_FILES_IN_PROMPT:
            file_list += f", ... ({len(candidate.changed_files) - MAX_FILES_IN_PROMPT} more)"
        sections.append(
            textwrap.dedent(
                f"""\
                [{list_index}] PR #{candidate.number}
                title: {candidate.title}
                url: {candidate.url}
                body: {_compact_text(candidate.body, limit=MAX_BODY_CHARS)}
                changed files ({len(candidate.changed_files)}): {file_list}
                recent comments:
                {chr(10).join(comment_lines)}
                """
            ).strip()
        )
    return "\n\n".join(sections)


def apply_group_assessments(
    group: Sequence[PullRequestCandidate],
    assessments: Sequence[GroupAssessment],
) -> list[PullRequestCandidate]:
    by_index = {assessment.list_index: assessment for assessment in assessments}
    updated: list[PullRequestCandidate] = []
    for list_index, candidate in enumerate(group, start=1):
        assessment = by_index[list_index]
        updated.append(
            PullRequestCandidate(
                number=candidate.number,
                title=candidate.title,
                body=candidate.body,
                url=candidate.url,
                head_sha=candidate.head_sha,
                changed_files=list(candidate.changed_files),
                comments=list(candidate.comments),
                group_index=list_index,
                crash_likelihood=assessment.crash_likelihood,
                selected=assessment.selected,
                reason=assessment.reason,
            )
        )
    return updated


def format_final_ranking_prompt(candidates: Sequence[PullRequestCandidate]) -> str:
    sections: list[str] = []
    for list_index, candidate in enumerate(candidates, start=1):
        top_files = ", ".join(candidate.changed_files[:MAX_FILES_IN_RANKING_PROMPT])
        if len(candidate.changed_files) > MAX_FILES_IN_RANKING_PROMPT:
            top_files += ", ..."
        top_comments = "; ".join(
            _compact_text(comment.body, limit=100)
            for comment in candidate.comments[:2]
        ) or "none"
        sections.append(
            textwrap.dedent(
                f"""\
                [{list_index}] PR #{candidate.number}
                title: {candidate.title}
                body summary: {_compact_text(candidate.body, limit=240)}
                files: {top_files}
                recent comment hints: {top_comments}
                stage1 selected: {candidate.selected}
                stage1 score: {candidate.crash_likelihood}
                stage1 reason: {candidate.reason or "none"}
                """
            ).strip()
        )
    return "\n\n".join(sections)


def create_projects_from_ranking(
    ranking: Sequence[FinalRankingEntry],
    *,
    top_n: int,
    dry_run: bool,
) -> list[dict[str, object]]:
    created: list[dict[str, object]] = []
    for entry in ranking[:top_n]:
        name = f"pr-{entry.pr_number}"
        root = project_path(name)
        info = {
            "project_name": name,
            "pr_number": entry.pr_number,
            "path": str(root),
            "created": False,
            "reason": entry.reason,
            "score": entry.crash_likelihood,
        }
        if root.exists():
            info["skipped"] = "already-exists"
            created.append(info)
            continue
        if not dry_run:
            save_project(
                name,
                ProjectConfig(
                    env_id=default_env_id(name, entry.pr_number),
                    repo=DEFAULT_REPO,
                    pr_id=entry.pr_number,
                ),
            )
            info["created"] = True
        created.append(info)
    return created


def build_report(
    candidates: Sequence[PullRequestCandidate],
    ranking: Sequence[FinalRankingEntry],
    project_results: Sequence[dict[str, object]],
) -> dict[str, object]:
    ranked_by_pr = {entry.pr_number: entry for entry in ranking}
    candidates_payload = []
    for candidate in candidates:
        row = asdict(candidate)
        final_entry = ranked_by_pr.get(candidate.number)
        if final_entry is not None:
            row["final_score"] = final_entry.crash_likelihood
            row["final_reason"] = final_entry.reason
        candidates_payload.append(row)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "candidates": candidates_payload,
        "ranking": [asdict(entry) for entry in ranking],
        "projects": list(project_results),
    }


def write_report(path: Path, report: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
