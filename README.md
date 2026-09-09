# Pungol (MVP scaffold)

Turn a long-form YouTube video into ranked, captioned, vertical (9:16) clips.

**⚠️ Before you build a public product on this:** downloading and re-publishing
someone else's YouTube video, even as short clips, can conflict with YouTube's
Terms of Service and copyright law depending on how it's used. This scaffold
is built for clipping videos you own or have explicit permission to repurpose
(e.g. a creator auto-clipping their own uploads to promote them). If you plan
to let arbitrary users clip arbitrary videos, get legal advice first.

## How it works

```
YouTube URL
   -> [yt-dlp]            download source video
   -> [faster-whisper]    local transcription, word-level timestamps
   -> [LLM]                pick best moments (OpenAI / Anthropic / Gemini)
   -> [OpenCV]             find face position to center the vertical crop
   -> [.ass captions]      generate burned-in caption file per clip
   -> [ffmpeg]             crop to 9:16, trim, burn captions -> final .mp4
```

Job status and clip metadata are stored in SQLite via the API; the frontend
polls for progress and displays finished clips.

## Requirements

- Python 3.10+
- Node.js 18+
- **ffmpeg** installed and on PATH (`brew install ffmpeg` / `apt install ffmpeg`)
- An API key for at least one LLM provider (OpenAI, Anthropic, or Gemini)
- A GPU is optional — `faster-whisper` runs fine on CPU with the `small` model,
  just slower per video

## Setup

### Backend

```bash
cd backend
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# edit .env: set LLM_PROVIDER and the matching API key

uvicorn app.main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
cp .env.local.example .env.local

npm run dev
```

Open http://localhost:3000, paste a YouTube URL, click "Clip it."

## What's intentionally simplified for the MVP

- **Background jobs run in a thread pool**, not Celery/Redis. Fine for one
  person testing locally; swap `ThreadPoolExecutor` in `main.py` for a Celery
  task calling the same `run_job()` function once you need multiple workers
  or jobs that survive an API restart.
- **Face-centered cropping uses OpenCV Haar cascades**, not a tracked model.
  It samples a handful of frames per clip and holds one crop position. Good
  enough to avoid cutting people's faces off; upgrade to MediaPipe or a
  frame-by-frame tracked crop later for smoother motion.
- **Captions are grouped in ~4-word chunks**, not full karaoke-style word
  highlighting. The `.ass` format supports per-word color animation if you
  want to add that next — `captions.py` is the file to extend.
- **No auth, no billing, single local SQLite file.** Add these once the
  pipeline itself is solid.

## Suggested build order

1. Get one video through the full pipeline end-to-end locally.
2. Tune the highlighter prompt against a few real videos — this is the part
   that most determines output quality.
3. Improve the crop (motion smoothing, multi-speaker handling).
4. Add karaoke captions.
5. Add auth + a real Postgres DB + S3 storage.
6. Move background jobs to Celery + Redis for concurrency.
