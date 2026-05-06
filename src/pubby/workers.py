from __future__ import annotations

import time

import ffmpeg
import os
import json
import subprocess
import tempfile
import random
import shutil  # Import shutil for removing directories
import hashlib
from PySide6.QtCore import *  # Wildcard import as requested

# Only probe these extensions to save time
VIDEO_EXTENSIONS = {'.mp4', '.mov', '.insv', '.mxf', '.mkv'}


def _calculate_md5(file_path, chunk_size=8192):
    """Calculates MD5 hash of a file in chunks to prevent memory overflow on large files."""
    md5 = hashlib.md5()
    try:
        with open(file_path, "rb") as f:
            while True:
                data = f.read(chunk_size)
                if not data:
                    break
                md5.update(data)
        return md5.hexdigest()
    except Exception as e:
        print(f"DEBUG HASHING: Could not hash file {file_path}: {e}")
        return "Hash Error"


def _format_bytes(size):
    """Formats bytes into human-readable string (GB, MB, etc)."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} PB"


def _get_video_metadata(file_path):
    """Extracts video metadata using ffmpeg-python."""

    # 1. Check extension first to avoid probing non-video files (like .jpg, .png)
    ext = os.path.splitext(file_path)[1].lower()
    VIDEO_EXTENSIONS = {'.mp4', '.mov', '.insv', '.mxf', '.mkv'}

    if ext not in VIDEO_EXTENSIONS:
        return {
            'duration': 0,
            'resolution': 'N/A',
            'fps': 0,
            'format': ext.upper()  # e.g., .JPG, .PNG
        }

    try:
        # 2. Add a timeout (5 seconds) to prevent ffmpeg from hanging on corrupt/weird files
        probe = ffmpeg.probe(file_path, timeout=5)

        # Find the video stream
        video_stream = next((stream for stream in probe['streams'] if stream['codec_type'] == 'video'), None)

        if not video_stream:
            return {
                'duration': 0,
                'resolution': 'N/A',
                'fps': 0,
                'format': ext.upper()
            }

        # Extract resolution and FPS
        width = video_stream.get('width', 0)
        height = video_stream.get('height', 0)

        # Parse FPS (it can be a string like "25/1" or an int)
        r_frame_rate = video_stream.get('r_frame_rate', '0')
        if '/' in str(r_frame_rate):
            try:
                fps = eval(str(r_frame_rate))  # Safely evaluate fraction string like "24000/1001"
            except:
                fps = 0
        else:
            fps = float(r_frame_rate)

        # Extract Duration (from format section, usually more accurate for total file duration)
        duration_secs = float(probe['format'].get('duration', 0))

        return {
            'duration': duration_secs,
            'resolution': f"{width}x{height}",
            'fps': fps,
            'format': ext.upper()
        }
    except Exception as e:
        print(f"DEBUG METADATA: Could not extract metadata from {file_path}: {e}")
        return {'duration': 0, 'resolution': 'N/A', 'fps': 0, 'format': ext.upper()}


def _format_duration(seconds):
    """Formats seconds into HH:MM:SS or MM:SS format."""
    if not seconds: return "0s"
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)

    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d} h"
    else:
        return f"{minutes}:{secs:02d} min"


# ---------------------------------------------------------------------------
# Real Job Signals & Class
# ---------------------------------------------------------------------------

class FileCopyJobSignals(QObject):
    started = Signal(str)
    updated = Signal(str, dict)
    completed = Signal(str, dict)  # Changed: now takes (path, metadata_dict)
    error = Signal(str, str)


class FileCopyJob(QRunnable):
    def __init__(self, source_path: str, dest_path: str, job_id: str):
        super().__init__()
        self.setAutoDelete(True)
        self.source_path = source_path
        self.dest_path = dest_path
        self.job_id = job_id
        self.signals = FileCopyJobSignals()

    def run(self):
        print(f"DEBUG FILE COPY: Starting copy job for {self.source_path}")
        self.signals.started.emit(self.source_path)
        try:
            cmd = [
                "rclone", "copyto",
                self.source_path,
                self.dest_path,
                "--json", "--log-level=INFO", "--stats=1s"
            ]

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1
            )

            try:
                total_size = os.path.getsize(self.source_path)
                print(f"DEBUG FILE COPY: Total size for {self.source_path}: {total_size} bytes")

                # NEW: Record start time and calculate hash
                start_time = time.time()
                file_hash = _calculate_md5(self.source_path)
                video_meta = _get_video_metadata(self.source_path)

            except OSError as e:
                self.signals.error.emit(self.source_path, f"Could not get file size: {e}")
                return

            current_transferred = 0
            last_emit_time = time.time()
            throttle_interval = 0.15

            while True:
                line = process.stdout.readline()
                if not line:
                    break

                try:
                    data = json.loads(line.strip())
                    transferred_bytes = data.get('bytesTransferred', 0)
                    speed_bps = data.get('speed', 0)

                    if transferred_bytes != current_transferred:
                        current_transferred = current_transferred

                        if total_size > 0:
                            progress_ratio = min(current_transferred / total_size, 1.0)
                        else:
                            progress_ratio = 1.0 if current_transferred > 0 else 0

                        now = time.time()
                        if now - last_emit_time >= throttle_interval:
                            self.signals.updated.emit(
                                self.source_path,
                                {
                                    'progress': progress_ratio,
                                    'speed': speed_bps
                                }
                            )
                            last_emit_time = now

                except json.JSONDecodeError:
                    continue
                except Exception as e:
                    print(f"DEBUG FILE COPY: Error parsing JSON line: {e}")
                    pass

            process.wait()

            if process.returncode == 0:
                self.signals.updated.emit(
                    self.source_path,
                    {
                        'progress': 1.0,
                        'speed': 0,
                        'status': 'Complete'
                    }
                )

                # NEW: Emit metadata along with path
                self.signals.completed.emit(self.source_path, {
                    'size': total_size,
                    'start_time': start_time,
                    'hash': file_hash,
                    'dest_path': self.dest_path,
                    'video_meta': video_meta  # <--- ADDED
                })

            else:
                stderr_output = process.stderr.read().strip()
                self.signals.error.emit(
                    self.source_path,
                    f"rclone exited with code {process.returncode}: {stderr_output}"
                )

        except FileNotFoundError:
            self.signals.error.emit(self.source_path, "Error: 'rclone' command not found in PATH.")
        except Exception as e:
            self.signals.error.emit(self.source_path, f"Unexpected error: {str(e)}")


# ---------------------------------------------------------------------------
# Fake Job Signals & Class (Dry Run)
# ---------------------------------------------------------------------------

class FakeCopyJobSignals(QObject):
    started = Signal(str)
    updated = Signal(str, dict)
    completed = Signal(str, dict)  # Changed: now takes (path, metadata_dict)
    error = Signal(str, str)


def _generate_short_id(path: str) -> str:
    """Generates a short unique ID based on the file path."""
    # Use MD5 hash of the path and take the first 8 characters
    hash_obj = hashlib.md5(path.encode('utf-8'))
    return hash_obj.hexdigest()[:8]


class FakeCopyJob(QRunnable):
    """Simulates a file copy job for dry run purposes."""

    def __init__(self, source_path: str, dest_path: str, job_id: str, gbps_speed: float):
        super().__init__()
        self.setAutoDelete(True)
        self.source_path = source_path
        self.dest_path = dest_path
        self.job_id = job_id
        self.gbps_speed = gbps_speed  # Network speed in Gbps
        self.signals = FakeCopyJobSignals()
        self.temp_dir = None  # Track temp directory for cleanup

    def run(self):
        print(f"DEBUG FAKE COPY: Starting fake job for {self.source_path}")
        try:
            self.signals.started.emit(self.source_path)

            # Create a temporary directory for this fake copy operation
            temp_dir = None
            try:
                # Generate a short, unique ID based on the source path
                short_id = _generate_short_id(self.source_path)
                prefix = f"pubby_{short_id}_"
                print(f"DEBUG FAKE COPY: Creating temp dir with prefix: {prefix}")

                temp_dir = tempfile.mkdtemp(prefix=prefix)
                print(f"DEBUG FAKE COPY: Created temp dir: {temp_dir}")
                self.temp_dir = temp_dir  # Store for cleanup

                # Construct the actual destination path within temp dir
                rel_path = os.path.relpath(self.dest_path, os.path.dirname(self.dest_path))
                print(f"DEBUG FAKE COPY: Relative path from dest to parent: {rel_path}")

                actual_dest_path = os.path.join(temp_dir, rel_path)
                print(f"DEBUG FAKE COPY: Actual destination path: {actual_dest_path}")

                # Ensure directory exists for the fake file
                dir_to_create = os.path.dirname(actual_dest_path)
                print(f"DEBUG FAKE COPY: Creating directory: {dir_to_create}")
                os.makedirs(dir_to_create, exist_ok=True)
                print(f"DEBUG FAKE COPY: Directory created/exists")

            except Exception as e:
                error_msg = f"Could not create temp dir: {str(e)} (errno={e.errno if hasattr(e, 'errno') else 'N/A'}, filename={e.filename if hasattr(e, 'filename') else 'N/A'})"
                print(f"DEBUG FAKE COPY ERROR: {error_msg}")
                self.signals.error.emit(self.source_path, error_msg)
                return

            try:
                total_size = os.path.getsize(self.source_path)
                print(f"DEBUG FAKE COPY: Total size for {self.source_path}: {total_size} bytes")

                # NEW: Record start time and calculate hash
                start_time = time.time()
                file_hash = _calculate_md5(self.source_path)
                video_meta = _get_video_metadata(self.source_path)
            except OSError as e:
                self.signals.error.emit(self.source_path, f"Could not get file size: {e}")
                return

            current_transferred = 0
            last_emit_time = time.time()

            # Simulate realistic speed variation (50-90% of target)
            base_speed_mbps = self.gbps_speed * 1000 / 8  # Convert Gbps to MB/s
            min_speed = base_speed_mbps * 0.5
            max_speed = base_speed_mbps * 0.9

            print(f"DEBUG FAKE COPY: Base speed: {base_speed_mbps} Mbps, range: {min_speed}-{max_speed} Mbps")

            while current_transferred < total_size:
                # Calculate progress ratio
                if total_size > 0:
                    progress_ratio = current_transferred / total_size
                else:
                    progress_ratio = 1.0

                # Add some randomness to speed simulation
                speed_variation = random.uniform(0.8, 1.2)
                current_speed_mbps = min(max_speed, max(min_speed,
                                                        base_speed_mbps * speed_variation))

                # Calculate bytes to transfer in this iteration
                bytes_per_tick = int(current_speed_mbps * (1024 * 1024) * 0.1)  # 100ms ticks
                bytes_to_transfer = min(bytes_per_tick, total_size - current_transferred)

                if bytes_to_transfer <= 0:
                    break

                current_transferred += bytes_to_transfer

                # Emit progress update (throttled to ~10 times per second)
                now = time.time()
                if now - last_emit_time >= 0.1:
                    self.signals.updated.emit(
                        self.source_path,
                        {
                            'progress': min(current_transferred / total_size, 1.0),
                            'speed': int(current_speed_mbps * (1024 * 1024))  # Convert to bytes/sec
                        }
                    )
                    last_emit_time = now

                # Small sleep to simulate processing time
                time.sleep(0.05)

                if current_transferred % (1024 * 1024) < 1024 and current_transferred > 0:  # Every MB
                    print(f"DEBUG FAKE COPY: Transferred {current_transferred / (1024 * 1024):.2f} MB so far")

            # Create a dummy file in temp location to simulate successful copy
            try:
                print(f"DEBUG FAKE COPY: Creating dummy file at: {actual_dest_path}")
                with open(actual_dest_path, 'wb') as f:
                    # Write some dummy data (not the actual size, just enough to exist)
                    f.write(b'DUMMY DATA FOR DRY RUN' + b'\x00' * 1024)
                print(f"DEBUG FAKE COPY: Dummy file created successfully")
            except Exception as e:
                print(f"DEBUG FAKE COPY ERROR creating dummy file: {e}")

            # 1. Send a final "Complete" status update BEFORE emitting the completed signal.
            # This ensures the UI updates the text to "Complete" and progress to 100%.
            self.signals.updated.emit(
                self.source_path,
                {
                    'progress': 1.0,
                    'speed': 0,
                    'status': 'Complete'  # <--- ADD THIS
                }
            )

            # 2. Emit the completion signal (so other listeners like JobManager know it's done)
            self.signals.completed.emit(self.source_path, {
                'size': total_size,
                'start_time': start_time,
                'hash': file_hash,
                'dest_path': self.dest_path,
                'video_meta': video_meta  # <--- ADDED
            })

            print(f"DEBUG FAKE COPY: Completed fake job for {self.source_path}")

        except Exception as e:
            error_msg = f"Unexpected error: {str(e)} (errno={e.errno if hasattr(e, 'errno') else 'N/A'}, filename={e.filename if hasattr(e, 'filename') else 'N/A'})"
            print(f"DEBUG FAKE COPY ERROR: {error_msg}")
            self.signals.error.emit(self.source_path, error_msg)
        finally:
            # Clean up temporary directory after job completion (success or failure)
            if self.temp_dir and os.path.exists(self.temp_dir):
                try:
                    print(f"DEBUG FAKE COPY: Cleaning up temp dir: {self.temp_dir}")
                    shutil.rmtree(self.temp_dir, ignore_errors=True)
                    print(f"DEBUG FAKE COPY: Temp dir cleaned up successfully")
                except Exception as e:
                    print(f"DEBUG FAKE COPY ERROR cleaning up temp dir: {e}")


# ---------------------------------------------------------------------------
# JobManager
# ---------------------------------------------------------------------------

class JobManager(QObject):
    network_speed_changed = Signal(float)

    file_updated = Signal(str, dict)
    job_started = Signal(str)
    job_progress = Signal(str, float, int)
    job_finished = Signal(str, str)
    job_error = Signal(str, str)
    folder_progress_updated = Signal(str, float)  # folder_path, overall_progress_0-1

    def __init__(self, parent=None):
        super().__init__(parent)
        self.thread_pool = QThreadPool.globalInstance()
        self.active_jobs: dict[str, FileCopyJob | FakeCopyJob] = {}
        self.dry_run = False
        self.network_speed_gbps = 10.0
        self.completed_files: dict[str, dict] = {}  # source_path -> status

        self._folder_sizes: dict[str, int] = {}  # folder_path -> total bytes in folder
        self._folder_completed_bytes: dict[str, int] = {}  # folder_path -> completed bytes

    def start_job(self, source_path: str, dest_path: str):
        if source_path in self.active_jobs:
            return

        # Get the folder path for this file
        folder_path = os.path.dirname(source_path)

        # Initialize folder tracking if not already present
        if folder_path not in self._folder_sizes:
            try:
                self._folder_sizes[folder_path] = sum(
                    os.path.getsize(os.path.join(folder_path, f))
                    for f in os.listdir(folder_path)
                    if os.path.isfile(os.path.join(folder_path, f))
                )
                self._folder_completed_bytes[folder_path] = 0
            except Exception as e:
                print(f"Error calculating folder size for {folder_path}: {e}")
                return

        job_id = f"job_{source_path}" if not self.dry_run else f"fake_job_{source_path}"

        if self.dry_run:
            job = FakeCopyJob(source_path, dest_path, job_id, self.network_speed_gbps)
            # Connect fake job signals using the .signals object
            job.signals.started.connect(self._on_started)
            job.signals.updated.connect(self._on_updated)
            job.signals.completed.connect(self._on_completed)
            job.signals.error.connect(self._on_error)
        else:
            job = FileCopyJob(source_path, dest_path, job_id)
            # Connect real job signals using the .signals object
            job.signals.started.connect(self._on_started)
            job.signals.updated.connect(self._on_updated)
            job.signals.completed.connect(self._on_completed)
            job.signals.error.connect(self._on_error)

        self.active_jobs[source_path] = job
        self.thread_pool.start(job)

    def stop_job(self, source_path: str):
        if source_path in self.active_jobs:
            del self.active_jobs[source_path]

    @Slot(str)
    def _on_started(self, source_path: str):
        self.job_started.emit(source_path)
        self.file_updated.emit(source_path, {'status': 'Copying', 'progress': 0.0, 'speed': 0})

    @Slot(str, dict)
    def _on_updated(self, source_path: str, data: dict):
        progress = data.get('progress', 0.0)
        speed = data.get('speed', 0)

        self.job_progress.emit(source_path, progress, speed)
        self.file_updated.emit(source_path, {'status': 'Copying', 'progress': progress, 'speed': speed})

    @Slot(str, dict)  # Updated signature to accept metadata dict
    def _on_completed(self, source_path: str, metadata: dict):

        if source_path in self.active_jobs:
            del self.active_jobs[source_path]

        metadata['source_folder'] = os.path.dirname(source_path)
        # Track completed file with rich data instead of just string status
        self.completed_files[source_path] = metadata

        # Update folder-level progress (Existing logic)
        folder_path = os.path.dirname(source_path)
        if folder_path in self._folder_sizes:
            try:
                file_size = metadata.get('size', 0)  # Use size from metadata

                if folder_path not in self._folder_completed_bytes:
                    self._folder_completed_bytes[folder_path] = 0

                self._folder_completed_bytes[folder_path] += file_size

                total_size = self._folder_sizes[folder_path]
                completed_bytes = self._folder_completed_bytes[folder_path]
                if total_size > 0:
                    overall_progress = min(completed_bytes / total_size, 1.0)
                else:
                    overall_progress = 1.0

                print(f"DEBUG JobManager: Emitting folder_progress_updated for {folder_path}: {overall_progress}")
                self.folder_progress_updated.emit(folder_path, overall_progress)

            except Exception as e:
                print(f"ERROR JobManager: Error updating folder progress for {folder_path}: {e}")

        print(f"DEBUG JobManager: Emitting job_finished for {source_path}")
        self.job_finished.emit(source_path, "success")

        print(f"DEBUG JobManager: Emitting file_updated for {source_path} with Complete status")
        self.file_updated.emit(source_path, {'status': 'Complete', 'progress': 1.0, 'speed': 0})

    @Slot(str, str)
    def _on_error(self, source_path: str, error_msg: str):
        if source_path in self.active_jobs:
            del self.active_jobs[source_path]

        # Track failed file
        self.completed_files[source_path] = f"error: {error_msg}"

        # Still emit folder progress update even on error (partial completion)
        folder_path = os.path.dirname(source_path)
        if folder_path in self._folder_sizes:
            try:
                # Initialize folder completed bytes if not already present
                if folder_path not in self._folder_completed_bytes:
                    self._folder_completed_bytes[folder_path] = 0

                # For errors, we don't add any progress since the file didn't complete
                # The partial progress is already tracked in individual file progress updates

                current_completed = self._folder_completed_bytes[folder_path]
                total_size = self._folder_sizes[folder_path]
                if total_size > 0:
                    overall_progress = min(current_completed / total_size, 1.0)
                else:
                    overall_progress = 1.0

                self.folder_progress_updated.emit(folder_path, overall_progress)

            except Exception as e:
                print(f"Error updating folder progress for {folder_path}: {e}")

        self.job_error.emit(source_path, error_msg)
        display_error = f"Error: {error_msg[:50]}" if len(error_msg) > 50 else f"Error: {error_msg}"
        self.file_updated.emit(source_path, {'status': display_error, 'progress': -1.0, 'speed': 0})

    def set_dry_run(self, enabled: bool):
        """Enable or disable dry run mode."""
        self.dry_run = enabled

    def set_network_speed(self, gbps: float):
        """Set the simulated network speed for dry run mode."""
        self.network_speed_gbps = gbps
        self.network_speed_changed.emit(gbps)

    def get_completed_files(self) -> dict[str, dict]:
        """Returns a dictionary of completed files with their status."""
        return self.completed_files.copy()

    def export_csv(self, file_path: str):
        """Exports completed files data to a CSV report."""
        import csv
        from datetime import datetime

        if not self.completed_files:
            print("No completed files to export.")
            return

        try:
            with open(file_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)

                # Updated Header Row to include Source Folder
                writer.writerow([
                    "Name",
                    "Source Folder",  # <--- ADDED COLUMN
                    "File Type",
                    "File Size",
                    "Creation Date",
                    "Hash Values",
                    "Volumes",
                    "Verification State"
                ])

                for source_path, data in self.completed_files.items():
                    size_bytes = data.get('size', 0)
                    start_time = data.get('start_time', 0)
                    file_hash = data.get('hash', 'N/A')
                    dest_volume = data.get('dest_path', 'Unknown Volume')

                    # --- NEW CODE START ---
                    source_folder = data.get('source_folder', 'Unknown Folder')
                    # --- NEW CODE END ---

                    try:
                        ctime = os.path.getctime(source_path)
                        creation_date_str = datetime.fromtimestamp(ctime).strftime("%Y-%m-%d %H:%M")
                    except Exception:
                        creation_date_str = "N/A"

                    try:
                        start_date_str = datetime.fromtimestamp(start_time).strftime("%Y-%m-%d %H:%M")
                    except Exception:
                        start_date_str = "N/A"

                    filename = os.path.basename(source_path)
                    _, ext = os.path.splitext(filename)

                    verification_state = "verified" if file_hash != "Hash Error" else "error"

                    # Updated Row to include Source Folder
                    writer.writerow([
                        filename,
                        source_folder,  # <--- ADDED COLUMN DATA
                        ext.upper(),
                        _format_bytes(size_bytes),
                        creation_date_str,
                        file_hash,
                        dest_volume,
                        verification_state
                    ])

            print(f"DEBUG JobManager: CSV exported successfully to {file_path}")
        except Exception as e:
            print(f"ERROR JobManager: Failed to export CSV: {e}")

    def export_html(self, file_path: str, progress_callback=None):
        """Generates an HTML report using Jinja2."""
        from jinja2 import Template
        from datetime import datetime

        if not self.completed_files:
            print("No completed files to export.")
            return

        try:
            # Define video extensions to distinguish clips from other files
            VIDEO_EXTENSIONS = {'.mp4', '.mov', '.insv', '.mxf', '.mkv'}

            # --- PROGRESS UPDATE 1 ---
            if progress_callback: progress_callback(10, "Aggregating metadata...")

            folder_data = {}

            for source_path, data in self.completed_files.items():
                folder_name = os.path.basename(data.get('source_folder', 'Unknown'))

                if folder_name not in folder_data:
                    folder_data[folder_name] = {
                        'files': [],
                        'total_clips': 0,  # Only video files
                        'total_files': 0,  # All files
                        'total_duration_secs': 0,
                        'total_size_bytes': 0
                    }

                video_meta = data.get('video_meta', {})
                size = data.get('size', 0)
                duration = video_meta.get('duration', 0)

                # Get the file extension to determine if it's a clip
                ext = os.path.splitext(source_path)[1].lower()

                # Increment total files count for EVERY file
                folder_data[folder_name]['total_files'] += 1

                # Only increment clips count if it matches our video extensions
                if ext in VIDEO_EXTENSIONS:
                    folder_data[folder_name]['total_clips'] += 1

                folder_data[folder_name]['files'].append({
                    'name': os.path.basename(source_path),
                    'size': _format_bytes(size),
                    'duration': _format_duration(duration),
                    'resolution': video_meta.get('resolution', 'N/A'),
                    'fps': video_meta.get('fps', 0),
                    'format': video_meta.get('format', 'UNKNOWN'),
                    'hash': data.get('hash', 'N/A')
                })

                folder_data[folder_name]['total_duration_secs'] += duration
                folder_data[folder_name]['total_size_bytes'] += size

            # --- PROGRESS UPDATE 2 ---
            if progress_callback: progress_callback(40, "Sorting folders...")

            sorted_folders = dict(sorted(folder_data.items()))

            # --- PROGRESS UPDATE 3 ---
            if progress_callback: progress_callback(60, "Rendering HTML template...")

            # Updated Template with new columns and renamed headers
            template_str = """
            <!DOCTYPE html>
            <html lang="en">
            <head>
                <meta charset="UTF-8">
                <title>Offload Report</title>
                <style>
                    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; margin: 40px; color: #333; }
                    h1 { border-bottom: 2px solid #eee; padding-bottom: 10px; }
                    .report-meta { color: #666; font-size: 0.9em; margin-bottom: 30px; }
                    table { width: 100%; border-collapse: collapse; margin-bottom: 40px; background: white; box-shadow: 0 2px 5px rgba(0,0,0,0.05); }
                    th, td { padding: 12px 15px; text-align: left; border-bottom: 1px solid #ddd; }
                    th { background-color: #f8f9fa; font-weight: 600; color: #333; }
                    tr:hover { background-color: #f1f1f1; }
                    .folder-header { background-color: #e9ecef !important; font-weight: bold; }
                </style>
            </head>
            <body>
                <h1>Offload Report</h1>
                <div class="report-meta">Generated on {{ report_date }}</div>

                <table>
                    <thead>
                        <tr>
                            <th>Source Folder</th>
                            <th>Clips</th>
                            <th>Duration</th> <!-- Renamed from Total Duration -->
                            <th>Files</th>   <!-- New Column -->
                            <th>Size</th>    <!-- Renamed from Total Size -->
                        </tr>
                    </thead>
                    <tbody>
                        {% for folder_name, data in folders.items() %}
                        <tr class="folder-header">
                            <td>{{ folder_name }}</td>
                            <td>{{ data.total_clips }}</td>
                            <td>{{ _format_duration(data.total_duration_secs) }}</td>
                            <td>{{ data.total_files }}</td> <!-- New Data -->
                            <td>{{ _format_bytes(data.total_size_bytes) }}</td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>

                <h2>Detailed Clip List</h2>
                <table>
                    <thead>
                        <tr>
                            <th>Name</th>
                            <th>Format</th>
                            <th>Resolution</th>
                            <th>FPS</th>
                            <th>Duration</th>
                            <th>Size</th>
                            <th>Hash (xxHash64BE)</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for folder_name, data in folders.items() %}
                            {% for file in data.files %}
                            <tr>
                                <td>{{ file.name }}</td>
                                <td>{{ file.format }}</td>
                                <td>{{ file.resolution }}</td>
                                <td>{{ file.fps }}</td>
                                <td>{{ file.duration }}</td>
                                <td>{{ file.size }}</td>
                                <td style="font-family: monospace; font-size: 0.85em;">{{ file.hash }}</td>
                            </tr>
                            {% endfor %}
                        {% endfor %}
                    </tbody>
                </table>
            </body>
            </html>
            """

            template = Template(template_str)

            html_content = template.render(
                report_date=datetime.now().strftime("%Y-%m-%d %H:%M"),
                folders=sorted_folders,
                _format_bytes=_format_bytes,
                _format_duration=_format_duration
            )

            # --- PROGRESS UPDATE 4 ---
            if progress_callback: progress_callback(90, "Saving to disk...")

            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(html_content)

            print(f"DEBUG JobManager: HTML Report exported successfully to {file_path}")

        except Exception as e:
            import traceback
            print(f"ERROR JobManager: Failed to export HTML report: {e}")
            traceback.print_exc()


# ---------------------------------------------------------------------------
# HTML Export Runnable (Background Task)
# ---------------------------------------------------------------------------

class HtmlExportRunnableSignals(QObject):
    progress = Signal(int, str)
    finished = Signal()
    error = Signal(str)


class HtmlExportRunnable(QRunnable):
    def __init__(self, job_manager, file_path):
        super().__init__()
        self.setAutoDelete(True)  # Important: lets the thread pool clean it up after finish
        self.job_manager = job_manager
        self.file_path = file_path
        self.signals = HtmlExportRunnableSignals()

    def run(self):
        try:
            # Pass the progress signal as a callback to the export method
            self.job_manager.export_html(
                self.file_path,
                progress_callback=self.signals.progress.emit
            )
            self.signals.finished.emit()
        except Exception as e:
            import traceback
            print(f"ERROR HtmlExportRunnable: Failed to export HTML report: {e}")
            traceback.print_exc()
            self.signals.error.emit(str(e))
