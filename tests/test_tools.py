from __future__ import annotations

import unittest

from travel_agent_harness.redaction import redact
from travel_agent_harness.tools import ToolValidationError, build_travel_registry


class ToolTests(unittest.TestCase):
    def test_rejects_unknown_arguments(self):
        registry = build_travel_registry()
        with self.assertRaises(ToolValidationError):
            registry.execute(
                "weather_search",
                {"city": "杭州", "shell": "ignored"},
            )

    def test_agentic_rl_tool_contracts_are_preserved(self):
        names = [tool["function"]["name"] for tool in build_travel_registry().api_schemas()]
        self.assertEqual(
            [
                "visit",
                "search",
                "weather_search",
                "flights_search",
                "train_tickets_search",
                "route_planning",
                "poi_search",
                "around_search",
            ],
            names,
        )

    def test_poi_and_route_tools_return_map_geometry(self):
        registry = build_travel_registry()
        poi = registry.execute("poi_search", {"address": "断桥残雪", "region": "杭州"})
        location = poi["pois"][0]["location"]
        route = registry.execute(
            "route_planning",
            {
                "origin": f"{location['lng']},{location['lat']}",
                "destination": "120.1427,30.2587",
                "mode": "walking",
            },
        )
        self.assertEqual(2, len(route["polyline"]))
        self.assertGreater(route["distance_m"], 0)

    def test_schema_echo_gets_explicit_guidance_error(self):
        registry = build_travel_registry()
        with self.assertRaises(ToolValidationError) as ctx:
            registry.execute(
                "search",
                {"type": "object", "properties": {"query": {"type": "array"}}, "required": ["query"]},
            )
        message = str(ctx.exception)
        self.assertIn("工具定义", message)
        self.assertIn("query", message)

    def test_redacts_api_keys_recursively(self):
        secret = "sk-" + "x" * 24
        value = redact({"nested": [f"token={secret}"], "api_key": secret})
        self.assertNotIn(secret, str(value))
        self.assertEqual("[REDACTED]", value["api_key"])

    def test_ticket_simulator_overrides_train_and_flight_handlers(self):
        class FakeSimulator:
            def flights(self, date, from_city, to_city):
                return f"模拟航班 {from_city}->{to_city} {date}"

            def train_tickets(self, date, from_city, to_city):
                return f"模拟火车 {from_city}->{to_city} {date}"

        registry = build_travel_registry(ticket_simulator=FakeSimulator())
        flights = registry.execute(
            "flights_search", {"date": "2026-10-02", "from_city": "杭州", "to_city": "上海"}
        )
        trains = registry.execute(
            "train_tickets_search", {"date": "2026-10-02", "from_city": "杭州", "to_city": "上海"}
        )
        self.assertEqual("llm_ticket_simulator", flights["data_source"])
        self.assertIn("模拟航班 杭州->上海 2026-10-02", flights["text"])
        self.assertIn("模拟火车 杭州->上海 2026-10-02", trains["text"])
        # Other tools are untouched by the simulator override.
        weather = registry.execute("weather_search", {"city": "杭州"})
        self.assertNotEqual("llm_ticket_simulator", weather.get("data_source"))


if __name__ == "__main__":
    unittest.main()
