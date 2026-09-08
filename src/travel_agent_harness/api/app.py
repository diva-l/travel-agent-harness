from __future__ import annotations

import hmac
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..config import HarnessConfig
from ..harness import TravelHarness, build_default_harness
from .routes import build_router
from .service import TaskService


def create_app(
    config: HarnessConfig | None = None,
    harness: TravelHarness | None = None,
) -> FastAPI:
    selected_config = config or HarnessConfig.from_env()
    selected_harness = harness or build_default_harness(selected_config)
    service = TaskService(
        selected_harness,
        selected_config,
        max_workers=selected_config.api_workers,
        max_queue=selected_config.api_queue_size,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        service.shutdown()

    app = FastAPI(
        title="Agentic Travel Planner",
        version=__version__,
        description="A travel planning product powered by a bounded, traceable Agent Harness.",
        lifespan=lifespan,
    )
    app.state.task_service = service

    if selected_config.api_token:
        @app.middleware("http")
        async def bearer_auth(request: Request, call_next):
            # Optional deployment guard (TRAVEL_HARNESS_API_TOKEN): every /api
            # route except health requires the bearer token; the static UI and
            # health probes stay open. Constant-time compare, no logging of the
            # presented credential.
            path = request.url.path
            if path.startswith("/api") and path != "/api/health":
                header = request.headers.get("authorization", "")
                presented = header.removeprefix("Bearer ") if header.startswith("Bearer ") else ""
                if not presented or not hmac.compare_digest(presented, selected_config.api_token):
                    return JSONResponse(
                        status_code=401,
                        content={"detail": "missing or invalid bearer token"},
                        headers={"WWW-Authenticate": "Bearer"},
                    )
            return await call_next(request)

    app.include_router(build_router(service))

    web_dir = Path(__file__).resolve().parents[1] / "web_dist"
    if not (web_dir / "index.html").is_file():
        raise RuntimeError(
            "frontend build missing; run `npm ci && npm run build` in frontend/ "
            "or reinstall the package with bundled assets"
        )
    app.mount("/assets", StaticFiles(directory=web_dir / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(web_dir / "index.html")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return Response(status_code=204)

    return app
