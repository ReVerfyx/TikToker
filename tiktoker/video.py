import subprocess
from pathlib import Path


def split_vertical(source: Path, cfg: dict):
    part_seconds = int(cfg.get("part_seconds", 300))
    width = int(cfg.get("width", 1080))
    height = int(cfg.get("height", 1920))
    crf = str(cfg.get("crf", 23))
    preset = str(cfg.get("preset", "veryfast"))
    out_dir = source.parent / "parts"
    out_dir.mkdir(exist_ok=True)
    pattern = str(out_dir / "part_%03d.mp4")

    filt = (
        f"[0:v]split=2[bg][fg];"
        f"[bg]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},boxblur=20:10[bgv];"
        f"[fg]scale={width}:{height}:force_original_aspect_ratio=decrease[fgv];"
        f"[bgv][fgv]overlay=(W-w)/2:(H-h)/2,format=yuv420p[v]"
    )
    cmd = [
        "ffmpeg", "-y", "-i", str(source),
        "-filter_complex", filt,
        "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", preset, "-crf", crf,
        "-c:a", "aac", "-b:a", "128k",
        "-force_key_frames", f"expr:gte(t,n_forced*{part_seconds})",
        "-f", "segment", "-segment_time", str(part_seconds),
        "-reset_timestamps", "1", pattern,
    ]
    subprocess.run(cmd, check=True)
    return sorted(out_dir.glob("part_*.mp4"))
