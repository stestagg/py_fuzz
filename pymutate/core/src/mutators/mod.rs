//! Sub-mutator abstraction.
//!
//! A [`SubMutator`] inspects a parsed Python module and proposes **at most one**
//! [`Edit`] per call. The driver in [`crate::mutate`] picks a single sub-mutator
//! per invocation and applies its single edit — see that function for why we
//! keep mutations small and attributable.
//!
//! Adding a new mutation strategy is just: implement [`SubMutator`], then add it
//! to [`registry`].

use ruff_python_ast::visitor::Visitor;
use ruff_python_ast::Mod;
use ruff_text_size::TextRange;

use crate::rng::Rng;

pub mod arg_spray;
pub mod attr_wrap;
pub mod bignum;
pub mod del_insert;
pub mod line_dup;
pub mod name_subst;
pub mod operator_swap;
pub mod self_rebind;
pub mod splat_spray;
pub mod trickydata;
pub mod type_swap;

/// Candidate identifiers for the name-based mutators (`name_subst`, `attr_wrap`,
/// `arg_spray`), baked into the binary. Editing this file broadens coverage
/// without touching mutator logic; it is `include_str!`'d exactly once here.
static NAMES_DICT: &str = include_str!("../names.dict");

/// Parse `names.dict` into a list of candidate identifiers (skip blanks and `#`
/// comments). Shared by every mutator that needs an identifier pool.
pub(crate) fn load_name_dict() -> Vec<&'static str> {
    NAMES_DICT
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty() && !line.starts_with('#'))
        .collect()
}

/// The leading whitespace of the physical line containing `offset`, but only
/// when `offset` sits at the first non-whitespace column of that line.
///
/// This is what the insertion-based mutators use to indent a sibling statement
/// correctly. Returning `None` for a non-line-leading offset guards against
/// inline compound bodies (`if x: y = 1`): there `offset` points at `y`, whose
/// naive line prefix would be `"if x: "` — splicing that as "indent" would
/// duplicate the header, so we simply decline those sites.
pub(crate) fn line_indent(source: &str, offset: usize) -> Option<&str> {
    let line_start = source[..offset].rfind('\n').map_or(0, |nl| nl + 1);
    let indent = &source[line_start..offset];
    if indent.bytes().all(|b| b == b' ' || b == b'\t') {
        Some(indent)
    } else {
        None
    }
}

/// Drive a [`Visitor`] over a whole module. The visitor trait operates on
/// statements/expressions, not the `Mod` wrapper, so this dispatches over its
/// two shapes (a statement body vs. a single expression).
pub fn walk_module<'a, V: Visitor<'a>>(visitor: &mut V, module: &'a Mod) {
    match module {
        Mod::Module(m) => {
            for stmt in &m.body {
                visitor.visit_stmt(stmt);
            }
        }
        Mod::Expression(e) => visitor.visit_expr(&e.body),
    }
}

/// A single, localized source replacement described in byte offsets.
///
/// `range` is a byte range into the (UTF-8) source that produced it, so it is
/// always on valid char boundaries — it comes from a real AST node.
pub struct Edit {
    pub range: TextRange,
    /// Name of the sub-mutator that produced this edit (for `describe()` / logs).
    pub kind: &'static str,
    pub replacement: String,
}

/// Everything a sub-mutator needs about the current input: the original source
/// and its parsed module (possibly containing error nodes for malformed input).
pub struct AstCtx<'a> {
    pub source: &'a str,
    pub module: &'a Mod,
}

pub trait SubMutator {
    fn name(&self) -> &'static str;

    /// Produce exactly one edit, or `None` if this sub-mutator has nothing to do
    /// for this input (e.g. no `Name` nodes present).
    fn mutate(&self, ctx: &AstCtx, rng: &mut Rng) -> Option<Edit>;
}

/// The set of sub-mutators the driver chooses from. Order does not matter — the
/// driver shuffles before picking.
pub fn registry() -> Vec<Box<dyn SubMutator>> {
    vec![
        Box::new(arg_spray::ArgSpray::new()),
        Box::new(attr_wrap::AttrWrap::new()),
        Box::new(bignum::BigNum::new()),
        Box::new(del_insert::DelInsert::new()),
        Box::new(line_dup::LineDup::new()),
        Box::new(name_subst::NameSubstitution::new()),
        Box::new(operator_swap::OperatorSwap::new()),
        Box::new(self_rebind::SelfRebind::new()),
        Box::new(splat_spray::SplatSpray::new()),
        Box::new(trickydata::TrickyData::new()),
        Box::new(type_swap::TypeSwap::new()),
    ]
}

/// The names of the registered sub-mutators, in registry order. Handy for a
/// one-line startup banner (see the AFL shim's `init`).
pub fn registry_names() -> Vec<&'static str> {
    registry().iter().map(|m| m.name()).collect()
}
