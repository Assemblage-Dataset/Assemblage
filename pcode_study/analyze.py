"""Aggregate per-binary P-code stats and run the four analyses:
  1. Overall op frequency + Zipf / power-law fit
  2. Per-binary stability (JS divergence vs corpus mean)
  3. Op co-occurrence (pointwise mutual information within functions)
  4. Function-size scaling (op-mix as a function of function size bucket)

Outputs CSVs, plots, and a report.md.
"""

import argparse
import json
import math
import warnings
from collections import Counter, defaultdict
from pathlib import Path

warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import powerlaw
from scipy.spatial.distance import jensenshannon
from scipy.stats import entropy

ROOT = Path(__file__).parent
OUT_DIR = ROOT / "output"
REPORT_DIR = ROOT / "report"
REPORT_DIR.mkdir(parents=True, exist_ok=True)


def load_corpus(out_dir: Path):
    """Yield one parsed JSON per binary."""
    files = sorted(out_dir.glob("*.json"))
    print(f"loading {len(files)} per-binary JSONs from {out_dir}")
    bin_records = []
    fn_records = []
    for fp in files:
        try:
            with open(fp) as f:
                d = json.load(f)
        except Exception as e:
            print(f"skip {fp}: {e}")
            continue
        if d.get("n_decompiled", 0) == 0:
            continue
        bin_records.append({
            "binary_slug": fp.stem,
            "binary": d["binary"],
            "language": d.get("language"),
            "n_functions_total": d["n_functions_total"],
            "n_decompiled": d["n_decompiled"],
            "n_failed": d["n_failed"],
            "truncated": d.get("truncated", False),
            "duration_s": d.get("duration_s"),
            "op_totals": d["op_totals"],
        })
        for f in d.get("functions", []):
            fn_records.append({
                "binary_slug": fp.stem,
                "addr": f["addr"],
                "name": f["name"],
                "size_bytes": f["size_bytes"],
                "n_ops": f["n_ops"],
                "ops": f["ops"],
            })
    return bin_records, fn_records


def analysis_1_freq_and_zipf(bin_records, report_lines):
    print("\n[A1] overall op frequency + Zipf")
    corpus_total = Counter()
    for b in bin_records:
        corpus_total.update(b["op_totals"])
    df = pd.DataFrame(
        sorted(corpus_total.items(), key=lambda x: -x[1]),
        columns=["op", "count"],
    )
    df["rank"] = np.arange(1, len(df) + 1)
    df["freq"] = df["count"] / df["count"].sum()
    df["cum_freq"] = df["freq"].cumsum()
    df.to_csv(REPORT_DIR / "op_corpus_freq.csv", index=False)
    print(df.head(15).to_string(index=False))

    # log-log rank-frequency plot
    fig, ax = plt.subplots(figsize=(8, 6))
    ax.loglog(df["rank"].to_numpy(), df["count"].to_numpy(), "o-", markersize=4)
    for _, row in df.head(15).iterrows():
        ax.annotate(row["op"], (row["rank"], row["count"]),
                    textcoords="offset points", xytext=(4, 4), fontsize=8)
    ax.set_xlabel("rank")
    ax.set_ylabel("count")
    ax.set_title(f"P-code op rank-frequency  ({len(df)} ops, "
                 f"{df['count'].sum():,} total)")
    ax.grid(True, which="both", alpha=0.3)
    fig.tight_layout()
    fig.savefig(REPORT_DIR / "fig_rank_freq.png", dpi=140)
    plt.close(fig)

    # Power-law fit on the counts vector
    counts_arr = df["count"].values
    fit = powerlaw.Fit(counts_arr, discrete=True, verbose=False)
    R_exp, p_exp = fit.distribution_compare("power_law", "exponential",
                                            normalized_ratio=True)
    R_ln, p_ln = fit.distribution_compare("power_law", "lognormal",
                                          normalized_ratio=True)
    R_tpl, p_tpl = fit.distribution_compare("power_law", "truncated_power_law",
                                            normalized_ratio=True)

    # Also fit log-linear regression on the head (top-K) as a Zipf check.
    K = min(20, len(df))
    lx = np.log(df["rank"].values[:K])
    ly = np.log(df["count"].values[:K])
    slope, intercept = np.polyfit(lx, ly, 1)

    report_lines.append("### Analysis 1: Overall op frequency + Zipf\n")
    report_lines.append(f"- Unique P-code op types observed: **{len(df)}**")
    report_lines.append(f"- Total ops counted (sum across all binaries): "
                        f"**{int(df['count'].sum()):,}**")
    report_lines.append(f"- Top-10 ops cover **{df['cum_freq'].iloc[9]*100:.1f}%** of all ops")
    report_lines.append(f"- Top-5 ops cover **{df['cum_freq'].iloc[4]*100:.1f}%** of all ops")
    report_lines.append("")
    report_lines.append("**Top 15 by frequency**\n")
    report_lines.append("| rank | op | count | freq |")
    report_lines.append("|---:|---|---:|---:|")
    for _, row in df.head(15).iterrows():
        report_lines.append(f"| {row['rank']} | `{row['op']}` | {int(row['count']):,} | {row['freq']:.4f} |")
    report_lines.append("")
    report_lines.append(f"**Zipf head (top-{K}) log-log slope:** "
                        f"`{slope:.3f}` (Zipf's law predicts ≈ -1)")
    report_lines.append("")
    report_lines.append("**Power-law fit (Clauset et al. method)**")
    report_lines.append(f"- α (exponent on tail): `{fit.power_law.alpha:.3f}`")
    report_lines.append(f"- x_min (cutoff below which power-law not claimed): `{fit.power_law.xmin}`")
    report_lines.append(f"- vs exponential: R=`{R_exp:.2f}`, p=`{p_exp:.3g}` "
                        f"({'power-law preferred' if R_exp > 0 and p_exp < 0.1 else 'inconclusive/other'})")
    report_lines.append(f"- vs lognormal:   R=`{R_ln:.2f}`, p=`{p_ln:.3g}` "
                        f"({'power-law preferred' if R_ln > 0 and p_ln < 0.1 else 'inconclusive/other'})")
    report_lines.append(f"- vs truncated power-law: R=`{R_tpl:.2f}`, p=`{p_tpl:.3g}`")
    report_lines.append("")
    report_lines.append("![](fig_rank_freq.png)\n")

    return df, fit


def analysis_2_per_binary_stability(bin_records, op_corpus_df, report_lines):
    print("\n[A2] per-binary stability (Jensen-Shannon vs corpus mean)")
    ops = op_corpus_df["op"].tolist()
    op_idx = {o: i for i, o in enumerate(ops)}
    K = len(ops)

    mat = np.zeros((len(bin_records), K), dtype=np.float64)
    for i, b in enumerate(bin_records):
        for op, c in b["op_totals"].items():
            if op in op_idx:
                mat[i, op_idx[op]] = c
    row_sum = mat.sum(axis=1, keepdims=True)
    nonzero = (row_sum > 0).flatten()
    mat = mat[nonzero]
    row_sum = row_sum[nonzero]
    probs = mat / row_sum  # per-binary distribution over op types
    corpus_p = op_corpus_df["freq"].values  # same column order

    js = np.array([jensenshannon(p, corpus_p, base=2) for p in probs])  # in [0, 1]
    h = np.array([entropy(p, base=2) for p in probs])

    df_b = pd.DataFrame({
        "binary_slug": [b["binary_slug"] for b, ok in zip(bin_records, nonzero) if ok],
        "binary": [b["binary"] for b, ok in zip(bin_records, nonzero) if ok],
        "n_functions": [b["n_decompiled"] for b, ok in zip(bin_records, nonzero) if ok],
        "n_ops": row_sum.flatten().astype(int),
        "js_to_corpus": js,
        "entropy_bits": h,
    })
    df_b = df_b.sort_values("js_to_corpus")
    df_b.to_csv(REPORT_DIR / "per_binary_stability.csv", index=False)

    # Histograms
    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    axes[0].hist(js, bins=40, color="steelblue", edgecolor="white")
    axes[0].set_xlabel("Jensen-Shannon distance (bits) to corpus mean")
    axes[0].set_ylabel("# binaries")
    axes[0].set_title(f"Per-binary divergence (median={np.median(js):.3f})")
    axes[1].hist(h, bins=40, color="darkorange", edgecolor="white")
    axes[1].set_xlabel("entropy of op distribution (bits)")
    axes[1].set_ylabel("# binaries")
    axes[1].set_title(f"Per-binary entropy (median={np.median(h):.2f} bits, "
                      f"max possible={math.log2(K):.2f})")
    fig.tight_layout()
    fig.savefig(REPORT_DIR / "fig_stability.png", dpi=140)
    plt.close(fig)

    report_lines.append("### Analysis 2: Per-binary stability\n")
    report_lines.append(f"- Each binary's op-distribution compared to the corpus mean via Jensen-Shannon distance "
                        f"(0 = identical, 1 = orthogonal).")
    report_lines.append(f"- Median JS distance: **{np.median(js):.3f}**, "
                        f"95th pct: {np.percentile(js, 95):.3f}, "
                        f"max: {js.max():.3f}")
    report_lines.append(f"- Median op-distribution entropy: **{np.median(h):.2f} bits** "
                        f"(max possible with {K} ops = {math.log2(K):.2f} bits)")
    report_lines.append("")
    report_lines.append("**5 most typical binaries (lowest JS to corpus mean)**\n")
    report_lines.append("| binary | n_fns | n_ops | JS |")
    report_lines.append("|---|---:|---:|---:|")
    for _, r in df_b.head(5).iterrows():
        report_lines.append(f"| `{r['binary']}` | {r['n_functions']} | {r['n_ops']:,} | {r['js_to_corpus']:.3f} |")
    report_lines.append("")
    report_lines.append("**5 most atypical binaries (highest JS to corpus mean)**\n")
    report_lines.append("| binary | n_fns | n_ops | JS |")
    report_lines.append("|---|---:|---:|---:|")
    for _, r in df_b.tail(5).iloc[::-1].iterrows():
        report_lines.append(f"| `{r['binary']}` | {r['n_functions']} | {r['n_ops']:,} | {r['js_to_corpus']:.3f} |")
    report_lines.append("")
    report_lines.append("![](fig_stability.png)\n")

    return df_b


def analysis_3_co_occurrence(fn_records, op_corpus_df, report_lines):
    print("\n[A3] op co-occurrence (PMI)")
    top_ops = op_corpus_df["op"].head(20).tolist()
    op_idx = {o: i for i, o in enumerate(top_ops)}
    K = len(top_ops)

    presence = np.zeros((len(fn_records), K), dtype=np.float32)
    for i, fn in enumerate(fn_records):
        for op in fn["ops"].keys():
            j = op_idx.get(op)
            if j is not None:
                presence[i, j] = 1.0

    n_fn = len(fn_records)
    p_single = presence.mean(axis=0)   # P(op_i appears in a function)
    # P(op_i AND op_j)
    joint = (presence.T @ presence) / n_fn
    # PMI = log( P(A,B) / (P(A) P(B)) )
    denom = np.outer(p_single, p_single)
    with np.errstate(divide="ignore", invalid="ignore"):
        pmi = np.log2(joint / denom)
        pmi[~np.isfinite(pmi)] = 0.0

    pmi_df = pd.DataFrame(pmi, index=top_ops, columns=top_ops)
    pmi_df.to_csv(REPORT_DIR / "op_pmi.csv")

    fig, ax = plt.subplots(figsize=(10, 9))
    im = ax.imshow(pmi, cmap="RdBu_r", vmin=-2, vmax=2)
    ax.set_xticks(np.arange(K))
    ax.set_xticklabels(top_ops, rotation=90, fontsize=8)
    ax.set_yticks(np.arange(K))
    ax.set_yticklabels(top_ops, fontsize=8)
    ax.set_title("P-code op pairwise PMI (top-20 by frequency)\n"
                 "blue = co-occur less than chance, red = more than chance")
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04, label="PMI (bits)")
    fig.tight_layout()
    fig.savefig(REPORT_DIR / "fig_pmi.png", dpi=140)
    plt.close(fig)

    # Extract notable pairs
    pairs = []
    for i in range(K):
        for j in range(i + 1, K):
            if np.isfinite(pmi[i, j]):
                pairs.append((top_ops[i], top_ops[j], pmi[i, j], joint[i, j]))
    pairs_df = pd.DataFrame(pairs, columns=["op_a", "op_b", "pmi", "p_joint"])
    pairs_df = pairs_df.sort_values("pmi")
    pairs_df.to_csv(REPORT_DIR / "op_pairs.csv", index=False)

    report_lines.append("### Analysis 3: Op co-occurrence (PMI)\n")
    report_lines.append(f"Over **{n_fn:,}** functions, presence of each top-20 op was tabulated and "
                        f"pairwise pointwise mutual information computed:\n")
    report_lines.append("**Top 5 most positively associated pairs (op_a AND op_b co-occur more than chance)**\n")
    report_lines.append("| op_a | op_b | PMI (bits) | P(joint) |")
    report_lines.append("|---|---|---:|---:|")
    for _, r in pairs_df.tail(5).iloc[::-1].iterrows():
        report_lines.append(f"| `{r['op_a']}` | `{r['op_b']}` | {r['pmi']:+.2f} | {r['p_joint']:.3f} |")
    report_lines.append("")
    report_lines.append("**Top 5 most negatively associated pairs**\n")
    report_lines.append("| op_a | op_b | PMI (bits) | P(joint) |")
    report_lines.append("|---|---|---:|---:|")
    for _, r in pairs_df.head(5).iterrows():
        report_lines.append(f"| `{r['op_a']}` | `{r['op_b']}` | {r['pmi']:+.2f} | {r['p_joint']:.3f} |")
    report_lines.append("")
    report_lines.append("![](fig_pmi.png)\n")


def analysis_4_size_scaling(fn_records, op_corpus_df, report_lines):
    print("\n[A4] function-size scaling")
    top_ops = op_corpus_df["op"].head(10).tolist()
    sizes = np.array([fn["n_ops"] for fn in fn_records], dtype=np.float64)
    sizes = sizes[sizes > 0]
    # quantile buckets on n_ops
    qs = np.quantile(sizes, [0, 0.2, 0.4, 0.6, 0.8, 1.0])
    qs[0] = max(1, qs[0])
    bins = [(qs[i], qs[i + 1]) for i in range(len(qs) - 1)]
    labels = [f"Q{i+1}\n[{int(lo)}-{int(hi)}]" for i, (lo, hi) in enumerate(bins)]

    # Aggregate op share per bucket
    rows = []
    for (lo, hi), lbl in zip(bins, labels):
        agg = Counter()
        total = 0
        n_fns = 0
        for fn in fn_records:
            if lo <= fn["n_ops"] <= hi:
                agg.update(fn["ops"])
                total += fn["n_ops"]
                n_fns += 1
        if total == 0:
            continue
        row = {"bucket": lbl, "lo": lo, "hi": hi, "n_fns": n_fns, "total_ops": total}
        for op in top_ops:
            row["share_" + op] = agg.get(op, 0) / total
        rows.append(row)
    df_s = pd.DataFrame(rows)
    df_s.to_csv(REPORT_DIR / "size_scaling.csv", index=False)

    fig, ax = plt.subplots(figsize=(11, 6))
    x = np.arange(len(df_s))
    bottom = np.zeros(len(df_s))
    cmap = plt.get_cmap("tab10")
    for k, op in enumerate(top_ops):
        col = "share_" + op
        ax.bar(x, df_s[col].values, bottom=bottom, label=op,
               color=cmap(k / 10), edgecolor="white", linewidth=0.5)
        bottom = bottom + df_s[col].values
    ax.set_xticks(x)
    ax.set_xticklabels(df_s["bucket"], fontsize=9)
    ax.set_ylabel("share of total ops (top-10 ops shown)")
    ax.set_xlabel("function size bucket (quintiles by n_ops)")
    ax.set_title("Op-mix vs function size — is the distribution scale-invariant?")
    ax.legend(loc="center left", bbox_to_anchor=(1.0, 0.5), fontsize=8)
    fig.tight_layout()
    fig.savefig(REPORT_DIR / "fig_size_scaling.png", dpi=140)
    plt.close(fig)

    # Quantify: JS distance between bucket-1 and bucket-5
    cols = ["share_" + o for o in top_ops]
    p1 = df_s.iloc[0][cols].values.astype(float)
    p5 = df_s.iloc[-1][cols].values.astype(float)
    p1 /= p1.sum() if p1.sum() else 1
    p5 /= p5.sum() if p5.sum() else 1
    js15 = jensenshannon(p1, p5, base=2)

    report_lines.append("### Analysis 4: Function-size scaling\n")
    report_lines.append(f"Functions binned by n_ops quintile. Op-mix in each quintile reported as share-of-total.\n")
    report_lines.append("| bucket | range | n_fns | total ops |")
    report_lines.append("|---|---|---:|---:|")
    for _, r in df_s.iterrows():
        report_lines.append(f"| {r['bucket'].replace(chr(10), ' ')} | [{int(r['lo'])}–{int(r['hi'])}] | "
                            f"{int(r['n_fns']):,} | {int(r['total_ops']):,} |")
    report_lines.append("")
    report_lines.append(f"**JS distance Q1 vs Q5 (over top-10 ops):** `{js15:.3f}` "
                        f"(closer to 0 = scale-invariant; closer to 1 = different)\n")
    report_lines.append("![](fig_size_scaling.png)\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    args = ap.parse_args()

    bin_records, fn_records = load_corpus(Path(args.out_dir))
    print(f"loaded: {len(bin_records)} binaries, {len(fn_records)} functions")

    report = []
    report.append(f"# P-code op distribution study — Assemblage Linux ELF corpus\n")
    report.append(f"- Binaries with at least one decompiled function: **{len(bin_records):,}**")
    report.append(f"- Total decompiled functions: **{len(fn_records):,}**")
    truncated = sum(1 for b in bin_records if b.get("truncated"))
    report.append(f"- Binaries hitting the per-binary time budget (truncated): **{truncated}**\n")

    op_df, fit = analysis_1_freq_and_zipf(bin_records, report)
    analysis_2_per_binary_stability(bin_records, op_df, report)
    analysis_3_co_occurrence(fn_records, op_df, report)
    analysis_4_size_scaling(fn_records, op_df, report)

    out = REPORT_DIR / "report.md"
    out.write_text("\n".join(report))
    print(f"\nReport written -> {out}")


if __name__ == "__main__":
    main()
