// DO NOT reflow — line numbers are frozen E2E ground truth
// (see tests/fixtures/repos/rust-golden/README.md).
use golden_lib::{add, mix, pair_sum};
use std::hint::black_box;

fn mul3(x: i64) -> i64 {
    x * 3
}

fn main() {
    let twice = |v: i64| v * 2;
    let a = black_box(add(black_box(2), black_box(3)));
    let m = black_box(mul3(black_box(a)));
    let t = black_box(twice(black_box(m)));
    let pi = black_box(pair_sum::<i64>(black_box(a), black_box(m)));
    let pf = black_box(pair_sum::<f64>(black_box(1.5_f64), black_box(2.5_f64)));
    let n = black_box(mix(black_box("hello")));
    println!("{a} {m} {t} {pi} {pf} {n}");
}
