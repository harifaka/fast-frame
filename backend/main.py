"""Fast-Frame API: zero-copy GPU video speed & processing studio."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

import config
from config import (
    DEFAULT_SUBTITLE_FONT_SIZE,
    OUTPUT_EXTENSIONS,
    PathSecurityError,
    SPHERICAL_EXTENSIONS,
    SUBTITLE_EXTENSIONS,
    SUBTITLE_FONT_SIZES,
    SUPPORTED_SPEED_MAX,
    SUPPORTED_SPEED_MIN,
    VIDEO_EXTENSIONS,
)
from routers import ai, filesystem, network, queue
from services.ai_view_service import TRACKING_MODES
from services.gpu_service import get_gpu_info
from services.worker import event_bus, job_manager
from services.yolo_tracking_service import tracking_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("fastframe")

FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
APP_VERSION = "1.1.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    root = config.host_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:  # read-only or permission problem
        logger.warning("Could not create host mount %s: %s", root, exc)
    network_root = config.network_root()
    try:
        network_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("Could not create network mount root %s: %s", network_root, exc)
    logger.info("Fast-Frame serving host mount %s", root)
    logger.info("Network shares (SMB/CIFS) are registered under %s", network_root)
    await job_manager.start()
    logger.info("Render queue worker started")
    try:
        yield
    finally:
        await job_manager.shutdown()
        logger.info("Render queue worker stopped")


app = FastAPI(
    title="Fast-Frame",
    description="GPU-accelerated video speed & processing studio",
    version=APP_VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Range", "Accept-Ranges", "Content-Length", "X-File-Size"],
)

app.include_router(filesystem.router)
app.include_router(network.router)
app.include_router(queue.router)
app.include_router(ai.router)


@app.websocket("/ws/progress")
async def progress_socket(websocket: WebSocket) -> None:
    """Stream job snapshots and live FFmpeg progress to the studio UI."""

    await websocket.accept()
    subscriber = event_bus.subscribe()

    async def sender() -> None:
        while True:
            try:
                event = await asyncio.wait_for(subscriber.get(), timeout=20)
            except asyncio.TimeoutError:
                event = {"type": "ping", "ts": time.time()}
            await websocket.send_json(event)

    async def receiver() -> None:
        while True:
            await websocket.receive_text()

    try:
        await websocket.send_json({"type": "snapshot", **job_manager.snapshot()})
        tasks = [asyncio.create_task(sender()), asyncio.create_task(receiver())]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        for task in done:
            with contextlib.suppress(WebSocketDisconnect, asyncio.CancelledError, Exception):
                task.result()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        event_bus.unsubscribe(subscriber)


@app.exception_handler(PathSecurityError)
async def path_security_handler(_request: Request, exc: PathSecurityError) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": str(exc)})


@app.get("/api/health", tags=["system"])
async def health() -> dict:
    return {"status": "ok", "version": APP_VERSION, "host_root": str(config.host_root())}


@app.get("/api/system/gpu-check", tags=["system"])
async def gpu_check(refresh: bool = False) -> dict:
    """Report the detected GPU and NVENC encoder availability.

    When NVENC is unavailable (e.g. a development laptop) the response still
    succeeds and advertises the libx264/libx265 CPU fallback.
    """

    return await get_gpu_info(force=refresh)


@app.get("/api/system/config", tags=["system"])
async def system_config() -> dict:
    return {
        "version": APP_VERSION,
        "host_root": str(config.host_root()),
        "video_extensions": list(VIDEO_EXTENSIONS),
        "spherical_extensions": list(SPHERICAL_EXTENSIONS),
        "subtitle_extensions": list(SUBTITLE_EXTENSIONS),
        "output_extensions": list(OUTPUT_EXTENSIONS),
        "speed": {"min": SUPPORTED_SPEED_MIN, "max": SUPPORTED_SPEED_MAX},
        "subtitle_font_sizes": SUBTITLE_FONT_SIZES,
        "default_subtitle_font_size": DEFAULT_SUBTITLE_FONT_SIZE,
        "network": {
            "enabled": True,
            "root": str(config.network_root()),
            "url_schemes": list(config.NETWORK_URL_SCHEMES),
        },
        "ai_view": {
            "enabled": True,
            "mcp_ready": True,
            "view_state_endpoint": "/api/ai/view-state",
            "mcp_command_endpoint": "/api/ai/mcp-command",
            "tracking_modes": list(TRACKING_MODES),
            "tracking": {
                "enabled": True,
                "status_endpoint": "/api/ai/tracking/status",
                "toggle_endpoint": "/api/ai/tracking",
                "analyze_endpoint": "/api/ai/tracking/analyze",
                "frame_endpoint": "/api/ai/tracking/frame",
                "detector": tracking_service.status(),
            },
        },
        "codecs": [
            {"id": "h264", "label": "H.264 (max compatibility)"},
            {"id": "hevc", "label": "H.265 / HEVC (smaller, faster USB writes)"},
        ],
    }


if (FRONTEND_DIST / "index.html").is_file():
    from fastapi.staticfiles import StaticFiles

    if (FRONTEND_DIST / "assets").is_dir():
        app.mount(
            "/assets",
            StaticFiles(directory=FRONTEND_DIST / "assets"),
            name="assets",
        )

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> FileResponse:
        if full_path.startswith(("api/", "ws/")):
            raise HTTPException(status_code=404, detail="Not found")
        candidate = (FRONTEND_DIST / full_path).resolve()
        if candidate.is_file() and FRONTEND_DIST.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")

else:

    @app.get("/", include_in_schema=False)
    async def dev_root() -> dict:
        return {
            "service": "Fast-Frame API",
            "version": APP_VERSION,
            "frontend": "not built - run `npm run dev` in frontend/ or `npm run build`",
            "docs": "/docs",
        }
