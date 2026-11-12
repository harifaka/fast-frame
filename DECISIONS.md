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

## Phase 2 — GPU speed engine, HEVC toggle & pitch-correct audio

10. **Timestamp mode is `passthrough`, not `vfr`.** Verified with real encodes: after
    `setpts=PTS/2`, `-fps_mode vfr` resamples back to the nominal 30 fps and throws
    away half of the frames (62 of 120 in a 4 s test). `passthrough` keeps every
    frame and the correct duration. FFmpeg ≥ 5 uses `-fps_mode passthrough`,
    older builds (Ubuntu 22.04 ships 4.4) use `-vsync passthrough`; the version is
    detected once by `gpu_service` and stored on each job.
11. **Runtime NVENC fallback.** An encoder can be listed yet fail to open (driver
    too old for the bundled FFmpeg, NVENC session exhaustion, missing
    `/dev/nvidia*`). A failed hardware job is transparently retried **once** with
    `libx264`/`libx265`, the job reports `fallback_reason`, and the UI shows it.
12. **Metadata is stripped (`-map_metadata -1`).** Otherwise a source display matrix
    could be copied onto raw (already rotated) frames and players would rotate the
    output a second time.
13. **Job manager lands in Phase 2.** “Render Now”, live progress and Cancel need a
    server-side runner, so `JobManager`, `POST /api/queue/add`, `GET /api/queue`
    and `DELETE /api/queue/{id}` exist now. Phase 5 adds the batch UI, presets,
    `clear-completed` and the queue drawer on top of the same API.
14. **Collision protection is implemented early.** Default filenames already avoid
    overwriting, but a user-edited name could collide with the source; the server
    therefore resolves the final path (`_1`, `_2`, …) at enqueue time for every
    job instead of waiting for Phase 5.
15. **WebSocket protocol.** First frame is `{type: "snapshot", jobs, …}` so a late
    client is immediately consistent, followed by incremental `{type: "job", job}`
    events and 20 s `ping` keep-alives. Progress is throttled to 4 Hz.
16. **Pydantic models live in `models.py`.** Keeps request validation (speed range,
    crop bounds, segment order) reusable by routers and unit tests.
17. **Preview speed is browser-side.** `<video>.playbackRate` previews the chosen
    speed instantly; browsers do not pitch-correct below 0.5×/above 2×, so the
    rendered file (atempo chain) is the authoritative result.

## Phase 3 — Multi-cut segments & time-synced subtitle burn-in

18. **Subtitles are burned on the raw 1x timeline first.** The filter graph starts
    with `[0:v:0]subtitles=…[v_sub]`, and every `trim` reads from `v_sub`. Verified
    end-to-end: a 1–2 s subtitle still lands on the correct frame after cutting
    1–2 s + 3–4 s and speeding up 1.5×, and is absent afterwards.
19. **Filter paths are escaped for FFmpeg's double parsing.** Backslashes are
    normalised to `/`, then `' : , ; [ ] =` are backslash-escaped and the whole path
    is single-quoted. Validated with a real Windows path (`C\:/…`) because drive
    letters are the most common breakage.
20. **Keep segments are clamped, validated and sorted server-side.** Ends beyond the
    source duration are clamped, segments shorter than ~0µs are rejected, and
    ordering is normalised so the UI can never produce an invalid concat graph.
    Overlapping segments are allowed (deliberate replays) and duplicate footage.
21. **Single-segment renders skip `concat`.** `concat=n=1` is avoided; the trimmed
    pads are mapped directly. This keeps the graph smaller and avoids a known
    concat filter quirk with a single input.
22. **`force_style` always sets `Outline=1,Shadow=0`.** Burned text stays legible on
    bright footage without user configuration; only the font size is exposed.
23. **Subtitle auto-detection is a filename stem match.** On source selection the
    folder is listed and `<video-stem>.srt` (case-insensitive) is pre-selected;
    users can still pick any other `.srt` from the dropdown.
24. **Timeline in/out markers are ephemeral UI state.** Only committed keep
    segments are stored in settings (and later in presets); uncommitted markers do
    not leak into renders or saved presets.

## Phase 4 — Rotation & interactive crop

25. **Rotation is applied via `transpose` and consumes the file's display matrix
    default.** The UI pre-selects `suggested_rotation` from the probe result, and
    `-noautorotate` guarantees the render matches the preview exactly (verified:
    90° CW render is 180×320 portrait for a 640×360 source).
26. **Crop math uses the oriented frame.** Since cropping happens after rotation,
    width/height are swapped for `90_cw`/`90_ccw` before converting the normalised
    rectangle. Width, height and both offsets are rounded down to even numbers and
    clamped inside the frame, so yuv420p chroma alignment stays valid for every
    preset. A 1:1 preset on a 360×640 portrait frame renders exactly 324×324.
27. **The preview stage is measured in JS.** A `ResizeObserver` fits a box with the
    oriented aspect ratio; for 90° rotations the `<video>` element is sized by the
    reciprocal ratio and rotated about its centre, so the crop overlay's normalised
    coordinates map 1:1 to rendered pixels.
28. **Crop overlay uses normalized coordinates and pointer capture.** Dragging the
    box moves it; corner handles scale it with an optional aspect lock (16:9, 9:16,
    1:1) and a 5% minimum size. Presets re-fit a centred maximum box instead of
    distorting the current one.
29. **Burned subtitles rotate with the video.** `ROADMAP.md` fixes the pipeline
    order as subtitles → trim → rotate/flip → crop → speed, so subtitle text is part
    of the frame when rotation happens. Users who need upright text can render
    without rotation metadata or pre-rotate the file; this is intentional per spec.
30. **Transform/crop state lives with the render settings.** It is sent with every
    job (and will be captured in presets), so the render always mirrors what the
    preview shows.

## Phase 5 — Batch queue, presets & polish

31. **DELETE semantics are split by state.** A queued/running job is cancelled (the
    FFmpeg process is terminated, the partial file removed); a finished job is
    dismissed from the in-memory history. `POST /api/queue/clear-completed` clears
    all finished entries at once and broadcasts a fresh snapshot.
32. **`render_now` jumps the queue but never pre-empts the running job.** The GPU
    renders strictly one job at a time to avoid NVENC session exhaustion; “Render
    Now” inserts at the front of the pending list.
33. **Batch = repeated Add to Queue.** Every job snapshots its own settings at
    enqueue time, so three videos with different cuts/crops/speeds can be lined up
    and processed sequentially without the UI state affecting earlier jobs.
34. **Presets store transferable settings only** — speed, codec, subtitle font size
    and GPU preference. Segments, subtitle path, rotation, crop and filename are
    video-specific and stay out of presets to avoid silently applying the wrong
    geometry to another file. Built-in presets cannot be deleted; user presets can.
35. **Queue state is intentionally in-memory.** History is a runtime concern; it is
    cleared on container restart while rendered files remain on disk. This avoids a
    database dependency for a single-user studio app and is documented in the README.
36. **The queue UI is a right-side drawer.** It reuses the same `JobProgress` card as
    the command bar, so progress, FPS, speed, ETA, fallback notes, output size and
    expandable FFmpeg logs look identical in both places.
37. **README documents the real deployment matrix** — Windows `D:/:/mnt/host/d`,
    Linux `/media:/mnt/host/media`, `gpus: all` vs the `deploy.resources` fallback,
    GPU verification with `nvidia-smi`, CPU mode, permissions and troubleshooting.

## Phase 6 — Focus mode layout

38. **The configuration panel is a slide-over drawer, not a docked sidebar.** A docked column
    permanently steals width from the preview; the drawer lets `PREVIEW` and `MULTI-CUT TIMELINE`
    use the whole window and is dismissed with `Esc`, the backdrop or ✕. Card order is unchanged
    (Presets → Source → Speed → Encoding → Subtitles).
39. **Sections are independent accordions.** Each card collapses on its own so only the relevant
    controls stay on screen; `Expand all` / `Collapse all` and the section pills cover both the
    "everything at once" and "jump to one card" workflows. Sections start expanded so the drawer
    matches the previous panel on first open.
40. **Render actions are duplicated on purpose.** The command bar keeps `Render Now`,
    `+ Add to Queue` and the active destination folder visible at all times, and the drawer footer
    repeats the actions so a render can be launched while configuring. The command bar also owns
    the live `JobProgress` card, so render feedback is never hidden behind the drawer.
41. **Drawer, submenu and accordion state are transient.** They live in `App`/`ControlPanel` state
    and are not persisted; presets and the last destination folder remain the only LocalStorage
    state. Settings are grouped behind `Input`, `Output` and `Automation` buttons so only one
    submenu is on screen at a time.





## Phase 7 — Network sources: direct URLs & SMB/NAS shares

38. **URLs bypass the path sandbox instead of being downloaded.** `config.is_network_url`
    recognises http/https/rtsp/rtmp/WebDAV schemes; those inputs skip
    `resolve_host_path` and reach `ffprobe`/`ffmpeg` verbatim as `-i <url>`. Nothing is
    copied to disk first, which is what made the zero-copy promise hold for streams.
39. **Reconnect flags are injected by one helper.** `config.network_input_args` returns
    the input arguments for both ffprobe and ffmpeg, adding
    `-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5` for HTTP/WebDAV and
    `-rtsp_transport tcp` for RTSP. A dropped Wi-Fi link therefore stalls a render
    instead of failing it.
40. **WebDAV is translated, not advertised.** ffmpeg has no `webdav://` protocol, so
    `webdav(s)://` and `dav(s)://` are rewritten to http(s) for ffmpeg while the UI keeps
    the user's original URL. Browser credentials embedded in the URL become a Basic
    auth header in the preview proxy and are masked (`user:***@host`) everywhere else.
41. **SMB access is three-tiered, best first.** (a) `mount.cifs` when the container has
    `CAP_SYS_ADMIN` › the share is a real path under `/mnt/host/network/<share>` and
    every tool treats it like a drive; (b) the bundled `smbprotocol` client when it is
    not › browsing and preview read byte ranges on demand; (c) a native Windows path
    (`Z:`, `\\server\share`) that the host OS already resolves. Registered locations are
    the only paths allowed to skip the host-root sandbox.
42. **Non-mounted shares are relayed over loopback, not staged.** FFmpeg reads
    `/api/fs/network-stream?path=…` on `127.0.0.1`, an async Range-capable relay backed by
    the SMB client. Renders into such a share use a local staging file that is uploaded
    when the encode finishes — writing directly to SMB is impossible for an encoder that
    needs a seekable output, and this keeps the *source* copy-free.
43. **Credentials never leave the process.** Passwords are held in memory on the location
    object, stripped from every API response (`has_credentials` only), passed to
    `mount.cifs` through a 0600 credentials file that is deleted right after the mount,
    and redacted from FFmpeg commands and logs. LocalStorage bookmarks store the share
    path and username only.
44. **Output naming for streams reuses the existing rule.** `network_source_name` derives a
    clean basename from the URL path (`stream_video.mp4`, `channel.m3u8` › `channel.mp4`,
    no basename › `host_network_stream.mp4`) so the queue still produces
    `<name>_<speed>x_<codec>.mp4` for URL sources.
45. **The modal keeps one browse surface with two tabs.** `📁 Host Drives` and
    `🌐 Network / NAS / URL` share `FileEntryList`, so selection, double-click and
    destination picking behave identically; only the source of the listing changes.

## Phase 7 — Auto-mounted drives, symlink-safe sandbox & slim multi-stage image

46. **Zero-config host mounts instead of documented `.env` edits.** `docker-compose.yml` now
    mounts `/run/desktop/mnt/host:/mnt/host/drives` (Docker Desktop's WSL2 host root = every
    Windows drive and USB drive), `C:/:/mnt/host/c` (direct fallback) and `./media:/mnt/host/media`
    by default. The previous "uncomment one drive letter" step was the most common setup failure;
    on Linux the two Windows lines are meant to be replaced with `/media` or `/run/media` mounts.
47. **Path validation is lexical first, then symlink-aware.** `resolve_host_path` normalises `..`
    with `os.path.normpath` *before* touching the filesystem (traversal → 403) and only then calls
    `Path.resolve()`. If resolution leaves the root it is accepted only when the lexical path lives
    inside a drive bind mount (`/mnt/host/drives`, `/mnt/host/c`) or below a real mount point; every
    other escaping symlink is still rejected. This removes the false 403s Docker Desktop and USB
    drives produced without weakening traversal protection.
48. **Validation returns the lexical path, not the resolved one.** Returning the resolved path would
    hand the browser paths outside `HOST_MOUNT_PATH`, which the next request would reject. Entry
    paths therefore stay canonical (`/mnt/host/drives/usb/clip.mp4`) while the kernel follows the
    resolution itself when FFmpeg opens the file.
49. **Drive shortcuts are discovered, not configured.** `GET /api/fs/drives` lists the children of
    the `drives` mount (`c` → `C:`), the direct `C:` fallback, the first `Users` folder and
    `./media`; duplicates are collapsed by label, and Docker Desktop's internal `wslg`, `shm` and
    `wsl` folders are filtered both from the shortcuts and from `GET /api/fs/list`.
50. **The image is a 2-stage slim build (~240 MB compressed / ≈975 MB on disk).** `node:20-alpine`
    builds `frontend/dist` and is discarded; `python:3.11-slim-bookworm` installs only `ffmpeg`,
    `cifs-utils` and `ca-certificates` (`--no-install-recommends`). `curl` is deliberately absent —
    both the Dockerfile and Compose healthchecks use a Python `urllib` one-liner instead of adding a
    package just for the probe. Debian's ffmpeg depends on the full AV/GL library set (LLVM, Mesa,
    Vulkan), which is why the on-disk size is larger than the compressed download; purging those
    libraries was verified to remove ffmpeg itself, so they stay.
51. **NVENC without CUDA.** `NVIDIA_VISIBLE_DEVICES=all` and
    `NVIDIA_DRIVER_CAPABILITIES=compute,video,utility` make the Container Toolkit inject the host's
    `libnvidia-encode.so` and `nvidia-smi` into the slim image, so `h264_nvenc`/`hevc_nvenc` work
    without the ~3 GB CUDA base. `docker-compose.cpu.yml` clears the GPU request with `gpus: []`
    and blanks the `NVIDIA_*` variables for machines without a driver; the runtime NVENC → CPU
    retry from Phase 2 still protects individual jobs.

52. **Insta360 `.insp`/`.insv` are first-class sources, not extracted temp files.** They join
    `VIDEO_EXTENSIONS` so the browser, metadata, streaming and render paths behave like any other
    clip. `insp_service` probes them with `ffprobe` first and falls back to Pillow for JPEG
    stills; ffprobe's `jpeg_pipe` demuxer exposes a one-frame "video" stream with a fake 25 fps, so
    image-pipe formats are explicitly classified as stills. Pillow is pinned in
    `requirements.txt` because without it every `.insp` still would report unknown dimensions.
53. **AI framing lives in one shared, clamped document.** `ai_view_service` owns pan/tilt/roll/
    zoom/FOV, tracking mode, target and keyframe offsets and exposes the same snapshot to the UI and
    to MCP agents (`/api/ai/view-state`, `/api/ai/mcp-command`). Every write bumps a `revision`
    and clamps to safe ranges, so a misbehaving agent cannot push the preview off the sphere. The UI
    polls every 4 s (and pauses while it is saving) instead of adding a second WebSocket, keeping the
    existing `/ws/progress` channel focused on render jobs. State is in memory by default with an
    optional `AI_VIEW_STATE_FILE` mirror.
54. **AI framing is preview-only in Phase 8.** Rendering still uses the crop/rotation controls; the
    view selector intentionally does not alter FFmpeg output until a real tracker and projection
    filter are implemented, so the mock cannot silently change rendered files.

## Phase 9 — YOLO auto-framing

55. **Detection runs once, rendering only evaluates expressions.** `yolo_tracking_service` samples the
    clip at 2 fps (default cap: 180 frames), runs YOLOv8n per sample, smooths the framing and stores
    pan/tilt/zoom keyframes. `ffmpeg_builder` turns those keyframes into piecewise-linear FFmpeg
    expressions (`crop=w='trunc(if(lt(t,…),…)/2)*2'` plus a constant `scale`), so enabling
    auto-framing adds zero inference to the encode and the GPU encoder keeps a fixed frame size.
56. **One keyframe document, two interpretations.** `pan`/`tilt` are angular offsets from the frame
    centre. Spherical sources map yaw across the full 360° equirect width; rectilinear sources map
    the same value across ±180° of the frame, so the tracker, the preview frustum and the FFmpeg
    expression all agree without a second coordinate system. Keyframes are re-timed onto the
    concatenated timeline when keep-segments are used.
57. **Three strength levels are tuning profiles, not code paths.** `LEVEL_PROFILES` defines the
    smoothing time constant (1.1 s / 0.45 s / 0.15 s), deadband (2.5° / 1° / 0°), maximum camera
    speed (35 / 90 / 240 °/s), subject margin (55 % / 28 % / 10 %), zoom cap (1.8 / 2.6 / 3.6×) and
    minimum confidence. Both the analysis smoothing and the rendered framing use the same profile, so
    switching level and re-analysing gives a genuinely different camera (and the panel re-analyses
    automatically when keyframes already exist).
58. **The YOLO runtime is optional and lazily imported.** `ultralytics` is preferred when installed
    (weights auto-download), otherwise `onnxruntime` with `AI_TRACKING_MODEL`. Neither is imported at
    process start; `requirements-ai.txt` is installed only with `--build-arg INSTALL_AI_TRACKING=1`
    (`docker-compose.ai.yml`), keeping the default image unchanged. When no backend is available the
    API stays healthy, `/api/ai/tracking/status` reports the reason and the UI shows
    *tracker unavailable* instead of failing.
59. **The 12 MB model is mounted, not baked.** `docker-compose.ai.yml` mounts `./models` read-only and
    points `AI_TRACKING_MODEL` at `/models/yolov8n.onnx`; the image only grows by the ~50 MB
    onnxruntime + numpy wheels and only when the overlay is used.
60. **Live preview boxes are polled, not streamed.** While auto-framing is enabled the workspace
    requests one decoded frame every 1.2 s from `/api/ai/tracking/frame`, reusing the existing HTTP
    API rather than adding a second WebSocket. The overlay is purely diagnostic: it never writes to
    the framing state, so a paused preview cannot fight an MCP agent for control.