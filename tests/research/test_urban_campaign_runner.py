import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("urban_runner", ROOT / "experiments/future_transport_2026/run_urban_matrix.py")
urban = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(urban)
CAMPAIGN_SPEC = importlib.util.spec_from_file_location("urban_campaign_test", ROOT / "experiments/future_transport_2026/run_campaign.py")
campaign = importlib.util.module_from_spec(CAMPAIGN_SPEC)
CAMPAIGN_SPEC.loader.exec_module(campaign)


class UrbanCampaignRunnerTest(unittest.TestCase):
    def test_plan_has_270_distinct_urban_jobs(self):
        jobs = urban.jobs_for(Path("/research-urban/runs"), campaign)
        self.assertEqual(len(jobs), 270)
        self.assertEqual(sum(job["campaign"] == "urban_behavioral" for job in jobs), 150)
        self.assertEqual(sum(job["campaign"] == "urban_calibration" for job in jobs), 120)
        self.assertEqual(len({job["out"] for job in jobs}), 270)
        self.assertTrue(all("--channel-scenario=V2V-Urban" in job["argv"] for job in jobs))
        self.assertTrue(all("--channel-condition-update-ms=100" in job["argv"] for job in jobs))

    def test_command_keeps_default_unchanged_and_adds_urban_only_on_request(self):
        default = " ".join(campaign.command("native_good", 1, Path("/tmp/out")))
        urban_cmd = " ".join(campaign.command("native_good", 1, Path("/tmp/out"), channel_scenario="V2V-Urban"))
        dynamic_cmd = " ".join(campaign.command("native_good", 1, Path("/tmp/out"),
                                                  channel_scenario="V2V-Urban",
                                                  channel_condition_update_ms=100))
        self.assertNotIn("v2v-channel-model", default)
        self.assertIn("--v2v-channel-model=V2V-Urban", urban_cmd)
        self.assertIn("--channel-condition-update-ms=100", dynamic_cmd)
        self.assertNotIn("--grounded-vehicle-geometry", campaign.server_command())
        self.assertIn("--grounded-vehicle-geometry", campaign.server_command("V2V-Urban"))

    def test_manifest_requires_urban_model_buildings_and_geometry(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            data = {"channel_scenario": "V2V-Urban", "geometry_version": "grounded_vehicle_v2",
                    "campaign_version": "urban_dynamic_v3", "channel_condition_update_ms": 100,
                    "arm": "native_good", "channel_audit": {"model": "V2V-Urban", "buildings_registered": 2,
                    "actual_condition_model": "ns3::ThreeGppV2vUrbanChannelConditionModel",
                    "channel_condition_update_ms": 100, "three_gpp_channel_update_ms": 0,
                    "shadowing_enabled": False},
                    "scene_geometry": {"buildings": ["north", "south"]}}
            (out / "manifest.json").write_text(json.dumps(data))
            self.assertEqual(urban.urban_manifest_valid(out)[0], True)
            data["channel_audit"]["buildings_registered"] = 1
            (out / "manifest.json").write_text(json.dumps(data))
            self.assertEqual(urban.urban_manifest_valid(out)[0], False)
            data["channel_audit"]["buildings_registered"] = 2
            data["arm"] = "sionna_good"
            (out / "manifest.json").write_text(json.dumps(data))
            self.assertEqual(urban.urban_manifest_valid(out)[0], False)
            data["sionna_geometry_audit"] = {"antenna_z_m": 1.5, "mesh_center_z_m": 0.65,
                                             "mesh_height_m": 1.3}
            (out / "manifest.json").write_text(json.dumps(data))
            self.assertEqual(urban.urban_manifest_valid(out)[0], True)

    def test_channel_marker_matches_scene_geometry(self):
        marker = ("NR-SIDELINK-CHANNEL,model=V2V-Urban,buildings_registered=2,"
                  "bounds=-128:-90:8:35:14;-128:-90:-35:-8:11")
        self.assertEqual(campaign.validate_channel_audit(marker, "V2V-Urban")["buildings_registered"], 2)
        with self.assertRaisesRegex(ValueError, "differ from scene"):
            campaign.validate_channel_audit(marker.replace(":35:14", ":36:14"), "V2V-Urban")
        with self.assertRaisesRegex(ValueError, "marker is absent"):
            campaign.validate_channel_audit("", "V2V-Urban")

    def test_dynamic_marker_requires_actual_model_and_isolates_fading_updates(self):
        marker = ("NR-SIDELINK-CHANNEL,model=V2V-Urban,buildings_registered=2,"
                  "bounds=-128:-90:8:35:14;-128:-90:-35:-8:11\n"
                  "NR-SIDELINK-ACTUAL-CHANNEL,condition_model=ns3::ThreeGppV2vUrbanChannelConditionModel,"
                  "condition_update_ms=100,three_gpp_channel_update_ms=0,shadowing_enabled=0")
        audit = campaign.validate_channel_audit(marker, "V2V-Urban", 100)
        self.assertEqual(audit["channel_condition_update_ms"], 100)
        with self.assertRaisesRegex(ValueError, "Actual V2V-Urban channel audit failed"):
            campaign.validate_channel_audit(marker.replace("shadowing_enabled=0", "shadowing_enabled=1"),
                                            "V2V-Urban", 100)


if __name__ == "__main__":
    unittest.main()
