"""FFmpeg audio graph: segment assembly (with click-free joins), processing and loudness mastering."""

from __future__ import annotations

import json
import re

from shortforge.engines.editing.timeline import AudioSpec, EditTimeline, MusicTrack
from shortforge.engines.media import ffmpeg as ff

JOIN_FADE = 0.012  # seconds of fade at each jump-cut join (prevents clicks)


def segments_filter(timeline: EditTimeline, input_label: str = "1:a", base: float = 0.0) -> tuple[str, str]:
    """Trim each kept range from an input already seeked to ``base`` and concatenate them."""
    parts = []
    labels = []
    n = len(timeline.ranges)
    for i, r in enumerate(timeline.ranges):
        a, b = r.start - base, r.end - base
        d = b - a
        chain = f"[{input_label}]atrim=start={a:.4f}:end={b:.4f},asetpts=PTS-STARTPTS"
        if n > 1:
            if i > 0:
                chain += f",afade=t=in:st=0:d={JOIN_FADE}"
            if i < n - 1:
                chain += f",afade=t=out:st={max(0.0, d - JOIN_FADE):.4f}:d={JOIN_FADE}"
        chain += f"[s{i}]"
        parts.append(chain)
        labels.append(f"[s{i}]")
    if n == 1:
        return parts[0].replace("[s0]", "[voice_raw]"), "voice_raw"
    parts.append("".join(labels) + f"concat=n={n}:v=0:a=1[voice_raw]")
    return ";".join(parts), "voice_raw"


def processing_chain(spec: AudioSpec, duration: float) -> str:
    """Speech-friendly processing. Everything optional is off by default to keep speech natural."""
    chain = ["aresample=48000", "aformat=channel_layouts=stereo"]
    if spec.gain_db:
        chain.append(f"volume={spec.gain_db:.2f}dB")
    chain.append("highpass=f=70")  # remove rumble only; never touches voice
    if spec.denoise:
        chain.append("afftdn=nr=10:nf=-40:tn=1")
    if spec.eq:
        chain.append("equalizer=f=180:t=q:w=1.0:g=-1.5,equalizer=f=3500:t=q:w=1.2:g=2")
    if spec.compressor:
        chain.append("acompressor=threshold=-20dB:ratio=2.5:attack=15:release=180:makeup=2")
    fade = spec.fade_ms / 1000.0
    if fade > 0:
        chain.append(f"afade=t=in:st=0:d={fade:.3f}")
        chain.append(f"afade=t=out:st={max(0.0, duration - fade * 3):.3f}:d={fade * 3:.3f}")
    return ",".join(chain)


def music_filter(music: MusicTrack, duration: float, voice_label: str, music_input: str) -> tuple[str, str]:
    """Loop/trim music, fade it, and duck it under the voice with a sidechain compressor."""
    graph = (
        f"[{music_input}]atrim=start={music.offset:.3f}:duration={duration:.3f},asetpts=PTS-STARTPTS,"
        f"aresample=48000,aformat=channel_layouts=stereo,volume={music.volume_db:.1f}dB,"
        f"afade=t=in:st=0:d={music.fade_in:.2f},afade=t=out:st={max(0.0, duration - music.fade_out):.2f}:d={music.fade_out:.2f}[mus];"
        f"[{voice_label}]asplit=2[vmain][vsc];"
        f"[mus][vsc]sidechaincompress=threshold=0.03:ratio={max(2.0, -music.duck_db / 2):.1f}:attack=20:release=400[ducked];"
        f"[vmain][ducked]amix=inputs=2:duration=first:normalize=0[mixed]"
    )
    return graph, "mixed"


def measure_loudness(source: str, seek: float, pre_graph: str, out_label: str, target: AudioSpec,
                     duration: float, extra_inputs: list[str] | None = None) -> dict | None:
    """First loudnorm pass: measure the assembled programme."""
    graph = f"{pre_graph};[{out_label}]loudnorm=I={target.target_lufs}:TP={target.true_peak}:LRA=11:print_format=json[out]"
    args = ["-ss", f"{seek:.4f}", "-i", source]
    for extra in extra_inputs or []:
        args += ["-stream_loop", "-1", "-i", extra]
    args += ["-filter_complex", graph, "-map", "[out]", "-vn", "-sn", "-t", f"{duration:.3f}", "-f", "null", "-"]
    try:
        stderr = ff.run_ffmpeg_capture(args, timeout=600)
    except ff.FFmpegError:
        return None
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", stderr, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def loudnorm_filter(target: AudioSpec, measured: dict | None) -> str:
    base = f"loudnorm=I={target.target_lufs}:TP={target.true_peak}:LRA=11"
    if not measured:
        return base
    try:
        return (f"{base}:measured_I={float(measured['input_i']):.2f}:measured_TP={float(measured['input_tp']):.2f}:"
                f"measured_LRA={float(measured['input_lra']):.2f}:measured_thresh={float(measured['input_thresh']):.2f}:"
                f"offset={float(measured['target_offset']):.2f}:linear=true")
    except (KeyError, ValueError):
        return base
