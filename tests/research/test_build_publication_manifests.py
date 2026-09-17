import json
import tempfile
import unittest
from pathlib import Path

from scripts.research.build_publication_manifests import build, sha256, write_manifests


class PublicationManifestTests(unittest.TestCase):
    def test_binds_only_urban_figures_and_all_analysis_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            research, analysis, figures, repo = (root / name for name in ("research", "analysis", "figures", "repo"))
            for directory in (research, analysis, figures, repo / "tools"):
                directory.mkdir(parents=True)
            (research / "run_matrix.urban_dynamic_v3.plan.json").write_text("{}", encoding="utf-8")
            (research / "environment-manifest.json").write_text("{}", encoding="utf-8")
            (analysis / "summary.json").write_text('{"n":270}', encoding="utf-8")
            (repo / "tools" / "analyze.py").write_text("print(1)", encoding="utf-8")
            for stem in ("figure_1_synthetic_intersection", "figure_c_calibration_prr",
                         "figure_d_behavioral_collisions", "figure_e_sionna_no_ray_sentinel"):
                (figures / f"{stem}.png").write_bytes(stem.encode())
                (figures / f"{stem}.svg").write_bytes(stem.encode())
            a, f = build("urban_dynamic_v3", research, analysis, figures, repo, ["tools/analyze.py"])
            write_manifests(analysis, figures, a, f)
            saved = json.loads((figures / "figure-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["input_analysis_manifest_sha256"], sha256(analysis / "analysis-manifest.json"))
            self.assertEqual(a["output_files_sha256"]["summary.json"], sha256(analysis / "summary.json"))
            with self.assertRaises(FileExistsError):
                write_manifests(analysis, figures, a, f)
            (figures / "figure_a_emergency_prr.png").write_bytes(b"cross-cohort")
            with self.assertRaisesRegex(ValueError, "wrong or incomplete figure set"):
                build("urban_dynamic_v3", research, analysis, figures, repo, ["tools/analyze.py"])


if __name__ == "__main__":
    unittest.main()
