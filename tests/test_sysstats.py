"""整机占用采样器的结构与边界。

不测具体数值（不同机器、不同负载都不一样），只保证接口形状正确、第一次采样
不会给出假的 0%、第二次能算出区间内的值。
"""

import time
import unittest

from core.sysstats import SystemSampler


class SamplerTest(unittest.TestCase):
    def test_first_sample_has_shape(self):
        data = SystemSampler().sample()
        self.assertIn("cpu", data)
        self.assertIn("memory", data)
        self.assertIn("network", data)
        self.assertIn("uptime", data)
        self.assertGreaterEqual(data["cpu"]["cores"], 1)
        # 第一次没有参照物，CPU 与网络必须是 None 而不是 0——0 会被当成「空闲」
        self.assertIsNone(data["cpu"]["percent"])
        self.assertIsNone(data["network"]["rx_bps"])

    def test_second_sample_reports_percent(self):
        sampler = SystemSampler()
        sampler.sample()
        time.sleep(0.15)
        data = sampler.sample()
        percent = data["cpu"]["percent"]
        self.assertIsNotNone(percent)
        self.assertGreaterEqual(percent, 0)
        self.assertLessEqual(percent, 100)

    def test_memory_totals_are_positive(self):
        data = SystemSampler().sample()
        self.assertIsNotNone(data["memory"]["total"])
        self.assertGreater(data["memory"]["total"], 0)
        self.assertGreaterEqual(data["memory"]["used"], 0)
        if data["memory"]["percent"] is not None:
            self.assertGreaterEqual(data["memory"]["percent"], 0)
            self.assertLessEqual(data["memory"]["percent"], 100)


if __name__ == "__main__":
    unittest.main()
