# FAST-FRAME
## GPU-Accelerated Video Speed & Processing Studio (Zero-Copy Docker WebApp)
## Development Roadmap & Specification

**Status:** All nine phases (1–9) are implemented, verified and committed.
No roadmap item is outstanding; the phase specs below are the reference for
behaviour that must not regress.

**Contents**

1. [Project overview & core principles](#1-project-overview--core-principles)
2. [Tech stack](#2-tech-stack)
3. [Phase status at a glance](#3-phase-status-at-a-glance)
4. [Completed phase specifications](#4-completed-phase-specifications)
5. [Remaining work](#5-remaining-work)
6. [Target project structure](#6-target-project-structure)

### 1. Project Overview & Core Principles
The goal is to build a Dockerized, web-based video processing application utilizing NVIDIA RTX 3060 (NVENC) GPU acceleration.

**Critical Architectural Requirements:**
1. **Zero-Copy Operation:** Do NOT upload files from the browser to the server/container. Host machine drives (including USB flash drives) are bind-mounted into the Docker container (`/mnt/host`). The web UI acts as a server-side file browser: FFmpeg reads the selected source file directly from its physical location on the host and writes the output directly to the selected target directory.
2. **GPU Hardware Encoding (NVIDIA NVENC):** Visually lossless (Constant Quality) fast rendering using `h264_nvenc` and `hevc_nvenc` (H.265) encoders.
3. **Pitch-Correct Audio (No Chipmunk Effect):** Changing video speed (`0.5x` - `4.0x`) must preserve the original audio pitch. Use dynamic `atempo` filter chaining or the `rubberband` filter.
4. **Supported Formats:** Input: `.mp4`, `.mkv`, `.avi` (+ `.srt` subtitles). Output: `.mp4` or `.mkv`.

---

### 2. Tech Stack
* **Backend:** Python 3.11 + FastAPI + Uvicorn + WebSockets (`asyncio` subprocess management for FFmpeg jobs).
* **Frontend:** React (Vite) + TailwindCSS + Lucide Icons (modern, dark-themed "studio" UI).
* **Video Engine:** FFmpeg (NVENC via driver injection, `libx264`/`libx265` fallback) + `ffprobe` (metadata, local & URL).
* **Infrastructure:** Docker, built as a slim multi-stage image — `node:20-alpine` compiles `frontend/dist`,
  `python:3.11-slim-bookworm` runs the app with FFmpeg + `cifs-utils`. `NVIDIA_VISIBLE_DEVICES` /
  `NVIDIA_DRIVER_CAPABILITIES` make the NVIDIA Container Toolkit inject the host's NVENC driver
  (`libnvidia-encode.so`, `nvidia-smi`) into the container, so no ~3 GB CUDA base image is needed.
  `docker-compose.yml` (`gpus: all`) plus `docker-compose.cpu.yml` for machines without NVIDIA drivers.

---

### 3. Phase Status at a Glance

The project was built as nine sequential phases. Every phase below is complete and
verified; the detailed specifications in section 4 are kept as the regression contract.

| Phase | Scope | Status |
| --- | --- | --- |
| 1 | Docker foundation, GPU passthrough, zero-copy file browser | ✅ Implemented |
| 2 | GPU speed engine, HEVC toggle, pitch-correct audio | ✅ Implemented |
| 3 | Multi-cut segments & time-synced subtitle burn-in | ✅ Implemented |
| 4 | Rotation & interactive crop | ✅ Implemented |
| 5 | Render queue, presets & fault tolerance | ✅ Implemented |
| 6 | Focus-mode layout: configuration drawer & always-on render bar | ✅ Implemented |
| 7 | Auto-mounted host drives, network/SMB sources & slim multi-stage image | ✅ Implemented |
| 8 | Insta360 `.insp` support & mock AI view selector (MCP-ready) | ✅ Implemented |
| 9 | Ultra-lightweight YOLO tracking & 360° smart framing (3 strength levels) | ✅ Implemented |

Each phase lists the tasks it delivered and its definition of done.

---

### 4. Completed Phase Specifications

#### 🚀 PHASE 1: Docker Foundation, NVIDIA GPU Passthrough & Zero-Copy File Browser ✅
**Goal:** Enable the container to access the RTX 3060 GPU and the host filesystem (`/mnt/host`), allowing the user to browse source files and target directories via the web UI and preview videos.

**Tasks (Backend & Docker):**
1. Create `Dockerfile` and `docker-compose.yml`. The image is a multi-stage build (see Phase 7): `node:20-alpine` compiles the frontend, `python:3.11-slim-bookworm` installs `ffmpeg` and the Python app; NVENC comes from the host driver injected by the NVIDIA Container Toolkit.
2. `GET /api/system/gpu-check`: Runs `nvidia-smi` and `ffmpeg -encoders | grep nvenc`, returning the GPU model name (e.g., RTX 3060) and the availability of `h264_nvenc` / `hevc_nvenc`.
3. `GET /api/fs/list?path=/mnt/host/...`: Lists directories and supported video/subtitle files (`.mp4, .mkv, .avi, .srt`) with file size and modification date. Include path traversal protection.
4. `GET /api/video/metadata?path=...`: Uses `ffprobe` to return video duration, resolution, FPS, codec, and audio stream details.
5. `GET /api/video/stream?path=...`: HTTP Range Request (`206 Partial Content`) endpoint enabling instant playback and seeking of the selected local video file in the browser's `<video>` element without copying the file.

**Tasks (Frontend):**
1. Header Status Bar: Green indicator when NVENC is active + displays detected GPU model.
2. Dual-Pane (or Modal) File Browser:
   - **Select Source Video** (selecting a file loads its metadata and initializes the video player preview).
   - **Select Target Directory** (where the processed file will be written directly, e.g., a mounted USB drive folder).

**Phase 1 Definition of Done:**
The container starts, the header displays "NVIDIA GeForce RTX 3060 - NVENC Ready", the user can browse and select a video from the host drive, and play/seek through it in the browser preview player.

---

#### ⚡ PHASE 2: GPU Speed Engine, HEVC (H.265) Toggle & Pitch-Correct Audio ✅
**Goal:** Transcode the selected video directly into the target folder at custom speeds (`0.5x`, `0.75x`, `1.0x`, `1.25x`, `1.5x`, `2.0x`, `3.0x`, or custom slider), with selectable H.264 / H.265 (HEVC) GPU encoding and real-time progress tracking.

**Tasks (Backend):**
1. **Dynamic `atempo` Chain Generator:** Since FFmpeg's `atempo` filter is limited to values between `0.5` and `2.0`, implement a helper function that automatically chains filters for extreme speeds (e.g., `3.0x` becomes `atempo=2.0,atempo=1.5`; `0.25x` becomes `atempo=0.5,atempo=0.5`).
2. **Video PTS (Presentation Timestamp) Scaling:** Apply `setpts=PTS/<SPEED>` filter.
3. **NVENC Encoder & HEVC (H.265) Option:**
   - If **H.264** is selected: `-c:v h264_nvenc -preset p6 -rc vbr -cq 19 -b:v 0`
   - If **HEVC (H.265)** is selected (50% smaller file size, faster USB write speeds): `-c:v hevc_nvenc -preset p6 -rc vbr -cq 21 -b:v 0 -tag:v hvc1`
4. **WebSocket Progress Tracking (`/ws/progress`):** Asynchronously parse FFmpeg's `-progress pipe:1` output. Calculate accurate completion percentage (`0-100%`) based on the new target duration (`target_duration = original_duration / speed`), along with current encoding speed and ETA.

**Tasks (Frontend):**
1. Speed preset buttons (`0.5x`, `0.75x`, `1x`, `1.25x`, `1.5x`, `2x`, `3x`) + fine-tuning slider (`0.25x - 4.0x`). Dynamically update the HTML5 `<video>` player's `playbackRate` property so the user can preview the target speed in real time.
2. Codec selector toggle: **H.264 (Max Compatibility)** vs. **H.265 / HEVC (50% Smaller Size, Faster USB Write)**.
3. Output filename input (defaults to `<original_name>_<speed>x_<codec>.mp4`).
4. Live progress bar showing percentage, FPS, encoding speed, ETA, and a Cancel button.

**Phase 2 Definition of Done:**
Transcoding a 1080p/4K video at `2.0x` speed with HEVC NVENC outputs a visually lossless, compact video file directly into the target folder with natural, non-chipmunk voice pitch.

---

#### ✂️ PHASE 3: Multi-Cut (Split & Keep Segments) & Time-Synced Subtitle Burn-in ✅
**Goal:** Allow trimming not just the start and end, but also cutting out unwanted middle sections (defining multiple "Keep" segments), while accurately burning in `1x` `.srt` subtitles onto the cut and speed-adjusted video.

**Tasks (Backend):**
1. **Multi-Cut (Multi-Segment Trimming & Concatenation):**
   - Receive a list of **Keep segments** from the frontend: e.g., `segments: [{start: 10.5, end: 45.0}, {start: 60.0, end: 120.0}]`.
   - Build an FFmpeg `filter_complex` that applies `trim=start=...:end=...,setpts=PTS-STARTPTS` (and `atrim` + `asetpts` for audio) to each segment, then merges them seamlessly using `concat=n=<SEGMENT_COUNT>:v=1:a=1`.
2. **Time-Accurate Subtitle Burn-in:**
   - When an `.srt` file is selected (or auto-detected with a matching filename), burn the subtitles onto the video stream **BEFORE trimming and speed scaling** (at the original `1x` timestamps) at the very start of the `filter_complex` (`[0:v]subtitles='...':force_style='...'[v_sub]`). Then pass `[v_sub]` into the `trim`, `concat`, and `setpts` (speed) filters.
   - *Advantage:* Regardless of how many middle sections are cut or what speed multiplier is applied, burned-in subtitles remain synced to the exact millisecond.
   - Properly escape special characters (colons, slashes, spaces) in file paths for the FFmpeg `subtitles` filter.

**Tasks (Frontend):**
1. **Timeline / Multi-Cut Segment Editor below the player:**
   - Buttons: `[ Set In Point ]` and `[ Set Out Point ]` at the current playback timestamp, plus `[ + Add Keep Segment ]`.
   - Display added segments as interactive chips/cards (e.g., `Segment 1: 00:12 - 01:45`, `Segment 2: 02:10 - 04:00`) with delete buttons and click-to-seek functionality.
   - If no segments are added, process the entire video by default.
2. **Subtitle Selector:** Dropdown listing `.srt` files in the current directory + font size selector (Small, Medium, Large).

**Phase 3 Definition of Done:**
Selecting 2 separate segments from a video, attaching a `1x` `.srt` subtitle file, and rendering at `1.5x` speed produces a single concatenated video with perfectly synced burned-in subtitles.

---

#### 🔄 PHASE 4: Visual Transformations (Rotation & Interactive Crop) ✅
**Goal:** Rotate videos recorded in the wrong orientation and crop out unwanted edges using an interactive visual overlay on the video preview.

**Tasks (Backend):**
1. **Finalize `filter_complex` Pipeline Order:**
   `[0:v]` -> `1. Subtitles (optional)` -> `2. Multi-Trim & Concat` -> `3. Rotate / Flip` -> `4. Crop (w:h:x:y)` -> `5. Speed (setpts=PTS/SPEED)` -> `NVENC Encoder`.
2. **Rotation Parameters:** Support `90_cw` (`transpose=1`), `90_ccw` (`transpose=2`), `180` (`transpose=1,transpose=1`), and horizontal flip (`hflip`).
3. **Crop Parameters:** Convert normalized frontend coordinates (`0.0 - 1.0` for `x, y, width, height`) to actual pixel dimensions based on the source video's resolution (accounting for rotation if applied first, and rounding to even numbers for YUV420p compatibility).

**Tasks (Frontend):**
1. Rotation controls next to the player (90° Left, 90° Right, 180°, Flip Horizontal) with live CSS transform preview on the `<video>` element.
2. **Interactive Crop Overlay:** Toggleable draggable/resizable bounding box over the video player, with aspect ratio lock presets (`Free`, `16:9`, `9:16 Vertical`, `1:1`).

**Phase 4 Definition of Done:**
Rotating and cropping a region via the mouse overlay in the preview player produces a rendered output that matches the visual preview.

---

#### 📦 PHASE 5: Render Queue (Batch Processing), Presets & Fault Tolerance ✅
**Goal:** Queue multiple videos (or multiple cuts from a single long video) for unattended sequential GPU rendering, and save favorite configuration presets.

**Tasks (Backend):**
1. **Asynchronous Job Queue Manager:**
   - `POST /api/queue/add`: Enqueue a new render job (`queued` status).
   - Background Worker loop that processes 1 job at a time on the RTX 3060 (`processing`), updates status to `completed` (or `failed` with captured FFmpeg stderr log), and automatically starts the next job in line.
   - `GET /api/queue` and `DELETE /api/queue/{job_id}` (cancel running job or remove queued job).
2. **Filename Collision Protection:** If the target file already exists in the destination folder, automatically append an incrementing suffix (`_1`, `_2`) to prevent overwriting source or previous output files.

**Tasks (Frontend):**
1. Add an **"Add to Queue (+Queue)"** button alongside **"Render Now"**.
2. **Render Queue Panel:** Displays queued, active (with live progress bar), completed, and failed jobs, including elapsed render time and final output file size.
3. **Quick Presets (saved in LocalStorage):** Save and load custom configurations with one click (e.g., *"Default Fast: 1.5x + HEVC"*).

**Phase 5 Definition of Done:**
Queueing 3 different videos with distinct multi-cut, crop, and speed settings processes them sequentially without error directly into the target directory.

---

#### 🎛️ PHASE 6: Focus Mode Layout — Configuration Drawer & Always-On Render Bar ✅
**Goal:** Give the preview and multi-cut timeline the whole window and move every configuration card (Presets, Source, Speed, Encoding, Subtitles) into a toggleable drawer that is summoned on demand.

**Tasks (Frontend):**
1. **Retire the permanent right column:** `ControlPanel` becomes a right-side slide-over drawer (max 480 px, blurred backdrop, `Esc` / backdrop / ✕ to close). Nothing is docked, so with the drawer closed the `PREVIEW` + `MULTI-CUT TIMELINE` panel spans the full available width and re-expands instantly.
2. **`⚙️ Configuration` command bar:** an always-visible compact bar above the workspace holding the drawer toggle, live summary pills (`1.25×`, `H.265 / HEVC`, `Subtitles`, `N cuts`, `Output ~mm:ss`), the active `DESTINATION FOLDER` chip, `Render Now` and `+ Add to Queue`.
3. **Accordion sections inside the drawer:** `PRESETS`, `SOURCE`, `SPEED`, `ENCODING` and `SUBTITLES` collapse/expand independently with a single `Expand all` / `Collapse all` control. Section pills at the top of the drawer jump straight to one card, and the command-bar summary pills deep-link to the matching section.
4. **Primary actions stay reachable:** `Render Now` and `+ Add to Queue` live in the command bar *and* in the drawer footer, so a render can be started whether the drawer is open or closed.
5. **`Browse files` in the PREVIEW header** in addition to the empty-state `Browse host files` call to action, so a new source can be picked at any time.

**Phase 6 Definition of Done:**
The studio opens with the preview spanning the full width of the window, every configuration card is only reachable through the `Configuration` drawer, and `Render Now` works without ever opening it.

---

#### 🌐 PHASE 7: Auto-Mounted Host Drives, Network/SMB Sources & Slim Multi-Stage Image ✅
**Goal:** Automatically expose all Windows/Linux host drives (`C:`, `D:`, USB drives) without manual `.env` or compose edits, support Network URLs & SMB/NAS shares directly in the File Browser modal, and shrink the Docker image from ~3 GB to under 1 GB while keeping full NVENC GPU acceleration and automatic CPU fallback.

**Tasks (Docker & Infrastructure):**
1. **Multi-Stage Slim `Dockerfile` (~240 MB compressed, ≈975 MB on disk with NVENC):**
   - Stage 1 (`node:20-alpine`): Build `frontend/dist`.
   - Stage 2 (`python:3.11-slim-bookworm`): Install only `ffmpeg`, `cifs-utils`, and `ca-certificates` (`--no-install-recommends`). Set `NVIDIA_VISIBLE_DEVICES=all` and `NVIDIA_DRIVER_CAPABILITIES=compute,video,utility` so the host's NVENC drivers (`libnvidia-encode.so`) and `nvidia-smi` are injected at runtime without needing the 3GB CUDA toolkit image. (Debian's `ffmpeg` pulls a large AV/GL library set; the image is ~240 MB to download, ≈975 MB uncompressed.)
2. **Zero-Config Host Drive Mounts in `docker-compose.yml`:**
   - Mount `/run/desktop/mnt/host:/mnt/host/drives` (Docker Desktop WSL2 automatic root containing all Windows drives `c`, `d`, `e`, USB drives) and `C:/:/mnt/host/c` alongside `./media:/mnt/host/media` by default so the host machine is immediately browsable out of the box.
   - Bind port `"0.0.0.0:8000:8000"` for instant LAN access on Windows 10/11.
   - Provide `docker-compose.cpu.yml` (`gpus: []`) so machines without NVIDIA drivers/toolkit installed start cleanly in CPU mode.

**Tasks (Backend & Frontend):**
1. **Quick Drive Bar in `FileBrowser.jsx`:** One-click buttons for detected Windows/Linux drives (`C:`, `D:`, `E:`, …), `Users` and `Media`, with proper symlink/mount-point resolution under `/mnt/host` (WSL system folders hidden).
2. **Network / NAS / URL Tab in `FileBrowser.jsx`:**
   - Direct Stream URLs (`http://`, `https://`, `rtsp://`, `webdav://`) passed straight to FFmpeg `-i` with `-reconnect 1`.
   - Windows SMB / NAS share connector (`//SERVER/Share` or `//host.docker.internal/Share`) usable as both Source and Destination.

**Phase 7 Definition of Done:**
Running `docker compose up --build -d` builds the slim image (~240 MB compressed, ≈975 MB on disk), immediately displays all host Windows drives (`C:`, `D:`, USB) and the `Network / NAS / URL` tab in `Browse files`, and renders via NVENC (or CPU fallback if no GPU is installed).

---

#### 🔮 PHASE 8: Insta360 `.insp` File Support & Mock AI View Selector (MCP-Ready) ✅
**Goal:** Add support for Insta360 `.insp` container files with a neutral rectilinear preview mode and a mocked AI View Selector in the UI, designed to be controlled remotely via Model Context Protocol (MCP) by external AI agents for intelligent framing, tracking, and zoom.

**Tasks (Backend & Services):**
1. **`.insp` Format Handler / Demuxer (`backend/services/insp_service.py`):**
   - Insta360 `.insp` files are typically single-frame or dual-fisheye equirectangular container JPEGs/MP4-likes.
   - Implement metadata and stream inspection via `ffprobe` / Pillow so `.insp` files can be selected, previewed in a neutral equirectangular/rectilinear projection view, and fed directly into the FFmpeg rendering pipeline without manual extraction.
2. **AI View Selector Service & Mock State (`backend/services/ai_view_service.py`):**
   - Provide a backend state manager storing AI framing directives (e.g., target pan, tilt, zoom, subject tracking keyframes, crop window).
   - Expose REST / WebSocket endpoints (`/api/ai/view-state`, `/api/ai/mcp-command`) allowing external MCP agents to query and adjust framing parameters dynamically.

**Tasks (Frontend):**
1. **Neutral View Selector Widget in `VideoWorkspace.jsx`:**
   - A toggleable minimalist UI overlay when an `.insp` or 360° source is loaded, showing a neutral framing viewport (mock AI view selector with pan/tilt/zoom preview indicators).
   - Visual indicators showing that AI framing / subject tracking mode is active (ready for MCP control).

**Phase 8 Definition of Done:**
`.insp` files are successfully recognized and listed in the file browser, open in a neutral preview mode, and the UI exposes the mock AI view selector state which can be updated via backend API endpoints.

---

#### 👁️ PHASE 9: Ultra-Lightweight YOLO AI Tracking & 360° Smart Framing ✅
**Goal:** Upgrade the mock AI View Selector to a real, ultra-lightweight YOLO-based object detection and subject-tracking engine (running on `ultralytics` YOLO nano model) with 3 selectable aggression/strength levels, fully toggleable in the UI, keeping the Docker image size tightly controlled while unlocking native smart framing for 360°/.insp video processing.

**Delivered (Backend & AI Engine):**
1. **Lightweight YOLO integration (`backend/services/yolo_tracking_service.py`):**
   - Pluggable inference backends resolved lazily: `ultralytics` (`yolov8n.pt`, ~6 MB, auto-download) or `onnxruntime` with an exported `yolov8n.onnx` (~12 MB). Neither is imported at startup, and the optional wheels live in `backend/requirements-ai.txt` (~50 MB) behind `--build-arg INSTALL_AI_TRACKING=1` / `docker-compose.ai.yml`.
   - Frame-by-frame detection of people, pets and vehicles via FFmpeg RGB24 sampling (2 fps, max 180 samples), with COCO post-processing/NMS for the ONNX path and equirectangular/rectilinear coordinate mapping plus temporal continuity scoring.
2. **3 strength levels:** `LEVEL_PROFILES` tunes smoothing (1.1 s / 0.45 s / 0.15 s), deadband, max camera speed, subject margin, zoom cap and confidence floor for *Smooth*, *Dynamic* and *Aggressive / Lock-on*.
3. **Toggleable API & pipeline integration:** `POST /api/ai/tracking`, `DELETE /api/ai/tracking`, `POST /api/ai/tracking/analyze`, `GET /api/ai/tracking/frame`, `GET /api/ai/tracking/status`; the shared framing document carries `ai_tracking_enabled`, `ai_tracking_level`, `tracking_keyframes` and `tracking_analysis`, and render options accept `ai_tracking: { enabled, level, mode, keyframes }`.
4. **FFmpeg integration:** `build_ai_tracking_filter` emits a time-varying `crop` (piecewise-linear `if(lt(t,…))` expressions) plus a constant `scale`, inserted after the manual crop and before the speed change, with keyframes re-timed onto the multi-cut timeline.

**Delivered (Frontend):**
- `AiViewPanel.jsx`: real-time *AI YOLO Auto-Framing* toggle (with detector/backend status), 3-level strength buttons (auto re-analysing when keyframes exist), `Analyse clip`, `Clear track`, keyframe/frame counters.
- `VideoWorkspace.jsx`: live bounding-box debug overlay for every detection (primary subject highlighted) with a `Track boxes` toggle and a YOLO status chip showing level and current pan/tilt.

**Phase 9 Definition of Done:**
Enabling YOLO AI tracking in the UI automatically detects and tracks subjects in `.insp` or standard videos, dynamically adjusting framing coordinates across the 3 strength levels without bloating the Docker image beyond a few extra megabytes (default image unchanged; the opt-in overlay adds ~50 MB of runtime plus the mounted ~12 MB model).

---

### 5. Remaining Work

**None.** Phases 1–9 cover the product scope in this roadmap and are all shipped.
The items previously listed as open were documentation drift only; they are now
resolved:

- Phase 7's heading no longer carries an in-progress marker; all Phase 7 tasks
  (multi-stage slim image, zero-config host mounts, Quick drives bar, network /
  SMB / URL sources) are implemented and verified.
- The top-level status and phase count reflect the nine shipped phases.
- The target structure below matches the files that actually exist — the neutral
  360° framing viewport is implemented inside `VideoWorkspace.jsx` with its
  controls in `AiViewPanel.jsx`, so no separate `AIViewSelector.jsx` file exists.

Future enhancements should be added as new numbered phases rather than reopening
completed ones.

---

### 6. Target Project Structure

```text
fast-frame/
├── docker-compose.yml          # GPU passthrough + automatic host drive mounts (/run/desktop/mnt/host & C:/)
├── docker-compose.cpu.yml      # CPU-only override (gpus: []) for machines without NVIDIA toolkit
├── docker-compose.ai.yml       # Optional YOLO runtime overlay (numpy + onnxruntime, mounts ./models)
├── Dockerfile                  # Multi-stage slim image (Node 20 builder -> Python 3.11 slim + FFmpeg + NVENC env)
├── README.md
├── ROADMAP.md
├── DECISIONS.md
├── SUMMARY.md
├── models/                     # Exported yolov8n.onnx for the optional ONNX tracker
├── backend/
│   ├── requirements.txt
│   ├── requirements-ai.txt     # Optional numpy/onnxruntime for YOLO tracking
│   ├── main.py                 # FastAPI app, WebSocket & static frontend serving
│   ├── config.py               # Mount paths (/mnt/host), symlink-safe validation, drive shortcuts, network helpers
│   ├── models.py               # Pydantic job & options schemas
│   ├── routers/
│   │   ├── filesystem.py       # Drive listing, metadata, HTTP 206 stream & quick drive endpoints
│   │   ├── network.py          # SMB/NAS mount & stream relay endpoints
│   │   ├── queue.py            # Render queue & job control API
│   │   └── ai.py               # AI view-state, MCP command and YOLO tracking endpoints
│   ├── services/
│   │   ├── ffmpeg_builder.py   # Filter_complex, NVENC, atempo, multi-cut command builder
│   │   ├── ffprobe_service.py  # Video metadata extractor (local & URL)
│   │   ├── gpu_service.py      # nvidia-smi & NVENC encoder detection with CPU fallback
│   │   ├── insp_service.py     # Insta360 .insp demuxer & equirectangular/rectilinear preview metadata
│   │   ├── ai_view_service.py  # AI framing state manager + /api/ai REST bridge
│   │   ├── yolo_tracking_service.py # YOLOv8n detection, 3 strength levels, keyframe generation
│   │   ├── network_service.py  # SMB/CIFS & smbprotocol zero-copy handler
│   │   └── worker.py           # Async background FFmpeg runner & progress parser
│   └── tests/                  # Command builder, queue helper, network, host-mount, .insp, AI view & tracking tests
└── frontend/
    ├── package.json
    ├── vite.config.js
    ├── index.html
    └── src/
        ├── App.jsx
        ├── api.js
        ├── utils.js
        └── components/
            ├── AiViewPanel.jsx     # AI view selector, YOLO toggle, strength levels, analysis
            ├── CommandBar.jsx      # Configuration toggle, summary pills, destination, Render Now / + Add to Queue
            ├── ControlPanel.jsx    # Slide-over Configuration drawer, grouped into Input / Output / Automation submenus
            ├── FileBrowser.jsx     # Host Drives (with Quick Drive bar) + Network/SMB/URL modal
            ├── FileEntryList.jsx   # Shared entry list for the Host Drives and Network tabs
            ├── Header.jsx          # GPU status bar and studio title
            ├── JobProgress.jsx     # Live render progress card
            ├── NetworkPanel.jsx    # Direct URL & Windows/NAS SMB share connection UI
            ├── RenderQueue.jsx     # Live progress bar & Batch queue drawer
            ├── Timeline.jsx        # Multi-cut segment editor
            └── VideoWorkspace.jsx  # Preview player, neutral 360° view, YOLO box overlay, rotate/crop toolbar
```