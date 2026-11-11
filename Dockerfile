# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Stage 1 - build the React studio UI (Node 20, throwaway)
# ---------------------------------------------------------------------------
FROM node:20-alpine AS frontend-build
WORKDIR /frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2 - slim runtime: Python 3.11 + FFmpeg (NVENC via driver injection)
#
# The NVIDIA Container Toolkit injects the host's libnvidia-encode.so and
# nvidia-smi into the container because of the two NVIDIA_* variables below, so
# the ~3GB CUDA base image is not needed for hardware encoding.
# ---------------------------------------------------------------------------
FROM python:3.11-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HOST_MOUNT_PATH=/mnt/host \
    NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=compute,video,utility

# ffmpeg (NVENC + libass for subtitle burn-in) and cifs-utils for kernel SMB
# mounts; curl is intentionally not installed, the healthcheck uses Python.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        cifs-utils \
        ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ backend/
COPY --from=frontend-build /frontend/dist frontend/dist

EXPOSE 8000
VOLUME ["/mnt/host"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"]

WORKDIR /app/backend
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
