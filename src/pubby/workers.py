"""Copy job classes, JobManager, and background threading logic."""

from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import tempfile
import time
from PySide6.QtCore import *  # noqa: F403

from pubby.utils import calculate_md5, get_video_metadata, VIDEO_EXTENSIONS


# ---------------------------------------------------------------------------
# Shared signal class (used by both real and fake copy jobs)
# ---------------------------------------------------------------------------

class CopyJobSignals(QObject):
    started = Signal(str)
    updated = Signal(str, dict)
    completed = Signal(str, dict)  # (path, metadata_dict)
    error = Signal(str, str)


# ---------------------------------------------------------------------------
# Real file copy job (rclone)
# ---------------------------------------------------------------------------

class FileCopyJob(QRunnable):
    """Copies files using rclone."""

    def __init__(self, source_path: str, dest_path: str, job_id: str, is_dry_run: bool = False):
        super().__init__()
        self.setAutoDelete(True)
        self.source_path = source_path
        self.dest_path = dest_path
        self.job_id = job_id
        self.is_dry_run = is_dry_run
        self.signals = CopyJobSignals()

    def run(self):
        print(f"DEBUG FILE COPY: Starting copy job for {self.source_path}")
        self.signals.started.emit(self.source_path)

        try:
            cmd = [
                "rclone", "copyto",
                self.source_path, self.dest_path,
                "--use-json-log", "--log-level=INFO", "--stats=1s",
            ]
            if self.is_dry_run:
                cmd.append("--dry-run")

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )

            try:
                total_size = os.path.getsize(self.source_path)
                start_time = time.time()
                file_hash = calculate_md5(self.source_path)
                video_meta = get_video_metadata(self.source_path)
            except OSError as e:
                self.signals.error.emit(self.source_path, f"Could not get file size: {e}")
                return

            current_transferred = 0
            last_emit_time = time.time()
            throttle_interval = 0.15

            while True:
                line = process.stderr.readline()
                if not line:
                    break

                try:
                    data = json.loads(line.strip())
                    if 'stats' in data:
                        stats = data['stats']
                        transferred_bytes = stats.get('bytes', 0)
                        speed_bps = stats.get('speed', 0)

                        if transferred_bytes != current_transferred:
                            current_transferred = transferred_bytes

                            if total_size > 0:
                                progress_ratio = min(current_transferred / total_size, 1.0)
                            else:
                                progress_ratio = 1.0 if current_transferred > 0 else 0

                            now = time.time()
                            if now - last_emit_time >= throttle_interval:
                                self.signals.updated.emit(self.source_path, {
                                    'progress': progress_ratio,
                                    'speed': speed_bps,
                                })
                                last_emit_time = now

                except json.JSONDecodeError:
                    continue
                except Exception as e:
                    print(f"DEBUG FILE COPY: Error parsing JSON line: {e}")

            process.wait()

            if process.returncode == 0:
                self.signals.updated.emit(self.source_path, {
                    'progress': 1.0, 'speed': 0, 'status': 'Complete',
                })
                self.signals.completed.emit(self.source_path, {
                    'size': total_size,
                    'start_time': start_time,
                    'hash': file_hash,
                    'dest_path': self.dest_path,
                    'video_meta': video_meta,
                })
            else:
                stderr_output = process.stderr.read().strip()
                print(f"DEBUG RCLONE ERROR: {stderr_output}")
                self.signals.error.emit(
                    self.source_path,
                    f"rclone exited with code {process.returncode}: {stderr_output}",
                )

        except FileNotFoundError:
            self.signals.error.emit(self.source_path, "Error: 'rclone' command not found in PATH.")
        except Exception as e:
            self.signals.error.emit(self.source_path, f"Unexpected error: {str(e)}")


# ---------------------------------------------------------------------------
# Fake / simulated copy job (dry run)
# ---------------------------------------------------------------------------

class FakeCopyJob(QRunnable):
    """Simulates a file copy for dry-run mode."""

    def __init__(self, source_path: str, dest_path: str, job_id: str, gbps_speed: float):
        super().__init__()
        self.setAutoDelete(True)
        self.source_path = source_path
        self.dest_path = dest_path
        self.job_id = job_id
        self.gbps_speed = gbps_speed
        self.signals = CopyJobSignals()
        self.temp_dir = None

    def run(self):
        print(f"DEBUG FAKE COPY: Starting fake job for {self.source_path}")
        self.signals.started.emit(self.source_path)

        temp_dir = None
        try:
            short_id = hashlib.md5(self.source_path.encode('utf-8')).hexdigest()[:8]
            prefix = f"pubby_{short_id}_"
            temp_dir = tempfile.mkdtemp(prefix=prefix)
            self.temp_dir = temp_dir

            rel_path = os.path.relpath(self.dest_path, os.path.dirname(self.dest_path))
            actual_dest_path = os.path.join(temp_dir, rel_path)
            dir_to_create = os.path.dirname(actual_dest_path)
            os.makedirs(dir_to_create, exist_ok=True)

        except Exception as e:
            error_msg = f"Could not create temp dir: {str(e)}"
            print(f"DEBUG FAKE COPY ERROR: {error_msg}")
            self.signals.error.emit(self.source_path, error_msg)
            return

        try:
            total_size = os.path.getsize(self.source_path)
            start_time = time.time()
            file_hash = calculate_md5(self.source_path)
            video_meta = get_video_metadata(self.source_path)
        except OSError as e:
            self.signals.error.emit(self.source_path, f"Could not get file size: {e}")
            return

        current_transferred = 0
        last_emit_time = time.time()

        base_speed_mbps = self.gbps_speed * 1000 / 8
        min_speed = base_speed_mbps * 0.5
        max_speed = base_speed_mbps * 0.9

        print(f"DEBUG FAKE COPY: Base speed: {base_speed_mbps} Mbps, range: {min_speed}-{max_speed} Mbps")

        while current_transferred < total_size:
            progress_ratio = min(current_transferred / total_size, 1.0) if total_size > 0 else 1.0

            speed_variation = random.uniform(0.8, 1.2)
            current_speed_mbps = min(max_speed, max(min_speed, base_speed_mbps * speed_variation))

            bytes_per_tick = int(current_speed_mbps * (1024 * 1024) * 0.1)
            bytes_to_transfer = min(bytes_per_tick, total_size - current_transferred)

            if bytes_to_transfer <= 0:
                break

            current_transferred += bytes_to_transfer

            now = time.time()
            if now - last_emit_time >= 0.1:
                self.signals.updated.emit(self.source_path, {
                    'progress': min(current_transferred / total_size, 1.0),
                    'speed': int(current_speed_mbps * (1024 * 1024)),
                })
                last_emit_time = now

            time.sleep(0.05)

            if current_transferred % (1024 * 1024) < 1024 and current_transferred > 0:
                print(f"DEBUG FAKE COPY: Transferred {current_transferred / (1024 * 1024):.2f} MB so far")

        # Create dummy file to simulate successful copy
        try:
            with open(actual_dest_path, 'wb') as f:
                f.write(b'DUMMY DATA FOR DRY RUN' + b'\x00' * 1024)
        except Exception as e:
            print(f"DEBUG FAKE COPY ERROR creating dummy file: {e}")

        # Final status update before completion signal
        self.signals.updated.emit(self.source_path, {
            'progress': 1.0, 'speed': 0, 'status': 'Complete',
        })

        self.signals.completed.emit(self.source_path, {
            'size': total_size,
            'start_time': start_time,
            'hash': file_hash,
            'dest_path': self.dest_path,
            'video_meta': video_meta,
        })

        print(f"DEBUG FAKE COPY: Completed fake job for {self.source_path}")

    def __del__(self):
        """Clean up temp directory."""
        if self.temp_dir and os.path.exists(self.temp_dir):
            try:
                shutil.rmtree(self.temp_dir, ignore_errors=True)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# JobManager — orchestrates copy jobs and tracks progress
# ---------------------------------------------------------------------------

class JobManager(QObject):
    network_speed_changed = Signal(float)

    file_updated = Signal(str, dict)
    job_started = Signal(str)
    job_progress = Signal(str, float, int)
    job_finished = Signal(str, str)
    job_error = Signal(str, str)
    folder_progress_updated = Signal(str, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.thread_pool = QThreadPool.globalInstance()
        self.active_jobs: dict[str, FileCopyJob | FakeCopyJob] = {}
        self.dry_run_mode = 0  # 0=real, 1/2=simulated 1Gbps/10Gbps, 3=rclone dry-run
        self.network_speed_gbps = 10.0
        self.completed_files: dict[str, dict] = {}

        self._folder_sizes: dict[str, int] = {}
        self._folder_completed_bytes: dict[str, int] = {}

    def start_job(self, source_path: str, dest_path: str):
        if source_path in self.active_jobs:
            return

        folder_path = os.path.dirname(source_path)

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

        is_dry_run = self.dry_run_mode > 0
        job_id = f"dry_job_{source_path}" if is_dry_run else f"job_{source_path}"

        if self.dry_run_mode == 3:
            job = FileCopyJob(source_path, dest_path, job_id, is_dry_run=True)
        elif self.dry_run_mode > 0:
            job = FakeCopyJob(source_path, dest_path, job_id, self.network_speed_gbps)
        else:
            job = FileCopyJob(source_path, dest_path, job_id)

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

    @Slot(str, dict)
    def _on_completed(self, source_path: str, metadata: dict):
        if source_path in self.active_jobs:
            del self.active_jobs[source_path]

        metadata['source_folder'] = os.path.dirname(source_path)
        self.completed_files[source_path] = metadata

        folder_path = os.path.dirname(source_path)
        self._update_folder_progress(folder_path, metadata.get('size', 0))

        self.job_finished.emit(source_path, "success")
        self.file_updated.emit(source_path, {'status': 'Complete', 'progress': 1.0, 'speed': 0})

    @Slot(str, str)
    def _on_error(self, source_path: str, error_msg: str):
        if source_path in self.active_jobs:
            del self.active_jobs[source_path]

        self.completed_files[source_path] = f"error: {error_msg}"

        folder_path = os.path.dirname(source_path)
        self._update_folder_progress(folder_path, 0)

        self.job_error.emit(source_path, error_msg)
        display_error = f"Error: {error_msg[:50]}" if len(error_msg) > 50 else f"Error: {error_msg}"
        self.file_updated.emit(source_path, {'status': display_error, 'progress': -1.0, 'speed': 0})

    def _update_folder_progress(self, folder_path: str, file_size: int):
        """Update the overall progress for a given folder."""
        if folder_path not in self._folder_sizes:
            return

        try:
            if folder_path not in self._folder_completed_bytes:
                self._folder_completed_bytes[folder_path] = 0
            self._folder_completed_bytes[folder_path] += file_size

            total_size = self._folder_sizes[folder_path]
            completed_bytes = self._folder_completed_bytes[folder_path]
            overall_progress = min(completed_bytes / total_size, 1.0) if total_size > 0 else 1.0

            print(f"DEBUG JobManager: Emitting folder_progress_updated for {folder_path}: {overall_progress}")
            self.folder_progress_updated.emit(folder_path, overall_progress)

        except Exception as e:
            print(f"ERROR JobManager: Error updating folder progress for {folder_path}: {e}")

    def set_dry_run_mode(self, mode: int):
        """Set dry run mode. 0 = real copy, >0 = simulated or rclone dry run."""
        self.dry_run_mode = mode

    def set_network_speed(self, gbps: float):
        """Set the simulated network speed for dry run mode."""
        self.network_speed_gbps = gbps
        self.network_speed_changed.emit(gbps)

    def get_completed_files(self) -> dict[str, dict]:
        """Returns a copy of completed files with their metadata."""
        return self.completed_files.copy()
