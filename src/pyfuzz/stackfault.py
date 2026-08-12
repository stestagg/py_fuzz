from __future__ import annotations

from dataclasses import dataclass
import re

from .analysis import Artifact, list_artifacts
from .project import Project


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[mK]")
_FAULT_ADDR_RE = re.compile(r"fault address=0x([0-9a-fA-F]+)")
_REG_RE = re.compile(r"^\s*(?:sp|rsp)\s+=\s+0x([0-9a-fA-F]+)\b", re.MULTILINE)
_FRAME_NO_RE = re.compile(r"\bframe #(\d+):")
_TOP_FRAME_RE = re.compile(r"\*\s*frame #0:.*")
_MEM_REGION_BLOCK_RE = re.compile(
    r"\(lldb\) memory region (\S+)\n(.*?)(?=\n\(lldb\)|\Z)", re.DOTALL
)
_REGION_RANGE_RE = re.compile(r"\[0x([0-9a-fA-F]+)-0x([0-9a-fA-F]+)\)\s*([a-zA-Z-]*)")
_SEGFAULT_RE = re.compile(r"stop reason = (?:signal )?SIG(?:SEGV|BUS)\b")

# Small offsets off a NULL pointer (e.g. a NULL-checked struct field access)
# fault at low addresses like 0x9, 0x18, 0x30 ... These are never a stack
# guard-page fault (the stack lives at a large, ASLR-randomized address), so a
# fault address below this is only treated as stack-related if it also lands
# close to the observed stack pointer.
_NULL_DEREF_ADDR_THRESHOLD = 0x10000


@dataclass(frozen=True)
class StackFaultAnalysis:
    classification: str
    score: int
    signals: tuple[str, ...]
    factors: tuple[str, ...]
    fault_address: int | None = None
    stack_pointer: int | None = None
    max_frame: int | None = None

    @property
    def likely(self) -> bool:
        return self.classification == "likely"

    def render(self) -> str:
        lines = [
            f"classification: {self.classification}",
            f"score: {self.score}",
        ]
        if self.fault_address is not None:
            lines.append(f"fault_address: 0x{self.fault_address:x}")
        if self.stack_pointer is not None:
            lines.append(f"stack_pointer: 0x{self.stack_pointer:x}")
        if self.max_frame is not None:
            lines.append(f"max_frame: {self.max_frame}")
        lines.append("signals:")
        lines.extend(f"- {factor}" for factor in self.factors)
        return "\n".join(lines) + "\n"


def _parse_hex(regex: re.Pattern[str], text: str) -> int | None:
    match = regex.search(text)
    if match is None:
        return None
    try:
        return int(match.group(1), 16)
    except ValueError:
        return None


def classify_lldb_stack_fault(lldb_text: str) -> StackFaultAnalysis:
    """Score whether LLDB output looks like a stack-growth segfault.

    This is intentionally heuristic. The goal is to surface artifacts worth a
    closer look, not to prove root cause from text alone.
    """
    text = _ANSI_RE.sub("", lldb_text)
    score = 0
    # Each entry is (is_positive, text): is_positive marks signals that count
    # toward "likely" (scored evidence); negative entries are veto signals
    # that argue against a stack fault regardless of score.
    signals: list[tuple[bool, str]] = []

    if _SEGFAULT_RE.search(text):
        score += 1
        signals.append((True, "process stopped on SIGSEGV/SIGBUS"))

    fault_address = _parse_hex(_FAULT_ADDR_RE, text)
    stack_pointer = _parse_hex(_REG_RE, text)

    close_to_sp = False
    if fault_address is not None and stack_pointer is not None:
        distance = abs(fault_address - stack_pointer)
        if distance <= 1024 * 1024:
            score += 3
            close_to_sp = True
            signals.append(
                (True, f"fault address is {distance} bytes from stack pointer")
            )
        elif distance <= 8 * 1024 * 1024:
            score += 1
            close_to_sp = True
            signals.append(
                (True, f"fault address is within 8 MiB of stack pointer ({distance} bytes)")
            )

    # A non-null fault address alone says nothing about whether the stack was
    # involved — a wild/NULL pointer dereference elsewhere in the interpreter
    # is just as capable of producing one. Only treat a low fault address as
    # neutral-or-worse: if it's small and nowhere near the stack pointer, it
    # is almost certainly a NULL/small-offset dereference, so veto the
    # classification outright rather than let backtrace-shape signals below
    # carry it to "likely".
    null_deref_override = False
    if (
        fault_address is not None
        and 0 < fault_address < _NULL_DEREF_ADDR_THRESHOLD
        and not close_to_sp
    ):
        null_deref_override = True
        signals.append((
            False,
            f"fault address 0x{fault_address:x} is near-null and far from the "
            "stack pointer — looks like a NULL/small-offset dereference, not "
            "a stack-growth fault",
        ))

    sp_region = None
    fault_region = None
    for region_arg, block in _MEM_REGION_BLOCK_RE.findall(text):
        range_match = _REGION_RANGE_RE.search(block)
        if range_match is None:
            continue
        low = int(range_match.group(1), 16)
        high = int(range_match.group(2), 16)
        perms = range_match.group(3)
        if region_arg == "$sp":
            sp_region = (low, high, perms)
        else:
            fault_region = (low, high, perms)

    if fault_region is not None:
        fault_low, _fault_high, fault_perms = fault_region
        is_unmapped = not any(c in fault_perms for c in "rwx")
        if fault_low == 0:
            null_deref_override = True
            signals.append((
                False,
                "fault address's unmapped region starts at 0x0 — NULL/small-offset "
                "dereference, not stack-related",
            ))
        elif is_unmapped and sp_region is not None:
            sp_low = sp_region[0]
            if fault_address is not None and fault_address <= sp_low:
                gap = sp_low - fault_address
                if gap <= 1024 * 1024:
                    score += 4
                    signals.append((
                        True,
                        f"fault address sits in the unmapped hole {gap} bytes below "
                        "the mapped stack region — consistent with a failed "
                        "stack-growth page fault",
                    ))

    if sp_region is not None:
        sp_low, sp_high, sp_perms = sp_region
        sp_unmapped = not any(c in sp_perms for c in "rwx")
        if sp_unmapped and stack_pointer is not None and sp_low <= stack_pointer < sp_high:
            score += 4
            signals.append((
                True,
                "stack pointer is in an unmapped memory region — consistent with "
                "a failed stack-growth page fault",
            ))
            if fault_address is not None and sp_low <= fault_address < sp_high:
                score += 2
                signals.append((
                    True,
                    "fault address is in the same unmapped memory region as the "
                    "stack pointer",
                ))

    top_frame = _TOP_FRAME_RE.search(text)
    if top_frame is not None and any(
        name in top_frame.group(0)
        for name in (
            "_PyEval_EvalFrameDefault",
            "_Py_CheckRecursiveCall",
            "_Py_EnterRecursiveCall",
        )
    ):
        score += 2
        signals.append((True, "top frame is evaluator or recursion checking code"))

    frame_numbers = [int(m.group(1)) for m in _FRAME_NO_RE.finditer(text)]
    max_frame = max(frame_numbers) if frame_numbers else None
    if max_frame is not None:
        if max_frame >= 512:
            score += 2
            signals.append((True, f"very deep backtrace ({max_frame + 1} frames)"))
        elif max_frame >= 128:
            score += 1
            signals.append((True, f"deep backtrace ({max_frame + 1} frames)"))

    eval_frames = text.count("_PyEval_EvalFrameDefault")
    import_frames = text.count("PyImport_ImportModuleLevelObject")
    builtin_import_frames = text.count("builtin___import__")
    if eval_frames >= 100:
        score += 2
        signals.append((True, f"many evaluator frames ({eval_frames})"))
    elif eval_frames >= 25:
        score += 1
        signals.append((True, f"repeated evaluator frames ({eval_frames})"))

    if import_frames >= 20 or builtin_import_frames >= 20:
        score += 2
        signals.append((
            True,
            "recursive import pattern "
            f"(PyImport={import_frames}, builtin___import__={builtin_import_frames})",
        ))

    pressure_terms = (
        "RLIMIT_AS",
        "MemoryError",
        "failed to map segment",
        "Cannot allocate memory",
        "Unrecoverable stack overflow",
        "Stack overflow",
    )
    matched_terms = [term for term in pressure_terms if term in text]
    if matched_terms:
        score += 1
        signals.append((True, "memory/stack pressure hint: " + ", ".join(matched_terms)))

    if null_deref_override:
        classification = "unlikely"
    elif score >= 6:
        classification = "likely"
    elif score >= 3:
        classification = "possible"
    else:
        classification = "unlikely"

    return StackFaultAnalysis(
        classification=classification,
        score=score,
        signals=tuple(text for _is_positive, text in signals),
        factors=tuple(
            ("+" if is_positive else "-") + text for is_positive, text in signals
        ),
        fault_address=fault_address,
        stack_pointer=stack_pointer,
        max_frame=max_frame,
    )


async def analyze_stack_fault_artifacts(
    project: Project,
    artifact_hashes: list[str] | None = None,
    *,
    write: bool = False,
    dest: str = "stackfault.txt",
) -> list[tuple[Artifact, StackFaultAnalysis | None]]:
    artifacts = await list_artifacts(project)
    if artifact_hashes is not None:
        wanted = set(artifact_hashes)
        artifacts = [artifact for artifact in artifacts if artifact.hash in wanted]

    results: list[tuple[Artifact, StackFaultAnalysis | None]] = []
    for artifact in sorted(artifacts, key=lambda item: item.hash):
        lldb_text = artifact.lldb_output
        analysis = classify_lldb_stack_fault(lldb_text) if lldb_text else None
        if write and analysis is not None:
            (artifact.dir / dest).write_text(analysis.render())
        results.append((artifact, analysis))
    return results
