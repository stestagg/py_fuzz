from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

from pyfuzz.trackscript import get_pid_output

_CAP_IDX_MAGIC = 0xF00D


def _idx_record(pid: int, ts_us: int, out_off: int, err_off: int) -> bytes:
    return struct.pack("<HIQQQ", _CAP_IDX_MAGIC, pid, ts_us, out_off, err_off)


class GetPidOutputTests(unittest.TestCase):
    def _write(self, base: Path, idx: bytes, out: bytes, err: bytes) -> None:
        base.with_name(base.name + ".idx").write_bytes(idx)
        base.with_name(base.name + "-stdout.log").write_bytes(out)
        base.with_name(base.name + "-stderr.log").write_bytes(err)

    def test_slices_region_by_pid(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            base = Path(d) / "child"
            out = b"hello\nworld\n"
            err = b"errA\n"
            idx = _idx_record(100, 1, 0, 0) + _idx_record(200, 2, 6, 5)
            self._write(base, idx, out, err)

            # First child: bounded by the next record's offsets.
            self.assertEqual(get_pid_output(base, 100), (b"hello\n", b"errA\n"))
            # Last child: runs to EOF.
            self.assertEqual(get_pid_output(base, 200), (b"world\n", b""))

    def test_pid_reuse_takes_last(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            base = Path(d) / "child"
            out = b"hello\nworld\nagain\n"
            idx = (
                _idx_record(100, 1, 0, 0)
                + _idx_record(200, 2, 6, 0)
                + _idx_record(100, 3, 12, 0)
            )
            self._write(base, idx, out, b"")

            self.assertEqual(get_pid_output(base, 100), (b"again\n", b""))

    def test_missing_idx_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            base = Path(d) / "child"
            self.assertEqual(get_pid_output(base, 100), (b"", b""))

    def test_unknown_pid_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            base = Path(d) / "child"
            self._write(base, _idx_record(100, 1, 0, 0), b"hello\n", b"")
            self.assertEqual(get_pid_output(base, 999), (b"", b""))


if __name__ == "__main__":
    unittest.main()
