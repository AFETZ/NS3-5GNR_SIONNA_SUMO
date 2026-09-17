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
                                "channel_audit": {"model": "V2V-Urban", "buildings_registered": 2},
                                "sionna_geometry_audit": {"antenna_z_m": 1.5, "mesh_center_z_m": 0.65,
                                                          "mesh_height_m": 1.3} if arm.startswith("sionna_") else None,
                                "scene_geometry": {"buildings": ["north", "south"]}, "inputs": {"input": "a" * 64}, "file_records": [record]}
                    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
                    jobs.append({"ordinal": ordinal, "campaign": campaign, "block": block, "arm": arm,
                                 "out": "/research/" + run.relative_to(self.research).as_posix()})
        (self.research / "run_matrix.urban.plan.json").write_text(json.dumps({"campaign": "urban", "channel_scenario": "V2V-Urban", "geometry_version": "grounded_vehicle_v2", "jobs": jobs}), encoding="utf-8")
        (self.research / "run_matrix.urban.progress.json").write_text(json.dumps({"campaign": "urban", "failures": [], "blockers": [], "states": {str(n): "completed" for n in range(1, 271)}}), encoding="utf-8")

    def test_qualify_requires_exact_completed_audited_urban_matrix(self):
        self.assertEqual(len(package.qualify(self.research)), 272)
        path = self.research / "runs" / "production" / "block-01" / "radar_only" / "manifest.json"
        data = json.loads(path.read_text(encoding="utf-8")); data["channel_audit"]["buildings_registered"] = 1
        path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "model/building"):
            package.qualify(self.research)

    def test_qualify_rejects_wrong_channel_plan(self):
        plan_path = self.research / "run_matrix.urban.plan.json"; plan = json.loads(plan_path.read_text(encoding="utf-8"))
        plan["channel_scenario"] = "V2V-Highway"; plan_path.write_text(json.dumps(plan), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "V2V-Urban"):
            package.qualify(self.research)


if __name__ == "__main__":
    unittest.main()
