from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .github import GitHubClient
from .openai_ranker import OpenAIRanker
from .workflow import (
    apply_group_assessments,
    build_report,
    chunked,
    collect_candidates,
    create_projects_from_ranking,
    existing_pr_numbers,
    format_final_ranking_prompt,
    format_group_prompt,
    write_report,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pick open CPython PRs to fuzz using GitHub metadata plus GPT-5.4 triage.",
    )
    parser.add_argument("--candidate-count", type=int, default=100, help="How many fresh PR candidates to collect.")
    parser.add_argument("--group-size", type=int, default=20, help="How many PRs to send in each stage-1 group.")
    parser.add_argument("--stage1-max-picks", type=int, default=5, help="Maximum selected PRs per group.")
    parser.add_argument("--create-top", type=int, default=10, help="How many top-ranked PRs to create pyfuzz projects for.")
    parser.add_argument("--comments-per-pr", type=int, default=6, help="How many recent comments/reviews to include per PR.")
    parser.add_argument("--page-size", type=int, default=25, help="GitHub GraphQL page size when iterating PRs.")
    parser.add_argument("--github-owner", default="python", help="GitHub owner to scan.")
    parser.add_argument("--github-repo", default="cpython", help="GitHub repo to scan.")
    parser.add_argument("--model", default="gpt-5.4", help="OpenAI model to use for ranking.")
    parser.add_argument("--reasoning-effort", default="medium", help="Responses API reasoning effort.")
    parser.add_argument("--verbosity", default="low", help="Responses API verbosity.")
    parser.add_argument("--dry-run", action="store_true", help="Do everything except creating pyfuzz projects.")
    parser.add_argument("--output", type=Path, default=None, help="Optional JSON report path.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.group_size <= 0:
        raise SystemExit("--group-size must be positive.")
    if args.candidate_count <= 0:
        raise SystemExit("--candidate-count must be positive.")
    if args.stage1_max_picks <= 0:
        raise SystemExit("--stage1-max-picks must be positive.")
    if args.create_top <= 0:
        raise SystemExit("--create-top must be positive.")

    github = GitHubClient.from_env(owner=args.github_owner, repo=args.github_repo)
    ranker = OpenAIRanker(
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        verbosity=args.verbosity,
    )

    already_created = existing_pr_numbers()
    print(f"[1/4] Existing pr-* projects: {len(already_created)}", file=sys.stderr)

    candidates = collect_candidates(
        github.iter_candidate_prs(
            comments_per_pr=args.comments_per_pr,
            page_size=args.page_size,
            exclude_pr_numbers=already_created,
        ),
        limit=args.candidate_count,
    )
    if not candidates:
        print("No new qualifying PRs found.", file=sys.stderr)
        return 1

    print(f"[2/4] Collected {len(candidates)} fresh PR candidates", file=sys.stderr)

    staged_candidates = []
    groups = list(chunked(candidates, args.group_size))
    for group_number, group in enumerate(groups, start=1):
        print(
            f"[stage1] Group {group_number}/{len(groups)}: triaging {len(group)} PRs",
            file=sys.stderr,
        )
        prompt = format_group_prompt(group)
        assessments = ranker.triage_group(
            group,
            prompt=prompt,
            max_picks=args.stage1_max_picks,
        )
        staged_candidates.extend(apply_group_assessments(group, assessments))

    print(f"[3/4] Ranking {len(staged_candidates)} candidates", file=sys.stderr)
    final_prompt = format_final_ranking_prompt(staged_candidates)
    ranking = ranker.rank_candidates(staged_candidates, prompt=final_prompt)

    print(f"[4/4] Creating top {args.create_top} projects", file=sys.stderr)
    project_results = create_projects_from_ranking(
        ranking,
        top_n=args.create_top,
        dry_run=args.dry_run,
    )

    for index, entry in enumerate(ranking[: args.create_top], start=1):
        print(
            f"{index:>2}. PR #{entry.pr_number} score={entry.crash_likelihood:>3} "
            f"reason={entry.reason}",
        )

    if args.output is not None:
        report = build_report(staged_candidates, ranking, project_results)
        write_report(args.output, report)
        print(f"Wrote report to {args.output}", file=sys.stderr)

    created_count = sum(1 for result in project_results if result.get("created"))
    skipped_count = sum(1 for result in project_results if result.get("skipped"))
    print(
        f"Project results: created={created_count} skipped={skipped_count} dry_run={args.dry_run}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
