from __future__ import annotations

from typing import Iterable

from pydantic import BaseModel, Field

from .models import FinalRankingEntry, GroupAssessment, PullRequestCandidate

GROUP_TRIAGE_PROMPT = """You are triaging open CPython pull requests for fuzzing.

We want pull requests that are more likely than average to introduce a crash,
segfault, assertion failure, refcount bug, use-after-free, NULL dereference,
buffer bug, race-triggered crash, or other runtime safety issue.

Bias slightly toward recall over precision: if a PR plausibly affects
interpreter/runtime safety in compiled code, it is better to keep it than drop
it. Prioritize risky C/C++/Objective-C/assembly paths such as:
- refcount and object lifetime handling
- parser/compiler/runtime internals
- exception unwinding and cleanup logic
- buffer and memory management
- threading, GC, and interpreter state
- error handling paths and rarely exercised fast paths

Return exactly one assessment for every listed PR.
- `selected=true` for at most {max_picks} PRs in the group.
- `crash_likelihood` is 0-100.
- `reason` must be short, concrete, and grounded in the provided metadata.
"""

FINAL_RANKING_PROMPT = """You are ranking CPython pull requests by how likely they are to introduce a crash,
segfault, assertion failure, or similar runtime error.

Return every candidate exactly once, ordered from highest likelihood to lowest.
Use the original PR metadata and the stage-1 triage score/reason as signals, but
feel free to overturn a stage-1 judgment if the overall evidence points another
way. Keep reasons terse and concrete.
"""


class GroupAssessmentModel(BaseModel):
    list_index: int = Field(ge=1)
    pr_number: int = Field(ge=1)
    selected: bool
    crash_likelihood: int = Field(ge=0, le=100)
    reason: str = Field(min_length=1)


class GroupTriageResponse(BaseModel):
    assessments: list[GroupAssessmentModel]


class FinalRankingEntryModel(BaseModel):
    list_index: int = Field(ge=1)
    pr_number: int = Field(ge=1)
    crash_likelihood: int = Field(ge=0, le=100)
    reason: str = Field(min_length=1)


class FinalRankingResponse(BaseModel):
    ranking: list[FinalRankingEntryModel]


def _normalize_reason(text: str) -> str:
    return " ".join(text.split())


class OpenAIRanker:
    def __init__(
        self,
        *,
        model: str = "gpt-5.4",
        reasoning_effort: str = "medium",
        verbosity: str = "low",
    ) -> None:
        from openai import OpenAI

        self._client = OpenAI()
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.verbosity = verbosity

    def triage_group(
        self,
        candidates: list[PullRequestCandidate],
        *,
        prompt: str,
        max_picks: int,
    ) -> list[GroupAssessment]:
        response = self._client.responses.parse(
            model=self.model,
            instructions=GROUP_TRIAGE_PROMPT.format(max_picks=max_picks),
            input=prompt,
            max_output_tokens=4_000,
            reasoning={"effort": self.reasoning_effort},
            text_format=GroupTriageResponse,
            verbosity=self.verbosity,
        )
        parsed = response.output_parsed
        if parsed is None:
            raise RuntimeError("OpenAI group triage response did not contain parsed output.")
        assessments = self._validate_group_response(candidates, parsed.assessments, max_picks=max_picks)
        return [
            GroupAssessment(
                list_index=item.list_index,
                pr_number=item.pr_number,
                selected=item.selected,
                crash_likelihood=item.crash_likelihood,
                reason=_normalize_reason(item.reason),
            )
            for item in assessments
        ]

    def rank_candidates(
        self,
        candidates: list[PullRequestCandidate],
        *,
        prompt: str,
    ) -> list[FinalRankingEntry]:
        response = self._client.responses.parse(
            model=self.model,
            instructions=FINAL_RANKING_PROMPT,
            input=prompt,
            max_output_tokens=8_000,
            reasoning={"effort": self.reasoning_effort},
            text_format=FinalRankingResponse,
            verbosity=self.verbosity,
        )
        parsed = response.output_parsed
        if parsed is None:
            raise RuntimeError("OpenAI ranking response did not contain parsed output.")
        ranking = self._validate_final_ranking(candidates, parsed.ranking)
        return [
            FinalRankingEntry(
                list_index=item.list_index,
                pr_number=item.pr_number,
                crash_likelihood=item.crash_likelihood,
                reason=_normalize_reason(item.reason),
            )
            for item in ranking
        ]

    @staticmethod
    def _validate_group_response(
        candidates: list[PullRequestCandidate],
        assessments: Iterable[GroupAssessmentModel],
        *,
        max_picks: int,
    ) -> list[GroupAssessmentModel]:
        by_index = {index: candidate.number for index, candidate in enumerate(candidates, start=1)}
        seen: set[int] = set()
        validated: list[GroupAssessmentModel] = []
        for item in assessments:
            expected_pr = by_index.get(item.list_index)
            if expected_pr is None:
                raise RuntimeError(f"Group triage returned unexpected list index {item.list_index}.")
            if item.pr_number != expected_pr:
                raise RuntimeError(
                    f"Group triage mismatched PR number for list index {item.list_index}: "
                    f"expected {expected_pr}, got {item.pr_number}."
                )
            if item.list_index in seen:
                raise RuntimeError(f"Group triage returned duplicate list index {item.list_index}.")
            seen.add(item.list_index)
            validated.append(item)
        missing = sorted(set(by_index) - seen)
        if missing:
            raise RuntimeError(f"Group triage omitted candidate indices: {missing}")

        selected = [item for item in validated if item.selected]
        if len(selected) > max_picks:
            selected_indices = {
                item.list_index
                for item in sorted(
                    selected,
                    key=lambda assessment: assessment.crash_likelihood,
                    reverse=True,
                )[:max_picks]
            }
            for item in validated:
                if item.list_index not in selected_indices:
                    item.selected = False
        return sorted(validated, key=lambda item: item.list_index)

    @staticmethod
    def _validate_final_ranking(
        candidates: list[PullRequestCandidate],
        ranking: Iterable[FinalRankingEntryModel],
    ) -> list[FinalRankingEntryModel]:
        by_index = {index: candidate.number for index, candidate in enumerate(candidates, start=1)}
        seen: set[int] = set()
        validated: list[FinalRankingEntryModel] = []
        for item in ranking:
            expected_pr = by_index.get(item.list_index)
            if expected_pr is None:
                raise RuntimeError(f"Final ranking returned unexpected list index {item.list_index}.")
            if item.pr_number != expected_pr:
                raise RuntimeError(
                    f"Final ranking mismatched PR number for list index {item.list_index}: "
                    f"expected {expected_pr}, got {item.pr_number}."
                )
            if item.list_index in seen:
                raise RuntimeError(f"Final ranking returned duplicate list index {item.list_index}.")
            seen.add(item.list_index)
            validated.append(item)
        missing = sorted(set(by_index) - seen)
        if missing:
            raise RuntimeError(f"Final ranking omitted candidate indices: {missing}")
        return validated
