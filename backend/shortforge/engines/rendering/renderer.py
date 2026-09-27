"""Short renderer: EditTimeline -> finished 9:16 MP4.

Pipeline (all streaming, bounded memory):
  FFmpeg decode (CFR, optional NVDEC) --raw BGR--> Python compositor (layouts, virtual camera,
  punch-ins, B-roll) --raw BGR--> FFmpeg encode (enhancement filters, libass captions,
  assembled/mastered audio, NVENC or x264) --> MP4 (+faststart)
"""

from __future__ import annotations

import contextlib
import math
import queue
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from shortforge.core.errors import FFmpegError, JobCancelled, ShortForgeError
from shortforge.core.logging import get_logger
from shortforge.engines.editing.timeline import EditTimeline
from shortforge.engines.media import ffmpeg as ff
from shortforge.engines.media.ffmpeg import CancelToken
from shortforge.engines.rendering import audio_graph as ag
from shortforge.engines.rendering.compositor import Compositor

log = get_logger("render")

ProgressFn = Callable[[float, str], None]


@dataclass
class RenderOptions:
    profile: str = "BALANCED"  # FAST | BALANCED | ULTRA
    codec: str = "h264"
    encoder: str = "auto"
    cq: int | None = None
    bitrate_kbps: int | None = None
    audio_bitrate_kbps: int = 192
    hwaccel_decode: bool = True


@dataclass
class RenderResult:
    path: Path
    encoder: str
    elapsed_s: float
    frames: int
    size: int
    duration: float
    ffmpeg_cmd: str
    crop_path: list[tuple[float, float, float, float, float]]  # t, x0, y0, w, h (source px)


class _FrameSource:
    """Continuous CFR decode of the source between ``start`` and ``end`` (source seconds)."""

    def __init__(self, path: str, start: float, end: float, fps: float, width: int, height: int,
                 hwaccel: bool) -> None:
        self.fps = fps
        self.start = start
        self.w, self.h = width, height
        self.frame_bytes = width * height * 3
        args = [ff.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-nostdin"]
        if hwaccel:
            args += ["-hwaccel", "auto"]
        args += ["-ss", f"{max(0.0, start):.4f}", "-i", path, "-t", f"{end - start + 1.0 / fps:.4f}",
                 "-vf", f"fps={fps},scale={width}:{height}:flags=bicubic", "-an", "-sn",
                 "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
        self.proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     creationflags=ff.CREATE_NO_WINDOW, bufsize=self.frame_bytes * 4)
        self.index = -1
        self.frame: np.ndarray | None = None

    def get(self, index: int) -> np.ndarray | None:
        """Frame number ``index`` since ``start`` (monotonic access only)."""
        assert self.proc.stdout is not None
        while self.index < index:
            buf = self.proc.stdout.read(self.frame_bytes)
            if len(buf) < self.frame_bytes:
                return self.frame  # hold the last frame at the very end
            self.frame = np.frombuffer(buf, dtype=np.uint8).reshape(self.h, self.w, 3)
            self.index += 1
        return self.frame

    def close(self) -> str:
        err = ""
        try:
            self.proc.kill()
            _, e = self.proc.communicate(timeout=5)
            err = e.decode("utf-8", errors="replace") if e else ""
        except Exception:
            pass
        return err


class _BrollSource:
    def __init__(self, path: str, start: float, fps: float, w: int, h: int) -> None:
        info = ff.probe(path)
        sw, sh = info.display_size
        scale = max(w / sw, h / sh)
        cw, ch = int(math.ceil(sw * scale / 2) * 2), int(math.ceil(sh * scale / 2) * 2)
        vf = f"fps={fps},scale={cw}:{ch}:flags=bicubic,crop={w}:{h}"
        args = [ff.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-nostdin", "-stream_loop", "-1",
                "-ss", f"{start:.3f}", "-i", path, "-vf", vf, "-an", "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
        self.proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     creationflags=ff.CREATE_NO_WINDOW)
        self.w, self.h = w, h
        self.last: np.ndarray | None = None

    def next(self) -> np.ndarray | None:
        assert self.proc.stdout is not None
        buf = self.proc.stdout.read(self.w * self.h * 3)
        if len(buf) == self.w * self.h * 3:
            self.last = np.frombuffer(buf, dtype=np.uint8).reshape(self.h, self.w, 3)
        return self.last

    def close(self) -> None:
        self.proc.kill()
        self.proc.wait()


def enhancement_filters(tl: EditTimeline) -> list[str]:
    e = tl.enhance
    chain = []
    if e.denoise:
        chain.append("hqdn3d=1.5:1.5:4:4")
    if e.contrast or e.color:
        chain.append(f"eq=contrast={1.06 if e.contrast else 1.0}:saturation={1.1 if e.color else 1.0}:gamma=1.0")
    if e.sharpen:
        chain.append("unsharp=5:5:0.55:5:5:0.0")
    if e.vignette:
        chain.append("vignette=angle=PI/5:mode=forward")
    return chain


def render_timeline(timeline: EditTimeline, out_path: Path, work_dir: Path, *, options: RenderOptions,
                    ass_path: Path | None = None, fonts_dir: Path | None = None,
                    progress: ProgressFn | None = None, cancel: CancelToken | None = None) -> RenderResult:
    if not timeline.ranges:
        raise ShortForgeError("Timeline has no source ranges.")
    work_dir.mkdir(parents=True, exist_ok=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    encoders = [ff.choose_video_encoder(options.codec, options.encoder, options.profile, options.cq,
                                        options.bitrate_kbps)]
    if encoders[0].hardware:
        encoders.append(ff.choose_video_encoder(options.codec, "cpu", options.profile, options.cq, options.bitrate_kbps))
    last_error: Exception | None = None
    for enc in encoders:
        for hw_decode in ([True, False] if options.hwaccel_decode else [False]):
            try:
                return _render_once(timeline, out_path, work_dir, options, enc, hw_decode, ass_path, fonts_dir,
                                    progress, cancel)
            except JobCancelled:
                raise
            except FFmpegError as exc:
                last_error = exc
                log.warning("render attempt failed (encoder=%s hw_decode=%s): %s | %s", enc.name, hw_decode,
                            exc.message, (exc.detail or "")[-400:])
    assert last_error is not None
    raise last_error


def _render_once(tl: EditTimeline, out_path: Path, work_dir: Path, options: RenderOptions, enc: ff.EncoderChoice,
                 hw_decode: bool, ass_path: Path | None, fonts_dir: Path | None, progress: ProgressFn | None,
                 cancel: CancelToken | None) -> RenderResult:
    t_start = time.monotonic()
    fps = tl.fps
    total = tl.duration
    n_frames = max(1, round(total * fps))
    src_start, src_end = tl.ranges[0].start, tl.ranges[-1].end

    # Decode at source resolution (capped at 2160p) so crops keep maximum detail.
    sw, sh = tl.source_width, tl.source_height
    if sh > 2160:
        sw, sh = int(round(sw * 2160 / sh / 2) * 2), 2160
    source = _FrameSource(tl.source_path, src_start, src_end, fps, sw, sh, hw_decode)

    # ---- audio graph (+ loudness measurement pass)
    seg_graph, voice = ag.segments_filter(tl, "1:a", base=src_start)
    proc_chain = ag.processing_chain(tl.audio, total)
    graph = f"{seg_graph};[{voice}]{proc_chain}[voice]"
    out_label = "voice"
    extra_inputs: list[str] = []
    if tl.music and Path(tl.music.path).exists():
        mg, out_label = ag.music_filter(tl.music, total, "voice", "2:a")
        graph += ";" + mg
        extra_inputs.append(tl.music.path)
    if progress:
        progress(0.01, "Measuring loudness")
    measured = ag.measure_loudness(tl.source_path, src_start, graph.replace("[1:a]", "[0:a]").replace("[2:a]", "[1:a]"),
                                   out_label, tl.audio, total, extra_inputs)
    if measured and not all(math.isfinite(float(measured.get(k, "nan"))) for k in ("input_i", "input_tp")):
        measured = None  # silent programme: loudnorm cannot be two-pass measured
    audio_graph = f"{graph};[{out_label}]{ag.loudnorm_filter(tl.audio, measured)},aresample=48000[aout]"

    # ---- video filter chain in the encoder
    vchain = enhancement_filters(tl)
    if ass_path is not None and ass_path.exists():
        local_ass = work_dir / "captions.ass"
        if ass_path.resolve() != local_ass.resolve():
            shutil.copyfile(ass_path, local_ass)
        fd = f":fontsdir='{ff.escape_filter_path(fonts_dir)}'" if fonts_dir else ""
        vchain.append(f"ass=captions.ass{fd}")
    vchain.append("format=yuv420p")
    filter_complex = f"[0:v]{','.join(vchain)}[vout];{audio_graph}"

    tmp_out = work_dir / f"{out_path.stem}.part.mp4"
    cmd = [ff.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{tl.width}x{tl.height}", "-r", f"{fps}", "-i", "pipe:0",
           "-ss", f"{src_start:.4f}", "-i", tl.source_path]
    for extra in extra_inputs:
        cmd += ["-stream_loop", "-1", "-i", extra]
    cmd += ["-filter_complex", filter_complex, "-map", "[vout]", "-map", "[aout]", *enc.args,
            "-r", f"{fps}", "-c:a", "aac", "-b:a", f"{options.audio_bitrate_kbps}k", "-ar", "48000",
            "-t", f"{total:.4f}", "-movflags", "+faststart", "-colorspace", "bt709", "-color_primaries", "bt709",
            "-color_trc", "bt709", str(tmp_out)]
    log.info("render %s: %d frames @ %.2f fps, encoder=%s, hw_decode=%s", out_path.name, n_frames, fps, enc.name,
             hw_decode)
    encoder = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(work_dir),
                               creationflags=ff.CREATE_NO_WINDOW)
    stderr_buf: list[bytes] = []

    def drain() -> None:
        assert encoder.stderr is not None
        for line in iter(encoder.stderr.readline, b""):
            stderr_buf.append(line)

    t_err = threading.Thread(target=drain, daemon=True)
    t_err.start()

    compositor = Compositor(tl, quality=options.profile)
    q: queue.Queue[np.ndarray | None] = queue.Queue(maxsize=8)
    writer_error: list[BaseException] = []

    def writer() -> None:
        assert encoder.stdin is not None
        try:
            while True:
                item = q.get()
                if item is None:
                    break
                encoder.stdin.write(item.tobytes())
        except BaseException as exc:  # broken pipe when ffmpeg dies
            writer_error.append(exc)
        finally:
            with contextlib.suppress(OSError):
                encoder.stdin.close()

    t_w = threading.Thread(target=writer, daemon=True)
    t_w.start()

    broll_sources: dict[int, _BrollSource] = {}
    crop_path: list[tuple[float, float, float, float, float]] = []
    decode_err = ""
    try:
        for k in range(n_frames):
            if cancel and k % 15 == 0:
                cancel.raise_if_cancelled()
            if writer_error:
                break
            t_out = k / fps
            t_src = tl.output_to_source(t_out)
            idx = round((t_src - src_start) * fps)
            frame = source.get(idx)
            if frame is None:
                raise FFmpegError("Could not decode source frames.", stderr=source.close())
            composed = None
            for bi, b in enumerate(tl.broll):
                if b.start <= t_out < b.end and Path(b.path).exists():
                    if bi not in broll_sources:
                        broll_sources[bi] = _BrollSource(b.path, b.source_start, fps, tl.width, tl.height)
                    bframe = broll_sources[bi].next()
                    if bframe is not None:
                        if b.mode == "full":
                            composed = bframe.copy()
                            # short crossfade in/out of B-roll
                            edge = min(t_out - b.start, b.end - t_out)
                            if edge < 0.15:
                                base = compositor.compose(frame, t_out)
                                a = edge / 0.15
                                composed = cv2.addWeighted(bframe, a, base, 1 - a, 0)
                        else:
                            composed = compositor.compose(frame, t_out)
                            pw, ph = tl.width // 3, tl.height // 3
                            small = cv2.resize(bframe, (pw, ph), interpolation=cv2.INTER_AREA)
                            composed[60 : 60 + ph, tl.width - pw - 40 : tl.width - 40] = small
                    break
            if composed is None:
                composed = compositor.compose(frame, t_out)
            if compositor.last_rect and k % max(1, int(fps / 10)) == 0:
                crop_path.append((round(t_out, 3), *(round(v, 1) for v in compositor.last_rect)))
            q.put(np.ascontiguousarray(composed))
            if progress and k % 10 == 0:
                progress(0.03 + 0.95 * k / n_frames, f"Rendering {k}/{n_frames} frames")
    except JobCancelled:
        encoder.kill()
        raise
    finally:
        q.put(None)
        t_w.join(timeout=120)
        decode_err = source.close()
        for bsrc in broll_sources.values():
            bsrc.close()
    ret = encoder.wait(timeout=600)
    t_err.join(timeout=5)
    stderr = b"".join(stderr_buf).decode("utf-8", errors="replace")
    if ret != 0 or writer_error:
        tmp_out.unlink(missing_ok=True)
        raise FFmpegError(f"Encoder failed (exit {ret})", stderr=stderr + "\n" + decode_err, cmd=cmd)
    tmp_out.replace(out_path)
    info = ff.probe(out_path)
    elapsed = time.monotonic() - t_start
    log.info("rendered %s in %.1fs (%.1f fps) with %s", out_path.name, elapsed, n_frames / max(0.01, elapsed), enc.name)
    if progress:
        progress(1.0, "Render complete")
    return RenderResult(out_path, enc.name, round(elapsed, 2), n_frames, out_path.stat().st_size, info.duration,
                        " ".join(cmd), crop_path)
