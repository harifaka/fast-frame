# Fast-Frame — Implementation Summary

All five phases from `ROADMAP.md` are implemented, verified and committed.

## ▶ Run it when you're back

1. **Configure your host drives** in [`docker-compose.yml`](docker-compose.yml) (only required edit).
   The default is `./media:/mnt/host/media`; for a whole Windows drive use `D:/:/mnt/host/d`, on
   Linux use e.g. `/media:/mnt/host/media`.
2. Start the stack:

```bash
docker compose up --build -d
```

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

---

## Verification performed

- **43 backend unit tests pass** (`python -m unittest discover -s tests`): atempo math, filter-graph
  ordering, multi-cut concat, subtitle escaping/ordering, NVENC command flags, fps-mode
  compatibility, crop pixel math (rotation-aware, even), target duration, filename sanitising and
  collision protection.
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
- **Frontend builds cleanly** (`npm run build`, ~229 kB JS / 32 kB CSS) and was exercised in a real
  browser: file browsing, metadata, playback-rate sync, timeline segment creation, subtitle
  auto-detect, rotation/crop overlay dragging, queueing 3 jobs, cancel, clear finished, preset
  save/apply.
- **`docker-compose.yml` parses** with `gpus: all` and the documented volume mounts.

> Docker itself is not installed in this environment, so the container image build was validated by
> inspection (base image, package availability, copy order, healthcheck) rather than an actual
> `docker compose build`. The API/frontend were verified end-to-end natively with a local FFmpeg 9
> build and the repo's Python 3.11 venv.

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
