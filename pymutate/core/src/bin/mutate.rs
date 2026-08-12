//! Dev CLI for eyeballing mutations without AFL.
//!
//! Usage:
//!   mutate [--seed N] [--max-size N] [--count K] [--max-edits N] [--weights] [FILE]
//!
//! Reads Python source from FILE (or stdin if omitted), applies the mutator, and
//! prints the result to stdout. With `--count K` it prints K mutations using
//! seeds `seed..seed+K` (separated by a marker) so you can scan a spread quickly.
//!
//!   cargo run -p pymutate-core --bin mutate -- --seed 1 ../testcases/decimal/1.py
//!   cargo run -p pymutate-core --bin mutate -- --count 5 ../testcases/decimal/1.py
//!   cargo run -p pymutate-core --bin mutate -- --only operator_swap example.py
//!   cargo run -p pymutate-core --bin mutate -- --max-edits 4 example.py
//!   cargo run -p pymutate-core --bin mutate -- --weights example.py

use std::io::{Read, Write};
use std::process::ExitCode;

fn main() -> ExitCode {
    let mut seed: u32 = 0;
    let mut max_size: usize = 1 << 20; // 1 MiB, generous for a dev tool
    let mut count: u32 = 1;
    // 1 = one edit per output, as `--only` and the unit tests assume. Raise it to
    // see what the AFL shim actually produces (it defaults to 4).
    let mut max_edits: usize = 1;
    // Report the driver's sub-mutator weighting for the input instead of mutating.
    let mut weights = false;
    let mut path: Option<String> = None;
    // Restrict to these sub-mutators (by name); empty means "use all of them".
    let mut only: Vec<String> = Vec::new();

    let mut args = std::env::args().skip(1);
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--seed" => seed = parse_next(&mut args, "--seed"),
            "--max-size" => max_size = parse_next(&mut args, "--max-size"),
            "--count" => count = parse_next(&mut args, "--count"),
            "--max-edits" => max_edits = parse_next(&mut args, "--max-edits"),
            "--weights" => weights = true,
            "--only" => only.push(parse_next(&mut args, "--only")),
            "-h" | "--help" => {
                eprintln!(
                    "usage: mutate [--seed N] [--max-size N] [--count K] \
                     [--max-edits N] [--weights] [--only NAME]... [FILE]\
                     \n\nsub-mutators: {}",
                    pymutate_core::mutators::registry_names().join(", ")
                );
                return ExitCode::SUCCESS;
            }
            other if other.starts_with('-') => {
                eprintln!("unknown flag: {other}");
                return ExitCode::FAILURE;
            }
            other => path = Some(other.to_string()),
        }
    }

    let input = match read_input(path.as_deref()) {
        Ok(bytes) => bytes,
        Err(err) => {
            eprintln!("error reading input: {err}");
            return ExitCode::FAILURE;
        }
    };

    let stdout = std::io::stdout();
    let mut out = stdout.lock();

    if weights {
        let report = pymutate_core::weight_report(&input, &[]);
        let total: usize = report.iter().map(|&(_, _, w)| w).sum();
        for (name, space, weight) in report {
            let share = if total == 0 {
                0.0
            } else {
                100.0 * weight as f64 / total as f64
            };
            let _ = writeln!(
                out,
                "{name:<14} space={space:<8} weight={weight:<3} {share:5.1}%"
            );
        }
        return ExitCode::SUCCESS;
    }

    for i in 0..count {
        let s = seed.wrapping_add(i);
        if count > 1 {
            let _ = writeln!(out, "===== seed {s} =====");
        }
        let result = if !only.is_empty() {
            // `--only` isolates one strategy, so it stays strictly one edit.
            let names: Vec<&str> = only.iter().map(String::as_str).collect();
            pymutate_core::mutate_with(&input, s, max_size, &names)
        } else {
            pymutate_core::mutate_stacked(&input, s, max_size, &[], max_edits)
        };
        match result {
            Some(bytes) => {
                let _ = out.write_all(&bytes);
                if !bytes.ends_with(b"\n") {
                    let _ = writeln!(out);
                }
            }
            None => {
                let _ = writeln!(out, "<no mutation (mutator returned None)>");
            }
        }
    }
    ExitCode::SUCCESS
}

fn parse_next<T: std::str::FromStr>(args: &mut impl Iterator<Item = String>, flag: &str) -> T {
    let raw = args
        .next()
        .unwrap_or_else(|| panic!("{flag} requires a value"));
    raw.parse()
        .unwrap_or_else(|_| panic!("invalid value for {flag}: {raw}"))
}

fn read_input(path: Option<&str>) -> std::io::Result<Vec<u8>> {
    match path {
        Some(p) => std::fs::read(p),
        None => {
            let mut buf = Vec::new();
            std::io::stdin().read_to_end(&mut buf)?;
            Ok(buf)
        }
    }
}
