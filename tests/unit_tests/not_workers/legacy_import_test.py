"""The legacy quarantine must still import cleanly.

DeepHistory (``scripts/build_deephistory.py`` -> ``assemblage.legacy.conan_strategy``)
and the frozen Windows strategy are excluded from lint/type gates, but the P7
move must not have broken their imports — the DeepHistory batch path depends on
them.
"""

import importlib
import importlib.util
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BUILD_DEEPHISTORY = _REPO_ROOT / "backend" / "scripts" / "build_deephistory.py"


class TestLegacyImports(unittest.TestCase):
    def test_conan_strategy_imports(self):
        importlib.import_module("assemblage.legacy.conan_strategy")

    def test_windows_strategy_imports(self):
        importlib.import_module("assemblage.legacy.windows.strategy")

    def test_build_deephistory_imports(self):
        spec = importlib.util.spec_from_file_location("build_deephistory", _BUILD_DEEPHISTORY)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertTrue(hasattr(module, "main"))


if __name__ == "__main__":
    unittest.main()
