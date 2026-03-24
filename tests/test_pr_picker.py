from __future__ import annotations

import unittest

from tools.pr_picker.github import is_compiled_source_path
from tools.pr_picker.models import PRComment, PullRequestCandidate
from tools.pr_picker.workflow import chunked, existing_pr_numbers, format_group_prompt


class PRPickerTests(unittest.TestCase):
    def test_is_compiled_source_path(self) -> None:
        self.assertTrue(is_compiled_source_path("Python/ceval.c"))
        self.assertTrue(is_compiled_source_path("Include/internal/pycore_gc.h"))
        self.assertTrue(is_compiled_source_path("Modules/_decimal/libmpdec/io.c"))
        self.assertFalse(is_compiled_source_path("Lib/test/test_decimal.py"))

    def test_chunked(self) -> None:
        candidates = [
            PullRequestCandidate(
                number=index,
                title=f"title {index}",
                body="body",
                url=f"https://example.invalid/{index}",
                head_sha=f"sha-{index}",
                changed_files=["Python/ceval.c"],
            )
            for index in range(1, 6)
        ]
        grouped = list(chunked(candidates, 2))
        self.assertEqual([[candidate.number for candidate in group] for group in grouped], [[1, 2], [3, 4], [5]])

    def test_existing_pr_numbers(self) -> None:
        class FakeEntry:
            def __init__(self, name: str, is_dir: bool = True) -> None:
                self.name = name
                self._is_dir = is_dir

            def is_dir(self) -> bool:
                return self._is_dir

        class FakeProjectsDir:
            def exists(self) -> bool:
                return True

            def iterdir(self):
                return iter(
                    [
                        FakeEntry("pr-123"),
                        FakeEntry("pr-456"),
                        FakeEntry("main"),
                        FakeEntry("pr-not-a-number"),
                        FakeEntry("notes.txt", is_dir=False),
                    ]
                )

        self.assertEqual(existing_pr_numbers(FakeProjectsDir()), {123, 456})

    def test_format_group_prompt_numbers_candidates(self) -> None:
        candidate = PullRequestCandidate(
            number=12345,
            title="Touch ceval cleanup",
            body="Adjust an error path in ceval and update a helper.",
            url="https://example.invalid/pr/12345",
            head_sha="deadbeef",
            changed_files=["Python/ceval.c", "Include/internal/pycore_frame.h"],
            comments=[
                PRComment(
                    author="core-dev",
                    created_at="2026-03-24T10:00:00Z",
                    body="This reworks the exception cleanup path.",
                    source="comment",
                )
            ],
        )
        prompt = format_group_prompt([candidate])
        self.assertIn("[1] PR #12345", prompt)
        self.assertIn("Python/ceval.c", prompt)
        self.assertIn("recent comments", prompt)


if __name__ == "__main__":
    unittest.main()
