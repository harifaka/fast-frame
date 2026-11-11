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
| **Auto host drives** | Every Windows drive + USB all at once via `/mnt/host/drives`, one-click Quick drives bar, no `.env` |
| **Network / NAS / URL** | HTTP(S), RTSP, RTMP and WebDAV streams plus SMB/NAS shares as source *or* destination |

---

## 1. Requirements

- **Docker Desktop** (Windows/macOS) or Docker Engine + Compose v2 (Linux), with **WSL2** backend on Windows.
- An **NVIDIA GPU** (GTX 10xx / RTX 20xx+), a recent driver, and the
  **[NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)**
  — or skip the GPU entirely with [`docker-compose.cpu.yml`](#cpu-only-mode-no-nvidia-driver--toolkit).
- Verify GPU passthrough works before building:

```bash
docker run --rm --gpus all nvidia/cuda:12.2.0-base-ubuntu22.04 nvidia-smi
```

If that prints your GPU, Fast-Frame will detect it. If it fails, the app still starts and falls back
to `libx264`/`libx265` (see [CPU fallback](#7-cpu-fallback--no-gpu)).

---

## 2. Host drives are mounted automatically (zero config)

The shipped `docker-compose.yml` already exposes the host, so there is nothing to edit before the
first run:

| Container path | Host source | What it is |
| --- | --- | --- |
| `/mnt/host/drives` | `/run/desktop/mnt/host` | Docker Desktop (WSL2) host root: every Windows drive (`drives/c`, `drives/d`, …) **and USB drives** |
| `/mnt/host/c` | `C:/` | Direct fallback access to the main Windows `C:` drive |
| `/mnt/host/media` | `./media` | Portable folder next to `docker-compose.yml` |
| `/mnt/host/network` | named volume | Kernel `mount.cifs` SMB shares |

The `Browse files` modal opens with a **Quick drives** bar listing the detected drives (`C:`, `D:`,
`E:` …), `Users` and `Media`, so a source or destination is one click away. Docker Desktop's
internal WSL folders (`wslg`, `shm`, `wsl`) are hidden from the listing.

### Linux hosts

Replace the two Windows mounts with the folders you want to expose:

```yaml
    volumes:
      - /media:/mnt/host/media
      - /run/media:/mnt/host/run-media
```

USB drives usually appear under `/media/$USER/<label>` or `/run/media/$USER/<label>`; mapping the
parent directory makes every plugged-in drive visible in one place. `HOST_MOUNT_PATH` can be
changed via environment variable if a different container root is wanted.

**Security:** every requested path is normalised lexically first, so `..` traversal outside
`HOST_MOUNT_PATH` always returns HTTP 403. The path is then resolved and must stay inside the
root; resolution may cross the mount boundary **only** inside the drive bind mounts
(`/mnt/host/drives`, `/mnt/host/c`) or below a real mount point — that is how Docker Desktop and
USB drives are exposed, and rejecting it was what produced false 403s before. Any other symlink
that points outside the root is rejected.

---

## 3. Run it

```bash
docker compose up --build -d
```

Open **http://localhost:8000**. From another device on your LAN use
**`http://<windows-host-ip>:8000`** — the port is published on `0.0.0.0` and Windows Defender
Firewall asks to allow it the first time the container starts.

The image is a two-stage build (~240 MB compressed, ≈975 MB on disk because Debian's FFmpeg pulls a
large AV/GL library set): Node lives only in the builder stage, and the runtime stage contains
Python 3.11, FFmpeg, `cifs-utils` and the app. NVENC support comes from the host driver that the
NVIDIA Container Toolkit injects at runtime, not from a CUDA base image.

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

### CPU-only mode (no NVIDIA driver / toolkit)

Start the stack with the CPU override instead of editing the main file:

```bash
docker compose -f docker-compose.yml -f docker-compose.cpu.yml up --build -d
```

`docker-compose.cpu.yml` clears the GPU device request (`gpus: []`) and the `NVIDIA_*` variables, so
the container starts on machines without an NVIDIA driver. Renders use `libx264`/`libx265` and
`/api/system/gpu-check` reports `status: "fallback"` with the reason. Use this file as the base
compose file (e.g. rename it or pass both `-f` flags) whenever you run without a GPU.

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

### Network sources: direct URLs & SMB/NAS shares

The `Browse files` modal has two tabs: **📁 Host Drives** (the classic zero-copy browser) and
**🌐 Network / NAS / URL**.

- **Direct video URL** — paste an `http(s)://`, `rtsp://` or WebDAV (`webdav://`, `webdavs://`) link
  and press **Load Network Stream**. The URL is validated with `ffprobe` and rendered straight from
  `-i <url>` with `-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5`
  (`-rtsp_transport tcp` for RTSP) so a dropped connection does not kill the job. Nothing is
  downloaded first. HTTP/WebDAV sources preview through `/api/video/stream?url=…` (Range is passed
  through); RTSP/RTMP sources can be rendered but not previewed in a browser.
- **SMB / NAS share** — enter `//192.168.1.100/videos`, `\\SERVER\Share` or `smb://server/share`
  (credentials optional) and press **Connect**. The share is exposed at
  `/mnt/host/network/<share>` and can be used for **both** the source video and the destination
  folder.
  - If the container may create kernel mounts (`cap_add: [SYS_ADMIN]`, see `docker-compose.yml`)
    `mount.cifs` is used and the share behaves like a local drive.
  - Otherwise the bundled `smbprotocol` client is used: browsing and preview read byte ranges on
    demand, and FFmpeg consumes the file through a loopback relay
    (`/api/fs/network-stream`), so the source is still never copied. Renders are staged locally and
    uploaded to the share as soon as the encode finishes.
  - Windows mapped drives (`Z:`, `Y:`, …) that are bind-mounted into the container are ordinary
    host drives and appear under **📁 Host Drives**.
- Recent URLs and share bookmarks are kept in LocalStorage for one-click reconnect. Passwords are
  never stored: they live in backend memory only and are never returned by the API.

#### Sharing a folder from Windows 10/11 over the LAN (SMB)

1. Explorer → right-click the folder → **Properties → Sharing → Share…**, add your account (or
   `Everyone`) and grant *Read* — add *Read/Write* when the folder is the render destination.
2. Keep the network profile on **Private** and enable *Network discovery* + *File and printer
   sharing* (Settings → Network & Internet → Advanced network settings → Advanced sharing settings).
3. In Fast-Frame open `Browse files → 🌐 Network / NAS / URL`, enter `\\HOST\ShareName` (or
   `\\HOST-IP\ShareName`; `//host/share` also works), add the Windows username/password and press
   **Connect**. `host.docker.internal` resolves to the Windows host, so
   `\\host.docker.internal\ShareName` is a reliable fallback when the machine name does not resolve.
4. SMB2/SMB3 are supported; the legacy SMB1 protocol is not.
5. To reach the studio from other LAN devices, allow the port once in Windows Defender Firewall:

   ```powershell
   New-NetFirewallRule -DisplayName "Fast-Frame" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow
   ```

   then browse `http://<HOST-IP>:8000`.

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
| GET | `/api/fs/drives` | Quick drive bar shortcuts (`C:`, `D:`, `Users`, `Media`) |
| GET | `/api/fs/network-mounts` | Connected SMB/NAS locations + SMB client availability |
| POST | `/api/fs/network-mount` | Connect a share (`{ path, username?, password?, name? }`) |
| DELETE | `/api/fs/network-mount/{id}` | Disconnect a share |
| GET | `/api/fs/network-stream?path=…` | Loopback relay FFmpeg uses for non-mounted shares |
| GET | `/api/video/metadata?path=…` | Duration, resolution, FPS, codec, audio, rotation |
| GET | `/api/video/metadata?url=…` | Same, probed directly from a network URL |
| GET | `/api/video/stream?path=…` | HTTP 206 byte-range streaming (seekable `<video>`) |
| GET | `/api/video/stream?url=…` | Proxy a remote HTTP/WebDAV stream (Range preserved) |
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

- **No NVIDIA GPU at all** — run with the CPU override:
  `docker compose -f docker-compose.yml -f docker-compose.cpu.yml up --build -d`. The API reports
  `status: "fallback"` and renders with `libx264`/`libx265`. Expect roughly realtime speed instead
  of 10–50× realtime.
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
| Folders are empty | Verify the bind mount target starts with `/mnt/host` and that `HOST_MOUNT_PATH` matches. On Linux, adjust the Windows drive mounts in `docker-compose.yml` to your `/media` paths. |
| Drives are missing from the Quick drives bar | Docker Desktop only exposes `/run/desktop/mnt/host` while the WSL2 backend is active; restart Docker Desktop, then `docker compose up -d`. The `/mnt/host/c` fallback mount always shows the `C:` drive. |
| `403 Path is outside of the configured host mount` | The requested path (or a symlink target) resolves outside the mount and is not inside `/mnt/host/drives` or `/mnt/host/c`; map the real location. |
| `Permission denied` writing output | On Linux the container runs as root; check the host folder's ownership/SELinux label (`:z` suffix on the volume can help). |
| Video does not play in the browser | MKV/AVI codecs must be browser-decodable (H.264/AAC works everywhere). The render itself supports any codec FFmpeg decodes. |
| NVENC job fails, then completes as CPU | Expected: runtime fallback kicked in. See the `fallback_reason` on the job. |
| Port 8000 already in use | Change the mapping in `docker-compose.yml` (`"8080:8000"`) and open http://localhost:8080. |
| Container is slow to start | The first build compiles the frontend and installs Python deps; later `up` calls are fast (layers are cached). |

---

## 10. Project layout

```
fast-frame/
├── Dockerfile                 # 2-stage slim build (Node 20 builder -> Python 3.11 + FFmpeg + NVENC env)
├── docker-compose.yml         # GPU passthrough + automatic host drive mounts
├── docker-compose.cpu.yml     # CPU-only override (gpus: []) for machines without NVIDIA drivers
├── backend/
│   ├── main.py                # FastAPI app, WebSocket, static frontend
│   ├── config.py              # Mount root, drive shortcuts, path-traversal defence
│   ├── models.py              # Pydantic job/options models
│   ├── routers/{filesystem,network,queue}.py
│   └── services/
│       ├── ffmpeg_builder.py  # filter_complex + NVENC command builder
│       ├── ffprobe_service.py # metadata extraction
│       ├── gpu_service.py     # nvidia-smi + encoder detection
│       ├── network_service.py # SMB/CIFS + smbprotocol access
│       └── worker.py          # async queue, FFmpeg runner, progress parser
├── frontend/                  # React + Vite + Tailwind studio UI
│   └── src/components/
│       ├── CommandBar.jsx     # Configuration toggle, summary pills, destination, render actions
│       ├── ControlPanel.jsx   # Slide-over configuration drawer with collapsible cards
│       ├── FileBrowser.jsx    # Host drives (quick drive bar) + network/SMB/URL tabs
│       ├── NetworkPanel.jsx   # Direct URL & SMB/NAS share connector
│       ├── VideoWorkspace.jsx # Preview player, rotate/crop toolbar, multi-cut timeline
│       └── RenderQueue.jsx    # Live progress & batch queue drawer
└── backend/tests/             # command-builder, queue helper, network & host-mount tests
```

`DECISIONS.md` records every architectural trade-off made during implementation.
