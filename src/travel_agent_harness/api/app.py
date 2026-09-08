from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response
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
