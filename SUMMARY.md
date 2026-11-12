# Fast-Frame — Implementation Summary

All nine phases from `ROADMAP.md` are implemented, verified and committed.

## ▶ Run it when you're back

1. **Nothing to configure.** [`docker-compose.yml`](docker-compose.yml) already mounts
   `/run/desktop/mnt/host` (every Windows drive + USB), `C:/` and `./media`; the Quick drives bar in
   `Browse files` lists them. On Linux, replace the two Windows mounts with `/media` / `/run/media`.
2. Start the stack:

```bash
docker compose up --build -d
```

   On a machine without NVIDIA drivers use the CPU override:
   `docker compose -f docker-compose.yml -f docker-compose.cpu.yml up --build -d`.

3. Open **http://localhost:8000** — the header should show
   **“NVIDIA GeForce RTX 3060 — NVENC Ready”** (or your GPU) within a few seconds.

Full setup, troubleshooting and API docs: [`README.md`](README.md). Every autonomous trade-off:
[`DECISIONS.md`](DECISIONS.md).

---

## Completed features

### Phase 1 — Docker, GPU passthrough, zero-copy file browser
- `Dockerfile` on `nvidia/cuda:12.2.0-runtime-ubuntu22.04` with FFmpeg (NVENC + libass),
  Python 3.11 (deadsnakes), Node 20 (NodeSource); builds the frontend into the image.
- `docker-compose.yml` with `gpus: all`, NVIDIA driver capabilities, `/mnt/host` bind mounts,
  healthcheck and a documented `deploy.resources` fallback for older Compose.
- `GET /api/system/gpu-check` — `nvidia-smi` GPU name/driver/VRAM + NVENC encoder detection with a
  30 s cache and graceful `libx264`/`libx265` fallback.
- `GET /api/fs/list` — directory listing with breadcrumbs, size/date, video/subtitle classification
  and strict path-traversal protection (403 outside `HOST_MOUNT_PATH`, symlinks resolved).
- `GET /api/video/metadata` — duration, dimensions, display dimensions, FPS, codec, bitrate, audio
  streams, display-matrix rotation.
- `GET /api/video/stream` — full HTTP **206 Partial Content** streaming (start-end and suffix ranges,
  416 handling, 1 MiB chunks) so large MKV/AVI files seek instantly.
- React/Vite/Tailwind dark studio shell: header GPU status bar, dual-pane file browser (source video
  and target directory), metadata panel and streaming preview player.

### Phase 2 — GPU speed engine, HEVC toggle, pitch-correct audio
- `atempo` chain generator (0.25×–4×) that never leaves the 0.5–2.0 per-filter bounds
  (3× → `atempo=2,atempo=1.5`, 0.25× → `atempo=0.5,atempo=0.5`), skipped for silent videos.
- `setpts=PTS/<speed>` with `passthrough` timestamp mode (version-aware `-fps_mode` /
  `-vsync`), so 2× speed keeps every frame instead of dropping half of them.
- NVENC args: `h264_nvenc -preset p6 -rc vbr -cq 19 -b:v 0` /
  `hevc_nvenc -preset p6 -rc vbr -cq 21 -b:v 0 -tag:v hvc1`.
- Async job manager + `GET/POST /api/queue*` + `WS /ws/progress` parsing `-progress pipe:1`
  (`out_time_us`) into percentage vs. the speed-adjusted target duration, FPS, encode speed and ETA.
- Automatic **runtime NVENC→CPU retry** when the GPU encoder cannot open.
- UI: speed presets (0.5×–3×), `+0.25`, fine slider 0.25×–4× synced live to `<video>.playbackRate`,
  H.264/HEVC toggle, editable output filename, live progress with cancel.

### Phase 3 — Multi-cut & subtitle burn-in
- Keep-segment list (`trim`/`atrim` + `setpts=PTS-STARTPTS`, `concat=n=N:v=1:a=1`, `v=1:a=0` for
  silent files); `target_duration = Σ segment durations / speed`.
- `.srt` burn-in at original 1× timestamps **before** trimming, with FFmpeg-safe path escaping
  (validated on a real Windows `C:/…` path) and Small/Medium/Large font sizes (16/24/32).
- Interactive timeline: Set In / Set Out / Add Keep Segment, click-to-seek chips with delete,
  playhead, kept-region overlay, output-length preview.
- Automatic subtitle detection (`<video>.srt`), dropdown and font-size selector.

### Phase 4 — Rotation & interactive crop
- Pipeline order enforced: **Subtitles → Multi-trim/Concat → Rotate/Flip → Crop → Speed**.
- `transpose=1`, `transpose=2`, `transpose=1,transpose=1`, `hflip`; display-matrix rotation is
  pre-selected in the UI so preview and render match exactly.
- Normalized crop → even pixel coordinates on the **oriented** frame (width/height swapped for 90°),
  clamped to the frame, yuv420p-safe.
- Live CSS transform preview, draggable/resizable crop overlay with rule-of-thirds guides,
  Free / 16:9 / 9:16 / 1:1 presets and live pixel dimensions.

### Phase 5 — Batch queue, presets & polish
- Sequential batch rendering (one GPU job at a time), Render Now / Add to Queue, cancel running
  FFmpeg (`terminate`, then kill after grace) and removal of partial files.
- Collision protection at enqueue time (`_1`, `_2`, …), source file always protected, queued names
  reserved so parallel jobs cannot overwrite each other.
- Render queue drawer: processing/queued/history sections, live percent/FPS/speed/ETA, output path
  and size, expandable FFmpeg error logs, dismiss individual jobs, clear finished, written-bytes
  summary.
- LocalStorage presets with built-ins **“Fast Review (1.5x HEVC)”** and **“2x Speedup (H.264)”**,
  save/apply/delete (speed, codec, subtitle size, GPU preference).
- Production `README.md` (Windows/Linux mounts, `--gpus all`, CPU fallback, API, troubleshooting).

### Phase 6 — Focus mode layout (configuration drawer & always-on render bar)
- The permanent right column is gone: `ControlPanel` is now a right-side slide-over drawer
  (480 px, blurred backdrop, `Esc` / backdrop / ✕ to close) and the `PREVIEW` + `MULTI-CUT
  TIMELINE` panel uses the full window width.
- New `CommandBar` above the workspace: `⚙️ Configuration` toggle, live summary pills
  (`1.25×`, `H.265 / HEVC`, `Subtitles`, `N cuts`, `Output ~mm:ss`), the active destination folder,
  `Render Now` and `+ Add to Queue` — plus the live `JobProgress` card while a render runs.
- Drawer sections live behind three grouped submenu buttons — `Input` (Source, Subtitles),
  `Output` (Speed, Encoding) and `Automation` (Presets, AI View) — each with a tooltip and its
  section summary. Only the active group is shown; its cards stay independent accordions with
  `Expand all` / `Collapse all`, and command-bar pills deep-link into the matching section and
  group.
- `Render Now` / `+ Add to Queue` also live in the drawer footer, so a render can be started
  without closing it. `Browse files` was added next to the `PREVIEW` title.

### Phase 7 — Auto-mounted host drives, network/SMB sources & slim image
- **Network sources (delivered first, now part of this phase):** `🌐 Network / NAS / URL` tab with
  direct HTTP(S)/RTSP/WebDAV streams (`-reconnect` flags, Range-preserving preview proxy) and SMB/NAS
  shares usable as source *and* destination (`mount.cifs` → `smbprotocol` → native path).
- **Zero-config host drives:** `docker-compose.yml` mounts `/run/desktop/mnt/host:/mnt/host/drives`
  (all Windows and USB drives), `C:/:/mnt/host/c` and `./media:/mnt/host/media` by default, and
  publishes `0.0.0.0:8000` for LAN access.
- **Mount-aware path sandbox:** `..` traversal is rejected lexically before touching the filesystem;
  symlink/mount resolution may cross the root boundary only inside `/mnt/host/drives`,
  `/mnt/host/c` or below a real mount point, so bind mounts no longer produce false 403s.
- **Quick drives bar:** `GET /api/fs/drives` + one-click `C:`, `D:`, `Users`, `Media` buttons at the
  top of the Host Drives tab; Docker Desktop's `wslg`, `shm`, `wsl` folders are hidden.
- **Slim two-stage image (~450 MB):** `node:20-alpine` builds the UI, `python:3.11-slim-bookworm`
  installs only `ffmpeg`, `cifs-utils` and `ca-certificates`; `NVIDIA_VISIBLE_DEVICES` /
  `NVIDIA_DRIVER_CAPABILITIES` let the Container Toolkit inject the host NVENC driver.
- **CPU override + docs:** `docker-compose.cpu.yml` (`gpus: []`) and README sections for CPU-only
  mode, Windows 10/11 LAN access and SMB folder sharing.

### Phase 8 — Insta360 `.insp` support & mock AI view selector (MCP-ready)
- **Spherical containers:** `.insp`/`.insv` joined `.mp4`/`.mkv`/`.avi` in `VIDEO_EXTENSIONS`, and
  `backend/services/insp_service.py` probes them in place with `ffprobe`, falling back to Pillow for
  JPEG stills (`jpeg_pipe` one-frame streams are still treated as stills). Listings carry a
  `spherical` flag and `/api/video/metadata` returns a `spherical` block
  (projection, lens count, still/video, dimensions, XMP-derived tags).
- **Neutral preview:** selecting an `.insp` file renders the still/first frame with a rule-of-thirds
  grid and a toggleable `AI view` frustum overlay driven by pan/tilt/zoom/FOV.
- **AI view selector:** `backend/services/ai_view_service.py` owns the framing document (clamped
  pan/tilt/roll/zoom/FOV, tracking mode, target, keyframe offsets, revision) exposed via
  `GET/POST /api/ai/view-state`, `POST /api/ai/view-state/reset` and `POST /api/ai/mcp-command`.
  `AiViewPanel.jsx` in the new `AI VIEW` drawer card shows a green *MCP AI Control Ready* badge,
  live sliders, keyframe capture and a `Simulate AI` button; the UI polls every 4 s so external MCP
  agents steer the preview live. Optional `AI_VIEW_STATE_FILE` persists state across restarts.
- **Rendering unchanged:** framing is preview-only in this phase; renders still use the configured
  crop/rotation.

### Phase 9 — Ultra-lightweight YOLO tracking & 360° smart framing
- **YOLO engine (`backend/services/yolo_tracking_service.py`):** pluggable, lazily imported backends
  (`ultralytics` with `yolov8n.pt`, or `onnxruntime` with an exported `yolov8n.onnx`), FFmpeg RGB24
  frame sampling (2 fps, ≤180 frames), COCO NMS post-processing, subject ranking
  (confidence × area × temporal continuity), equirect yaw/pitch and rectilinear offset mapping, and
  keyframe compression into `time/pan/tilt/zoom` samples.
- **Three strength levels:** Smooth / Dynamic / Aggressive-Lock-on profiles drive smoothing time
  constant, deadband, maximum camera speed, subject margin, zoom cap and confidence floor; both the
  analysis pass and the rendered framing use the same profile.
- **Toggleable API:** `GET /api/ai/tracking/status`, `POST/DELETE /api/ai/tracking`,
  `POST /api/ai/tracking/analyze` and `GET /api/ai/tracking/frame`; the shared framing document now
  carries `ai_tracking_enabled`, `ai_tracking_level`, `tracking_keyframes` and `tracking_analysis`,
  and render options accept `ai_tracking: { enabled, level, mode, keyframes }`.
- **Render integration:** `build_ai_tracking_filter` emits a time-varying `crop` built from
  piecewise-linear `if(lt(t,…))` expressions plus a constant `scale`, placed after the manual crop
  and before the speed change; keyframes are re-timed onto the concatenated keep-segment timeline.
  No inference runs during encoding.
- **Frontend:** `AiViewPanel.jsx` gains the *AI YOLO Auto-Framing* switch, three strength buttons
  (auto re-analyse when keyframes exist), `Analyse clip` / `Clear track`, detector status and
  keyframe/frame counters; `VideoWorkspace.jsx` polls `/api/ai/tracking/frame` every 1.2 s and draws
  a labelled bounding-box overlay (primary subject highlighted) behind a `Track boxes` toggle.
- **Slim image preserved:** default Docker image unchanged; `docker-compose.ai.yml` adds the optional
  numpy + onnxruntime runtime (~50 MB) and mounts `./models` read-only for the ~12 MB ONNX weights.
  Without a backend the API stays healthy and reports why the tracker is unavailable.

---

## Verification performed

- **154 backend unit tests pass** (`python -m unittest discover -s tests`): atempo math, filter-graph
  ordering, multi-cut concat, subtitle escaping/ordering, NVENC command flags, fps-mode
  compatibility, crop pixel math (rotation-aware, even), target duration, filename sanitising,
  collision protection, network URL/SMB helpers, the host-mount rules (lexical `..` rejection,
  drive-mount symlink resolution, hidden WSL folders, drive shortcuts, unreadable entries), `.insp`
  discovery/projection/metadata shaping, AI view state clamping/keyframes/MCP commands/persistence,
  and the Phase 9 tracker: level profiles, subject selection, equirect/rectilinear framing math,
  smoother convergence/deadband/speed limits, keyframe compression, cache reuse, ONNX
  letterbox/NMS post-processing (skipped when numpy is absent), and the FFmpeg AI-tracking
  expression builder incl. segment re-timing.
- **FastAPI app imports cleanly** and exposes every route; WebSocket snapshot + progress events
  verified against a live render (percent rose 0 → 21 → 45 → 69 → 91 → 100 with FPS/speed/ETA).
- **Real FFmpeg renders verified**:
  - 2× HEVC: 4 s → 2.03 s, 120/120 frames preserved, `hvc1` tag, AAC audio.
  - 0.25× silent source: 2 s → 7.88 s, no audio stream, frames preserved.
  - Multi-cut + 1× `.srt` + 1.5×: output 2.00 s; subtitle visible in the first kept segment and
    gone after the cut (frame-by-frame inspection).
  - 90° rotation + flip + crop: 640×360 → 180×320 (custom crop) and 324×324 (1:1 preset), matching
    the preview.
  - NVENC driver mismatch on the test machine triggered the automatic CPU retry and completed.
- **Frontend builds cleanly** (`npm run build`, ~237 kB JS / 34 kB CSS) and was exercised in a real
  browser: file browsing, metadata, playback-rate sync, timeline segment creation, subtitle
  auto-detect, rotation/crop overlay dragging, queueing 3 jobs, cancel, clear finished, preset
  save/apply.
- **Focus-mode layout verified in the browser**: configuration drawer opens/closes via button,
  summary pill, backdrop and `Esc`; accordion `Collapse all` / `Expand all`; jump pills scroll to
  the right card; no horizontal overflow at 470 px / 700 px / 1070 px viewport widths; the preview
  panel spans the full width while the drawer is closed.
- **`docker-compose.yml` parses** with `gpus: all` and the documented volume mounts.
- **Phase 7 verified with a real Docker build and container** (`docker compose up --build -d`,
  Docker 29.9.2 / Compose v5.6.1):
  - Image builds in two stages; `docker images` reports **975 MB on disk**, `docker save | gzip`
    shows **237.6 MB compressed** (the old CUDA-based image was ~3 GB).
  - `GET /api/fs/list?path=/mnt/host` returns `c`, `drives`, `media` and `network`;
    `GET /api/fs/drives` returns `C:`, `D:`, `G:`, `P:`, `Users`, `Media`.
  - Browsing `/mnt/host/drives/c` and `/mnt/host/drives/d` through the API/UI works (no 403), and
    unreadable Windows system entries (`pagefile.sys`, `System Volume Information`) no longer fail
    the whole listing — a pre-existing `Path.is_dir()` `PermissionError` surfaced by the drive mount
    was fixed.
  - `GET /api/system/gpu-check` reports `NVIDIA GeForce GTX 1660 Ti`, driver 560.94,
    `h264_nvenc` + `hevc_nvenc`, `status: "ready"`, `message: "NVENC Ready"` — with the slim image
    and driver injection only.
  - Real NVENC encodes inside the container succeed for both `h264_nvenc` and `hevc_nvenc`; a queued
    render (`/api/queue/add`, 1.5× HEVC) completed with `encoder: hevc_nvenc` and wrote the output
    file into `/mnt/host/media`.
  - `docker-compose.cpu.yml` merges cleanly (`docker compose -f docker-compose.yml -f
    docker-compose.cpu.yml config`) and removes the GPU request.
  - The UI was exercised in a real browser: `Browse files` shows the **Quick drives** bar
    (`C:`, `D:`, `G:`, `P:`, `Users`, `Media`), `C:` opens the drive root, and the header shows
    *NVIDIA GeForce GTX 1660 Ti — NVENC Ready*.

> Docker **is** available in this environment now, so the Phase 7 build, container start, drive
> mounts, NVENC detection and a real queued render were verified end-to-end against Docker
> Desktop (WSL2). The Phase 1–6 verification below was originally done natively with a local
> FFmpeg build and the repo's Python 3.11 venv.

---

## Key autonomous decisions (full list in `DECISIONS.md`)

- Project root is the repo root (not a nested `video-speed-studio/`).
- `-noautorotate` + explicit transpose (no double rotation) and `-map_metadata -1` (no inherited
  display matrix).
- `passthrough` frame timing instead of `vfr` — empirically required to avoid dropping frames on
  speed-up.
- Runtime NVENC failure → one transparent CPU retry per job.
- Filename collision protection implemented as soon as jobs existed (safety over phase ordering).
- Presets intentionally exclude per-video geometry (segments, subtitle path, rotation, crop,
  filename).
- Queue history is in-memory; rendered files persist on disk.
- Burned subtitles rotate with the video because `ROADMAP.md` fixes the pipeline order
  (subtitles first).
- Layout is focus-mode: configuration lives in a slide-over drawer, while the destination and the
  render actions stay in an always-visible command bar (and are duplicated in the drawer footer).
- Path validation is lexical first (`..` rejected), then mount-aware: symlinks may resolve outside
  `/mnt/host` only inside the `drives`/`c` bind mounts or below a real mount point. Validation
  returns the lexical path so browser-facing entry paths stay canonical.
- The image keeps Debian's `ffmpeg` package (and its large GL/Vulkan dependency set) instead of a
  third-party static build, because that combination was verified to expose `h264_nvenc`/
  `hevc_nvenc` through driver injection on the test machine.
