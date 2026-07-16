// DO NOT reflow — line numbers are frozen E2E ground truth
// (see tests/fixtures/repos/rust-golden/README.md).

pub fn add(a: i64, b: i64) -> i64 {
    a + b
}

pub fn pair_sum<T: std::ops::Add<Output = T>>(x: T, y: T) -> T {
    x + y
}

pub fn mix(s: &str) -> usize {
    s.len() + 1
}
