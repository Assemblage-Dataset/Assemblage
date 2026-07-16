# Frozen wire-format goldens

Captured 2026-07-15 from the pre-re-architecture `mq/messages.py` inside the
worker image (`refactor-baseline-v0` + P1). The typed-message rewrite must
satisfy **dict-equality** against these files (key order is irrelevant; every
consumer `json.loads`es).

## Frozen facts

**Queues** (names on the wire; direction relative to the coordinator):

| queue | consumer | message fixture |
|---|---|---|
| `scrape` | coordinator | `scraper_data_out_bundle` — a **bare JSON array**, bundle size `SCRAPER_REPO_BUNDLESIZE=25` in production |
| `clone` | coordinator | `clone_status_msg_in` |
| `build` | coordinator | `build_status_msg_in` |
| `binary` | coordinator | `binary_task_msg_in` |
| `builder_reg` | coordinator | `builder_reg_in` (props: `reply_to=builder_ctrl_{uuid}`, `correlation_id={uuid}`) |
| `scraper_reg` | coordinator | `scraper_control_task_in` (same props pattern) |
| `build_opt_{id}` | builder | `builder_task_out`, published to topic exchange `build_opt` with routing key `build_opt_{id}` |
| `builder_ctrl_{uuid}` / `scraper_ctrl_{uuid}` | worker | `builder_reg_out` / `scraper_control_task_out_*`; non-durable, auto-delete |

## Sanctioned evolutions

**2026-07-16 — `BuilderRegistration` v2 (Rust rollout R1).** The registration
message gained two additive fields with defaults:

- `codegen_backend: str = ""` — rustc codegen backend (`llvm` / `cranelift` /
  `gcc`); native C/C++ toolchains keep `""`.
- `build_mode: str = "RelWithDebInfo"` — mirrors `buildopt.build_type`.

Rationale: each Rust (backend × mode × flag) combination is its own buildopt
row, so both fields join the registration identity (7 → 9 columns).
Compatibility across mixed-era queues:

- **Old JSON, new coordinator**: `builder_reg_in.json` stays byte-frozen and is
  the backward-compat fixture — it must always parse, with the defaults filling
  the new fields (so pre-evolution C builders keep matching their live rows).
- **New JSON, old coordinator**: receivers ignore unknown keys, so the two new
  keys are dropped harmlessly.
- `builder_reg_v2.json` is the golden for the evolved serialization (a Rust
  example: `compiler=rustc`, `language=rust`, `codegen_backend=llvm`,
  `build_mode=RelWithDebInfo`); dict-equality round-trip tests point at it.

**Enum wire values** are the lowercase member *values* (`enum_wire_values.json`).
The database, by contrast, stores member *names* (`'SUCCESS'`) in varchar
columns — see `backend/alembic/README.md`.

**Known wire quirks, frozen deliberately:**
- `ScraperControlTaskOut.qualifiers` is always `null` (the constructor
  discards its argument — bug kept on the wire for compatibility; the field
  may carry real values after the rewrite since it was never read).
- `BuilderTaskOut.output_dir` / `mod_timestamp` are written but never read by
  the builder; the rewrite drops them (receivers ignore unknown/missing keys).
- Clone-status messages are parsed via `BuildStatus(msg.status)` — works only
  because the two enums share value strings; pinned by test.

**S3 layout** (bucket → key):
- `project-archive` → `{owner}/{project}/{commit}.tar.gz` and `{owner}/{project}/latest.txt`
- `artifacts` → `{owner}_{project}_{commit[:12]}_{compiler}_{flag}/<binary>` and `.../assemblage_meta.json`

**Metadata JSON keys** (consumed by the dataset pipeline; do not rename):
`Platform, Build_mode, Compiler, Compiler_version, URL, Commit, Optimization,
Pushed_at, compiler_flag, language, library` + `Binary_info_list`
(`file`, `functions[].function_name/source_file/intersect_ratio`,
`functions[].function_info[].rva_start/rva_end`,
`functions[].lines[].line_number/rva/length/source_code/source_file`).
