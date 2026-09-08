from __future__ import annotations

import unittest

from travel_agent_harness.reporting import ReportError, normalize_report


class ReportingTests(unittest.TestCase):
    def test_normalizes_map_report_without_inventing_values(self):
        report = normalize_report(
            {
                "title": "杭州路线",
                "map_kind": "geo",
                "days": [
                    {
                        "day": "bad",
                        "theme": "湖滨",
                        "stops": [
                            {
                                "name": "西湖",
                                "cost_cny": "",
                                "coordinates": {"lng": 120.143, "lat": 30.2496},
                            }
                        ],
                    }
                ],
                "budget": {"total_cny": None, "items": []},
            }
        )
        self.assertEqual(1, report["days"][0]["day"])
        self.assertIsNone(report["days"][0]["stops"][0]["cost_cny"])
        self.assertEqual("geo", report["map_kind"])

    def test_invalid_coordinate_forces_schematic_map(self):
        report = normalize_report(
            {
                "map_kind": "geo",
                "days": [{"day": 1, "stops": [{"name": "未知点", "coordinates": None}]}],
            }
        )
        self.assertEqual("schematic", report["map_kind"])

    def test_rejects_report_without_route_stops(self):
        with self.assertRaises(ReportError):
            normalize_report({"days": []})


if __name__ == "__main__":
    unittest.main()
