from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PRComment:
    author: str
    created_at: str
    body: str
    source: str


@dataclass
class PullRequestCandidate:
    number: int
    title: str
    body: str
    url: str
    head_sha: str
    changed_files: list[str]
    comments: list[PRComment] = field(default_factory=list)
    group_index: int | None = None
    crash_likelihood: int | None = None
    selected: bool = False
    reason: str | None = None

    @property
    def project_name(self) -> str:
        return f"pr-{self.number}"


@dataclass(frozen=True)
class GroupAssessment:
    list_index: int
    pr_number: int
    selected: bool
    crash_likelihood: int
    reason: str


@dataclass(frozen=True)
class FinalRankingEntry:
    list_index: int
    pr_number: int
    crash_likelihood: int
    reason: str
