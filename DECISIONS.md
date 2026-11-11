# Autonomous Engineering Decisions

This file records every non-obvious architectural decision made while implementing
`ROADMAP.md` unattended, so the choices can be reviewed later.

## Phase 1 — Docker, GPU passthrough & zero-copy file browser

1. **Project root = repository root.** `ROADMAP.md` shows a `video-speed-studio/`
   folder in the directory sketch, but the repository (`fast-frame`) already is the
   project. All paths are therefore rooted at the repo root; the Docker build
   context is `.`.
2. **`services/gpu_service.py` added.** The roadmap only lists
   `ffmpeg_builder.py`, `ffprobe_service.py` and `worker.py`; GPU/NVENC probing is
   isolated in its own module instead of bloating `main.py`, with a 30 s cache.
3. **Graceful CPU fallback.** `/api/system/gpu-check` always returns HTTP 200.
   If NVENC is missing it reports `status: "fallback"` and the encoder names
   `libx264`/`libx265`, so development on a laptop without CUDA still works.
4. **Auto-rotation is disabled (`-noautorotate`).** Raw coded dimensions are used
   for all geometry math and the file's display-matrix rotation is exposed as
   `suggested_rotation`. The UI pre-selects that value, so the CSS preview and the
   rendered output remain identical (WYSIWYG) and rotation is never applied twice.
5. **`-vsync vfr` instead of `-fps_mode vfr`.** Ubuntu 22.04 ships FFmpeg 4.4,
   which predates `-fps_mode`; `-vsync vfr` keeps the new timestamps produced by
   `setpts` instead of duplicating frames.
6. **Range streaming in 1 MiB chunks.** `GET /api/video/stream` implements
   `bytes=start-end`, suffix ranges and 416 responses, with `HEAD` support, so
   large MKV/AVI files seek instantly without copying.
7. **Path safety.** Every request path is resolved (symlinks followed) and compared
   against the real `HOST_MOUNT_PATH` with `os.path.commonpath`; violations return
   `403`, missing paths return `404`.
8. **Docker base toolchain.** Ubuntu 22.04's stock Node (12) and Python (3.10) are
   too old, so the image adds the deadsnakes PPA for Python 3.11 and NodeSource for
   Node 20. Frontend is built inside the image and served by FastAPI; Vite's dev
   server proxies `/api` and `/ws` to the backend during development.
9. **Compose defaults.** `./media:/mnt/host/media` is mounted by default so the
   stack starts out of the box; Windows (`D:/:/mnt/host/d`) and Linux
   (`/media:/mnt/host/media`) examples are included as comments and documented in
   the README.
