from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Header, Request
from fastapi.responses import StreamingResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from pi_beacon.collector import Collector
from pi_beacon.config import Settings, load_settings
from pi_beacon.models import DashboardSnapshot, RuntimeStatus, WaybarPayload

API_VERSION = 1
REVISION_HEADER = b"x-pi-beacon-revision"


class RevisionHeaderMiddleware:
    def __init__(self, app: ASGIApp, revision: Callable[[], int]):
        self.app = app
        self.revision = revision

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/v1/"):
            await self.app(scope, receive, send)
            return

        async def send_with_revision(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((REVISION_HEADER, str(self.revision()).encode("ascii")))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_revision)


def get_collector(request: Request) -> Collector:
    return request.app.state.collector


CollectorDependency = Annotated[Collector, Depends(get_collector)]
LastEventId = Annotated[str | None, Header(alias="Last-Event-ID")]


def parse_revision(last_event_id: str | None) -> int:
    try:
        return int(last_event_id or "0")
    except ValueError:
        return 0


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or load_settings()
    collector = Collector(resolved_settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.collector = collector
        await collector.start()
        try:
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(collector.run())
                try:
                    yield
                finally:
                    collector.stop()
        finally:
            await collector.close()

    app = FastAPI(title="Pi Beacon", version="1.1.0", lifespan=lifespan)
    app.add_middleware(RevisionHeaderMiddleware, revision=lambda: collector.revision)
    v1 = APIRouter(prefix="/v1")

    @app.get("/healthz")
    async def health(current: CollectorDependency) -> dict[str, int | str]:
        return {"status": "ok", "version": API_VERSION, "revision": current.revision}

    @v1.get("/snapshot", response_model=DashboardSnapshot)
    async def snapshot(current: CollectorDependency) -> DashboardSnapshot:
        return current.snapshot

    @v1.get("/runtime", response_model=RuntimeStatus)
    async def runtime(current: CollectorDependency) -> RuntimeStatus:
        return current.snapshot.runtime

    @v1.get("/waybar", response_model=WaybarPayload)
    async def waybar(current: CollectorDependency) -> WaybarPayload:
        return current.waybar

    def event_response(stream: AsyncIterator[str]) -> StreamingResponse:
        return StreamingResponse(
            stream,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @v1.get("/events", response_class=StreamingResponse)
    async def events(
        current: CollectorDependency,
        last_event_id: LastEventId = None,
    ) -> StreamingResponse:
        return event_response(current.events(parse_revision(last_event_id)))

    @v1.get("/events/waybar", response_class=StreamingResponse)
    async def waybar_events(
        current: CollectorDependency,
        last_event_id: LastEventId = None,
    ) -> StreamingResponse:
        return event_response(current.events(parse_revision(last_event_id), "waybar"))

    app.include_router(v1)
    return app


app = create_app()
