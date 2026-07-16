# rust-golden — Rust E2E ground-truth fixture

A hermetic two-crate cargo workspace (no crates.io dependencies, `Cargo.lock`
committed) that the golden-repo E2E gate builds under `assemblage-rust:default`
at llvm `-O0` and `-O2`. The source line numbers below are **frozen ground
truth** asserted by `tests/e2e/injector.py`; the top-of-file banner in each
source says "DO NOT reflow" for that reason.

## Frozen line numbers

`golden_lib/src/lib.rs`

| item | signature | decl line | body line |
|---|---|---|---|
| `add` | `pub fn add(a: i64, b: i64) -> i64` | 4 | 5 (`    a + b`) |
| `pair_sum` | `pub fn pair_sum<T: std::ops::Add<Output = T>>(x: T, y: T) -> T` | 8 | 9 (`    x + y`) |
| `mix` | `pub fn mix(s: &str) -> usize` | 12 | 13 (`    s.len() + 1`) |

`golden_bin/src/main.rs`

| item | signature | decl line | body line |
|---|---|---|---|
| `mul3` | `fn mul3(x: i64) -> i64` | 6 | 7 (`    x * 3`) |
| `main` | `fn main()` | 10 | — |
| closure `twice` | `let twice = \|v: i64\| v * 2;` | 11 | — |

`main` calls `add`, `mul3`, `pair_sum::<i64>`, `pair_sum::<f64>`, `mix`, and the
`twice` closure, each wrapped in `std::hint::black_box(...)` so nothing is
optimised away and results are printed via `println!`.

## What the gate proves

- **Names / mangling**: symbol-mangling-version v0 — every fixture function's
  `function_name` starts with `_R` and its `demangled_name` is the full item
  path (`golden_lib::add`, `golden_bin::mul3`, `golden_lib::pair_sum::<i64>` vs
  `::<f64>` — two distinct monomorphisations, `golden_bin::main::{closure#0}`).
- **Origin**: all fixture functions classify `in_repo` (rustc emits
  comp_dir-relative paths that resolve under the clone dir).
- **Lines / source text**: `add` body (line 5) and `mul3` body (line 7) carry
  the exact fixture source text at `-O0`.
- **RVAs**: at `-O0`, `add` and `mul3` have nonempty DWARF ranges whose extent
  contains the ELF `.symtab` `st_value` for the matching v0 symbol.
- **Inlining at `-O2`**: `add`/`mul3`/`mix`/`pair_sum`/closure are inlined into
  `main` — each appears as a `DW_TAG_inlined_subroutine` whose RVA range is
  nested inside `main`'s range, and their standalone `.symtab` symbols are gone.

## Note on `{closure#0}` vs `{{closure}}`

Under v0 mangling, `rustfilt` demangles the closure to `...main::{closure#0}`
(single braces, indexed). The pre-v0 (`_ZN`/legacy) form was `{{closure}}`; the
gate asserts the `{closure#` shape that the v0 toolchain actually emits.
