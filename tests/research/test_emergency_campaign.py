import csv
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("emergency", ROOT / "tools/analysis/emergency_campaign.py")
emergency = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(emergency)


class EmergencyCampaignTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "artifacts").mkdir()
        (self.root / "net.xml").write_text('<net><edge><lane id="a" length="200" shape="0,0 200,0"/></edge></net>')
        (self.root / "route.xml").write_text('<routes><vehicle id="veh2" type="Car0"/></routes>')
        states = ['<timestep time="0.00"><edge><lane id="a"><vehicle id="veh2" pos="0"/><vehicle id="veh1" pos="100"/><vehicle id="veh3" pos="151"/></lane></edge></timestep>',
                  '<timestep time="0.05"><edge><lane id="a"><vehicle id="veh2" pos="1"/><vehicle id="veh1" pos="100"/><vehicle id="veh3" pos="152"/></lane></edge></timestep>',
                  '<timestep time="65.55"><edge><lane id="a"><vehicle id="veh2" pos="1"/><vehicle id="veh1" pos="100"/><vehicle id="veh3" pos="152"/></lane></edge></timestep>',
                  '<timestep time="100.00"/>']
        (self.root / "artifacts/eva-netstate.xml").write_text("<netstate>" + "".join(states) + "</netstate>")
        header = ["vehicle_id", "msg_seq", "tx_t_s", "rx_t_s", "rx_ok", "msg_type", "tx_id", "rx_id", "cam_gdt_ms", "pkt_uid"]
        self.header = header
        self.write("veh2", [["veh2", "65500", "0.02", "", "0", "CAM", "2", "", "65500", "-1"], ["veh2", "10", "65.55", "", "0", "CAM", "2", "", "10", "-1"]])
        for i in range(1, 21):
            if i != 2:
                self.write(f"veh{i}", [])

    def write(self, vehicle, rows):
        with (self.root / f"artifacts/eva-{vehicle}-MSG.csv").open("w", newline="") as stream:
            writer = csv.writer(stream); writer.writerow(self.header); writer.writerows(rows)

    def test_eligibility_and_wrap_aware_join(self):
        self.write("veh1", [["veh1", "10", "", "65.56", "1", "CAM", "2", "1", "10", "-1"]])
        result = emergency.analyze(self.root, self.root / "net.xml", self.root / "route.xml")
        one = next(row for row in result["receivers"] if row["receiver"] == "veh1")
        three = next(row for row in result["receivers"] if row["receiver"] == "veh3")
        self.assertEqual((one["eligible_tx"], one["received_unique"], one["first_eligible_rx_s"]), (2, 1, 65.56))
        self.assertEqual((three["eligible_tx"], three["received_unique"], three["first_rx_censor_s"]), (0, 0, None))

    def test_duplicate_rx_rejected(self):
        rx = ["veh1", "10", "", "65.56", "1", "CAM", "2", "1", "10", "-1"]
        self.write("veh1", [rx, rx])
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            emergency.analyze(self.root, self.root / "net.xml", self.root / "route.xml")

    def test_stale_wrapped_identity_rejected(self):
        self.write("veh1", [["veh1", "65500", "", "50.00", "1", "CAM", "2", "1", "65500", "-1"]])
        with self.assertRaisesRegex(ValueError, "no recent TX"):
            emergency.analyze(self.root, self.root / "net.xml", self.root / "route.xml")


if __name__ == "__main__":
    unittest.main()
