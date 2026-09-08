from __future__ import annotations

import json
import unittest
import urllib.error
import urllib.parse

from travel_agent_harness.tools import ToolValidationError, build_travel_registry


def fake_opener(request, timeout):
    path = urllib.parse.urlparse(request.full_url).path
    payload = json.loads(request.data.decode("utf-8"))
    if path == "/v1/search" and payload["query"] == "boom":
        raise urllib.error.URLError("connection reset")
    if path == "/v1/search":
        return json.dumps({
            "success": True,
            "data": [
                {"title": "西湖攻略", "url": "https://example.com/xihu", "description": "景点介绍"},
                {"title": "断桥", "url": "https://example.com/duanqiao", "description": "开放时间"},
            ],
        }).encode()
    if path == "/v1/scrape":
        if payload["url"].endswith("empty"):
            return json.dumps({"success": True, "data": {"markdown": "  "}}).encode()
        return json.dumps({
            "success": True,
            "data": {"markdown": "# 正文\n" + "内容" * 4000},
        }).encode()
    return json.dumps({"success": False, "error": "unknown path"}).encode()


class FirecrawlProviderTests(unittest.TestCase):
    def registry(self):
        return build_travel_registry(
            search_provider="firecrawl",
            firecrawl_key="test-fc-key",
            firecrawl_opener=fake_opener,
        )

    def test_search_maps_results(self):
        result = self.registry().execute("search", {"query": ["杭州 西湖"]})
        self.assertEqual("firecrawl_search", result["data_source"])
        items = result["results"][0]["items"]
        self.assertEqual(2, len(items))
        self.assertEqual("西湖攻略", items[0]["title"])
        self.assertEqual("景点介绍", items[0]["snippet"])

    def test_search_per_query_failure_does_not_kill_batch(self):
        result = self.registry().execute("search", {"query": ["杭州 西湖", "boom"]})
        ok, failed = result["results"]
        self.assertEqual(2, len(ok["items"]))
        self.assertEqual([], failed["items"])
        self.assertIn("error", failed)

    def test_visit_scrapes_markdown_and_truncates(self):
        result = self.registry().execute(
            "visit", {"url": "https://example.com/xihu", "goal": "开放时间"}
        )
        self.assertEqual("firecrawl_scrape", result["data_source"])
        self.assertEqual("开放时间", result["goal"])
        summary = result["pages"][0]["summary"]
        self.assertTrue(summary.startswith("# 正文"))
        self.assertLessEqual(len(summary), 6000)

    def test_visit_multiple_urls_and_empty_content(self):
        result = self.registry().execute(
            "visit",
            {"url": ["https://example.com/xihu", "https://example.com/empty"], "goal": "g"},
        )
        ok, empty = result["pages"]
        self.assertTrue(ok["summary"])
        self.assertEqual("", empty["summary"])
        self.assertIn("error", empty)

    def test_visit_uses_extractor_when_provided(self):
        from travel_agent_harness.firecrawl import FirecrawlProvider

        provider = FirecrawlProvider(
            "test-fc-key",
            opener=fake_opener,
            extractor=lambda markdown, goal: '{"rational": "r", "evidence": "e", "summary": "蒸馏结果"}',
        )
        result = provider.visit("https://example.com/xihu", "开放时间")
        self.assertEqual('{"rational": "r", "evidence": "e", "summary": "蒸馏结果"}', result["pages"][0]["summary"])
        self.assertIn("LLM 按目标提炼", result["notice"])

    def test_visit_falls_back_to_raw_markdown_when_extractor_fails(self):
        from travel_agent_harness.firecrawl import FirecrawlProvider

        def boom(markdown, goal):
            raise RuntimeError("extractor down")

        provider = FirecrawlProvider("test-fc-key", opener=fake_opener, extractor=boom)
        result = provider.visit("https://example.com/xihu", "开放时间")
        self.assertTrue(result["pages"][0]["summary"].startswith("# 正文"))

        empty_extractor = FirecrawlProvider(
            "test-fc-key", opener=fake_opener, extractor=lambda m, g: ""
        )
        result = empty_extractor.visit("https://example.com/xihu", "开放时间")
        self.assertTrue(result["pages"][0]["summary"].startswith("# 正文"))

    def test_authorization_header_sent(self):
        seen = {}

        def opener(request, timeout):
            seen["auth"] = request.headers.get("Authorization")
            seen["method"] = request.get_method()
            return json.dumps({"success": True, "data": []}).encode()

        registry = build_travel_registry(
            search_provider="firecrawl", firecrawl_key="fc-secret", firecrawl_opener=opener
        )
        registry.execute("search", {"query": ["test"]})
        self.assertEqual("Bearer fc-secret", seen["auth"])
        self.assertEqual("POST", seen["method"])

    def test_requires_api_key(self):
        with self.assertRaises(ToolValidationError):
            build_travel_registry(search_provider="firecrawl")

    def test_geo_and_intercity_tools_stay_on_their_providers(self):
        result = self.registry().execute("weather_search", {"city": "杭州"})
        self.assertEqual("demo_fixture", result["data_source"])

    def test_contracts_identical_with_and_without_firecrawl(self):
        demo = [tool["function"]["name"] for tool in build_travel_registry().api_schemas()]
        live = [tool["function"]["name"] for tool in self.registry().api_schemas()]
        self.assertEqual(demo, live)

    def test_unknown_search_provider_rejected(self):
        with self.assertRaises(ValueError):
            build_travel_registry(search_provider="unknown")

    def test_combines_with_amap_provider(self):
        registry = build_travel_registry(
            provider="demo",
            search_provider="firecrawl",
            firecrawl_key="test-fc-key",
            firecrawl_opener=fake_opener,
        )
        catalog = {tool["name"] for tool in registry.catalog()}
        self.assertEqual(8, len(catalog))

    def test_legacy_text_search_matches_training_format(self):
        registry = build_travel_registry(
            search_provider="firecrawl",
            firecrawl_key="test-fc-key",
            firecrawl_opener=fake_opener,
            legacy_text=True,
        )
        result = registry.execute("search", {"query": ["杭州 西湖"]})
        text = result["text"]
        self.assertTrue(text.startswith("A web search for '杭州 西湖' returned:"))
        self.assertIn("## Web Results", text)
        self.assertIn("1. [西湖攻略](https://example.com/xihu)", text)
        self.assertIn("snippet: 景点介绍", text)

    def test_legacy_text_search_failure_matches_training_message(self):
        registry = build_travel_registry(
            search_provider="firecrawl",
            firecrawl_key="test-fc-key",
            firecrawl_opener=fake_opener,
            legacy_text=True,
        )
        result = registry.execute("search", {"query": ["boom"]})
        self.assertIn("Firecrawl search Timeout or error", result["text"])
        self.assertIn("return None, Please try again later.", result["text"])

    def test_legacy_text_visit_joins_summaries(self):
        registry = build_travel_registry(
            search_provider="firecrawl",
            firecrawl_key="test-fc-key",
            firecrawl_opener=fake_opener,
            legacy_text=True,
        )
        result = registry.execute("visit", {"url": "https://example.com/a", "goal": "了解"})
        self.assertIn("text", result)
        self.assertIn("正文", result["text"])


if __name__ == "__main__":
    unittest.main()
