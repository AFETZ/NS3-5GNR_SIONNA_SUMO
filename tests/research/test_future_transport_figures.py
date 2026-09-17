import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("future_transport_figures", ROOT / "tools/plots/future_transport_figures.py")
figures = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(figures)


class FutureTransportFiguresTest(unittest.TestCase):
    def test_writes_five_svg_and_png_figures_from_tiny_valid_summaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); emergency = root / "emergency"; intersection = root / "intersection"; out = root / "figures"
            emergency.mkdir(); intersection.mkdir()
            powers = []
            emergency_rows = []
            for power, prr in ((-20, .3), (0, .8)):
                powers.append({"power_dbm": power, "prr_run_level": {"estimate": prr, "ci95": [prr - .1, prr + .1]},
                               "first_eligible_receiver_reception": {"primary_delay_from_first_eligibility": {
                                   "p90_estimable": True, "p90_s": .2, "block_bootstrap_ci95": {"ci95_estimable": True, "ci95_p90_s": [.1, .3]}}}})
                emergency_rows.append({"power_dbm": power, "prr": prr})
            (emergency / "runs.json").write_text(json.dumps(emergency_rows))
            (emergency / "summary.json").write_text(json.dumps({"powers": powers}))
            cal_arms = ("native_good", "native_bad", "sionna_good", "sionna_bad")
            behavior_arms = ("radar_only",) + cal_arms
            rows = ([{"kind": "calibration", "block": "block-01", "arm": arm, "cam_prr": .5,
                      "sionna_no_path_fraction": .1 if arm.startswith("sionna") else "NA"} for arm in cal_arms] +
                    [{"kind": "behavior", "block": "block-01", "arm": arm, "collision": int(arm == "radar_only"),
                      "sionna_no_path_fraction": .1 if arm.startswith("sionna") else "NA"} for arm in behavior_arms])
            collision = {arm: {"runs": 1, "rate": int(arm == "radar_only"), "wilson95": [0, 1]} for arm in behavior_arms}
            (intersection / "runs.json").write_text(json.dumps(rows))
            (intersection / "summary.json").write_text(json.dumps({"collision": collision, "bootstrap": {
                "calibration:cam_prr:native_good-native_bad": {"estimate": .1, "ci95": [0, .2]}}}))
            figures.generate(emergency, intersection, out)
            self.assertEqual(len(list(out.glob("*.svg"))), 5)
            self.assertEqual(len(list(out.glob("*.png"))), 5)


if __name__ == "__main__":
    unittest.main()
