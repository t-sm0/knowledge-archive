import asyncio
from pathlib import Path


class VideoProcessingError(RuntimeError):
    pass


async def extract_frames(video_path: Path, output_dir: Path, max_frames: int = 20) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    pattern = output_dir / "frame-%03d.jpg"
    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video_path),
        "-vf",
        "fps=1/3",
        "-frames:v",
        str(max_frames),
        str(pattern),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode != 0:
        raise VideoProcessingError(stderr.decode("utf-8", errors="replace")[:1000])
    return sorted(output_dir.glob("frame-*.jpg"))[:max_frames]

