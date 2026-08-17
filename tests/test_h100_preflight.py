from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "script" / "h100_preflight.py"
SPEC = importlib.util.spec_from_file_location("h100_preflight", SCRIPT)
assert SPEC and SPEC.loader
PREFLIGHT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREFLIGHT)


class H100PreflightTests(unittest.TestCase):
    def test_resolves_symbolic_revision_from_huggingface_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "hub"
            ref = cache / "models--org--model" / "refs" / "main"
            ref.parent.mkdir(parents=True)
            revision = "a" * 40
            ref.write_text(revision + "\n", encoding="utf-8")
            previous = os.environ.get("HF_HUB_CACHE")
            os.environ["HF_HUB_CACHE"] = str(cache)
            try:
                result = PREFLIGHT.resolve_model_revision("org/model", "main")
            finally:
                if previous is None:
                    os.environ.pop("HF_HUB_CACHE", None)
                else:
                    os.environ["HF_HUB_CACHE"] = previous
        self.assertTrue(result["immutable"])
        self.assertEqual(result["resolved_revision"], revision)

    def test_parses_numa_cpu_ranges(self) -> None:
        self.assertEqual(PREFLIGHT.parse_cpu_list("0-2,8,10-11"), [0, 1, 2, 8, 10, 11])

    def test_failure_still_writes_manifest_and_failure_log(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "artifacts"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--repo-root",
                    str(REPO),
                    "--model",
                    "org/model",
                    "--model-revision",
                    "main",
                    "--output-dir",
                    str(output),
                ],
                text=True,
                capture_output=True,
                check=False,
                timeout=30,
            )
            self.assertNotEqual(completed.returncode, 0)
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "failed")
            self.assertIn("model_revision", {item["check"] for item in manifest["failures"]})
            self.assertTrue((output / "preflight.log").is_file())
            self.assertTrue((output / "failure.log").read_text(encoding="utf-8"))
            self.assertIn("cuda", manifest["checks"])
            self.assertIn("nsight", manifest["checks"])
            self.assertIn("numa_placement", manifest["checks"])

    def test_slurm_job_targets_full_h100_without_mem_or_gres(self) -> None:
        text = (REPO / "script" / "slurm" / "h100_preflight.sbatch").read_text(
            encoding="utf-8"
        )
        self.assertIn("#SBATCH --partition=ai-h100l", text)
        self.assertIn("#SBATCH --nodelist=ai-h100l-00", text)
        directives = [line for line in text.splitlines() if line.startswith("#SBATCH")]
        self.assertFalse(any("--mem" in line or "--gres" in line for line in directives))
        self.assertIn("H100_MODEL_REVISION", text)


if __name__ == "__main__":
    unittest.main()
