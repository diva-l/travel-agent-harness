from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import Any, Callable


class ToolValidationError(ValueError):
    pass


Guardrail = Callable[[dict[str, Any]], None]
OutputGuardrail = Callable[[Any], None]


@dataclass(slots=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Any]
    requires_approval: bool = False
    side_effect_free: bool = True
    input_guardrails: list[Guardrail] = field(default_factory=list)
    output_guardrails: list[OutputGuardrail] = field(default_factory=list)

    def api_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"duplicate tool: {spec.name}")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolValidationError(f"tool not found: {name}") from exc

    def api_schemas(self) -> list[dict[str, Any]]:
        return [spec.api_schema() for spec in self._tools.values()]

    def catalog(self) -> list[dict[str, Any]]:
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.parameters,
                "requires_approval": spec.requires_approval,
                "side_effect_free": spec.side_effect_free,
                "input_guardrails": len(spec.input_guardrails),
                "output_guardrails": len(spec.output_guardrails),
            }
            for spec in self._tools.values()
        ]

    def execute(self, name: str, arguments: dict[str, Any]) -> Any:
        spec = self.get(name)
        validate_json_object(arguments, spec.parameters)
        for guardrail in spec.input_guardrails:
            guardrail(arguments)
        result = spec.handler(**arguments)
        for guardrail in spec.output_guardrails:
            guardrail(result)
        return result


def _looks_like_schema_echo(value: dict[str, Any]) -> bool:
    """The planner sometimes copies the tool *definition* into arguments
    ({"type": "object", "properties": {...}}) instead of passing values —
    an OOD failure mode of the tagged RL model on field-style queries."""
    return value.get("type") == "object" and isinstance(value.get("properties"), dict)


def validate_json_object(value: dict[str, Any], schema: dict[str, Any]) -> None:
    if not isinstance(value, dict) or schema.get("type") != "object":
        raise ToolValidationError("tool schema root must be object")
    if _looks_like_schema_echo(value):
        required = schema.get("required") or []
        raise ToolValidationError(
            "参数错误：arguments 里填的是工具定义（JSON Schema），不是具体取值。"
            f"请重新调用，并把参数写成具体值，例如本工具需要字段：{required}。"
        )
    required = set(schema.get("required") or [])
    missing = required - value.keys()
    if missing:
        raise ToolValidationError(f"missing required fields: {sorted(missing)}")
    properties = schema.get("properties") or {}
    if schema.get("additionalProperties") is False:
        extras = value.keys() - properties.keys()
        if extras:
            raise ToolValidationError(f"unexpected fields: {sorted(extras)}")
    for key, item in value.items():
        property_schema = properties.get(key)
        if property_schema is None:
            continue
        expected = property_schema.get("type")
        allowed_types = expected if isinstance(expected, list) else [expected]
        if not any(_matches_json_type(item, kind) for kind in allowed_types):
            raise ToolValidationError(f"{key} must be {' or '.join(allowed_types)}")
        if "enum" in property_schema and item not in property_schema["enum"]:
            raise ToolValidationError(f"{key} must be one of {property_schema['enum']}")
        if isinstance(item, list):
            if len(item) < property_schema.get("minItems", 0):
                raise ToolValidationError(f"{key} has too few items")
            if "maxItems" in property_schema and len(item) > property_schema["maxItems"]:
                raise ToolValidationError(f"{key} has too many items")
            item_schema = property_schema.get("items") or {}
            item_type = item_schema.get("type")
            if item_type and any(not _matches_json_type(child, item_type) for child in item):
                raise ToolValidationError(f"{key} items must be {item_type}")
        if isinstance(item, (int, float)) and not isinstance(item, bool):
            if "minimum" in property_schema and item < property_schema["minimum"]:
                raise ToolValidationError(f"{key} is below minimum")
            if "maximum" in property_schema and item > property_schema["maximum"]:
                raise ToolValidationError(f"{key} is above maximum")


def _matches_json_type(value: Any, expected: str | None) -> bool:
    checks = {
        "string": lambda: isinstance(value, str),
        "integer": lambda: isinstance(value, int) and not isinstance(value, bool),
        "number": lambda: isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": lambda: isinstance(value, bool),
        "array": lambda: isinstance(value, list),
        "object": lambda: isinstance(value, dict),
        "null": lambda: value is None,
    }
    return checks.get(expected, lambda: True)()


def _stable_number(*parts: str, minimum: int, maximum: int) -> int:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).digest()
    return minimum + int.from_bytes(digest[:4], "big") % (maximum - minimum + 1)


def _data_source_guardrail(result: Any) -> None:
    if not isinstance(result, dict) or "data_source" not in result:
        raise ToolValidationError("travel lookup must identify its data source")


CITY_CENTERS: dict[str, tuple[float, float]] = {
    "上海": (121.4737, 31.2304),
    "杭州": (120.1551, 30.2741),
    "苏州": (120.5853, 31.2989),
    "重庆": (106.5516, 29.5630),
    "北京": (116.4074, 39.9042),
    "成都": (104.0665, 30.5723),
    "西安": (108.9398, 34.3416),
    "广州": (113.2644, 23.1291),
    "深圳": (114.0579, 22.5431),
}

POI_COORDINATES: dict[str, tuple[float, float]] = {
    "西湖": (120.1430, 30.2496),
    "断桥残雪": (120.1484, 30.2591),
    "北山街": (120.1427, 30.2587),
    "灵隐寺": (120.1022, 30.2408),
    "法喜寺": (120.0929, 30.2258),
    "龙井村": (120.1114, 30.2186),
    "小河直街": (120.1465, 30.3204),
    "桥西历史街区": (120.1397, 30.3199),
    "外滩": (121.4903, 31.2417),
    "武康路": (121.4389, 31.2085),
    "洪崖洞": (106.5790, 29.5624),
    "解放碑": (106.5770, 29.5570),
    "磁器口": (106.4497, 29.5811),
}


def _coordinate_for(text: str, region: str = "") -> tuple[float, float]:
    for name, coordinate in POI_COORDINATES.items():
        if name in text or text in name:
            return coordinate
    center = next(
        (coordinate for city, coordinate in CITY_CENTERS.items() if city in region or city in text),
        (116.4074, 39.9042),
    )
    lng_delta = _stable_number(text, "lng", minimum=-400, maximum=400) / 10_000
    lat_delta = _stable_number(text, "lat", minimum=-300, maximum=300) / 10_000
    return round(center[0] + lng_delta, 6), round(center[1] + lat_delta, 6)


def _parse_coordinate(value: str) -> tuple[float, float]:
    try:
        lng_text, lat_text = value.split(",", 1)
        lng, lat = float(lng_text), float(lat_text)
    except (AttributeError, ValueError) as exc:
        raise ToolValidationError("coordinate must use lng,lat format") from exc
    if not -180 <= lng <= 180 or not -90 <= lat <= 90:
        raise ToolValidationError("coordinate is out of range")
    return lng, lat


def search(query: list[str]) -> dict[str, Any]:
    return {
        "data_source": "demo_fixture",
        "queries": query,
        "results": [
            {
                "query": item,
                "items": [{
                    "title": f"{item}｜出行信息索引",
                    "url": f"https://example.invalid/travel/{index}",
                    "snippet": "演示检索摘要；营业时间、价格和预约规则需以官方渠道复核。",
                }],
            }
            for index, item in enumerate(query, start=1)
        ],
        "notice": "离线演示检索，不代表实时网页结果。",
    }


def visit(url: str | list[str], goal: str) -> dict[str, Any]:
    urls = [url] if isinstance(url, str) else url
    return {
        "data_source": "demo_fixture",
        "goal": goal,
        "pages": [{
            "url": item,
            "summary": "演示页面摘要；涉及价格、开放时间和交通状态时请二次确认。",
        } for item in urls],
        "notice": "离线演示访问器未抓取真实网页。",
    }


def weather_search(city: str) -> dict[str, Any]:
    temperature = _stable_number(city, "temperature", minimum=8, maximum=31)
    condition = ["晴", "多云", "小雨"][_stable_number(city, "weather", minimum=0, maximum=2)]
    return {
        "data_source": "demo_fixture",
        "city": city,
        "forecast": [{"condition": condition, "temperature_c": temperature}],
        "notice": "演示天气，不可替代出发前的实时预报。",
    }


def flights_search(date: str, from_city: str, to_city: str) -> dict[str, Any]:
    fare = _stable_number(from_city, to_city, date, "flight", minimum=360, maximum=980)
    return {
        "data_source": "demo_fixture",
        "query": {"date": date, "from_city": from_city, "to_city": to_city},
        "options": [{"flight_no": "DEMO01", "fare_cny": fare, "duration_minutes": 125}],
        "notice": "演示航班，不代表实时票价、班次或余票。",
    }


def train_tickets_search(date: str, from_city: str, to_city: str) -> dict[str, Any]:
    fare = _stable_number(from_city, to_city, date, "rail", minimum=80, maximum=420)
    return {
        "data_source": "demo_fixture",
        "query": {"date": date, "from_city": from_city, "to_city": to_city},
        "options": [{
            "train_no": "G-DMO",
            "fare_cny": fare,
            "duration_minutes": max(45, fare // 2),
        }],
        "notice": "演示车次，不代表实时票价、班次或余票。",
    }


def poi_search(address: str, region: str = "") -> dict[str, Any]:
    lng, lat = _coordinate_for(address, region)
    return {
        "data_source": "demo_fixture",
        "query": {"address": address, "region": region},
        "pois": [{
            "name": address,
            "address": f"{region}{address}" if region else address,
            "location": {"lng": lng, "lat": lat},
        }],
        "notice": "公开演示坐标仅用于路线可视化，导航前请用地图服务复核。",
    }


def around_search(
    location: str,
    radius: int = 5000,
    keyword: str = "",
    region: str = "",
) -> dict[str, Any]:
    lng, lat = _parse_coordinate(location)
    pois = []
    for index in range(3):
        angle = math.radians(50 + index * 120)
        scale = min(radius, 5000) / 111_000 * (0.25 + index * 0.14)
        pois.append({
            "name": f"{keyword or '周边地点'} {index + 1}",
            "region": region,
            "location": {
                "lng": round(lng + math.cos(angle) * scale, 6),
                "lat": round(lat + math.sin(angle) * scale, 6),
            },
            "distance_m": round(min(radius, 5000) * (0.25 + index * 0.14)),
        })
    return {
        "data_source": "demo_fixture",
        "center": {"lng": lng, "lat": lat},
        "radius_m": radius,
        "pois": pois,
        "notice": "演示周边搜索，不代表实时门店与营业状态。",
    }


def route_planning(
    origin: str,
    destination: str,
    mode: str = "driving",
    waypoints: str = "",
) -> dict[str, Any]:
    points = [_parse_coordinate(origin)]
    if waypoints.strip():
        points.extend(
            _parse_coordinate(item.strip()) for item in waypoints.split(";") if item.strip()
        )
    points.append(_parse_coordinate(destination))
    distance = 0.0
    for current, following in zip(points, points[1:]):
        dx = (following[0] - current[0]) * 95_000
        dy = (following[1] - current[1]) * 111_000
        distance += math.hypot(dx, dy)
    speed = {
        "walking": 4.5,
        "bicycling": 15,
        "electrobike": 22,
        "transit": 24,
        "driving": 32,
    }[mode]
    return {
        "data_source": "deterministic_route_geometry",
        "mode": mode,
        "distance_m": round(distance),
        "duration_minutes": max(1, round(distance / 1000 / speed * 60)),
        "polyline": [{"lng": lng, "lat": lat} for lng, lat in points],
        "notice": "演示路线关系，不提供逐向导航；出发前请用地图服务复核。",
    }


def build_travel_registry(
    provider: str = "demo",
    *,
    search_provider: str = "demo",
    amap_key: str = "",
    amap_timeout: float = 15.0,
    amap_opener: Callable[..., Any] | None = None,
    firecrawl_key: str = "",
    firecrawl_timeout: float = 30.0,
    firecrawl_opener: Callable[..., Any] | None = None,
    tool_http_pool_size: int = 8,
    tool_http_retries: int = 2,
    tool_cache_ttl: float = 0.0,
    visit_extractor: Callable[[str, str], str] | None = None,
    legacy_text: bool = False,
    ticket_simulator: Any | None = None,
) -> ToolRegistry:
    """Expose the same eight tool contracts used by the Agentic RL planner.

    Contracts (names, descriptions, parameter schemas) are verbatim copies of the
    RL training environment so the trained planner stays in-distribution; only
    the handlers behind them change. ``provider="demo"`` keeps the deterministic
    offline fixtures (default, used by tests); ``provider="amap"`` swaps the four
    location-based tools to the AMap Web service. ``search_provider="firecrawl"``
    independently swaps ``search``/``visit`` to live web retrieval, so an
    evaluation run can combine both (``provider="amap", search_provider="firecrawl"``).
    """
    definitions = [
        ToolSpec(
            "visit",
            "访问网页并根据目标信息返回内容摘要。",
            {
                "type": "object",
                "properties": {
                    "url": {
                        "type": ["string", "array"],
                        "items": {"type": "string"},
                        "minItems": 1,
                        "description": "要访问的网页URL，可为单个URL或URL数组。",
                    },
                    "goal": {"type": "string", "description": "访问网页需要获得的目标信息。"},
                },
                "required": ["url", "goal"],
                "additionalProperties": False,
            },
            visit,
        ),
        ToolSpec(
            "search",
            "执行批量 Google Search：提供 query 数组，一次调用检索每个查询前5个结果。",
            {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "查询字符串数组。",
                    }
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            search,
        ),
        ToolSpec(
            "weather_search",
            "根据城市名称查询指定城市天气。",
            {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "城市名称"},
                },
                "required": ["city"],
                "additionalProperties": False,
            },
            weather_search,
        ),
        ToolSpec(
            "flights_search",
            "根据日期查询城市间航班信息。",
            _intercity_schema(),
            flights_search,
        ),
        ToolSpec(
            "train_tickets_search",
            "根据日期查询城市间火车/动车/高铁票信息。",
            _intercity_schema(),
            train_tickets_search,
        ),
        ToolSpec(
            "route_planning",
            "路线规划：驾车/步行/骑行/电动车/公交。",
            {
                "type": "object",
                "properties": {
                    "origin": {"type": "string", "description": "起点经纬度，经度在前，格式 lng,lat"},
                    "destination": {"type": "string", "description": "终点经纬度，经度在前，格式 lng,lat"},
                    "mode": {
                        "type": "string",
                        "enum": ["driving", "walking", "bicycling", "electrobike", "transit"],
                        "description": "路线类型，默认 driving",
                    },
                    "waypoints": {"type": "string", "description": "途经点，多个点以 ; 分隔，每点格式 lng,lat"},
                },
                "required": ["origin", "destination"],
                "additionalProperties": False,
            },
            route_planning,
        ),
        ToolSpec(
            "poi_search",
            "按文本搜索地点，返回地址、经纬度、商业信息。",
            {
                "type": "object",
                "properties": {
                    "address": {"type": "string", "description": "待检索地点文本（单个地址，<=80字符）"},
                    "region": {"type": "string", "description": "可选，城市级区域（中文）"},
                },
                "required": ["address"],
                "additionalProperties": False,
            },
            poi_search,
        ),
        ToolSpec(
            "around_search",
            "以圆心+半径搜索周边地点。",
            {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "中心点经纬度，格式 lng,lat"},
                    "radius": {"type": "integer", "description": "半径（米），0-50000，默认5000"},
                    "keyword": {"type": "string", "description": "可选，单个关键词"},
                    "region": {"type": "string", "description": "可选，城市级区域（中文）"},
                },
                "required": ["location"],
                "additionalProperties": False,
            },
            around_search,
        ),
    ]
    registry = ToolRegistry()
    if provider == "amap":
        from .amap import amap_handlers

        overrides = amap_handlers(
            amap_key,
            timeout=amap_timeout,
            opener=amap_opener,
            pool_size=tool_http_pool_size,
            retries=tool_http_retries,
            cache_ttl=tool_cache_ttl,
        )
        for spec in definitions:
            if spec.name in overrides:
                spec.handler = overrides[spec.name]
    elif provider != "demo":
        raise ValueError(f"unknown tool provider: {provider}")
    if search_provider == "firecrawl":
        from .firecrawl import firecrawl_handlers

        overrides = firecrawl_handlers(
            firecrawl_key,
            timeout=firecrawl_timeout,
            opener=firecrawl_opener,
            # Firecrawl rate-limits harder than AMap; cap its share of the pool.
            pool_size=max(1, tool_http_pool_size // 2),
            retries=tool_http_retries,
            cache_ttl=tool_cache_ttl,
            extractor=visit_extractor,
            legacy_text=legacy_text,
        )
        for spec in definitions:
            if spec.name in overrides:
                spec.handler = overrides[spec.name]
    elif search_provider != "demo":
        raise ValueError(f"unknown search provider: {search_provider}")
    if ticket_simulator is not None:
        # Training parity: train/flight tickets are LLM-simulated
        # (travel_agentic_rl never called a real ticketing API).
        def _sim_flights(date: str, from_city: str, to_city: str) -> dict[str, Any]:
            return {
                "data_source": "llm_ticket_simulator",
                "text": ticket_simulator.flights(date, from_city, to_city),
                "notice": "航班信息为模拟数据，非真实票务；请以官方渠道复核。",
            }

        def _sim_trains(date: str, from_city: str, to_city: str) -> dict[str, Any]:
            return {
                "data_source": "llm_ticket_simulator",
                "text": ticket_simulator.train_tickets(date, from_city, to_city),
                "notice": "火车票信息为模拟数据，非真实票务；请以 12306 复核。",
            }

        for spec in definitions:
            if spec.name == "flights_search":
                spec.handler = _sim_flights
            elif spec.name == "train_tickets_search":
                spec.handler = _sim_trains
    for spec in definitions:
        spec.output_guardrails.append(_data_source_guardrail)
        registry.register(spec)
    return registry


def _intercity_schema() -> dict[str, Any]:
    city_field = lambda text: {"type": "string", "description": text}  # noqa: E731
    return {
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "日期，格式 YYYY-MM-DD"},
            "from_city": city_field("出发城市中文名"),
            "to_city": city_field("到达城市中文名"),
        },
        "required": ["date", "from_city", "to_city"],
        "additionalProperties": False,
    }
