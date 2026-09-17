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
                                    "inputs": {"input": "a" * 64}, "file_records": [record]}
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


if __name__ == "__main__":
    unittest.main()
