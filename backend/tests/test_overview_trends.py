import unittest
from unittest.mock import patch

import pandas as pd
from backend.main import get_overview_trends


class OverviewTrendsTests(unittest.TestCase):
    def setUp(self):
        # No duration column: the count-only endpoint must not depend on it.
        rows = [
            ("a", 2019, 1, "Tree"), ("a", 2019, 1, "Tree"),
            ("b", 2019, 12, "Wind"), ("c", 2020, 3, "Tree"),
            ("d", 2026, 6, "Wind"),
        ]
        self.frame = pd.DataFrame(rows, columns=["outage_id", "year", "month", "cause_category"])
        self.frame["municipality_psgc"] = "m1"
        self.frame["barangay_psgc"] = "b1"
        self.patcher = patch("backend.main.OUTAGES_DF", self.frame)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_exposes_only_month_and_unique_outage_count(self):
        payload = get_overview_trends()
        self.assertEqual(payload["granularity"], "monthly")
        self.assertEqual(sum(p["total_outages"] for p in payload["points"]), 4)
        for point in payload["points"]:
            self.assertEqual(set(point), {"period", "total_outages"})

    def test_zero_event_months_are_retained_inside_coverage(self):
        points = {p["period"]: p["total_outages"] for p in get_overview_trends()["points"]}
        self.assertEqual(points["2019-01"], 1)
        self.assertEqual(points["2019-02"], 0)
        self.assertEqual(points["2026-06"], 1)
        self.assertNotIn("2026-07", points)

    def test_filters_preserve_global_coverage(self):
        points = get_overview_trends(year=2026, cause_category="Tree", municipality_psgc="m1", barangay_psgc="b1")["points"]
        self.assertEqual(len(points), 6)
        self.assertEqual([p["total_outages"] for p in points], [0] * 6)
        self.assertEqual(get_overview_trends(year=2018)["points"], [])

    def test_empty_dataset(self):
        with patch("backend.main.OUTAGES_DF", self.frame.iloc[:0]):
            self.assertEqual(get_overview_trends(), {"granularity": "monthly", "points": []})


if __name__ == "__main__":
    unittest.main()
