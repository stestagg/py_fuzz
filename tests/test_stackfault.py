import unittest

from pyfuzz.stackfault import classify_lldb_stack_fault


def _recursive_import_lldb(frame_count: int = 700) -> str:
    frames = []
    for i in range(frame_count):
        if i % 6 == 0:
            frames.append(
                f"    frame #{i}: python`_PyEval_EvalFrameDefault(...) at ceval.c:1229"
            )
        elif i % 6 == 1:
            frames.append(
                f"    frame #{i}: python`PyImport_ImportModuleLevelObject(...) at import.c:4249"
            )
        elif i % 6 == 2:
            frames.append(
                f"    frame #{i}: python`builtin___import__(...) at bltinmodule.c.h:111"
            )
        else:
            frames.append(f"    frame #{i}: python`some_other_frame(...)")
    return "\n".join(
        [
            "(lldb) thread list",
            "Process 75 stopped",
            "* thread #1: stop reason = signal SIGSEGV: address not mapped to object "
            "(fault address=0x0000fffffff7df8c)",
            "(lldb) bt all",
            "* frame #0: python`_PyEval_EvalFrameDefault(...) at ceval.c:1229",
            *frames,
            "(lldb) register read",
            "General Purpose Registers:",
            "        sp = 0x0000fffffff7d280",
        ]
    )


class StackFaultClassificationTests(unittest.TestCase):
    def test_classifies_recursive_import_near_sp_as_likely_stack_fault(self) -> None:
        analysis = classify_lldb_stack_fault(_recursive_import_lldb())

        self.assertEqual(analysis.classification, "likely")
        self.assertGreaterEqual(analysis.score, 6)
        self.assertEqual(analysis.fault_address, 0x0000FFFF_FFF7_DF8C)
        self.assertEqual(analysis.stack_pointer, 0x0000FFFF_FFF7_D280)
        self.assertTrue(any("fault address is" in signal for signal in analysis.signals))
        self.assertTrue(
            any("recursive import pattern" in signal for signal in analysis.signals)
        )

    def test_classifies_near_null_fault_with_deep_backtrace_as_unlikely(self) -> None:
        # Real-world shape: a NULL/small-offset dereference (fault
        # address=0x9) deep inside a recursive import. Before the veto, the
        # backtrace-shape signals alone pushed this to "likely" even though
        # the fault address is nowhere near the stack pointer.
        text = _recursive_import_lldb()
        text = text.replace("fault address=0x0000fffffff7df8c)", "fault address=0x9)")

        analysis = classify_lldb_stack_fault(text)

        self.assertEqual(analysis.classification, "unlikely")
        self.assertEqual(analysis.fault_address, 0x9)
        self.assertTrue(
            any("near-null and far from the stack pointer" in signal for signal in analysis.signals)
        )
        self.assertFalse(any(signal == "non-null fault address" for signal in analysis.signals))

    def test_memory_region_gap_below_stack_is_strong_signal(self) -> None:
        text = "\n".join(
            [
                "* thread #1: stop reason = signal SIGSEGV: address not mapped to object "
                "(fault address=0x0000fffffff7c000)",
                "(lldb) bt all",
                "* frame #0: python`_PyEval_EvalFrameDefault(...) at ceval.c:1229",
                "(lldb) register read",
                "        sp = 0x0000fffffff7d000",
                "(lldb) memory region $sp",
                "[0x0000fffffff7d000-0x0000fffffff9d000) rw-",
                "(lldb) memory region 0x0000fffffff7c000",
                "[0x0000fffffff00000-0x0000fffffff7d000) ---",
            ]
        )

        analysis = classify_lldb_stack_fault(text)

        self.assertTrue(
            any("unmapped hole" in signal for signal in analysis.signals)
        )

    def test_memory_region_starting_at_zero_is_null_deref(self) -> None:
        text = "\n".join(
            [
                "* thread #1: stop reason = signal SIGSEGV: address not mapped to object "
                "(fault address=0x9)",
                "(lldb) bt all",
                "* frame #0: python`_PyEval_EvalFrameDefault(...) at ceval.c:1229",
                "(lldb) register read",
                "        sp = 0x0000fffffff7d000",
                "(lldb) memory region $sp",
                "[0x0000fffffff7d000-0x0000fffffff9d000) rw-",
                "(lldb) memory region 0x9",
                "[0x0000000000000000-0x0000aaaaaaaa0000) ---",
            ]
        )

        analysis = classify_lldb_stack_fault(text)

        self.assertEqual(analysis.classification, "unlikely")
        self.assertTrue(
            any("unmapped region starts at 0x0" in signal for signal in analysis.signals)
        )

    def test_classifies_shallow_null_deref_as_unlikely(self) -> None:
        text = "\n".join(
            [
                "* thread #1: stop reason = signal SIGSEGV: address not mapped to object "
                "(fault address=0x0000000000000000)",
                "(lldb) bt all",
                "* frame #0: python`dict_contains(...) at dictobject.c:5235",
                "    frame #1: python`PyDict_Contains(...) at dictobject.c:5254",
                "(lldb) register read",
                "        sp = 0x0000fffffff7d280",
            ]
        )

        analysis = classify_lldb_stack_fault(text)

        self.assertEqual(analysis.classification, "unlikely")
        self.assertFalse(any("fault address is" in signal for signal in analysis.signals))


if __name__ == "__main__":
    unittest.main()
