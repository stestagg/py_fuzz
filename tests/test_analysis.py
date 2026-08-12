from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pyfuzz.analysis import _find_oom_kill

_OOM_LOG = """\
[ 5669.805249] fuzz_python invoked oom-killer: gfp_mask=0x140cca(GFP_HIGHUSER_MOVABLE|__GFP_COMP), order=0, oom_score_adj=0
[ 5669.837840] oom-kill:constraint=CONSTRAINT_NONE,nodemask=(null),task=fuzz_python,pid=16747,uid=0
[ 5669.837893] Out of memory: Killed process 16747 (fuzz_python) total-vm:1707336kB, anon-rss:1209556kB, file-rss:4kB, shmem-rss:264kB, UID:0 pgtables:2532kB oom_score_adj:0
[ 6896.397116] oom-kill:constraint=CONSTRAINT_NONE,nodemask=(null),task=fuzz_python,pid=21054,uid=0
[ 6896.397221] Out of memory: Killed process 21054 (fuzz_python) total-vm:2095052kB, anon-rss:952296kB, file-rss:4kB, shmem-rss:264kB, UID:0 pgtables:2004kB oom_score_adj:0
"""


class FindOomKillTests(unittest.TestCase):
    def _log(self, text: str) -> Path:
        d = Path(tempfile.mkdtemp())
        p = d / "kernel.log"
        p.write_text(text)
        return p

    def test_returns_victim_summary_for_killed_pid(self) -> None:
        line = _find_oom_kill(self._log(_OOM_LOG), 16747)
        self.assertIsNotNone(line)
        assert line is not None
        self.assertIn("Killed process 16747 (fuzz_python)", line)
        self.assertIn("anon-rss:1209556kB", line)

    def test_matches_the_right_pid(self) -> None:
        line = _find_oom_kill(self._log(_OOM_LOG), 21054)
        assert line is not None
        self.assertIn("Killed process 21054", line)
        self.assertNotIn("16747", line)

    def test_none_when_pid_not_a_victim(self) -> None:
        self.assertIsNone(_find_oom_kill(self._log(_OOM_LOG), 99999))

    def test_none_when_log_missing(self) -> None:
        self.assertIsNone(_find_oom_kill(Path("/no/such/kernel.log"), 16747))

    def test_falls_back_to_oom_kill_line_without_summary(self) -> None:
        text = "[ 1.0] oom-kill:constraint=CONSTRAINT_NONE,task=fuzz_python,pid=4242,uid=0\n"
        line = _find_oom_kill(self._log(text), 4242)
        assert line is not None
        self.assertIn("oom-kill:", line)
        self.assertIn("pid=4242", line)

    def test_substring_pid_does_not_false_match(self) -> None:
        # pid=1674 must not match a query for 16747 (or vice-versa).
        text = "[ 1.0] oom-kill:constraint=CONSTRAINT_NONE,task=fuzz_python,pid=1674,uid=0\n"
        self.assertIsNone(_find_oom_kill(self._log(text), 16747))


if __name__ == "__main__":
    unittest.main()
