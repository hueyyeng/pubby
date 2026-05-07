"""Shared utility functions used across the application."""

from __future__ import annotations

import hashlib
import os
from fractions import Fraction


VIDEO_EXTENSIONS = {'.mp4', '.mov', '.insv', '.mxf', '.mkv'}


def format_size(num_bytes: int) -> str:
    """Format bytes into a human-readable string (e.g. '2.5 GB')."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num_bytes < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} PB"


def format_duration(seconds: float) -> str:
    """Format seconds into a human-readable duration string (e.g. '5h 23m')."""
    if seconds <= 0:
        return "N/A"

    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)

    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0 or not parts:
        parts.append(f"{minutes}m")

    return " ".join(parts)


def format_speed(bytes_per_sec: float) -> str:
    """Format bytes per second into MB/s."""
    if bytes_per_sec <= 0:
        return "N/A"

    mbps = bytes_per_sec / (1024 * 1024)
    return f"{mbps:.2f} MB/sec"


def calculate_md5(file_path: str, chunk_size: int = 8192) -> str:
    """Calculate MD5 hash of a file in chunks to prevent memory overflow."""
    md5 = hashlib.md5()
    try:
        with open(file_path, "rb") as f:
            while True:
                data = f.read(chunk_size)
                if not data:
                    break
                md5.update(data)
        return md5.hexdigest()
    except OSError:
        return "Hash Error"


def get_video_metadata(file_path: str) -> dict:
    """Extract video metadata (duration, resolution, fps, codec) using ffmpeg-python."""
    ext = os.path.splitext(file_path)[1].lower()

    if ext not in VIDEO_EXTENSIONS:
        return {
            'duration': 0,
            'resolution': 'N/A',
            'fps': 0,
            'format': ext.upper(),
        }

    try:
        import ffmpeg
        probe = ffmpeg.probe(file_path, timeout=5)

        video_stream = next(
            (s for s in probe['streams'] if s['codec_type'] == 'video'), None
        )

        if not video_stream:
            return {
                'duration': 0,
                'resolution': 'N/A',
                'fps': 0,
                'format': ext.upper(),
            }

        width = video_stream.get('width', 0)
        height = video_stream.get('height', 0)

        r_frame_rate = video_stream.get('r_frame_rate', '0')
        if '/' in str(r_frame_rate):
            try:
                fps = float(Fraction(str(r_frame_rate)))
            except (ValueError, ZeroDivisionError):
                fps = 0.0
        else:
            try:
                fps = float(r_frame_rate)
            except ValueError:
                fps = 0.0

        duration_secs = float(probe['format'].get('duration', 0))
        codec = video_stream.get('codec_name', '?')

        return {
            'duration': duration_secs,
            'resolution': f"{width}x{height}",
            'fps': fps,
            'format': ext.upper(),
            'codec': codec,
        }
    except Exception:
        return {
            'duration': 0,
            'resolution': 'N/A',
            'fps': 0,
            'format': ext.upper(),
        }
