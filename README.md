# Mananabas (MVP scaffold)

Turn a long-form YouTube video into ranked, captioned, vertical (9:16) clips
with subject-centered crop, short-form grade/motion, and mixed audio.

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
   -> [LLM]               pick best moments (OpenAI / Anthropic / Gemini)
   -> [OpenCV]            center the 9:16 crop on the main subject
   -> [.ass captions]     burned-in captions with pop-in animation
   -> [ffmpeg]            crop, grade, Ken Burns zoom, fades,
                          opening/ending SFX, generated BGM -> final .mp4
```

Job status and clip metadata are stored in SQLite via the API. The frontend
polls for progress and plays finished clips.

Every new job uses this same pipeline. Re-run a URL to pick up render changes;
already-finished jobs keep the files they already wrote.

## Tech stack

| Layer | Stack |
|---|---|
| Backend | FastAPI, SQLModel (SQLite), yt-dlp, faster-whisper, OpenCV, ffmpeg (`imageio-ffmpeg` fallback) |
| LLM | OpenAI, Anthropic, and Gemini (startup requires one provider key; highlight selection can fall back across providers) |
| Frontend | Next.js 14, React 18, TypeScript, Tailwind CSS |

## Requirements

- Python 3.10+
- Node.js 18+
- **ffmpeg** on PATH is preferred (`brew install ffmpeg` / `apt install ffmpeg`). If it is missing, the backend falls back to the static binary from `imageio-ffmpeg`
- An API key for at least one LLM provider (OpenAI, Anthropic, or Gemini)
- A GPU is optional — `faster-whisper` runs on CPU with the `tiny`/`small` models, just slower per video

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

`LLM_PROVIDER` must be one of `openai`, `anthropic`, or `gemini`, and the
matching `*_API_KEY` must be set — the app fails fast on startup if it isn't.

Confirm the API is up at http://127.0.0.1:8000 (health JSON) or
http://127.0.0.1:8000/docs (Swagger). The clipping UI is not served from
port 8000.

There is also a standalone runner that skips the API/frontend:

```bash
cd backend
source venv/bin/activate
python run_pipeline.py "https://www.youtube.com/watch?v=..."
```

### Frontend

```bash
cd frontend
npm install
cp .env.local.example .env.local

npm run dev
```

Open http://localhost:3000, paste a YouTube URL, click "Clip it."

## Configuration reference

Backend env vars (see `backend/.env.example`):

| Variable | Default | Notes |
|---|---|---|
| `LLM_PROVIDER` | `openai` | `openai` \| `anthropic` \| `gemini` |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` | — | The key matching `LLM_PROVIDER` is required at startup. Extra keys let highlight stages fall back to another provider |
| `OPENAI_MODEL` | `gpt-4o-mini` | |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-6` | |
| `GEMINI_MODEL` | `gemini-3.6-flash` | |
| `WHISPER_MODEL_SIZE` | `tiny` | `tiny` \| `base` \| `small` \| `medium` \| `large-v3` — bigger is more accurate, slower |
| `WHISPER_DEVICE` | `cpu` | `cpu` or `cuda` (needs NVIDIA GPU + drivers) |
| `MEDIA_DIR` | `./media` | Where source downloads and rendered clips are written and served from |
| `DATABASE_URL` | `sqlite:///./mananabas.db` | |
| `MAX_CLIPS_PER_JOB` | `5` | |
| `CLIP_MIN_SECONDS` / `CLIP_MAX_SECONDS` | `20` / `90` | Clip length bounds passed to the highlighter |

Frontend env vars (see `frontend/.env.local.example`):

| Variable | Default | Notes |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | Base URL the frontend calls for the API |

## API reference

Interactive docs are at `/docs` once the backend is running.

| Method | Path | Description |
|---|---|---|
| GET | `/` | Health check (`{"status": "ok", ...}`) |
| POST | `/api/jobs` | Submit a YouTube URL; starts a background job |
| GET | `/api/jobs` | List all jobs, newest first |
| GET | `/api/jobs/{job_id}` | Get a job's status and its clips |
| GET | `/api/clips/{clip_id}/captions?format=txt\|srt\|ass` | A clip's generated captions as text (default `txt`); served as a file download |
| GET | `/media/{job_id}/{file}` | Serve a rendered clip file |

A job moves through `queued -> downloading -> transcribing -> ranking ->
rendering -> done` (or `failed` on error). Each clip includes a `title`,
`hook`, `score`, `start`/`end` timestamps, and `file_path`.

## Project structure

```
backend/
  app/
    main.py              # FastAPI app, routes, background job dispatch
    models.py            # SQLModel tables: Job, Clip, JobStatus
    schemas.py           # Pydantic request/response models
    jobs.py              # run_job() orchestrator
    config.py            # env-driven settings + validation
    database.py          # SQLite session setup
    pipeline/
      downloader.py      # yt-dlp video download
      transcriber.py     # faster-whisper transcription
      highlighter.py     # multi-provider moment selection
      reframe.py         # subject-centered 9:16 crop (faces, motion, energy)
      captions.py        # .ass captions with pop-in animation
      render.py          # ffmpeg crop, grade, zoom, SFX, BGM
  tests/
    test_pipeline.py
  run_pipeline.py        # CLI runner (validates env at import)
  run_pipeline_env_gated.py
  e2e_probe.py

frontend/
  app/
    page.tsx             # submit URL, poll job, show clips
    layout.tsx
    globals.css
```

## Testing

```bash
cd backend
source venv/bin/activate
pip install pytest          # not pinned in requirements.txt
python -m pytest tests/test_pipeline.py -q
```

`e2e_probe.py` can be run manually to exercise the full pipeline against a
real video.

## What's intentionally simplified for the MVP

- **Background jobs run in a thread pool**, not Celery/Redis. Fine for local
  use; swap `ThreadPoolExecutor` in `main.py` for a Celery task calling the
  same `run_job()` once you need multiple workers or jobs that survive an API
  restart.
- **Crop position is still one window per clip**, not a tracked pan. Detection
  now combines frontal/profile faces, upper body, motion, and edge energy so
  the subject is less likely to sit off-center, but the window does not follow
  the speaker frame-by-frame.
- **Captions are ~4-word chunks** with a short fade/scale pop-in, not karaoke
  word highlighting. `captions.py` is the file to extend.
- **Music and stings are generated in ffmpeg** (pad + texture + whoosh/sting),
  not licensed tracks. Speech stays in front of the bed.
- **No auth, no billing, single local SQLite file.** Add these once the
  pipeline itself is solid.

## Suggested next steps

1. Tune the highlighter prompts against a few real videos — this still
   determines clip quality more than anything else.
2. Track the crop over time (multi-speaker, smooth pans).
3. Add karaoke-style word highlighting.
4. Add auth + Postgres + object storage for clips.
5. Move background jobs to Celery + Redis.

## License

No license file is currently included in this repository.