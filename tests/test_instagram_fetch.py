import unittest
import sys
import types
from unittest.mock import patch

# この単体テストはHTTPをすべてモックするため、最小ランタイムでもimportできるようにする。
try:
    import requests  # noqa: F401
except ModuleNotFoundError:
    sys.modules["requests"] = types.ModuleType("requests")

from etl.instagram_fetch import EtlError, fetch_range_insights


class FetchRangeInsightsTest(unittest.TestCase):
    @patch("etl.instagram_fetch.DAY_METRICS", ["reach", "profile_views"])
    @patch("etl.instagram_fetch._get")
    def test_collects_each_day_and_keeps_other_metric_when_one_fails(self, get):
        get.side_effect = [
            {"data": [{"name": "reach", "values": [
                {"value": 120, "end_time": "2026-08-28T08:00:00+0000"},
                {"value": 150, "end_time": "2026-08-29T08:00:00+0000"},
            ]}]},
            EtlError("profile_views is unavailable"),
        ]

        rows, skipped = fetch_range_insights("ig-1", "2026-08-28", "2026-08-29")

        self.assertEqual(rows, {
            "2026-08-28": {"reach": 120},
            "2026-08-29": {"reach": 150},
        })
        self.assertEqual(len(skipped), 1)
        self.assertIn("profile_views", skipped[0])

    @patch("etl.instagram_fetch.DAY_METRICS", ["reach"])
    @patch("etl.instagram_fetch._get")
    def test_ignores_values_outside_requested_range(self, get):
        get.return_value = {"data": [{"values": [
            {"value": 90, "end_time": "2026-08-27T08:00:00+0000"},
            {"value": 120, "end_time": "2026-08-28T08:00:00+0000"},
            {"value": 150, "end_time": "2026-08-30T08:00:00+0000"},
        ]}]}

        rows, skipped = fetch_range_insights("ig-1", "2026-08-28", "2026-08-29")

        self.assertEqual(rows, {"2026-08-28": {"reach": 120}})
        self.assertEqual(skipped, [])


if __name__ == "__main__":
    unittest.main()
