//! End-to-end tests over the real seed corpus plus adversarial inputs.
//!
//! These assert the invariants from the plan: the mutator never panics, never
//! exceeds `max_size`, keeps valid UTF-8 valid, is deterministic, and actually
//! changes inputs that contain `Name` nodes.

use std::path::{Path, PathBuf};

const MAX_SIZE: usize = 1 << 20;

/// Locate the repo's `testcases/` directory relative to this crate.
fn testcases_dir() -> PathBuf {
    // CARGO_MANIFEST_DIR = <repo>/pymutate/core
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../testcases")
        .canonicalize()
        .expect("testcases dir should exist")
}

/// Recursively collect every `.py` file under `dir`.
fn collect_py_files(dir: &Path, out: &mut Vec<PathBuf>) {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            collect_py_files(&path, out);
        } else if path.extension().is_some_and(|e| e == "py") {
            out.push(path);
        }
    }
}

#[test]
fn corpus_never_panics_and_respects_invariants() {
    let mut files = Vec::new();
    collect_py_files(&testcases_dir(), &mut files);
    assert!(!files.is_empty(), "expected to find seed .py files");

    let mut mutated_count = 0usize;
    for path in &files {
        let input = std::fs::read(path).expect("read seed");
        for seed in 0..32u32 {
            if let Some(out) = pymutate_core::mutate(&input, seed, MAX_SIZE) {
                mutated_count += 1;
                assert!(
                    out.len() <= MAX_SIZE,
                    "{}: output exceeded max_size",
                    path.display()
                );
                // Valid-UTF-8 input must yield valid-UTF-8 output (we only splice
                // ASCII candidate identifiers at char boundaries).
                if std::str::from_utf8(&input).is_ok() {
                    assert!(
                        std::str::from_utf8(&out).is_ok(),
                        "{}: produced invalid UTF-8",
                        path.display()
                    );
                }
            }
        }
    }
    // The corpus is full of Name-bearing code; we should have mutated plenty.
    assert!(mutated_count > 0, "expected at least some mutations");
}

#[test]
fn deterministic_for_same_seed() {
    let src = b"from decimal import Decimal\nx = Decimal('1')\nprint(x, len(x))\n";
    for seed in 0..16u32 {
        let a = pymutate_core::mutate(src, seed, MAX_SIZE);
        let b = pymutate_core::mutate(src, seed, MAX_SIZE);
        assert_eq!(a, b, "seed {seed} was not deterministic");
    }
}

#[test]
fn changes_input_with_names() {
    let src = b"print(value)\n";
    // Across seeds, at least one name substitution must change the input.
    let changed = (0..64u32).any(|seed| {
        pymutate_core::mutate_with(src, seed, MAX_SIZE, &["name_subst"])
            .map(|out| out.as_slice() != src.as_slice())
            .unwrap_or(false)
    });
    assert!(changed, "expected a Name substitution to change the input");
}

#[test]
fn type_swap_changes_a_literal_to_a_different_kind() {
    // Isolate TypeSwap: every output it produces for a lone literal must (a)
    // differ from the input and (b) be a literal whose *kind* differs — e.g. `42`
    // never becomes another int literal.
    use ruff_python_ast::{Expr, Mod, Number};
    use ruff_python_parser::{parse_unchecked, Mode, ParseOptions};

    /// Classify a single-expression module, or `None` if it isn't a lone int.
    fn is_int_literal(src: &str) -> bool {
        let parsed = parse_unchecked(src, ParseOptions::from(Mode::Module));
        let Mod::Module(m) = parsed.syntax() else {
            return false;
        };
        matches!(
            m.body.as_slice(),
            [stmt] if matches!(
                stmt.as_expr_stmt().map(|e| e.value.as_ref()),
                Some(Expr::NumberLiteral(n)) if matches!(n.value, Number::Int(_))
            )
        )
    }

    let src = b"42\n";
    let mut swapped = 0usize;
    for seed in 0..64u32 {
        if let Some(out) = pymutate_core::mutate_with(src, seed, MAX_SIZE, &["type_swap"]) {
            assert_ne!(out.as_slice(), src.as_slice(), "seed {seed}: no-op swap");
            let out_str = std::str::from_utf8(&out).unwrap();
            assert!(
                !is_int_literal(out_str.trim()),
                "seed {seed}: int was swapped for another int: {out_str:?}"
            );
            swapped += 1;
        }
    }
    assert!(swapped > 0, "expected TypeSwap to fire on a lone literal");
}

#[test]
fn operator_swap_can_change_an_operator() {
    // Isolate OperatorSwap: it may turn `aa < bb` into another comparison, an
    // arithmetic BinOp, or a boolean BoolOp — but the operator must change.
    use ruff_python_ast::{Expr, Mod};
    use ruff_python_parser::{parse_unchecked, Mode, ParseOptions};

    /// The operator spelling of a lone-expression statement, if it is a
    /// compare/binop/boolop; `None` for any other shape.
    fn top_op(src: &str) -> Option<String> {
        let parsed = parse_unchecked(src, ParseOptions::from(Mode::Module));
        let Mod::Module(m) = parsed.syntax() else {
            return None;
        };
        let [stmt] = m.body.as_slice() else {
            return None;
        };
        let expr = stmt.as_expr_stmt()?.value.as_ref();
        match expr {
            Expr::Compare(c) => Some(
                c.ops
                    .iter()
                    .map(|o| o.as_str())
                    .collect::<Vec<_>>()
                    .join(","),
            ),
            Expr::BinOp(b) => Some(b.op.as_str().to_string()),
            Expr::BoolOp(b) => Some(b.op.as_str().to_string()),
            _ => None,
        }
    }

    let src = b"aa < bb\n";
    let base = top_op("aa < bb\n");
    let changed = (0..128u32).any(|seed| {
        pymutate_core::mutate_with(src, seed, MAX_SIZE, &["operator_swap"])
            .and_then(|out| String::from_utf8(out).ok())
            .map(|s| top_op(&s) != base)
            .unwrap_or(false)
    });
    assert!(changed, "expected OperatorSwap to change the operator");
}

#[test]
fn operator_swap_keeps_boolean_operands_valid() {
    // Regression: `and`/`or` bind below `not`/comparison, so their operands can be
    // exprs (`not b`) that a tighter operator rejects — swapping `a and not b` to
    // `a < not b` is a SyntaxError. `and`/`or` therefore only swap with each
    // other. For this cleanly-parsing input every mutator's output must still
    // parse with zero errors: name_subst only rewrites operands (to valid
    // identifiers/singletons), type_swap can't fire (no literals), and
    // operator_swap must not cross `and` out of the boolean family.
    use ruff_python_parser::{parse_unchecked, Mode, ParseOptions};

    let src = b"a and not b\n";
    assert!(
        parse_unchecked(
            std::str::from_utf8(src).unwrap(),
            ParseOptions::from(Mode::Module)
        )
        .errors()
        .is_empty(),
        "test precondition: input should parse cleanly"
    );

    // Isolate operator_swap so no other (deliberately-invalidating) mutator can
    // muddy the result: every operator_swap output here must still parse.
    for seed in 0..256u32 {
        if let Some(out) = pymutate_core::mutate_with(src, seed, MAX_SIZE, &["operator_swap"]) {
            let s = std::str::from_utf8(&out).unwrap();
            let parsed = parse_unchecked(s, ParseOptions::from(Mode::Module));
            assert!(
                parsed.errors().is_empty(),
                "seed {seed}: mutation introduced a syntax error: {s:?}"
            );
        }
    }
}

#[test]
fn operator_swap_converts_assignment_to_augmented() {
    // `a = 1` has a simple single target, so operator_swap may turn its `=` into
    // an augmented form (`a += 1`, `a **= 1`, …). Detect that the top-level
    // statement became an AugAssign for at least one seed. (name_subst only
    // rewrites `a`/nothing else here; type_swap swaps the `1`; only assignment
    // swap changes the statement *kind*.)
    use ruff_python_ast::{Mod, Stmt};
    use ruff_python_parser::{parse_unchecked, Mode, ParseOptions};

    fn is_aug_assign(src: &str) -> bool {
        let parsed = parse_unchecked(src, ParseOptions::from(Mode::Module));
        let Mod::Module(m) = parsed.syntax() else {
            return false;
        };
        matches!(m.body.as_slice(), [Stmt::AugAssign(_)])
    }

    let src = b"a = 1\n";
    let became_aug = (0..128u32).any(|seed| {
        pymutate_core::mutate_with(src, seed, MAX_SIZE, &["operator_swap"])
            .and_then(|out| String::from_utf8(out).ok())
            .map(|s| is_aug_assign(&s))
            .unwrap_or(false)
    });
    assert!(became_aug, "expected `a = 1` to become an augmented assignment");
}

#[test]
fn operator_swap_leaves_multi_target_assignment_valid() {
    // Multi-target (`a = b = c`) and tuple-target (`a, b = c, d`) assignments have
    // no augmented form, so operator_swap must not touch their `=` (`a += b = c`
    // is a SyntaxError). Isolating operator_swap, every output must still parse.
    use ruff_python_parser::{parse_unchecked, Mode, ParseOptions};

    for src in [b"a = b = c\n".as_slice(), b"a, b = c, d\n".as_slice()] {
        assert!(
            parse_unchecked(
                std::str::from_utf8(src).unwrap(),
                ParseOptions::from(Mode::Module)
            )
            .errors()
            .is_empty(),
            "test precondition: input should parse cleanly"
        );
        for seed in 0..256u32 {
            if let Some(out) = pymutate_core::mutate_with(src, seed, MAX_SIZE, &["operator_swap"]) {
                let s = std::str::from_utf8(&out).unwrap();
                assert!(
                    parse_unchecked(s, ParseOptions::from(Mode::Module))
                        .errors()
                        .is_empty(),
                    "seed {seed}: mutation introduced a syntax error: {s:?}"
                );
            }
        }
    }
}

#[test]
fn malformed_inputs_do_not_panic() {
    // Truncated / broken sources: parse_unchecked should still yield a partial
    // tree, and we must never panic regardless of what mutate returns.
    let cases: &[&[u8]] = &[
        b"",
        b"def f(",
        b"x = 'unterminated",
        b"class C:\n    def m(self):\n        return foo(",
        b"(((((",
        b"import \n\n@@@\nfor for for",
        b"lambda: \xff\xfe not utf8 tail", // invalid UTF-8 -> should be None
        &[0xff, 0xfe, 0x00, 0x01],
    ];
    for case in cases {
        for seed in 0..16u32 {
            // Just must not panic.
            let _ = pymutate_core::mutate(case, seed, MAX_SIZE);
        }
    }
}

#[test]
fn respects_max_size() {
    // A tiny max_size forces edits that grow the buffer to be rejected (None)
    // rather than producing oversized output.
    let src = b"a = b\n";
    for seed in 0..64u32 {
        if let Some(out) = pymutate_core::mutate(src, seed, 4) {
            assert!(out.len() <= 4, "output exceeded tiny max_size: {out:?}");
        }
    }
}

#[test]
fn non_utf8_returns_none() {
    let bad = [0xff, 0xff, 0xfe, 0x00];
    for seed in 0..8u32 {
        assert!(pymutate_core::mutate(&bad, seed, MAX_SIZE).is_none());
    }
}
