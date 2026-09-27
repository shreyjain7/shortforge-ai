"""Smoke-test every GET endpoint of a running engine with real IDs; report server errors.

    python scripts/api_sweep.py [--base http://127.0.0.1:8756]
"""

from __future__ import annotations

import argparse
import re
import sys

import httpx


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8756")
    args = ap.parse_args()
    c = httpx.Client(base_url=args.base, timeout=120)
    spec = c.get("/openapi.json").json()
    ids: dict[str, list[str]] = {
        "video_id": [str(v["id"]) for v in c.get("/api/videos").json()["items"][:3]],
        "short_id": [str(s["id"]) for s in c.get("/api/shorts").json()["items"][:3]],
        "cid": [str(x["id"]) for x in c.get("/api/candidates").json()["items"][:3]],
        "candidate_id": [str(x["id"]) for x in c.get("/api/candidates").json()["items"][:2]],
        "source_id": [str(s["id"]) for s in c.get("/api/sources").json()[:3]],
        "job_id": [str(j["id"]) for j in c.get("/api/jobs").json()["items"][:3]],
        "name": ["Bold", "Neon"],
        "kind": ["proxy", "thumbnail", "video", "cover", "captions", "frame"],
    }
    failures = 0
    checked = 0
    for path, ops in spec["paths"].items():
        if "get" not in ops or path in ("/api/events",):
            continue
        params = re.findall(r"{(\w+)(?::path)?}", path)
        variants = [path]
        for p in params:
            values = ids.get(p) or ["1"]
            variants = [v.replace("{" + p + "}", val).replace("{" + p + ":path}", val) for v in variants for val in values]
        for url in variants[:6]:
            r = c.get(url)
            checked += 1
            if r.status_code >= 500:
                failures += 1
                print(f"FAIL {r.status_code} {url}: {r.text[:300]}")
    print(f"checked {checked} requests, {failures} server errors")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
