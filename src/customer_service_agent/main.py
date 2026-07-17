from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from customer_service_agent.api.dependencies import Container, build_container
from customer_service_agent.api.routes import router
from customer_service_agent.middleware.logging import TraceLoggingMiddleware

WEB_ROOT = Path(__file__).resolve().parent / "web"


def create_app(container: Container | None = None) -> FastAPI:
    container = container or build_container()
    logging.basicConfig(
        level=getattr(logging, container.settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    app = FastAPI(
        title="Logistics Customer Service DeepAgent",
        version="0.1.0",
        description="Trilingual stateful customer-service demo migrated from Dify.",
    )
    app.state.container = container
    app.add_middleware(TraceLoggingMiddleware)
    app.mount("/static", StaticFiles(directory=WEB_ROOT), name="static")
    app.include_router(router)

    @app.get("/", include_in_schema=False)
    async def chat_interface() -> FileResponse:
        return FileResponse(WEB_ROOT / "index.html")

    return app


app = create_app()
