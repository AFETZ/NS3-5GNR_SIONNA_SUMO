import importlib.util
from pathlib import Path
import sys
import types
import unittest

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "src/sionna/sionna_v1_server_script.py"


class Tensor:
    def __init__(self, value):
        self.value = np.asarray(value)

    def numpy(self):
        return self.value


def load_server_module():
    tensorflow = types.ModuleType("tensorflow")
    tensorflow.config = types.SimpleNamespace(
        list_physical_devices=lambda _: [],
        experimental=types.SimpleNamespace(set_memory_growth=lambda *_: None),
    )
    tensorflow.get_logger = lambda: types.SimpleNamespace(setLevel=lambda _: None)
    mitsuba = types.ModuleType("mitsuba")
    mitsuba.set_variant = lambda _: None
    mitsuba.variant = lambda: "cuda_ad_mono_polarized"
    rt = types.ModuleType("sionna.rt")
    rt.load_scene = object()
    rt.PlanarArray = lambda **_: object()
    rt.Transmitter = lambda *_, **__: object()
    rt.Receiver = lambda *_, **__: object()
    rt.PathSolver = object()
    sionna = types.ModuleType("sionna")
    sionna.rt = rt
    originals = {name: sys.modules.get(name) for name in ("tensorflow", "mitsuba", "sionna", "sionna.rt")}
    sys.modules.update({"tensorflow": tensorflow, "mitsuba": mitsuba, "sionna": sionna, "sionna.rt": rt})
    try:
        spec = importlib.util.spec_from_file_location("sionna_v1_server_test", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, original in originals.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


class MatchRaysTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = load_server_module()

    def test_masks_invalid_paths_and_identifies_los_from_zero_depth(self):
        coefficient = np.zeros((2, 1, 2, 1, 3), dtype=complex)
        coefficient[1, 0, 0, 0] = [1, 99, 2]
        coefficient[0, 0, 1, 0, 2] = 3
        delays = np.zeros((2, 2, 3))
        delays[1, 0] = [1e-8, 99, 2e-8]
        delays[0, 1, 2] = 3e-8
        valid = np.zeros((2, 2, 3), dtype=bool)
        valid[1, 0] = [True, False, True]
        valid[0, 1, 2] = True
        interactions = np.zeros((2, 2, 2, 3), dtype=int)
        interactions[0, 1, 0, 2] = 1
        interactions[0, 0, 1, 2] = 1
        paths = types.SimpleNamespace(
            _src_positions=Tensor([[0, 10], [0, 0], [1.5, 1.5]]),
            _tgt_positions=Tensor([[0, 10], [0, 0], [1.5, 1.5]]),
            a=(Tensor(coefficient.real), Tensor(coefficient.imag)),
            tau=Tensor(delays),
            interactions=Tensor(interactions),
            valid=Tensor(valid),
        )
        state = {
            "antenna_displacement": [0, 0, 1.5],
            "position_threshold": 0.1,
            "verbose": False,
            "sionna_location_db": {1: {"x": 0, "y": 0, "z": 0}, 2: {"x": 10, "y": 0, "z": 0}},
        }

        matched = self.server.match_rays_to_cars(paths, state)
        link = matched["car_1"]["car_2"]
        np.testing.assert_array_equal(link["path_coefficients"][0], [1, 2])
        np.testing.assert_array_equal(link["delays"][0], [1e-8, 2e-8])
        self.assertEqual(link["is_los"], [True])
        self.assertEqual(matched["car_2"]["car_1"]["is_los"], [False])

    def test_no_path_values_preserve_ns3_contract(self):
        state = {
            "rays_cache": {"car_1": {"car_2": {
                "path_coefficients": [np.array([], dtype=complex)],
                "delays": [np.array([], dtype=float)],
                "is_los": [False],
            }}},
            "verbose": False,
            "time_checker": False,
        }
        self.assertEqual(self.server.get_path_loss("car_1", "car_2", state), 300.0)
        self.assertEqual(self.server.get_delay("car_1", "car_2", state), 1e5)
        self.assertEqual(self.server.manage_los_request("CALC_REQUEST_LOS:veh1,veh2", state), [False])


if __name__ == "__main__":
    unittest.main()
