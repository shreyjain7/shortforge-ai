"""Media streaming (HTTP Range requests for smooth seeking in the preview player)."""

from __future__ import annotations

import mimetypes
import os
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from sqlalchemy.orm import Session

from shortforge.database.models import Short, Video
from shortforge.database.session import get_session
from shortforge.workers.context import get_context

router = APIRouter()

_RANGE = re.compile(r"bytes=(\d*)-(\d*)")
CHUNK = 1024 * 1024


def ranged_file(request: Request, path: Path, media_type: str | None = None) -> Response:
    if not path.exists():
        raise HTTPException(404, "File not found")
    media_type = media_type or mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    size = path.stat().st_size
    header = request.headers.get("range")
    if not header:
        return FileResponse(path, media_type=media_type, headers={"Accept-Ranges": "bytes",
                                                                  "Cache-Control": "no-cache"})
    m = _RANGE.match(header.strip())
    if not m:
        raise HTTPException(416, "Invalid range")
    start_s, end_s = m.groups()
    if start_s == "":
        length = int(end_s or 0)
        start, end = max(0, size - length), size - 1
    else:
        start = int(start_s)
        end = int(end_s) if end_s else size - 1
    end = min(end, size - 1)
    if start > end or start >= size:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})

    def iterfile():  # type: ignore[no-untyped-def]
        with path.open("rb") as fh:
            fh.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                data = fh.read(min(CHUNK, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data

    return StreamingResponse(iterfile(), status_code=206, media_type=media_type, headers={
        "Content-Range": f"bytes {start}-{end}/{size}", "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1), "Cache-Control": "no-cache"})


def _safe(path: str | None) -> Path:
    if not path:
        raise HTTPException(404, "Not available")
    p = Path(path).resolve()
    root = get_context().paths.root.resolve()
    # Local imports may live outside the data dir; everything else must be inside it.
    if not (str(p).startswith(str(root)) or p.exists()):
        raise HTTPException(403, "Forbidden")
    return p


@router.get("/media/video/{video_id}/{kind}")
def video_media(video_id: int, kind: str, request: Request, s: Session = Depends(get_session)) -> Response:
    v = s.get(Video, video_id)
    if v is None:
        raise HTTPException(404, "Video not found")
    if kind == "proxy":
        return ranged_file(request, _safe(v.proxy_path), "video/mp4")
    if kind == "thumbnail":
        return ranged_file(request, _safe(v.thumbnail_path), "image/jpeg")
    if kind == "source":
        return ranged_file(request, _safe(v.local_path))
    raise HTTPException(404, "Unknown media kind")


@router.get("/media/short/{short_id}/{kind}")
def short_media(short_id: int, kind: str, request: Request, s: Session = Depends(get_session)) -> Response:
    sh = s.get(Short, short_id)
    if sh is None:
        raise HTTPException(404, "Short not found")
    if kind == "video":
        return ranged_file(request, _safe(sh.output_path), "video/mp4")
    if kind == "cover":
        return ranged_file(request, _safe(sh.cover_path), "image/jpeg")
    if kind == "captions":
        return ranged_file(request, _safe(str(get_context().paths.captions / f"short_{short_id}.ass")), "text/plain")
    raise HTTPException(404, "Unknown media kind")


@router.get("/media/candidate/{candidate_id}/frame")
def candidate_frame(candidate_id: int, request: Request, s: Session = Depends(get_session)) -> Response:
    """A representative frame from the middle of a candidate (cached JPEG from the proxy)."""
    from shortforge.database.models import CandidateClip
    from shortforge.engines.media.proxy import make_thumbnail

    c = s.get(CandidateClip, candidate_id)
    if c is None:
        raise HTTPException(404, "Candidate not found")
    v = s.get(Video, c.video_id)
    out = get_context().paths.thumbnails / "candidates" / f"cand_{candidate_id}_{int(c.start * 10)}.jpg"
    if not out.exists():
        source = v.proxy_path or v.local_path if v else None
        if not source or not Path(source).exists():
            raise HTTPException(404, "No media for this candidate")
        make_thumbnail(Path(source), out, at=c.start + min(3.0, c.duration / 3), width=480)
    return ranged_file(request, out, "image/jpeg")


@router.post("/media/reveal")
def reveal(body: dict) -> dict:
    """Open the containing folder in Explorer (local desktop convenience)."""
    path = Path(str(body.get("path", ""))).resolve()
    if not path.exists():
        raise HTTPException(404, "Path not found")
    if not str(path).startswith(str(get_context().paths.root.resolve())):
        raise HTTPException(403, "Only files inside the ShortForge data folder can be revealed")
    if os.name == "nt":
        import subprocess

        subprocess.Popen(["explorer", "/select,", str(path)])
    return {"ok": True}
