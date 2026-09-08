"""AMap (高德) Web service provider for the geo tool contracts.

Only the four location-based tools have a real upstream here; ``search``,
``visit``, ``flights_search`` and ``train_tickets_search`` stay on the demo
provider because AMap does not offer equivalents. All handlers keep the same
signatures and result envelopes as the demo provider, and every result carries
``data_source`` so the output guardrail and the evidence boundary still apply.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from .http_client import PooledHttpClient, TTLCache
from .tools import ToolValidationError


BASE_URL = "https://restapi.amap.com"
MAX_POLYLINE_POINTS = 120
# infocode signalled inside a 200 body when the concurrent QPS limit is hit;
# worth retrying because the window is sub-second.
RATE_LIMIT_INFOCODES = {"10021"}

Opener = Callable[..., Any]


def _is_rate_limited(body: dict[str, Any]) -> bool:
    return body.get("status") == "0" and str(body.get("infocode", "")) in RATE_LIMIT_INFOCODES


def _amap_error(body: Any) -> ToolValidationError:
    if not isinstance(body, dict):
        return ToolValidationError("amap returned an unexpected payload")
    if body.get("status") == "0":
        return ToolValidationError(
            f"amap error {body.get('infocode', '?')}: {body.get('info', 'unknown')}"
        )
    return ToolValidationError(
        f"amap error {body.get('errcode')}: {body.get('errmsg', 'unknown')}"
    )


def _default_opener(request: urllib.request.Request, timeout: float) -> bytes:
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


class AmapProvider:
    """Small AMap Web service client with an injectable opener for tests."""

    def __init__(
        self,
        api_key: str,
        *,
        timeout: float = 15.0,
        opener: Opener | None = None,
        pool_size: int = 8,
        retries: int = 2,
        cache_ttl: float = 0.0,
    ) -> None:
        if not api_key:
            raise ToolValidationError("amap provider requires an API key")
        self._key = api_key
        self._timeout = timeout
        self._opener = opener or PooledHttpClient(maxsize=pool_size, retries=retries).open
        self._cache = TTLCache(cache_ttl)

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        query = urllib.parse.urlencode({**params, "key": self._key, "output": "json"})
        url = f"{BASE_URL}{path}?{query}"
        cached = self._cache.get(url)
        if cached is not None:
            return json.loads(cached.decode("utf-8"))
        # amap signals its concurrent-QPS limit inside a 200 body
        # (infocode 10021 CUQPS_HAS_EXCEEDED_THE_LIMIT), which bypasses the
        # HTTP client's transport-level retries — retry it here with backoff
        # so parallel tool bursts degrade into latency instead of failures.
        last_error: ToolValidationError | None = None
        for attempt in range(4):
            body, raw = self._fetch(url)
            if isinstance(body, dict) and not _is_rate_limited(body):
                if isinstance(raw, bytes):
                    self._cache.put(url, raw)
                return body
            last_error = _amap_error(body)
            if attempt < 3:
                time.sleep(0.4 * (2 ** attempt))
        raise last_error or ToolValidationError("amap request failed")

    def _fetch(self, url: str) -> tuple[Any, Any]:
        request = urllib.request.Request(url, method="GET")
        try:
            raw = self._opener(request, timeout=self._timeout)
            body = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ToolValidationError(f"amap request failed: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ToolValidationError("amap returned invalid JSON") from exc
        if not isinstance(body, dict):
            raise ToolValidationError("amap returned an unexpected payload")
        # v3 APIs signal errors with status/infocode; v4 APIs with errcode/errmsg.
        if body.get("status") == "0" and not _is_rate_limited(body):
            raise _amap_error(body)
        if body.get("errcode") not in (None, 0):
            raise _amap_error(body)
        return body, raw

    # --- tool handlers -------------------------------------------------

    def weather_search(self, city: str) -> dict[str, Any]:
        body = self._get("/v3/weather/weatherInfo", {"city": city, "extensions": "all"})
        forecasts = body.get("forecasts") or []
        casts = forecasts[0].get("casts") if forecasts else []
        return {
            "data_source": "amap_web_service",
            "city": city,
            "forecast": [
                {
                    "date": cast.get("date", ""),
                    "condition": cast.get("dayweather", ""),
                    "temperature_c": _number(cast.get("daytemp")),
                    "night_condition": cast.get("nightweather", ""),
                    "night_temperature_c": _number(cast.get("nighttemp")),
                }
                for cast in (casts or [])[:4]
            ],
            "notice": "实时天气来自高德 Web 服务；极端天气仍以官方预警为准。",
        }

    def poi_search(self, address: str, region: str = "") -> dict[str, Any]:
        params: dict[str, Any] = {"keywords": address, "city_limit": "true"}
        if region:
            params["region"] = region
        body = self._get("/v3/place/text", params)
        return {
            "data_source": "amap_web_service",
            "query": {"address": address, "region": region},
            "pois": [_map_poi(poi) for poi in (body.get("pois") or [])[:8]],
            "notice": "地点数据来自高德 Web 服务；营业状态与预约规则请二次确认。",
        }

    def poi_snapshot(self, name: str, region: str = "") -> dict[str, Any]:
        """Best-effort POI enrichment: address, a photo URL and rating for cards."""
        params: dict[str, Any] = {
            "keywords": name,
            "extensions": "all",
            "offset": 5,
        }
        if region:
            params["region"] = region
            params["city_limit"] = "true"
        body = self._get("/v3/place/text", params)
        for poi in body.get("pois") or []:
            if not isinstance(poi, dict):
                continue
            photos = [
                str(photo.get("url"))
                for photo in (poi.get("photos") or [])
                if isinstance(photo, dict) and str(photo.get("url") or "").startswith("http")
            ]
            biz_ext = poi.get("biz_ext") if isinstance(poi.get("biz_ext"), dict) else {}
            return {
                "address": poi.get("address") if isinstance(poi.get("address"), str) else "",
                "image_url": photos[0] if photos else "",
                "rating": _number(biz_ext.get("rating")),
            }
        return {"address": "", "image_url": "", "rating": None}

    def around_search(
        self,
        location: str,
        radius: int = 5000,
        keyword: str = "",
        region: str = "",
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"location": location, "radius": min(radius, 50000)}
        if keyword:
            params["keywords"] = keyword
        body = self._get("/v3/place/around", params)
        return {
            "data_source": "amap_web_service",
            "center": _split_coordinate(location),
            "radius_m": radius,
            "pois": [_map_poi(poi, distance=True) for poi in (body.get("pois") or [])[:10]],
            "notice": "周边数据来自高德 Web 服务；营业状态请二次确认。",
        }

    def route_planning(
        self,
        origin: str,
        destination: str,
        mode: str = "driving",
        waypoints: str = "",
    ) -> dict[str, Any]:
        geocoded: dict[str, str] = {}
        if not _is_lnglat(origin):
            origin = self._geocode_place(origin, geocoded, "origin")
        if not _is_lnglat(destination):
            destination = self._geocode_place(destination, geocoded, "destination")
        if mode == "transit":
            return self._transit_route(origin, destination)
        if mode in {"bicycling", "electrobike"}:
            body = self._get("/v4/direction/bicycling", {"origin": origin, "destination": destination})
            paths = (body.get("data") or {}).get("paths") or []
            notice_suffix = "；电动车按骑行口径估算" if mode == "electrobike" else ""
        else:
            params: dict[str, Any] = {"origin": origin, "destination": destination}
            if mode == "driving" and waypoints.strip():
                params["waypoints"] = waypoints.strip()
            path = "/v3/direction/driving" if mode == "driving" else "/v3/direction/walking"
            body = self._get(path, params)
            paths = (body.get("route") or {}).get("paths") or []
            notice_suffix = "；waypoints 仅对驾车模式生效" if waypoints.strip() and mode != "driving" else ""
        if not paths:
            raise ToolValidationError("amap returned no route for the given coordinates")
        best = paths[0]
        result = {
            "data_source": "amap_web_service",
            "mode": mode,
            "distance_m": int(_number(best.get("distance")) or 0),
            "duration_minutes": max(1, round((_number(best.get("duration")) or 0) / 60)),
            "polyline": _extract_polyline(best),
            "notice": "路线数据来自高德 Web 服务" + notice_suffix + "；出发前请复核实时路况。",
        }
        if geocoded:
            result["geocoded"] = geocoded
        return result

    def _geocode_place(self, place: str, geocoded: dict[str, str], role: str) -> str:
        """Resolve a place name to ``lng,lat`` — the same fallback the RL rollout loop used."""
        body = self._get("/v3/place/text", {"keywords": place, "offset": 1})
        pois = body.get("pois") or []
        location = str(pois[0].get("location") or "") if pois else ""
        if not _is_lnglat(location):
            raise ToolValidationError(f"cannot geocode {role}: {place!r}")
        geocoded[role] = f"{place} -> {location}"
        return location

    def _transit_route(self, origin: str, destination: str) -> dict[str, Any]:
        regeo = self._get("/v3/geocode/regeo", {"location": origin})
        city = (
            ((regeo.get("regeocode") or {}).get("addressComponent") or {}).get("city")
            or ((regeo.get("regeocode") or {}).get("addressComponent") or {}).get("province")
        )
        if isinstance(city, list):  # AMap returns [] instead of "" for empty fields
            city = ""
        if not city:
            raise ToolValidationError("cannot resolve origin city for transit routing")
        body = self._get(
            "/v3/direction/transit/integrated",
            {"origin": origin, "destination": destination, "city": city},
        )
        transits = (body.get("route") or {}).get("transits") or []
        if not transits:
            raise ToolValidationError("amap returned no transit route for the given coordinates")
        best = transits[0]
        return {
            "data_source": "amap_web_service",
            "mode": "transit",
            "distance_m": int(_number(best.get("distance")) or 0),
            "duration_minutes": max(1, round((_number(best.get("duration")) or 0) / 60)),
            "polyline": [_split_coordinate(origin), _split_coordinate(destination)],
            "notice": "公交方案来自高德 Web 服务，折线仅表示起终点关系；班次以实时查询为准。",
        }


def amap_handlers(
    api_key: str,
    *,
    timeout: float = 15.0,
    opener: Opener | None = None,
    pool_size: int = 8,
    retries: int = 2,
    cache_ttl: float = 0.0,
) -> dict[str, Any]:
    """Return handler overrides for the four geo tools (contracts stay identical)."""
    provider = AmapProvider(
        api_key, timeout=timeout, opener=opener,
        pool_size=pool_size, retries=retries, cache_ttl=cache_ttl,
    )
    return {
        "weather_search": provider.weather_search,
        "poi_search": provider.poi_search,
        "around_search": provider.around_search,
        "route_planning": provider.route_planning,
    }


# --- payload helpers -----------------------------------------------------


def _is_lnglat(text: Any) -> bool:
    parts = str(text or "").split(",")
    if len(parts) != 2:
        return False
    return _number(parts[0]) is not None and _number(parts[1]) is not None


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _split_coordinate(value: str) -> dict[str, float]:
    lng_text, lat_text = str(value).split(",", 1)
    return {"lng": float(lng_text), "lat": float(lat_text)}


def _map_poi(poi: dict[str, Any], *, distance: bool = False) -> dict[str, Any]:
    mapped: dict[str, Any] = {
        "name": poi.get("name", ""),
        "address": poi.get("address") if isinstance(poi.get("address"), str) else "",
        "location": _split_coordinate(poi.get("location", "0,0")),
    }
    if distance:
        mapped["distance_m"] = int(_number(poi.get("distance")) or 0)
    return mapped


def _extract_polyline(path: dict[str, Any]) -> list[dict[str, float]]:
    points: list[dict[str, float]] = []
    for step in path.get("steps") or []:
        for pair in str(step.get("polyline") or "").split(";"):
            try:
                points.append(_split_coordinate(pair))
            except (ValueError, IndexError):
                continue
    if len(points) > MAX_POLYLINE_POINTS:
        stride = len(points) / MAX_POLYLINE_POINTS
        points = [points[int(index * stride)] for index in range(MAX_POLYLINE_POINTS)]
    return points
