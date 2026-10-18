"""Clip demo. Upload a short video and see what Fluffy would do.

Run from the repo root: `uv run fluffy-demo`, then open http://127.0.0.1:8091
"""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from perceptron import SDKError

from fluffy.demo.analyze import SAMPLE_CLIP_NAME, SAMPLE_CLIP_URL, AnalyzeError, analyze_clip
from fluffy.profile import load_profiles

ROOT = Path(__file__).resolve().parents[3]
STATIC = Path(__file__).resolve().parent / "static" / "index.html"
PROFILES = load_profiles(ROOT / "config" / "profiles")
MAX_BYTES = 40 * 1024 * 1024
ALLOWED_SUFFIXES = {".mp4", ".webm", ".mov", ".m4v"}

app = FastAPI(title="Fluffy clip demo")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC)


@app.get("/api/health")
def health() -> dict[str, str | bool]:
    ready = bool(_api_key())
    return {"ok": True, "inference": "configured" if ready else "unavailable"}


@app.post("/api/analyze")
async def analyze(
    profile: str = Form(...),
    utterance: str = Form(""),
    video_file: UploadFile | None = File(default=None),
) -> dict:
    chosen = _profile(profile)
    if video_file is None or not video_file.filename:
        raise HTTPException(status_code=400, detail="Choose a video clip first.")
    suffix = Path(video_file.filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail="Use an mp4, webm, or mov clip.",
        )
    payload = await video_file.read()
    if not payload:
        raise HTTPException(status_code=400, detail="That file is empty.")
    if len(payload) > MAX_BYTES:
        raise HTTPException(status_code=400, detail="Keep the clip under 40 MB.")
    return _run(payload, suffix, chosen, utterance)


@app.post("/api/analyze-sample")
def analyze_sample(profile: str = Form(...), utterance: str = Form("")) -> dict:
    """Download Perceptron's public sample clip once, then analyze it."""
    import urllib.request

    chosen = _profile(profile)
    cached = Path(os.environ.get("TMPDIR", "/tmp")) / SAMPLE_CLIP_NAME
    if not cached.exists() or cached.stat().st_size < 1000:
        try:
            urllib.request.urlretrieve(SAMPLE_CLIP_URL, cached)
        except OSError as exc:
            raise HTTPException(status_code=502, detail=f"Could not download the sample clip ({exc}).") from exc
    return _run(cached.read_bytes(), ".mp4", chosen, utterance)


def _run(payload: bytes, suffix: str, profile, utterance: str) -> dict:
    import tempfile

    key = _api_key()
    if not key:
        raise HTTPException(status_code=500, detail="PERCEPTRON_API_KEY is not set.")
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
        handle.write(payload)
        path = Path(handle.name)
    try:
        try:
            return analyze_clip(path, profile, utterance, api_key=key)
        except SDKError as exc:
            message = _public_error(exc)
            print(f"perceptron error: {message}", flush=True)
            raise HTTPException(status_code=502, detail=message) from exc
        except AnalyzeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        path.unlink(missing_ok=True)


def _profile(name: str):
    profile = PROFILES.get(name)
    if profile is None:
        raise HTTPException(status_code=400, detail="Unknown profile.")
    return profile


def _api_key() -> str:
    from fluffy.config import load_integrations

    return load_integrations().perceptron_api_key


def _public_error(exc: SDKError) -> str:
    message = str(exc).strip() or exc.code or "Perceptron request failed"
    return message.replace("\n", " ")


def main() -> None:
    uvicorn.run("fluffy.demo.app:app", host="127.0.0.1", port=8091, reload=False)


if __name__ == "__main__":
    main()
