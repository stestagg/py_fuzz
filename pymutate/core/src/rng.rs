//! Thin, seedable RNG wrapper.
//!
//! Wrapping `rand::rngs::StdRng` behind a small type keeps the mutation code
//! decoupled from the concrete generator and gives us a single place to add
//! helpers (uniform choice over a slice, etc.). Seeding is deterministic: the
//! same seed always yields the same sequence, which is what makes mutations
//! reproducible in tests and from the dev CLI.

use rand::rngs::StdRng;
use rand::{Rng as _, SeedableRng};

pub struct Rng {
    inner: StdRng,
}

impl Rng {
    /// Seed the generator from AFL's `u32` seed (widened to `u64`).
    pub fn from_seed(seed: u32) -> Self {
        Rng {
            inner: StdRng::seed_from_u64(seed as u64),
        }
    }

    /// Uniform index in `0..len`. Returns `None` when `len == 0`.
    pub fn index(&mut self, len: usize) -> Option<usize> {
        if len == 0 {
            None
        } else {
            Some(self.inner.gen_range(0..len))
        }
    }

    /// Pick a uniform reference from a non-empty slice, else `None`.
    pub fn choose<'a, T>(&mut self, items: &'a [T]) -> Option<&'a T> {
        self.index(items.len()).map(|i| &items[i])
    }

    /// Pick an index with probability proportional to `weights[i]`. Zero-weight
    /// entries are never chosen; `None` when every weight is zero (or empty).
    pub fn weighted_index(&mut self, weights: &[usize]) -> Option<usize> {
        let total: usize = weights.iter().sum();
        let mut pick = self.index(total)?;
        for (i, &w) in weights.iter().enumerate() {
            if pick < w {
                return Some(i);
            }
            pick -= w;
        }
        // Unreachable: `pick < total` and the weights sum to `total`.
        None
    }

    /// Draw `1..=max` with a halving distribution: 1 half the time, 2 a quarter
    /// of the time, and so on, with the tail collected at `max`.
    ///
    /// Used for the number of edits to stack, so the common case stays a single
    /// attributable edit while deeper combinations still get sampled.
    pub fn geometric(&mut self, max: usize) -> usize {
        let mut n = 1;
        while n < max && self.index(2) == Some(0) {
            n += 1;
        }
        n
    }
}
