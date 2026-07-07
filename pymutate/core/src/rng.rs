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

    /// In-place Fisher–Yates shuffle (used to visit sub-mutators in a random
    /// order without allocating a new collection).
    pub fn shuffle<T>(&mut self, items: &mut [T]) {
        for i in (1..items.len()).rev() {
            let j = self.inner.gen_range(0..=i);
            items.swap(i, j);
        }
    }
}
