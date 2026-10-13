"""Run with: python -m unittest discover -s backend/tests -v."""
import asyncio
import importlib
import json
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

import pandas as pd


COLUMNS = [
    "outage_id", "year", "month", "duration_minutes",
    "municipality_psgc", "barangay_psgc", "cause_category",
]


def sample_outages():
    return pd.DataFrame([
        ("a", 2024, 1, 60, "001", "00101", "Weather"),
        ("b", 2024, 1, 120, "001", "00101", "Equipment"),
        ("c", 2024, 3, 30, "001", "00102", "Weather"),
        ("d", 2026, 2, 90, "002", "00201", "Weather"),
        ("e", 2026, 6, 45, "001", "00101", "Weather"),
    ], columns=COLUMNS)


# Datasets are local and gitignored. Exercise the real loader with fixture
# data, including a duplicate, so tests also run in a clean checkout.
fixture = sample_outages()
with patch("pandas.read_csv", return_value=pd.concat([fixture, fixture.iloc[[0]]])):
    main = importlib.import_module("backend.main")
LOADED_OUTAGES = main.OUTAGES_DF.copy()


async def request_trends(params):
    """Call the real ASGI app, including query validation and JSON encoding."""
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    await main.app({
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/overview/trends",
        "raw_path": b"/api/overview/trends",
        "query_string": urlencode(params).encode(),
        "root_path": "",
        "headers": [],
        "client": ("127.0.0.1", 1234),
        "server": ("test", 80),
    }, receive, send)
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in messages
                    if m["type"] == "http.response.body")
    return status, json.loads(body)


class OverviewTrendsTests(unittest.TestCase):
    def setUp(self):
        self.patch = patch.object(main, "OUTAGES_DF", sample_outages())
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def get(self, **params):
        status, body = asyncio.run(request_trends(params))
        self.assertEqual(status, 200, body)
        return body

    def assert_zero(self, point):
        self.assertEqual(point["total_outages"], 0)
        self.assertEqual(point["total_duration_hours"], 0)
        self.assertIsNone(point["average_duration_minutes"])

    def test_loader_deduplicates_outage_ids(self):
        self.assertEqual(len(LOADED_OUTAGES), 5)
        self.assertTrue(LOADED_OUTAGES["outage_id"].is_unique)

    def test_annual_aggregation_and_missing_year(self):
        body = self.get()
        self.assertEqual(body["granularity"], "annual")
        self.assertEqual([p["period"] for p in body["points"]],
                         ["2024", "2025", "2026"])
        self.assertEqual(body["points"][0], {
            "period": "2024", "total_outages": 3,
            "total_duration_hours": 3.5, "average_duration_minutes": 70.0,
        })
        self.assert_zero(body["points"][1])
        self.assertEqual(body["points"][2]["total_duration_hours"], 2.25)
        self.assertEqual(body["points"][2]["average_duration_minutes"], 67.5)

    def test_monthly_aggregation_and_zero_months(self):
        body = self.get(year=2024)
        self.assertEqual(body["granularity"], "monthly")
        self.assertEqual(len(body["points"]), 12)
        self.assertEqual(body["points"][0], {
            "period": "2024-01", "total_outages": 2,
            "total_duration_hours": 3.0, "average_duration_minutes": 90.0,
        })
        self.assert_zero(body["points"][1])
        self.assertEqual(body["points"][2]["total_outages"], 1)

    def test_each_filter_and_combined_filters(self):
        cases = [
            ({"municipality_psgc": "001"}, 4),
            ({"barangay_psgc": "00101"}, 3),
            ({"cause_category": "Weather"}, 4),
            ({"year": 2024}, 3),
            ({"year": 2024, "municipality_psgc": "001",
              "barangay_psgc": "00101", "cause_category": "Weather"}, 1),
            ({"municipality_psgc": "002", "barangay_psgc": "00101"}, 0),
        ]
        for params, expected in cases:
            with self.subTest(params=params):
                body = self.get(**params)
                self.assertEqual(sum(p["total_outages"] for p in body["points"]),
                                 expected)

    def test_reuses_shared_filter_and_does_not_mutate_source(self):
        before = main.OUTAGES_DF.copy(deep=True)
        with patch.object(main, "filter_outages", wraps=main.filter_outages) as shared:
            self.get(year=2024, municipality_psgc="001",
                     barangay_psgc="00101", cause_category="Weather")
        shared.assert_called_once_with(
            year=2024, municipality_psgc="001",
            barangay_psgc="00101", cause_category="Weather",
        )
        pd.testing.assert_frame_equal(main.OUTAGES_DF, before)

    def test_partial_final_year_uses_global_coverage(self):
        body = self.get(year=2026, municipality_psgc="002")
        self.assertEqual([p["period"] for p in body["points"]],
                         [f"2026-{m:02d}" for m in range(1, 7)])
        self.assert_zero(body["points"][0])
        self.assertEqual(body["points"][1]["total_outages"], 1)
        self.assert_zero(body["points"][-1])

    def test_partial_first_year_does_not_prepend_unobserved_months(self):
        main.OUTAGES_DF = main.OUTAGES_DF[main.OUTAGES_DF["outage_id"] != "a"]
        main.OUTAGES_DF = main.OUTAGES_DF[main.OUTAGES_DF["outage_id"] != "b"]
        body = self.get(year=2024)
        self.assertEqual(body["points"][0]["period"], "2024-03")
        self.assertEqual(body["points"][-1]["period"], "2024-12")

    def test_empty_filters_keep_covered_periods(self):
        for params, expected in [
            ({"cause_category": "missing"}, 3),
            ({"year": 2026, "barangay_psgc": "missing"}, 6),
            ({"year": 2025}, 12),
        ]:
            with self.subTest(params=params):
                body = self.get(**params)
                self.assertEqual(len(body["points"]), expected)
                for point in body["points"]:
                    self.assert_zero(point)

    def test_valid_year_outside_coverage_returns_no_points(self):
        for year in [1, 2023, 2027, 9999]:
            with self.subTest(year=year):
                self.assertEqual(self.get(year=year),
                                 {"granularity": "monthly", "points": []})

    def test_empty_dataset(self):
        main.OUTAGES_DF = main.OUTAGES_DF.iloc[:0]
        for params, granularity in [({}, "annual"), ({"year": 2026}, "monthly")]:
            self.assertEqual(self.get(**params),
                             {"granularity": granularity, "points": []})

    def test_invalid_year_rejected_by_http_validation(self):
        for year in ["abc", "2026.5", "", "0", "-1", "10000"]:
            with self.subTest(year=year):
                status, body = asyncio.run(request_trends({"year": year}))
                self.assertEqual(status, 422)
                self.assertEqual(body["detail"][0]["loc"], ["query", "year"])

    def test_unique_events_and_numeric_duration_semantics(self):
        main.OUTAGES_DF = pd.DataFrame([
            ("a", 2026, 6, "60.5", "001", "00101", "Weather"),
            ("a", 2026, 6, "60.5", "001", "00101", "Weather"),
            ("b", 2026, 6, "invalid", "001", "00101", "Weather"),
            ("c", 2026, 6, None, "001", "00101", "Equipment"),
        ], columns=COLUMNS)
        self.assertEqual(self.get(year=2026)["points"], [{
            "period": "2026-06", "total_outages": 3,
            "total_duration_hours": 1.01, "average_duration_minutes": 60.5,
        }])
        point = self.get(year=2026, cause_category="Equipment")["points"][0]
        self.assertEqual(point["total_outages"], 1)
        self.assertEqual(point["total_duration_hours"], 0)
        self.assertIsNone(point["average_duration_minutes"])


if __name__ == "__main__":
    unittest.main()