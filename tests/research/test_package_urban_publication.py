import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("package_urban_publication", ROOT / "scripts/research/package_urban_publication.py")
package = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(package)


class PackageUrbanPublicationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.research = Path(self.tmp.name) / "research"; self.research.mkdir(); jobs = []; ordinal = 0
        self.repo = Path(self.tmp.name) / "frozen-ns3"; self.repo.mkdir()
        (self.repo / "input").write_text("frozen urban input", encoding="utf-8")
        self.input_hash = hashlib.sha256((self.repo / "input").read_bytes()).hexdigest()
        self.highway = self.research / "environment-manifest.highway-reference.json"
        self.highway.write_text(json.dumps({"research_files_sha256": {"input": self.input_hash}}))
        grounded = self.research / "environment-manifest.grounded-static-reference.json"
        grounded.write_text("{}", encoding="utf-8")
        grounded_progress = self.research / "run_matrix.grounded-static.progress.json"
        grounded_progress.write_text("{}", encoding="utf-8")
        (self.research / "environment-manifest.json").write_text(json.dumps({
            "study_variant": "post_freeze_v2v_urban_dynamic_replication",
            "research_files_sha256": {"input": self.input_hash},
            "highway_environment_reference_sha256": hashlib.sha256(self.highway.read_bytes()).hexdigest(),
            "grounded_static_environment_reference_sha256": hashlib.sha256(grounded.read_bytes()).hexdigest(),
            "grounded_static_progress_sha256": hashlib.sha256(grounded_progress.read_bytes()).hexdigest()}))
        for campaign, arms in (("urban_behavioral", package.ARMS), ("urban_calibration", package.ACTIVE_ARMS)):
            for block in range(1, 31):
                for arm in arms:
                    ordinal += 1; parent = "production" if campaign == "urban_behavioral" else "calibration-production"
                    run = self.research / "runs" / parent / f"block-{block:02d}" / arm; (run / "artifacts").mkdir(parents=True)
                    log = run / "simulator.log"; log.write_text("complete", encoding="utf-8")
                    record = {"name": "simulator.log", "bytes": log.stat().st_size, "sha256": hashlib.sha256(log.read_bytes()).hexdigest()}
                    manifest = {"status": package.STATUS, "pilot_excluded": False, "simulator_exit_code": 0,
                                "cohort": "behavioral_intersection" if campaign == "urban_behavioral" else "radio_calibration",
                                "arm": arm, "block": block, "channel_scenario": "V2V-Urban",
                                "geometry_version": "grounded_vehicle_v2",
                                "campaign_version": package.CAMPAIGN_VERSION,
                                "channel_condition_update_ms": package.CONDITION_UPDATE_MS,
                                "channel_audit": {"model": "V2V-Urban", "buildings_registered": 2,
                                                  "actual_condition_model": "ns3::ThreeGppV2vUrbanChannelConditionModel",
                                                  "channel_condition_update_ms": package.CONDITION_UPDATE_MS,
                                                  "three_gpp_channel_update_ms": 0,
                                                  "shadowing_enabled": False},
                                "sionna_geometry_audit": {"antenna_z_m": 1.5, "mesh_center_z_m": 0.65,
                                                          "mesh_height_m": 1.3} if arm.startswith("sionna_") else None,
                                "scene_geometry": {"buildings": ["north", "south"]}, "inputs": {"input": self.input_hash}, "file_records": [record]}
                    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
                    jobs.append({"ordinal": ordinal, "campaign": campaign, "block": block, "arm": arm,
                                 "out": "/research/" + run.relative_to(self.research).as_posix()})
        (self.research / package.PLAN_NAME).write_text(json.dumps({"campaign": package.CAMPAIGN_VERSION, "channel_scenario": "V2V-Urban", "geometry_version": "grounded_vehicle_v2", "channel_condition_update_ms": package.CONDITION_UPDATE_MS, "jobs": jobs}), encoding="utf-8")
        (self.research / package.PROGRESS_NAME).write_text(json.dumps({"campaign": package.CAMPAIGN_VERSION, "failures": [], "blockers": [], "states": {str(n): "completed" for n in range(1, 271)}}), encoding="utf-8")

    def test_qualify_requires_exact_completed_audited_urban_matrix(self):
        self.assertEqual(len(package.qualify(self.research)), 272)
        path = self.research / "runs" / "production" / "block-01" / "radar_only" / "manifest.json"
        data = json.loads(path.read_text(encoding="utf-8")); data["channel_audit"]["buildings_registered"] = 1
        path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "model/building"):
            package.qualify(self.research)

    def test_qualify_rejects_wrong_channel_plan(self):
        plan_path = self.research / package.PLAN_NAME; plan = json.loads(plan_path.read_text(encoding="utf-8"))
        plan["channel_scenario"] = "V2V-Highway"; plan_path.write_text(json.dumps(plan), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "V2V-Urban"):
            package.qualify(self.research)

    def test_execution_checkout_rejects_mismatched_bytes(self):
        scene = self.repo / "scenario.sumocfg"; scene.write_text("frozen urban scenario", encoding="utf-8")
        first = self.research / "runs" / "production" / "block-01" / "radar_only" / "manifest.json"
        first_data = json.loads(first.read_text()); first_data["inputs"][scene.name] = hashlib.sha256(scene.read_bytes()).hexdigest()
        first.write_text(json.dumps(first_data))
        qualified = package.qualify(self.research)
        self.assertEqual(len(package.validate_execution_checkout(self.research, self.repo, qualified[2:])), 2)
        (self.repo / "input").write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "frozen execution checkout"):
            package.validate_execution_checkout(self.research, self.repo, qualified[2:])

    def test_urban_figures_reject_highway_emergency_figure(self):
        analysis, figures = Path(self.tmp.name) / "analysis", Path(self.tmp.name) / "figures"
        analysis.mkdir(); figures.mkdir()
        output = analysis / "summary.json"; output.write_text("{}")
        analysis_manifest = {"schema": 1, "cohort": package.CAMPAIGN_VERSION,
            "input_plan_sha256": hashlib.sha256((self.research / package.PLAN_NAME).read_bytes()).hexdigest(),
            "input_environment_manifest_sha256": hashlib.sha256((self.research / "environment-manifest.json").read_bytes()).hexdigest(),
            "output_files_sha256": {output.name: hashlib.sha256(output.read_bytes()).hexdigest()}}
        (analysis / "analysis-manifest.json").write_text(json.dumps(analysis_manifest))
        image = figures / "figure_a_emergency_prr.png"; image.write_bytes(b"not-an-image")
        (figures / "figure-manifest.json").write_text(json.dumps({"schema": 1, "cohort": package.CAMPAIGN_VERSION,
            "input_analysis_manifest_sha256": hashlib.sha256((analysis / "analysis-manifest.json").read_bytes()).hexdigest(),
            "files": {image.name: hashlib.sha256(image.read_bytes()).hexdigest()}}))
        with self.assertRaisesRegex(ValueError, f"outside the {package.CAMPAIGN_VERSION}"):
            package.validate_analysis_and_figures(analysis, figures, self.research, None)

    def test_qualify_rejects_static_channel_condition(self):
        path = self.research / "runs" / "production" / "block-01" / "native_good" / "manifest.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["channel_audit"]["channel_condition_update_ms"] = 0
        path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "dynamic channel-condition"):
            package.qualify(self.research)

    def test_urban_run_archive_rejects_undeclared_file(self):
        run = self.research / "runs" / "production" / "block-01" / "radar_only"
        (run / "artifacts" / "rogue.txt").write_text("not recorded")
        with self.assertRaisesRegex(ValueError, "undeclared"):
            package.declared_run_files(run)

    def test_protocol_amendment_in_frozen_checkout_must_match_its_hash(self):
        amendment = self.repo / "input"; amendment.write_text("changed amendment")
        with self.assertRaisesRegex(ValueError, "protocol amendment"):
            package.validate_reference_provenance(self.research, self.repo, self.highway, amendment)

    def test_rejects_unrelated_highway_reference(self):
        wrong = self.research / "wrong-highway.json"
        wrong.write_text(json.dumps({"research_files_sha256": {"input": self.input_hash}}) + " ")
        with self.assertRaisesRegex(ValueError, "highway reference does not match"):
            package.validate_reference_provenance(self.research, self.repo, wrong, self.repo / "input")


if __name__ == "__main__":
    unittest.main()
