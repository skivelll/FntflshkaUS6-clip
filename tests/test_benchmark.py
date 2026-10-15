import importlib.util
from pathlib import Path
import struct
import unittest

spec = importlib.util.spec_from_file_location("benchmark_animation", Path(__file__).parents[1] / "tools/benchmark_animation.py")
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


class BenchmarkTests(unittest.TestCase):
    def test_record_units_and_size_validation(self):
        values = [1, 200000, 180000, 190000, 200000, 180000, 100, 1, 123,
                  96, 96, 18432, 5, 50000, 3, 300000, 5120, 200000, 180000, 189000]
        result = benchmark.decode_metrics(struct.pack("<20I", *values))
        self.assertEqual(result["read_ms_per_frame"], 10)
        self.assertEqual(result["render_ms_per_frame"], 100)
        self.assertEqual(result["measured_render_fps_ceiling"], 10)
        for index, value in ((0, 2), (9, 321), (11, 3), (12, 0), (13, 0), (14, 0), (16, 100)):
            invalid = list(values)
            invalid[index] = value
            with self.assertRaises(ValueError): benchmark.decode_metrics(struct.pack("<20I", *invalid))
        with self.assertRaises(ValueError): benchmark.decode_metrics(b"")
