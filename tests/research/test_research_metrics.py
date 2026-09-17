import csv
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("metrics", ROOT / "tools/analysis/research_metrics.py")
metrics = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(metrics)


class MetricsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, name, rows):
        path = self.root / name
        with path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0])
            writer.writeheader()
            writer.writerows(rows)
        return path

    def test_sensor_action_can_precede_cam_action(self):
        path = self.write("ctrl.csv", [{"time_s": "1", "event_type": "sensor_reaction"},
                                      {"time_s": "2", "event_type": "cam_reaction"}])
        self.assertEqual(metrics.first_action(path)["first_action_s"], 1)

    def test_no_action_is_missing_not_horizon(self):
        path = self.write("ctrl.csv", [{"time_s": "5", "event_type": "drop_decision_no_action"}])
        self.assertIsNone(metrics.first_action(path)["first_action_s"])

    def test_censored_p90_cannot_be_invented(self):
        data = [(1, True), (2, True), (3, False)]
        self.assertEqual(metrics.survival_quantile(data, .5), 2)
        self.assertIsNone(metrics.survival_quantile(data, .9))

    def test_zero_collisions_has_nonzero_upper_interval(self):
        lo, hi = metrics.wilson(0, 30)
        self.assertAlmostEqual(lo, 0)
        self.assertAlmostEqual(hi, .113513, places=5)

    def test_prr_deduplicates_deliveries_and_excludes_inferred_drops(self):
        tx = self.write("tx.csv", [dict(msg_type="CAM", tx_id="2", cam_gdt_ms="1000", tx_t_s="1")])
        rx_row = dict(msg_type="CAM", tx_id="2", rx_id="3", cam_gdt_ms="1000", rx_t_s="1.01", rx_ok="1")
        rx = self.write("rx.csv", [rx_row, rx_row, dict(rx_row, msg_type="CAM_DROP_PHY_INFERRED", rx_ok="0")])
        result = metrics.analyze_cam_link(tx, rx, "2", "3", 0, 5)
        self.assertEqual((result["eligible_tx"], result["received_unique"], result["duplicate_rx_records"]), (1, 1, 1))
        self.assertEqual(result["prr"], 1)

    def test_unmatched_reception_rejects_run(self):
        tx = self.write("tx.csv", [dict(msg_type="CAM", tx_id="2", cam_gdt_ms="1000", tx_t_s="1")])
        rx = self.write("rx.csv", [dict(msg_type="CAM", tx_id="2", rx_id="3", cam_gdt_ms="2000", rx_t_s="2.01", rx_ok="1")])
        with self.assertRaises(ValueError):
            metrics.analyze_cam_link(tx, rx, "2", "3", 0, 5)

    def test_reception_at_half_open_window_end_is_excluded(self):
        tx = self.write("tx.csv", [dict(msg_type="CAM", tx_id="2", cam_gdt_ms="4000", tx_t_s="4")])
        rx = self.write("rx.csv", [dict(msg_type="CAM", tx_id="2", rx_id="3", cam_gdt_ms="4000",
                                        rx_t_s="5", rx_ok="1")])
        result = metrics.analyze_cam_link(tx, rx, "2", "3", 2, 5)
        self.assertEqual((result["eligible_tx"], result["received_unique"], result["prr"]),
                         (1, 0, 0))


if __name__ == "__main__":
    unittest.main()
