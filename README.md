# vid-gen

Type a topic, get back an mp4. vid-gen writes a script, finds real stock footage and photos,
records a voiceover, burns in word-by-word captions, adds background music and renders a
finished video. No manual steps between the topic and the file.

Everything runs locally on a CPU with free tools: the script comes from a local LLM
(Ollama), footage from the free Pixabay API, the voice from edge-tts and the edit from FFmpeg.

## How it works

Each job gets its own folder under `data/jobs/<id>/`. The pipeline runs six stages in
order; each stage reads the files the previous one wrote and writes its own, so a job can
resume after a failure or redo any single stage.

| # | Stage | What happens | Output |
|---|---|---|---|
| 1 | **Script** | `qwen2.5:3b` on Ollama writes a title, a music mood and 5–8 scenes (narration, a visual description, stock-footage search terms). The JSON schema is enforced, word counts are checked against the target length, and short scenes are expanded or retried with feedback. | `script.json` |
| 2 | **Footage** | Every search term is run against Pixabay (video and photos). Results are scored by tag relevance, orientation, length and resolution; a clip used by one scene isn't reused by another. Searches and downloads are cached. | `assets/`, `assets.json` |
| 3 | **Voiceover** | edge-tts reads each scene's narration and reports the timing of every word. | `audio/scene_XX.mp3` + word timings |
| 4 | **Captions** | Words are grouped into short chunks and written as an `.ass` subtitle file with the current word highlighted. | `captions.ass` |
| 5 | **Music** | A royalty-free track is picked from `backend/assets/music/<mood>/` using the mood from the script. | `music.mp3` |
| 6 | **Render** | Each scene is cropped/trimmed to its narration (photos get a slow Ken Burns zoom), then FFmpeg crossfades the clips, lays down the voice, mixes the music lower under it, burns in the captions and encodes for the web. | `final.mp4` |

If a scene finds no usable footage it gets an animated gradient instead, so a video is
always produced.

```
Next.js UI ──HTTP/SSE──▶ FastAPI ──queue──▶ worker thread ──▶ pipeline stages ──▶ final.mp4
                                                                  │
                                    Ollama · Pixabay · edge-tts · FFmpeg
```

## Requirements

- Python 3.13 and [uv](https://docs.astral.sh/uv/)
- Node 24 and pnpm
- [FFmpeg](https://ffmpeg.org/) on the PATH
- [Ollama](https://ollama.com/) with the model pulled: `ollama pull qwen2.5:3b`
- A free [Pixabay API key](https://pixabay.com/api/docs/) (no credit card)

## Setup

```powershell
# 1. Config: copy the example and add your Pixabay key
cp .env.example .env          # then set PIXABAY_API_KEY=...

# 2. Backend dependencies
cd backend
uv sync

# 3. Frontend dependencies
cd ../frontend
pnpm install
```

Background music lives in `backend/assets/music/<mood>/` (moods: `upbeat`, `calm`,
`mysterious`, `epic`, `sad`, `corporate`). Any royalty-free mp3 works; with no tracks the
video is rendered without music.

## Run

Two terminals:

```powershell
# API + worker: http://localhost:8000 (interactive docs at /docs)
cd backend
uv run uvicorn app.main:app --port 8000

# Web UI: http://localhost:3000
cd frontend
pnpm dev
```

In the UI, enter a topic, choose the length (30/60/90 s), format (vertical 9:16 or wide
16:9), tone and voice, and press **Generate video**. The job page shows live progress; when
it's done you can play and download the mp4, or redo it from the script, footage, voice or
render step. The status light in the header shows whether Ollama, FFmpeg, the API key and
music are all in place.

A 30-second video takes about 3–5 minutes on a CPU; writing the script is the slowest part.

### Command line

The same pipeline without the UI:

```powershell
cd backend
uv run python cli.py run "how honeybees make honey" --duration 30 --aspect 9:16
uv run python cli.py run --job <id> --from render     # resume / redo from a stage
uv run python cli.py captions --job <id>              # run one stage on its own
```

Options: `--duration 30|60|90`, `--aspect 9:16|16:9`, `--tone fun|informative|dramatic`,
`--voice en-GB-SoniaNeural`, `--no-captions`, `--no-music`.

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Checks Ollama, the model, FFmpeg, API key, music, caption font |
| `GET` | `/voices` | Available English voices |
| `POST` | `/jobs` | Start a job `{topic, duration_sec, aspect, tone, voice, captions, music}` |
| `GET` | `/jobs` · `/jobs/{id}` | List jobs / job detail with per-stage status and log |
| `GET` | `/jobs/{id}/events` | Live progress (server-sent events) |
| `POST` | `/jobs/{id}/retry` | Resume, or redo from a given stage |
| `GET` | `/jobs/{id}/video` · `/poster` · `/script` · `/scenes` | Results |
| `DELETE` | `/jobs/{id}` | Delete a job and its files |

## Project layout

```
backend/
  app/
    main.py          FastAPI app and routes
    jobs.py          job folders, state.json, queue + worker thread
    models.py        request / script / state models
    pipeline/        script, assets, voice, captions, music, render, runner
  assets/            music by mood, caption font
  cli.py             command-line entry point
  tests/
frontend/
  app/               pages: create form + recent videos, job page
  components/        form, progress, stage list, scenes, log
  lib/               typed API client, live-updates hook
data/                job folders and download cache (not committed)
```

## Tests

```powershell
cd backend
uv run pytest
```

## Known limitations

- Abstract topics (e.g. "patience is a superpower") can pull in content from the example
  scene in the script prompt, since a 3B model copies examples closely.
- Videos can run longer than the chosen length when the narration overshoots its target.
- Many scenes end up as photos rather than video clips.
- Pixabay is the only footage source; Openverse and Wikimedia Commons would help for
  historic topics.
- Voiceover needs an internet connection (edge-tts); there's no offline voice yet.
