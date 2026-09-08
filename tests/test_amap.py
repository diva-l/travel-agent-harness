from __future__ import annotations

import json
import unittest
import urllib.parse

from travel_agent_harness.tools import ToolValidationError, build_travel_registry


def fake_opener(request, timeout):
    path = urllib.parse.urlparse(request.full_url).path
    payloads = {
        "/v3/weather/weatherInfo": {
            "status": "1",
            "forecasts": [{
                "city": "杭州市",
                "casts": [{
                    "date": "2026-09-05",
                    "dayweather": "晴",
                    "daytemp": "33",
                    "nightweather": "多云",
                    "nighttemp": "25",
                }],
            }],
        },
        "/v3/place/text": {
            "status": "1",
            "pois": [{
                "name": "断桥残雪",
                "address": "北山街",
                "location": "120.1484,30.2591",
            }],
        },
        "/v3/place/around": {
            "status": "1",
            "pois": [{
                "name": "楼外楼",
                "address": [],
                "location": "120.1430,30.2496",
                "distance": "320",
            }],
        },
        "/v3/direction/driving": {
            "status": "1",
            "route": {
                "paths": [{
                    "distance": "5200",
                    "duration": "780",
                    "steps": [{"polyline": "120.14,30.25;120.15,30.26;120.16,30.27"}],
                }],
            },
        },
    }
    if path not in payloads:
        return json.dumps({"status": "0", "infocode": "10001", "info": "key error"}).encode()
    return json.dumps(payloads[path]).encode()


class AmapProviderTests(unittest.TestCase):
    def registry(self):
        return build_travel_registry(provider="amap", amap_key="test-amap-key", amap_opener=fake_opener)

    def test_weather_parses_real_forecast(self):
        result = self.registry().execute("weather_search", {"city": "杭州"})
        self.assertEqual("amap_web_service", result["data_source"])
        self.assertEqual("晴", result["forecast"][0]["condition"])
        self.assertEqual(33.0, result["forecast"][0]["temperature_c"])

    def test_poi_search_returns_coordinates(self):
        result = self.registry().execute("poi_search", {"address": "断桥残雪", "region": "杭州"})
        self.assertEqual({"lng": 120.1484, "lat": 30.2591}, result["pois"][0]["location"])

    def test_around_search_handles_empty_address_list(self):
        result = self.registry().execute(
            "around_search", {"location": "120.14,30.25", "radius": 800, "keyword": "餐厅"}
        )
        self.assertEqual("", result["pois"][0]["address"])
        self.assertEqual(320, result["pois"][0]["distance_m"])

    def test_route_planning_converts_duration_and_polyline(self):
        result = self.registry().execute(
            "route_planning",
            {"origin": "120.14,30.25", "destination": "120.16,30.27", "mode": "driving"},
        )
        self.assertEqual(5200, result["distance_m"])
        self.assertEqual(13, result["duration_minutes"])
        self.assertEqual(3, len(result["polyline"]))

    def test_amap_error_surfaces_as_tool_error(self):
        registry = self.registry()
        with self.assertRaises(ToolValidationError):
            registry.execute("route_planning", {"origin": "1,1", "destination": "2,2", "mode": "walking"})

    def test_non_geo_tools_stay_on_demo_provider(self):
        result = self.registry().execute("search", {"query": ["上海 住宿"]})
        self.assertEqual("demo_fixture", result["data_source"])

    def test_contracts_identical_between_providers(self):
        demo = [tool["function"]["name"] for tool in build_travel_registry().api_schemas()]
        amap = [tool["function"]["name"] for tool in self.registry().api_schemas()]
        self.assertEqual(demo, amap)

    def test_unknown_provider_rejected(self):
        with self.assertRaises(ValueError):
            build_travel_registry(provider="unknown")

    def test_cache_ttl_dedupes_identical_calls(self):
        from travel_agent_harness.amap import AmapProvider

        calls = []

        def counting_opener(request, timeout):
            calls.append(request.full_url)
            return json.dumps({
                "status": "1",
                "forecasts": [{"city": "杭州市", "casts": []}],
            }).encode("utf-8")

        provider = AmapProvider("k", opener=counting_opener, cache_ttl=60)
        provider.weather_search("杭州")
        provider.weather_search("杭州")
        self.assertEqual(1, len(calls))

        uncached = AmapProvider("k", opener=counting_opener, cache_ttl=0)
        uncached.weather_search("杭州")
        uncached.weather_search("杭州")
        self.assertEqual(3, len(calls))


    def test_concurrent_qps_limit_is_retried(self):
        from travel_agent_harness.amap import AmapProvider

        calls = []

        def flaky_opener(request, timeout):
            calls.append(request.full_url)
            if len(calls) < 3:
                return json.dumps({
                    "status": "0",
                    "infocode": "10021",
                    "info": "CUQPS_HAS_EXCEEDED_THE_LIMIT",
                }).encode()
            return json.dumps({
                "status": "1",
                "forecasts": [{"city": "杭州市", "casts": []}],
            }).encode()

        provider = AmapProvider("k", opener=flaky_opener)
        result = provider.weather_search("杭州")
        self.assertEqual("amap_web_service", result["data_source"])
        self.assertEqual(3, len(calls))

    def test_persistent_rate_limit_still_fails(self):
        from travel_agent_harness.amap import AmapProvider

        def limited_opener(request, timeout):
            return json.dumps({
                "status": "0",
                "infocode": "10021",
                "info": "CUQPS_HAS_EXCEEDED_THE_LIMIT",
            }).encode()

        provider = AmapProvider("k", opener=limited_opener)
        with self.assertRaisesRegex(ToolValidationError, "10021"):
            provider.weather_search("杭州")


if __name__ == "__main__":
    unittest.main()
