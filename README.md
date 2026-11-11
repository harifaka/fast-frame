# Fast-Frame

**GPU-accelerated video speed & processing studio — zero-copy, Dockerized, browser-based.**

Fast-Frame never uploads your media. Host drives are bind-mounted into the container, and FFmpeg
reads the source file and writes the result directly on the host filesystem. You get NVENC
(H.264/HEVC) rendering at 10–50× realtime without moving a single byte through the browser.

| Feature | What it does |
| --- | --- |
| **Speed engine** | 0.25× – 4.0× with pitch-corrected audio (chained `atempo`), never chipmunk audio |
| **H.264 / HEVC** | `h264_nvenc` (CQ 19) or `hevc_nvenc` (CQ 21, `hvc1`) — visually lossless |
| **Multi-cut** | Keep any number of segments, merged with `trim`/`atrim` + `concat` |
| **Subtitles** | Burn an `.srt` at its original 1× timestamps before cutting/speeding, 3 font sizes |
| **Rotation / flip** | 90° CW/CCW, 180°, horizontal flip, with live CSS preview |
| **Interactive crop** | Draggable/resizable overlay with Free / 16:9 / 9:16 / 1:1 presets, even-pixel rounding |
| **Batch queue** | Sequential GPU rendering, live progress/FPS/ETA, cancel, error logs, collision-safe names |
| **Presets** | Save speed/codec/subtitle-size combos in LocalStorage |

---

## 1. Requirements

- **Docker Desktop** (Windows/macOS) or Docker Engine + Compose v2 (Linux), with **WSL2** backend on Windows.
- An **NVIDIA GPU** (GTX 10xx / RTX 20xx+), a recent driver, and the
  **[NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)**.
- Verify GPU passthrough works before building:

```bash
docker run --rm --gpus all nvidia/cuda:12.2.0-base-ubuntu22.04 nvidia-smi
```

If that prints your GPU, Fast-Frame will detect it. If it fails, the app still starts and falls back
to `libx264`/`libx265` (see [CPU fallback](#7-cpu-fallback--no-gpu)).

---

## 2. Configure host drive access (the only required edit)

All media access is relative to **`HOST_MOUNT_PATH`**, which defaults to **`/mnt/host`** inside the
container. Map the folders you want to see in the UI to `/mnt/host/<name>` in `docker-compose.yml`.

### Windows (Docker Desktop + WSL2)

Map whole drives, one mount per drive letter. Inside the container they appear as `/mnt/host/d`,
`/mnt/host/e`, …:

```yaml
    volumes:
      - D:/:/mnt/host/d
      - E:/:/mnt/host/e
```

> Use the **drive-letter** form (`D:/`), not `\\wsl$\...` or `/mnt/d`. Docker Desktop shares drives
> through WSL2 and this form keeps NTFS permissions predictable. Ensure the drive is shared under
> *Docker Desktop → Settings → Resources → File sharing*.

### Linux

```yaml
    volumes:
      - /media:/mnt/host/media
      - /run/media:/mnt/host/run-media
      - /srv/video:/mnt/host/video
```

USB drives usually appear under `/media/$USER/<label>` or `/run/media/$USER/<label>`; mapping the
parent directory (`/media`, `/run/media`) makes every plugged-in drive visible in one place.

### Portable default

The shipped compose file maps `./media` so the stack works out of the box:

```yaml
    volumes:
      - ./media:/mnt/host/media
```

Put files in `./media` (created next to `docker-compose.yml`) or replace the line with your drive
mapping above. `HOST_MOUNT_PATH` can be changed via the environment variable if you want a different
container root.

**Security:** every path is resolved (symlinks included) and checked against `HOST_MOUNT_PATH`;
anything outside it returns HTTP 403. Only the folders you mount are reachable.

---

## 3. Run it

```bash
docker compose up --build -d
```

Open **http://localhost:8000**.

The image builds the React frontend and serves it from FastAPI, so there is only one port and one
process to manage. Useful commands:

```bash
docker compose logs -f fast-frame    # live API/FFmpeg logs
docker compose down                  # stop (renders in progress are terminated)
docker compose up --build -d         # rebuild after code changes
```

### GPU passthrough in Compose

`docker-compose.yml` uses the modern Compose syntax:

```yaml
    gpus: all
    environment:
      NVIDIA_VISIBLE_DEVICES: all
      NVIDIA_DRIVER_CAPABILITIES: compute,video,utility
```

On Compose releases older than v2.30 replace `gpus: all` with the commented block in the same file:

```yaml
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu, video]
```

Equivalently, without Compose: `docker run --gpus all -p 8000:8000 -v D:/:/mnt/host/d fast-frame`.

---

## 4. Using the studio

The `PREVIEW` + `MULTI-CUT TIMELINE` panel owns the whole window. Everything else lives behind the
**`⚙️ Configuration`** button in the command bar above it:

- Summary pills mirror the live settings (`1.25×`, `H.265 / HEVC`, `Subtitles`, `N cuts`,
  `Output ~mm:ss`) — click one to jump straight to the matching card.
- `Destination: /mnt/host` shows and changes the output folder.
- `Render Now` and `+ Add to Queue` are always here, so a render can be started without opening the
  drawer. The active job's progress card appears right below the bar.

The drawer slides in from the right (`Esc`, the backdrop or ✕ closes it) and holds five collapsible
cards — `PRESETS`, `SOURCE`, `SPEED`, `ENCODING`, `SUBTITLES`. The pills at the top of the drawer
jump to a card, and `Expand all` / `Collapse all` opens or folds every card at once. `Render Now`
and `+ Add to Queue` are repeated in the drawer footer.

1. **Pick a source** — `Browse files` in the preview header (or `Choose` in the drawer's `SOURCE`
   card) opens the zero-copy browser. Only `.mp4`, `.mkv`, `.avi` are
   selectable; clicking a folder enters it, double-clicking a file selects it.
2. **Pick a destination** — click `Destination: …` in the command bar (or the destination row in
   `ENCODING`) to choose the folder where the render is written on the host. Defaults to the
   source file's folder. The source is never modified.
3. **Speed** — preset buttons (`0.5×`…`3×`), the `+0.25` button, or the slider (`0.25×`–`4×`). The
   preview's `playbackRate` follows instantly; audio pitch is preserved in the render.
4. **Codec** — H.264 (maximum compatibility) or H.265/HEVC (~50% smaller, faster USB writes).
5. **Multi-cut** (optional) — seek, `Set In`, seek, `Set Out`, `Add Keep Segment`. Repeat for every
   section you want to keep; click a segment chip to jump to it, × to delete. No segments = whole video.
6. **Subtitles** (optional) — a matching `<video>.srt` is auto-selected. Pick any other `.srt` from
   the folder and choose Small/Medium/Large. Burn-in happens at 1× timestamps, so it stays synced
   through cuts and speed changes.
7. **Rotate / crop** (optional) — rotate 90° left/right, 180°, flip, then enable Crop and drag or
   resize the box (aspect presets: Free, 16:9, 9:16, 1:1). The preview is WYSIWYG.
8. **Render** — `Render Now` starts immediately; `Add to Queue` lines it up behind other jobs. The
   queue drawer (header button) shows live percent/FPS/speed/ETA, output paths, sizes, cancellable
   jobs and expandable FFmpeg logs. `Clear finished` tidies the history.
9. **Presets** — `Save current` stores speed/codec/subtitle-size; built-ins are
   *Fast Review (1.5× HEVC)* and *2× Speedup (H.264)*. Saved presets live in LocalStorage.

Keyboard shortcuts: `Space` play/pause, `←`/`→` seek ±5 s, `M` mute, `Esc` close the configuration
drawer.

> The queue lives in memory: restarting the container clears the job list (rendered files stay on
> disk). Presets and the last destination folder are stored in your browser's LocalStorage.

### Output naming & safety

Default name: `<source>_<speed>x_<codec>.mp4` (e.g. `holiday_1.5x_hevc.mp4`). You can edit it, but
Fast-Frame resolves the final path on the server: if the target exists (or is the source), `_1`,
`_2`, … is appended. Concurrent queued jobs reserve their names up front, so two jobs can never
write to the same file.

---

## 5. Rendering pipeline

The backend builds a single `filter_complex` in a fixed order:

```
[0:v] subtitles (1×, escaped path)
      → trim/setpts per keep-segment          (atrim/asetpts for audio)
      → concat=n=N:v=1:a=1
      → transpose/hflip
      → crop=w:h:x:y        (even pixels, oriented frame)
      → setpts=PTS/speed    (atempo chain for audio)
      → h264_nvenc | hevc_nvenc (or libx264/libx265)
```

Details worth knowing:

- **Timestamps** use `-fps_mode passthrough` (FFmpeg ≥ 5) / `-vsync passthrough` (FFmpeg 4.x) so
  speeding up keeps every frame instead of dropping to the nominal frame rate.
- **`-noautorotate` + explicit transpose**: phone rotation metadata is applied exactly once, and the
  output never inherits a display matrix (`-map_metadata -1`).
- **Auto-rotation default**: selecting a video whose display matrix says 90/180/270 pre-selects the
  equivalent rotation control so preview and render match.
- **NVENC runtime fallback**: if a listed NVENC encoder cannot open (driver/API mismatch, exhausted
  sessions, no `/dev/nvidia*`), the job is retried once with `libx264`/`libx265` and the UI shows a
  fallback note instead of failing.

---

## 6. API overview

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/health` | Liveness + host root |
| GET | `/api/system/gpu-check?refresh=true` | GPU name/driver, NVENC availability, CPU fallback encoders |
| GET | `/api/system/config` | Extensions, speed range, font sizes, codecs |
| GET | `/api/fs/list?path=…` | Directory listing (folders + videos + `.srt`), breadcrumbs |
| GET | `/api/video/metadata?path=…` | Duration, resolution, FPS, codec, audio, rotation |
| GET | `/api/video/stream?path=…` | HTTP 206 byte-range streaming (seekable `<video>`) |
| POST | `/api/queue/add` | Queue/render a job (`render_now: true` jumps the queue) |
| GET | `/api/queue` | Jobs, processing id, queue depth |
| DELETE | `/api/queue/{id}` | Cancel queued/running job or dismiss a finished one |
| POST | `/api/queue/clear-completed` | Remove completed/failed/cancelled history |
| WS | `/ws/progress` | Snapshot + live progress events |

Example job:

```json
{
  "input_path": "/mnt/host/d/clips/raw.mp4",
  "output_dir": "/mnt/host/d/exports",
  "output_filename": "raw_1.5x_hevc.mp4",
  "render_now": true,
  "options": {
    "speed": 1.5,
    "codec": "hevc",
    "segments": [{ "start": 10.0, "end": 45.0 }, { "start": 60.0, "end": 120.0 }],
    "subtitle_path": "/mnt/host/d/clips/raw.srt",
    "subtitle_font_size": 24,
    "rotation": "90_cw",
    "flip_horizontal": false,
    "crop": { "x": 0.1, "y": 0.1, "w": 0.8, "h": 0.8 },
    "prefer_gpu": true
  }
}
```

Interactive docs: **http://localhost:8000/docs**.

---

## 7. CPU fallback / no GPU

- **No NVIDIA GPU at all** — start with `gpus: all` removed and `NVIDIA_*` variables deleted; the
  API reports `status: "fallback"` and renders with `libx264`/`libx265`. Expect roughly realtime
  speed instead of 10–50× realtime.
- **GPU present but NVENC unavailable** — `/api/system/gpu-check` explains why
  (`message`, `ffmpeg_error`); renders automatically fall back per job.
- **CPU presets** are `veryfast` with CRF 19 (H.264) / CRF 21 (HEVC) for a good speed/quality trade-off.

---

## 8. Development (without Docker)

Backend (Python 3.11):

```bash
python -m venv .venv
.venv\Scripts\activate          # Linux/macOS: source .venv/bin/activate
pip install -r backend/requirements.txt
set HOST_MOUNT_PATH=C:\path\to\media   # Linux/macOS: export HOST_MOUNT_PATH=/path/to/media
cd backend
uvicorn main:app --reload --port 8000
```

Frontend (Node 20+): `cd frontend && npm install && npm run dev` → http://localhost:5173.
Vite proxies `/api` and `/ws` to port 8000. `npm run build` produces `frontend/dist`, which FastAPI
serves automatically.

Environment variables:

| Variable | Default | Meaning |
| --- | --- | --- |
| `HOST_MOUNT_PATH` | `/mnt/host` | Root of the zero-copy file browser |
| `FFMPEG_BIN` | `ffmpeg` | FFmpeg executable (full path allowed) |
| `FFPROBE_BIN` | `ffprobe` | ffprobe executable |

Tests (stdlib `unittest`, no FFmpeg required):

```bash
cd backend
python -m unittest discover -s tests
```

---

## 9. Troubleshooting

| Symptom | Fix |
| --- | --- |
| Header shows **CPU Fallback** | Run the `nvidia-smi` container test from §1; ensure `gpus: all` and the Container Toolkit are installed, then restart the container. |
| Folders are empty | Verify the bind mount target starts with `/mnt/host` and that `HOST_MOUNT_PATH` matches. The breadcrumb root is the container path. |
| `403 Path is outside of the configured host mount` | The requested path (or a symlink target) is outside the mount; map the real location. |
| `Permission denied` writing output | On Linux the container runs as root; check the host folder's ownership/SELinux label (`:z` suffix on the volume can help). |
| Video does not play in the browser | MKV/AVI codecs must be browser-decodable (H.264/AAC works everywhere). The render itself supports any codec FFmpeg decodes. |
| NVENC job fails, then completes as CPU | Expected: runtime fallback kicked in. See the `fallback_reason` on the job. |
| Port 8000 already in use | Change the mapping in `docker-compose.yml` (`"8080:8000"`) and open http://localhost:8080. |
| Container is slow to start | The first build compiles the frontend and installs Python deps; later `up` calls are fast (layers are cached). |

---

## 10. Project layout

```
fast-frame/
├── Dockerfile                 # CUDA 12.2 runtime + FFmpeg + Python 3.11 + Node 20 build
├── docker-compose.yml         # GPU passthrough + host mounts
├── backend/
│   ├── main.py                # FastAPI app, WebSocket, static frontend
│   ├── config.py              # Mount root, extensions, path-traversal defence
│   ├── models.py              # Pydantic job/options models
│   ├── routers/{filesystem,queue}.py
│   └── services/
│       ├── ffmpeg_builder.py  # filter_complex + NVENC command builder
│       ├── ffprobe_service.py # metadata extraction
│       ├── gpu_service.py     # nvidia-smi + encoder detection
│       └── worker.py          # async queue, FFmpeg runner, progress parser
├── frontend/                  # React + Vite + Tailwind studio UI
│   └── src/components/
│       ├── CommandBar.jsx     # Configuration toggle, summary pills, destination, render actions
│       ├── ControlPanel.jsx   # Slide-over configuration drawer with collapsible cards
│       ├── VideoWorkspace.jsx # Preview player, rotate/crop toolbar, multi-cut timeline
│       └── RenderQueue.jsx    # Live progress & batch queue drawer
└── backend/tests/             # command-builder & queue helper tests
```

`DECISIONS.md` records every architectural trade-off made during implementation.
