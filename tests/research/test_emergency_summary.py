import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("emergency_summary", ROOT / "tools/analysis/emergency_summary.py")
summary = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(summary)


def receivers(event=True):
    rows = [{"receiver": "veh1", "first_eligible_tx_s": 1.0,
             "first_rx_observed": event, "first_eligible_rx_s": 2.0 if event else None,
             "first_rx_censored": not event, "first_rx_censor_s": None if event else 100.0}]
    rows.extend({"receiver": f"veh{i}", "first_eligible_tx_s": None,
                 "first_rx_observed": False, "first_rx_censored": False,
                 "first_eligible_rx_s": None, "first_rx_censor_s": None} for i in range(2, 20))
    return rows


class EmergencySummaryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.net = self.root / "net.xml"
        self.route = self.root / "route.xml"
        self.net.write_text("<net/>")
        self.route.write_text("<routes/>")
        self._make_complete_matrix()

    def _make_complete_matrix(self):
        for power in summary.POWERS:
            for block in summary.BLOCKS:
                run = summary._run_dir(self.root, power, block)
                artifacts = run / "artifacts"
                artifacts.mkdir(parents=True)
                records = []
                for name in ("eva-netstate.xml", "eva-collision.xml", "eva-veh2-MSG.csv"):
                    path = artifacts / name
                    path.write_text(f"{power}/{block}/{name}")
                    records.append({"name": name, "bytes": path.stat().st_size,
                                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
                log = run / "simulator.log"
                log.write_text(f"{power}/{block}/log")
                records.append({"name": "simulator.log", "bytes": log.stat().st_size,
                                "sha256": hashlib.sha256(log.read_bytes()).hexdigest()})
                manifest = {"status": "completed_pending_metric_audit", "pilot_excluded": False,
                            "campaign": "emergency_warning_power", "power_dbm": power, "block": block,
                            "planned_horizon_seconds": 100.0, "sionna_enabled": False,
                            "simulator_exit_code": 0, "inputs": {"locked-input": "a" * 64},
                            "file_records": records}
                (run / "manifest.json").write_text(json.dumps(manifest))

    @staticmethod
    def _analyze(run, net, route):
        power = int(run.parent.name.removeprefix("power-").removesuffix("dBm"))
        return {"horizon_s": 100.0, "receiver_count": 19, "eligible_tx": 20,
                "received_unique": 10 if power < 0 else 15,
                "prr": .5 if power < 0 else .75, "receivers": receivers()}

    def test_collect_requires_complete_hashed_matrix_and_excludes_pilots(self):
        with patch.object(summary.emergency, "analyze", side_effect=self._analyze):
            rows = summary.collect(self.root, self.net, self.route)
        self.assertEqual(len(rows), 140)
        self.assertEqual({row["power_dbm"] for row in rows}, set(summary.POWERS))
        self.assertTrue(all("pilot" not in Path(row["run_dir"]).parts for row in rows))
        target = summary._run_dir(self.root, -20, 1) / "artifacts/eva-netstate.xml"
        target.write_text("tampered")
        with patch.object(summary.emergency, "analyze", side_effect=self._analyze):
            with self.assertRaisesRegex(ValueError, "(size|hash) mismatch"):
                summary.collect(self.root, self.net, self.route)

    def test_summary_bootstraps_runs_and_adjacent_paired_blocks(self):
        rows = []
        for power in summary.POWERS:
            for block in summary.BLOCKS:
                prr = (summary.POWERS.index(power) + block) / 30
                rows.append({"power_dbm": power, "block": block, "run_dir": "synthetic",
                             "eligible_tx": 30, "received_unique": round(30 * prr), "prr": prr,
                             "receivers": receivers()})
        with patch.object(summary, "BOOTSTRAP_DRAWS", 100):
            result = summary.summarize(rows)
        self.assertEqual(result["production_runs"], 140)
        self.assertEqual(result["powers"][0]["prr_run_level"]["n_runs"], 10)
        self.assertTrue(result["powers"][0]["first_eligible_receiver_reception"]
                        ["primary_delay_from_first_eligibility"]["block_bootstrap_ci95"]["ci95_estimable"])
        self.assertEqual(len(result["adjacent_power_paired_differences"]), 13)
        self.assertAlmostEqual(result["adjacent_power_paired_differences"][0]["prr_run_level"]["estimate"], 1 / 30)

    def test_censored_kaplan_meier_p90_is_not_imputed_as_an_event(self):
        result = summary._km_p90(receivers(event=False), eligibility_relative=True)
        self.assertFalse(result["p90_estimable"])
        self.assertIsNone(result["p90_s"])
        self.assertEqual((result["events"], result["right_censored"], result["never_eligible"]), (0, 1, 18))

    def test_manifest_rejects_pilot_before_artifact_analysis(self):
        run = summary._run_dir(self.root, -20, 1)
        manifest_path = run / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["pilot_excluded"] = True
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "not a qualifying"):
            summary._manifest(run, -20, 1)

    def test_primary_p90_ci_is_not_reported_when_block_bootstrap_is_censored(self):
        cells = {block: {"receivers": receivers(event=False)} for block in summary.BLOCKS}
        with patch.object(summary, "BOOTSTRAP_DRAWS", 100):
            result = summary._bootstrap_km_p90(cells, 0)
        self.assertFalse(result["ci95_estimable"])
        self.assertIsNone(result["ci95_p90_s"])
        self.assertEqual(result["estimable_draw_fraction"], 0.0)


if __name__ == "__main__":
    unittest.main()
