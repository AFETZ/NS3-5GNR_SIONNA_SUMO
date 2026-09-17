import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("intersection_campaign", ROOT / "tools/analysis/intersection_campaign.py")
campaign = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(campaign)


class IntersectionCampaignTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.run = Path(self.tmp.name) / "block-05" / "baseline"; art = self.run / "artifacts"; art.mkdir(parents=True)
        (self.run / "manifest.json").write_text(json.dumps({"status": "completed_pending_metric_audit"}))
        (art / "eva-netstate.xml").write_text('<netstate><timestep time="0"><edge><lane id="a"><vehicle id="veh2" pos="1" speed="2"/><vehicle id="veh3" pos="3" speed="4"/></lane></edge></timestep><timestep time="19.95"><edge><lane id="a"><vehicle id="veh2" pos="2" speed="2"/><vehicle id="veh3" pos="4" speed="4"/></lane></edge></timestep></netstate>')
        (art / "eva-collision.xml").write_text('<collisions><collision collider="veh2" victim="veh3" time="6"/></collisions>')
        self._csv(art / "eva-veh2-MSG.csv", [dict(msg_type="CAM", tx_id="2", cam_gdt_ms="2000", tx_t_s="2")])
        self._csv(art / "eva-veh3-MSG.csv", [dict(msg_type="CAM", tx_id="2", rx_id="3", cam_gdt_ms="2000", rx_t_s="2.1", rx_ok="1")])
        self._csv(art / "eva-veh3-CTRL.csv", [dict(time_s="2.2", event_type="sensor_reaction")])

    def _csv(self, path, rows):
        with path.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=rows[0]); writer.writeheader(); writer.writerows(rows)

    def test_behavior_uses_application_link_and_sensor_first_action(self):
        row, _ = campaign.analyze_run(self.run, "behavior", "block-05", "native_good")
        self.assertEqual((row["collision"], row["first_cam_rx_s"], row["first_action_source"], row["radar_prr"]), (1, 2.1, "sensor_reaction", None))

    def test_rejects_wrong_netstate_end(self):
        path = self.run / "artifacts/eva-netstate.xml"
        path.write_text(path.read_text().replace('time="19.95"', 'time="19.9"'))
        with self.assertRaisesRegex(ValueError, "end at 19.95"):
            campaign.analyze_run(self.run, "behavior", "block-05", "native_good")

    def test_calibration_rejects_action(self):
        with self.assertRaisesRegex(ValueError, "controller action"):
            campaign.analyze_run(self.run, "calibration", "block-06", "native_good")

    def test_paired_discordance_is_one_pair_per_block(self):
        rows = [dict(kind="behavior", block="b", arm="a", collision=1), dict(kind="behavior", block="b", arm="z", collision=0)]
        result = campaign.summarize(rows)
        self.assertEqual(result["paired_discordance"][0]["pairs"], 1)

    def test_first_action_median_respects_no_action_censoring(self):
        rows = [dict(kind="behavior", block=f"b{i}", arm="a", collision=0,
                     first_action_s=value, first_action_source="cam_reaction" if value is not None else None)
                for i, value in enumerate((2.0, None, None))]
        action = campaign.summarize(rows)["first_action"]["a"]
        self.assertEqual((action["events"], action["right_censored"], action["median_s"]), (1, 2, None))
        self.assertEqual(action["source_counts"], {"cam_reaction": 1})

    def test_manifest_file_records_verify_hash_and_bytes(self):
        path = self.run / "artifacts/eva-collision.xml"
        manifest = self.run / "manifest.json"
        data = json.loads(manifest.read_text())
        data["file_records"] = [{"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}]
        manifest.write_text(json.dumps(data))
        self.assertEqual(campaign._manifest(self.run)["status"], "completed_pending_metric_audit")
        path.write_text("changed")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            campaign._manifest(self.run)

    def test_launcher_arm_defaults_and_sionna_sentinel(self):
        self.assertEqual(campaign.BEHAVIOR_ARMS, ("radar_only", "sionna_good", "sionna_bad", "native_good", "native_bad"))
        self.assertEqual(campaign.CALIBRATION_ARMS, ("sionna_good", "sionna_bad", "native_good", "native_bad"))
        no_path = campaign._sionna_no_path({"sionna_audit": {"path_gain_status_counts": {"ok": 8, "no_path": 2}}}, "sionna_bad")
        self.assertEqual(no_path, (2, .2, "sionna_no_path_sentinel_not_physical_loss"))
        self.assertEqual(campaign._sionna_no_path({}, "native_good"), ("NA", "NA", "not_applicable_native"))
        with self.assertRaisesRegex(ValueError, "request count differs"):
            campaign._sionna_no_path({"sionna_audit": {"path_gain_status_counts": {"ok": 8, "no_path": 2},
                                                    "path_gain_request_count": 9}}, "sionna_good")

    def test_incomplete_default_block_is_rejected(self):
        runs_root = Path(self.tmp.name) / "runs"
        root = runs_root / "production" / "block-05"; (root / "radar_only").mkdir(parents=True)
        old = __import__("sys").argv
        self.addCleanup(setattr, __import__("sys"), "argv", old)
        __import__("sys").argv = ["campaign", "--runs-root", str(runs_root), "--out", str(Path(self.tmp.name) / "out")]
        with self.assertRaisesRegex(ValueError, "behavior blocks must be exactly"):
            campaign.main()

    def test_partial_or_mixed_arm_sets_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "behavior arms must be exactly"):
            campaign._requested_arms("radar_only,native_good", campaign.BEHAVIOR_ARMS, "behavior")
        with self.assertRaisesRegex(ValueError, "calibration arms must be exactly"):
            campaign._requested_arms("sionna_good,sionna_bad,native_good,radar_only", campaign.CALIBRATION_ARMS, "calibration")

    def test_paired_trajectory_gate_uses_registered_window(self):
        identical = [(0.0, ("a", 1.0, 2.0), ("b", 3.0, 4.0))]
        campaign._assert_matched_trajectories([("radar_only", identical), ("native_good", identical)], "behavior", "block-01")
        changed = [(0.0, ("a", 1.1, 2.0), ("b", 3.0, 4.0))]
        with self.assertRaisesRegex(ValueError, "t < 2 s.*block-01/native_good"):
            campaign._assert_matched_trajectories([("radar_only", identical), ("native_good", changed)], "behavior", "block-01")

    def test_missing_production_sionna_audit_is_rejected_but_legacy_pilot_is_labelled(self):
        with self.assertRaisesRegex(ValueError, "missing Sionna audit"):
            campaign._sionna_no_path({}, "sionna_good")
        self.assertEqual(campaign._sionna_no_path({"pilot_excluded": True}, "sionna_good"),
                         (None, None, "sionna_audit_missing_legacy_pilot"))


if __name__ == "__main__": unittest.main()
