"""Same-origin local monitor API and read-only live transport."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Protocol

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from refinery_ai.monitor.journals import JournalCatalog


class Connection(Protocol):
    async def is_disconnected(self) -> bool: ...


async def stream_events(
    request: Connection,
    catalog: JournalCatalog,
    run_id: str,
    after: int,
) -> AsyncIterator[str]:
    from refinery_ai.monitor.journals import JournalTail

    path, _ = catalog.journal(run_id)
    tail = JournalTail(path, run_id)
    heartbeat = time.monotonic()
    while not await request.is_disconnected():
        try:
            batch = await asyncio.to_thread(tail.read_batch)
        except (OSError, ValueError):
            yield 'event: diagnostic\ndata: {"message":"Journal changed or is invalid; reload this run"}\n\n'
            return
        for event in batch:
            if event.sequence > after:
                yield f"id: {event.sequence}\ndata: {event.model_dump_json()}\n\n"
        if len(batch) == 256 or tail.error is not None:
            await asyncio.sleep(0)
            continue
        if tail.sequence < after:
            yield 'event: diagnostic\ndata: {"message":"Cursor exceeds journal; reload this run"}\n\n'
            return
        if time.monotonic() - heartbeat >= 10:
            yield ": heartbeat\n\n"
            heartbeat = time.monotonic()
        await asyncio.sleep(0.25)


def create_app(repository: Path, state_directory: Path, *, allowed_host: str) -> FastAPI:
    catalog = JournalCatalog(repository, state_directory)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    static = Path(__file__).parent / "static"

    @app.middleware("http")
    async def local_only(request: Request, call_next: Any) -> Any:
        origin = request.headers.get("origin")
        if request.headers.get("host") != allowed_host or (
            origin is not None and origin != f"http://{allowed_host}"
        ):
            return JSONResponse({"detail": "Local same-origin requests only"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        )
        return response

    @app.get("/api/runs")
    def runs() -> Any:
        return catalog.list_runs()

    def check_run(run_id: str) -> None:
        try:
            catalog.journal(run_id)
        except ValueError as error:
            raise HTTPException(400, "Invalid or unsafe run") from error
        except FileNotFoundError as error:
            raise HTTPException(404, "Run not found") from error

    @app.get("/api/runs/{run_id}")
    def snapshot(run_id: str) -> Any:
        check_run(run_id)
        return catalog.snapshot(run_id)

    @app.get("/api/runs/{run_id}/events")
    async def events(request: Request, run_id: str, after: int = 0) -> StreamingResponse:
        check_run(run_id)
        try:
            cursor = int(request.headers.get("last-event-id", str(after)))
            if cursor < 0:
                raise ValueError
        except ValueError as error:
            raise HTTPException(400, "Invalid event cursor") from error
        return StreamingResponse(
            stream_events(request, catalog, run_id, cursor),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no"},
        )

    if (static / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

    @app.get("/{path:path}")
    def index(path: str) -> FileResponse:
        if path.startswith("api/") or path.startswith("assets/") or "." in path:
            raise HTTPException(404, "Not found")
        if not (static / "index.html").is_file():
            raise HTTPException(503, "Dashboard assets missing; rebuild web/monitor")
        return FileResponse(static / "index.html")

    return app
