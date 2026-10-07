import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("package_publication", ROOT / "scripts/research/package_publication.py")
package = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(package)


class PackagePublicationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.research = Path(self.tmp.name) / "research"; self.research.mkdir()
        self.repo = Path(self.tmp.name) / "frozen-ns3"; self.repo.mkdir()
        (self.repo / "input").write_text("frozen input", encoding="utf-8")
        self.input_hash = hashlib.sha256((self.repo / "input").read_bytes()).hexdigest()
        (self.research / "environment-manifest.json").write_text(json.dumps({"research_files_sha256": {"input": self.input_hash}}))
        self.jobs = []
        ordinal = 0
        for campaign, blocks, arms, powers in (
            ("emergency", range(1, 11), (None,), (-20, -15, -12, -9, -6, -3, 0, 3, 6, 9, 12, 15, 18, 20)),
            ("behavioral", range(1, 31), ("radar_only", "sionna_good", "sionna_bad", "native_good", "native_bad"), (None,)),
            ("calibration", range(1, 31), ("sionna_good", "sionna_bad", "native_good", "native_bad"), (None,))):
            for block in blocks:
                for arm in arms:
                    for power in powers:
                        ordinal += 1; run = self.research / "runs" / campaign / f"cell-{ordinal}"
                        (run / "artifacts").mkdir(parents=True)
                        log = run / "simulator.log"; log.write_text("complete")
                        record = {"name": "simulator.log", "bytes": log.stat().st_size,
                                  "sha256": hashlib.sha256(log.read_bytes()).hexdigest()}
                        job = {"ordinal": ordinal, "campaign": campaign, "block": block,
                               "out": "/research/" + run.relative_to(self.research).as_posix()}
                        manifest = {"status": package.STATUS, "pilot_excluded": False, "simulator_exit_code": 0,
                                    "inputs": {"input": self.input_hash}, "file_records": [record]}
                        if campaign == "emergency":
                            job["power_dbm"] = power; manifest.update(campaign="emergency_warning_power", power_dbm=power)
                        else:
                            job["arm"] = arm; manifest.update(arm=arm, cohort=("behavioral_intersection" if campaign == "behavioral" else "radio_calibration"))
                        manifest["block"] = block
                        (run / "manifest.json").write_text(json.dumps(manifest)); self.jobs.append(job)
        (self.research / "run_matrix.all.plan.json").write_text(json.dumps({"campaign": "all", "jobs": self.jobs}))
        (self.research / "run_matrix.all.progress.json").write_text(json.dumps({"campaign": "all", "failures": [], "blockers": [], "states": {str(n): "completed" for n in range(1, 411)}}))

    def test_qualify_requires_exact_completed_nonpilot_matrix(self):
        qualified = package.qualify(self.research)
        self.assertEqual(len(qualified), 412)
        target = self.research / "runs" / "emergency" / "cell-1" / "manifest.json"
        data = json.loads(target.read_text()); data["pilot_excluded"] = True; target.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "qualifying"):
            package.qualify(self.research)

    def test_execution_checkout_rejects_changed_or_inconsistent_input(self):
        scene = self.repo / "scenario.sumocfg"; scene.write_text("frozen scenario", encoding="utf-8")
        first = self.research / "runs" / "emergency" / "cell-1" / "manifest.json"
        first_data = json.loads(first.read_text()); first_data["inputs"][scene.name] = hashlib.sha256(scene.read_bytes()).hexdigest()
        first.write_text(json.dumps(first_data))
        qualified = package.qualify(self.research)
        self.assertEqual(len(package.validate_execution_checkout(self.research, self.repo, qualified[2:])), 2)
        target = self.research / "runs" / "emergency" / "cell-2" / "manifest.json"
        data = json.loads(target.read_text()); data["inputs"]["input"] = "b" * 64; target.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "inconsistent execution input"):
            package.validate_execution_checkout(self.research, self.repo, qualified[2:])

    def test_figures_cannot_mix_urban_outputs_into_highway_archive(self):
        analysis, figures = Path(self.tmp.name) / "analysis", Path(self.tmp.name) / "figures"
        analysis.mkdir(); figures.mkdir()
        plan_hash = hashlib.sha256((self.research / "run_matrix.all.plan.json").read_bytes()).hexdigest()
        environment_hash = hashlib.sha256((self.research / "environment-manifest.json").read_bytes()).hexdigest()
        output = analysis / "summary.json"; output.write_text("{}")
        analysis_manifest = {"schema": 1, "cohort": "frozen_highway", "input_plan_sha256": plan_hash,
                             "input_environment_manifest_sha256": environment_hash,
                             "output_files_sha256": {output.name: hashlib.sha256(output.read_bytes()).hexdigest()}}
        (analysis / "analysis-manifest.json").write_text(json.dumps(analysis_manifest))
        image = figures / "figure_c_calibration_prr.png"; image.write_bytes(b"not-an-image")
        (figures / "figure-manifest.json").write_text(json.dumps({"schema": 1, "cohort": "frozen_highway",
            "input_analysis_manifest_sha256": hashlib.sha256((analysis / "analysis-manifest.json").read_bytes()).hexdigest(),
            "files": {image.name: hashlib.sha256(image.read_bytes()).hexdigest()}}))
        with self.assertRaisesRegex(ValueError, "outside the frozen_highway"):
            package.validate_analysis_and_figures(analysis, figures, self.research, "frozen_highway", None)

    def test_analysis_manifest_rejects_changed_summary_bytes(self):
        analysis, figures = Path(self.tmp.name) / "analysis-hash", Path(self.tmp.name) / "figures-hash"
        analysis.mkdir(); figures.mkdir()
        output = analysis / "summary.json"; output.write_text("{}")
        manifest = {"schema": 1, "cohort": "frozen_highway",
            "input_plan_sha256": hashlib.sha256((self.research / "run_matrix.all.plan.json").read_bytes()).hexdigest(),
            "input_environment_manifest_sha256": hashlib.sha256((self.research / "environment-manifest.json").read_bytes()).hexdigest(),
            "output_files_sha256": {output.name: hashlib.sha256(output.read_bytes()).hexdigest()}}
        (analysis / "analysis-manifest.json").write_text(json.dumps(manifest))
        image = figures / "figure_a_emergency_prr.png"; image.write_bytes(b"image")
        (figures / "figure-manifest.json").write_text(json.dumps({"schema": 1, "cohort": "frozen_highway",
            "input_analysis_manifest_sha256": hashlib.sha256((analysis / "analysis-manifest.json").read_bytes()).hexdigest(),
            "files": {image.name: hashlib.sha256(image.read_bytes()).hexdigest()}}))
        output.write_text('{"changed": true}')
        with self.assertRaisesRegex(ValueError, "analysis output hash mismatch"):
            package.validate_analysis_and_figures(analysis, figures, self.research, "frozen_highway", None)

    def test_run_archive_rejects_undeclared_file(self):
        run = self.research / "runs" / "emergency" / "cell-1"
        (run / "artifacts" / "rogue.txt").write_text("not recorded")
        with self.assertRaisesRegex(ValueError, "undeclared"):
            package.declared_run_files(run)

    def test_analysis_and_figure_trees_reject_rogue_files(self):
        for root, manifest in ((Path(self.tmp.name) / "analysis-rogue", "analysis-manifest.json"),
                               (Path(self.tmp.name) / "figures-rogue", "figure-manifest.json")):
            root.mkdir(); (root / manifest).write_text("{}")
            declared = root / "summary.json"; declared.write_text("{}")
            (root / "rogue.txt").write_text("not declared")
            with self.assertRaisesRegex(ValueError, "undeclared"):
                package.exact_manifest_tree(root, manifest, {declared.name: hashlib.sha256(declared.read_bytes()).hexdigest()}, "test")


if __name__ == "__main__":
    unittest.main()
