# FAST-FRAME
## GPU-Accelerated Video Speed & Processing Studio (Zero-Copy Docker WebApp)
## Development Roadmap & Specification (for Cursor AI Agent)

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
* **Video Engine:** FFmpeg (with CUDA / NVENC support) + `ffprobe` (for reading metadata and duration).
* **Infrastructure:** Docker (based on `nvidia/cuda:12.2.0-runtime-ubuntu22.04`) + `docker-compose.yml` configured with NVIDIA Container Toolkit (`gpus: all`).

---

### 3. The 6-Phase Iterative Development Plan (Step-by-Step)

---

#### 🚀 PHASE 1: Docker Foundation, NVIDIA GPU Passthrough & Zero-Copy File Browser
**Goal:** Enable the container to access the RTX 3060 GPU and the host filesystem (`/mnt/host`), allowing the user to browse source files and target directories via the web UI and preview videos.

**Tasks (Backend & Docker):**
1. Create `Dockerfile` and `docker-compose.yml`. Install `ffmpeg`, Python environment, and Node.js (for building the frontend) on the Ubuntu-based CUDA image.
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

#### ⚡ PHASE 2: GPU Speed Engine, HEVC (H.265) Toggle & Pitch-Correct Audio
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

#### ✂️ PHASE 3: Multi-Cut (Split & Keep Segments) & Time-Synced Subtitle Burn-in
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

#### 🔄 PHASE 4: Visual Transformations (Rotation & Interactive Crop)
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

#### 📦 PHASE 5: Render Queue (Batch Processing), Presets & Fault Tolerance
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

#### 🎛️ PHASE 6: Focus Mode Layout — Configuration Drawer & Always-On Render Bar
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

### 4. Target Project Directory Structure

```text
video-speed-studio/
├── docker-compose.yml
├── Dockerfile
├── README.md
├── ROADMAP.md
├── backend/
│   ├── requirements.txt
│   ├── main.py                 # FastAPI app & WebSocket endpoints
│   ├── config.py               # Mount paths (/mnt/host) & settings
│   ├── routers/
│   │   ├── filesystem.py       # File browser, metadata & video range-stream endpoints
│   │   └── queue.py            # Render queue & job control API
│   └── services/
│       ├── ffmpeg_builder.py   # Filter_complex, NVENC, atempo, multi-cut command builder
│       ├── ffprobe_service.py  # Video metadata extractor
│       └── worker.py           # Async background FFmpeg runner & progress parser
└── frontend/
    ├── package.json
    ├── vite.config.js
    ├── index.html
    └── src/
        ├── App.jsx
        ├── components/
        │   ├── CommandBar.jsx      # Configuration toggle, summary pills, destination, Render Now / + Add to Queue
        │   ├── FileBrowser.jsx     # Source file & Target directory browser
        │   ├── VideoWorkspace.jsx  # Video player preview, Crop overlay, Multi-Cut timeline
        │   ├── ControlPanel.jsx    # Configuration drawer: collapsible Presets, Source, Speed, Encoding, Subtitles
        │   └── RenderQueue.jsx     # Live progress bar & Batch queue manager