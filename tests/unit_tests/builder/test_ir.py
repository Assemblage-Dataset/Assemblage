"""IR collection: scoping, crate attribution, packing, and backend capability.

The pure logic here decides how much data the corpus stores (repo-only vs all
crates is an ~18x difference, measured 2026-07-17), so it is worth pinning.
"""

import io
import json
import tarfile

import pytest
from assemblage.build.rust import (
    CraneliftAdapter,
    GccAdapter,
    LLVMAdapter,
    cargo_env,
    ride_along_emit_flag,
)
from assemblage.builder import ir
from assemblage.enums import IrScope, IrStage


class TestCrateAttribution:
    @pytest.mark.parametrize(
        ("filename", "expected"),
        [
            ("regex_syntax-1da876777453134b.ll", "regex_syntax"),
            ("golden_bin-cee9e1d8c8f66af0.mir", "golden_bin"),
            ("butt-31265d67ee536365.ll", "butt"),
            ("aho_corasick-27ba1b015e9df2a3.s", "aho_corasick"),
            # a dash that is NOT rustc's hex hash must survive
            ("some-crate.ll", "some_crate"),
            ("plain.ll", "plain"),
        ],
    )
    def test_crate_of(self, filename, expected):
        assert ir.crate_of(f"/t/deps/{filename}") == expected

    def test_package_and_crate_name_forms_unify(self):
        # cargo package `foo-bar` compiles to crate `foo_bar`
        assert ir.normalize_crate("foo-bar") == "foo_bar"
        assert ir.classify_scope("foo_bar", frozenset({"foo_bar"})) == "repo"
        assert ir.classify_scope("foo-bar", frozenset({"foo_bar"})) == "repo"

    def test_non_member_is_dependency(self):
        assert ir.classify_scope("serde", frozenset({"golden_lib"})) == "dependency"


class TestDiscoveryScoping:
    @staticmethod
    def _tree(tmp_path):
        deps = tmp_path / "release" / "deps"
        deps.mkdir(parents=True)
        (deps / "golden_lib-aaaa1111.ll").write_text("; repo llvm ir\n")
        (deps / "golden_lib-aaaa1111.mir").write_text("// repo mir\n")
        (deps / "serde-bbbb2222.ll").write_text("; dependency llvm ir\n")
        (deps / "serde-bbbb2222.mir").write_text("// dependency mir\n")
        return deps

    def test_repo_scope_drops_dependencies(self, tmp_path):
        self._tree(tmp_path)
        dumps = ir.discover_ride_along(
            str(tmp_path),
            [IrStage.LLVM_IR, IrStage.MIR],
            frozenset({"golden_lib"}),
            IrScope.REPO,
        )
        assert {d.crate for d in dumps} == {"golden_lib"}
        assert all(d.scope == "repo" for d in dumps)

    def test_all_scope_keeps_dependencies(self, tmp_path):
        self._tree(tmp_path)
        dumps = ir.discover_ride_along(
            str(tmp_path), [IrStage.LLVM_IR, IrStage.MIR], frozenset({"golden_lib"}), IrScope.ALL
        )
        assert {d.crate for d in dumps} == {"golden_lib", "serde"}

    def test_only_requested_stages_are_collected(self, tmp_path):
        self._tree(tmp_path)
        dumps = ir.discover_ride_along(
            str(tmp_path), [IrStage.LLVM_IR], frozenset({"golden_lib"}), IrScope.ALL
        )
        assert {d.stage for d in dumps} == {IrStage.LLVM_IR}

    def test_unpretty_stages_are_not_discovered_as_ride_along(self, tmp_path):
        self._tree(tmp_path)
        assert (
            ir.discover_ride_along(
                str(tmp_path), [IrStage.HIR], frozenset({"golden_lib"}), IrScope.ALL
            )
            == []
        )

    def test_missing_target_dir_is_not_an_error(self):
        assert ir.discover_ride_along("/nonexistent", [IrStage.MIR], frozenset(), IrScope.ALL) == []


class TestPacking:
    @staticmethod
    def _dumps(tmp_path):
        f1 = tmp_path / "golden_lib-aaaa1111.ll"
        f1.write_text("; ir\n" * 100)
        f2 = tmp_path / "golden_lib-aaaa1111.mir"
        f2.write_text("// mir\n" * 100)
        return [
            ir.IrDump(IrStage.LLVM_IR, "golden_lib", "repo", str(f1), f1.stat().st_size),
            ir.IrDump(IrStage.MIR, "golden_lib", "repo", str(f2), f2.stat().st_size),
        ]

    def test_one_tarball_per_stage_with_scoped_paths(self, tmp_path):
        bundle = ir.pack(self._dumps(tmp_path))
        assert set(bundle.tarballs) == {IrStage.LLVM_IR, IrStage.MIR}
        with tarfile.open(fileobj=io.BytesIO(bundle.tarballs[IrStage.LLVM_IR])) as tar:
            assert tar.getnames() == ["repo/golden_lib/golden_lib-aaaa1111.ll"]

    def test_gzip_actually_shrinks_ir_text(self, tmp_path):
        bundle = ir.pack(self._dumps(tmp_path))
        assert bundle.stored_bytes < bundle.raw_bytes

    def test_packing_is_deterministic(self, tmp_path):
        # mtime/uid are zeroed, so the same source gives the same bytes
        a = ir.pack(self._dumps(tmp_path))
        b = ir.pack(self._dumps(tmp_path))
        assert a.tarballs[IrStage.MIR] == b.tarballs[IrStage.MIR]

    def test_oversize_stage_is_dropped_whole_not_truncated(self, tmp_path):
        bundle = ir.pack(self._dumps(tmp_path), max_bytes=1)
        assert bundle.tarballs == {}
        # and it says so, rather than looking like a build with no IR
        assert set(bundle.skipped) == {"llvm-ir", "mir"}
        assert "IR_MAX_BYTES" in bundle.skipped["mir"]

    def test_manifest_reports_stages_and_costs(self, tmp_path):
        bundle = ir.pack(self._dumps(tmp_path))
        doc = json.loads(
            ir.manifest_bytes(bundle, toolchain="nightly-x", backend="llvm", scope="repo")
        )
        assert doc["Ir_scope"] == "repo"
        assert doc["Codegen_backend"] == "llvm"
        assert doc["Stages"]["llvm-ir"]["crates"] == ["golden_lib"]
        assert doc["Stages"]["llvm-ir"]["tarball"] == "llvm-ir.tar.gz"
        assert doc["Raw_bytes"] > 0

    def test_unreadable_file_is_skipped_not_fatal(self, tmp_path):
        dumps = self._dumps(tmp_path)
        dumps.append(ir.IrDump(IrStage.MIR, "ghost", "repo", str(tmp_path / "gone.mir"), 10))
        bundle = ir.pack(dumps)
        with tarfile.open(fileobj=io.BytesIO(bundle.tarballs[IrStage.MIR])) as tar:
            assert "repo/ghost/gone.mir" not in tar.getnames()


class TestBackendCapability:
    """A backend can only dump its own IR — this is not configurable."""

    def test_llvm_emits_llvm_ir_and_asm(self):
        caps = LLVMAdapter.ir_caps
        assert caps.supports(IrStage.LLVM_IR)
        assert caps.supports(IrStage.ASM)
        assert not caps.supports(IrStage.GIMPLE)
        assert not caps.supports(IrStage.CLIF)

    def test_cranelift_cannot_emit_llvm_ir_or_clif(self):
        # cranelift replaces LLVM; and the shipped cg_clif exposes no IR-writing
        # option (verified nightly-2026-06-15) — see CraneliftAdapter.
        caps = CraneliftAdapter.ir_caps
        assert not caps.supports(IrStage.LLVM_IR)
        assert not caps.supports(IrStage.CLIF)
        assert "not reachable" in caps.unsupported_reason[IrStage.CLIF]

    def test_gcc_emits_gimple_but_not_llvm_ir(self):
        caps = GccAdapter.ir_caps
        assert caps.supports(IrStage.GIMPLE)
        assert not caps.supports(IrStage.LLVM_IR)

    def test_every_backend_can_emit_frontend_stages(self):
        for adapter in (LLVMAdapter, CraneliftAdapter, GccAdapter):
            for stage in (IrStage.AST, IrStage.HIR, IrStage.THIR, IrStage.MIR):
                assert adapter.ir_caps.supports(stage), (adapter, stage)


class TestEmitFlag:
    def test_link_is_always_present_or_the_binary_disappears(self):
        flag = ride_along_emit_flag([IrStage.LLVM_IR, IrStage.MIR], LLVMAdapter())
        assert flag.startswith("--emit=link,")

    def test_unsupported_stages_are_dropped_not_fatal(self):
        # asking cranelift for llvm-ir yields no --emit at all rather than a broken build
        assert ride_along_emit_flag([IrStage.LLVM_IR], CraneliftAdapter()) is None

    def test_unpretty_stages_never_reach_emit(self):
        assert ride_along_emit_flag([IrStage.HIR, IrStage.THIR], LLVMAdapter()) is None

    def test_no_stages_means_no_flag(self):
        assert ride_along_emit_flag([], LLVMAdapter()) is None


class TestCargoEnvIntegration:
    def _env(self, stages):
        return cargo_env(
            build_mode="RelWithDebInfo",
            compiler_flag="-O2",
            adapter=LLVMAdapter(),
            cargo_home="/cargo",
            target_dir="/t",
            ir_stages=stages,
        )

    def test_ir_off_leaves_rustflags_untouched(self):
        # the default path must stay byte-identical to the pre-IR builder
        assert "--emit" not in self._env([])["RUSTFLAGS"]

    def test_ir_on_appends_emit_and_keeps_mangling(self):
        flags = self._env([IrStage.LLVM_IR, IrStage.MIR])["RUSTFLAGS"]
        assert "--emit=link,llvm-ir,mir" in flags
        assert "-Csymbol-mangling-version=v0" in flags

    def test_gcc_gimple_sets_dump_env(self):
        env = cargo_env(
            build_mode="RelWithDebInfo",
            compiler_flag="-O2",
            adapter=GccAdapter(),
            cargo_home="/cargo",
            target_dir="/t",
            ir_stages=[IrStage.GIMPLE],
        )
        assert env["CG_GCCJIT_DUMP_GIMPLE"] == "1"
        assert env["CG_GCCJIT_DUMP_TO_FILE"] == "1"
