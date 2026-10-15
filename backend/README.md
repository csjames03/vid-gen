# vid-gen backend

FastAPI app, job worker and the topic-to-mp4 pipeline. See the main [README](../README.md)
for setup and usage.

```
uv sync
uv run uvicorn app.main:app --port 8000
uv run pytest
```
