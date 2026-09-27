"""Build and (optionally) publish a ShortForge release.

    python scripts/release.py                 # build signed installer + latest.json into build/release/
    python scripts/release.py --publish       # ...and create/update the GitHub Release

Environment:
    TAURI_SIGNING_PRIVATE_KEY           signing key contents (or TAURI_SIGNING_PRIVATE_KEY_PATH)
    TAURI_SIGNING_PRIVATE_KEY_PASSWORD  optional
    GITHUB_TOKEN                        required for --publish (falls back to the git credential helper)
    GITHUB_REPOSITORY                   owner/repo (default: shreyjain7/shortforge-ai)
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
import urllib.parse
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "apps" / "desktop"
TAURI = DESKTOP / "src-tauri"
RES = TAURI / "resources"
OUT = ROOT / "build" / "release"
UV_VERSION = "0.12.19"
REPO = os.environ.get("GITHUB_REPOSITORY", "shreyjain7/shortforge-ai")


def sh(cmd: list[str], cwd: Path = ROOT, env: dict | None = None) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=cwd, check=True, env={**os.environ, **(env or {})}, shell=os.name == "nt" and cmd[0] in ("npm", "npx"))


def version() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]


def check_versions(v: str) -> None:
    conf = json.loads((TAURI / "tauri.conf.json").read_text())
    pkg = json.loads((DESKTOP / "package.json").read_text())
    cargo = re.search(r'^version = "([^"]+)"', (TAURI / "Cargo.toml").read_text(), re.M).group(1)
    init = re.search(r'__version__ = "([^"]+)"', (ROOT / "backend" / "shortforge" / "__init__.py").read_text()).group(1)
    found = {"pyproject": v, "tauri.conf.json": conf["version"], "package.json": pkg["version"], "Cargo.toml": cargo,
             "__init__.py": init}
    if len(set(found.values())) != 1:
        sys.exit(f"Version mismatch: {found}")


def release_notes(v: str) -> str:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    m = re.search(rf"^## \[?{re.escape(v)}\]?.*?\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    return m.group(1).strip() if m else f"ShortForge {v}"


def build_wheel() -> Path:
    engine = RES / "engine"
    shutil.rmtree(engine, ignore_errors=True)
    engine.mkdir(parents=True)
    sh([shutil.which("uv") or str(fetch_uv()), "build", "--wheel", "--out-dir", str(engine)])
    for extra in engine.iterdir():  # uv drops a .gitignore into the output folder
        if extra.suffix != ".whl":
            extra.unlink()
    wheels = list(engine.glob("shortforge-*.whl"))
    assert len(wheels) == 1, wheels
    return wheels[0]


def fetch_uv() -> Path:
    target = RES / "uv.exe"
    if target.exists() and target.stat().st_size > 1_000_000:
        return target
    url = f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}/uv-x86_64-pc-windows-msvc.zip"
    print("downloading", url)
    r = httpx.get(url, follow_redirects=True, timeout=120)
    r.raise_for_status()
    RES.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".part")
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        name = next(n for n in zf.namelist() if n.endswith("uv.exe"))
        tmp.write_bytes(zf.read(name))
    tmp.replace(target)
    return target


def build_app(v: str) -> tuple[Path, Path]:
    env: dict[str, str] = {}
    if not os.environ.get("TAURI_SIGNING_PRIVATE_KEY"):
        key_path = os.environ.get("TAURI_SIGNING_PRIVATE_KEY_PATH") or str(Path.home() / ".tauri" / "shortforge.key")
        env["TAURI_SIGNING_PRIVATE_KEY"] = Path(key_path).read_text().strip()
    env.setdefault("TAURI_SIGNING_PRIVATE_KEY_PASSWORD", os.environ.get("TAURI_SIGNING_PRIVATE_KEY_PASSWORD", ""))
    sh(["npm", "ci"], cwd=DESKTOP)
    sh(["npx", "tauri", "build"], cwd=DESKTOP, env=env)
    nsis = TAURI / "target" / "release" / "bundle" / "nsis"
    exe = next(nsis.glob(f"*_{v}_x64-setup.exe"))
    sig = exe.with_name(exe.name + ".sig")
    return exe, sig


def assemble(v: str, exe: Path, sig: Path) -> dict[str, Path]:
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)
    asset = f"ShortForge-AI_{v}_x64-setup.exe"
    shutil.copy(exe, OUT / asset)
    shutil.copy(sig, OUT / f"{asset}.sig")
    latest = {
        "version": v,
        "notes": release_notes(v),
        "pub_date": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "platforms": {"windows-x86_64": {
            "signature": sig.read_text().strip(),
            "url": f"https://github.com/{REPO}/releases/download/v{v}/{urllib.parse.quote(asset)}",
        }},
    }
    (OUT / "latest.json").write_text(json.dumps(latest, indent=2))
    return {asset: OUT / asset, f"{asset}.sig": OUT / f"{asset}.sig", "latest.json": OUT / "latest.json"}


def github_token() -> str:
    tok = os.environ.get("GITHUB_TOKEN")
    if tok:
        return tok
    out = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n", capture_output=True,
                         text=True, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"}).stdout
    for line in out.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1]
    sys.exit("No GitHub token available")


def already_published(v: str) -> bool:
    h = {"Authorization": f"token {github_token()}", "Accept": "application/vnd.github+json"}
    r = httpx.get(f"https://api.github.com/repos/{REPO}/releases/tags/v{v}", headers=h, timeout=30)
    return r.status_code == 200 and any(a["name"].endswith("-setup.exe") for a in r.json().get("assets", []))


def publish(v: str, files: dict[str, Path]) -> str:
    h = {"Authorization": f"token {github_token()}", "Accept": "application/vnd.github+json"}
    api = f"https://api.github.com/repos/{REPO}"
    with httpx.Client(headers=h, timeout=120) as c:
        r = c.get(f"{api}/releases/tags/v{v}")
        body = {"tag_name": f"v{v}", "name": f"ShortForge AI {v}", "body": release_notes(v) + INSTALL_NOTE,
                "draft": False, "prerelease": False, "make_latest": "true"}
        if r.status_code == 200:
            rel = c.patch(f"{api}/releases/{r.json()['id']}", json=body).json()
        else:
            sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
            rel = c.post(f"{api}/releases", json={**body, "target_commitish": sha}).json()
        if "id" not in rel:
            sys.exit(f"GitHub release failed: {rel}")
        existing = {a["name"]: a["id"] for a in rel.get("assets", [])}
        upload = rel["upload_url"].split("{")[0]
        for name, path in files.items():
            if name in existing:
                c.delete(f"{api}/releases/assets/{existing[name]}")
            ctype = "application/json" if name.endswith(".json") else "application/octet-stream"
            with path.open("rb") as fh:
                res = c.post(upload, params={"name": name}, content=fh.read(), headers={**h, "Content-Type": ctype},
                             timeout=600)
            res.raise_for_status()
            print("uploaded", name)
        return rel["html_url"]


INSTALL_NOTE = """

---
**Install:** download `ShortForge-AI_*_x64-setup.exe` below and run it (no admin rights needed). On first launch
ShortForge installs its local AI engine (~2 GB with NVIDIA CUDA support) and can install FFmpeg for you.
Installed copies update themselves automatically.
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--force", action="store_true", help="rebuild and replace an already published release")
    args = ap.parse_args()
    v = version()
    check_versions(v)
    print(f"== ShortForge {v}")
    # Publishing locally creates the tag, which triggers the tag workflow: don't rebuild what is already out.
    if args.publish and not args.force and already_published(v):
        print(f"v{v} is already published; nothing to do (use --force to replace it)")
        return
    cargo_bin = Path.home() / ".cargo" / "bin"
    if not shutil.which("cargo") and cargo_bin.is_dir():
        os.environ["PATH"] = f"{cargo_bin}{os.pathsep}{os.environ['PATH']}"
    fetch_uv()
    build_wheel()
    exe, sig = build_app(v)
    files = assemble(v, exe, sig)
    print("built:", *files.values(), sep="\n  ")
    if args.publish:
        print("release:", publish(v, files))


if __name__ == "__main__":
    main()
