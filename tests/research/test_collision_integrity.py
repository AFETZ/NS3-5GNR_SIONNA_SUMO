import importlib.util
import math
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "summary", ROOT / "experiments/intersection_radar_comm/tools/summarize_runs.py")
summary = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(summary)


class CollisionIntegrity(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "collision.xml"

    def test_missing_is_not_collision_free(self):
        with self.assertRaises(FileNotFoundError):
            summary.read_collision(self.path, "veh2", "veh3")

    def test_truncated_is_not_collision_free(self):
        self.path.write_text('<collisions><collision time="2"')
        with self.assertRaises(ValueError):
            summary.read_collision(self.path, "veh2", "veh3")

    def test_wrong_logger_is_rejected(self):
        self.path.write_text('<netstate/>')
        with self.assertRaises(ValueError):
            summary.read_collision(self.path, "veh2", "veh3")

    def test_valid_empty_logger(self):
        self.path.write_text('<collisions/>')
        flag, timestamp = summary.read_collision(self.path, "veh2", "veh3")
        self.assertEqual(flag, 0)
        self.assertTrue(math.isnan(timestamp))

    def test_pair_is_unordered_and_first_event_is_used(self):
        self.path.write_text('''<collisions>
          <collision collider="veh3" victim="veh2" time="5.2"/>
          <collision collider="veh2" victim="veh3" time="4.8"/>
          <collision collider="veh2" victim="veh9" time="1.0"/>
        </collisions>''')
        self.assertEqual(summary.read_collision(self.path, "veh2", "veh3"), (1, 4.8))


if __name__ == "__main__":
    unittest.main()
