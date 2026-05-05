from __future__ import annotations

import time
import os
import json
import subprocess
import tempfile
import random
import shutil  # Import shutil for removing directories
import hashlib
from PySide6.QtCore import *  # Wildcard import as requested


# ---------------------------------------------------------------------------
# Real Job Signals & Class
# ---------------------------------------------------------------------------

class FileCopyJobSignals(QObject):
    started = Signal(str)
    updated = Signal(str, dict)
    completed = Signal(str)
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
                self.signals.completed.emit(self.source_path)
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
    completed = Signal(str)
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
            self.signals.completed.emit(self.source_path)
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
        self.network_speed_gbps = 1.0
        self.completed_files: dict[str, str] = {}  # source_path -> status

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

    @Slot(str)
    def _on_completed(self, source_path: str):
        print(f"DEBUG JobManager: _on_completed called for {source_path}")

        if source_path in self.active_jobs:
            del self.active_jobs[source_path]
            print(f"DEBUG JobManager: Removed from active_jobs")

        # Track completed file
        self.completed_files[source_path] = "success"
        print(f"DEBUG JobManager: Added to completed_files: {source_path} -> success")

        # Update folder-level progress
        folder_path = os.path.dirname(source_path)
        if folder_path in self._folder_sizes:
            try:
                file_size = 0
                try:
                    file_size = os.path.getsize(source_path)
                except OSError:
                    pass

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

    def get_completed_files(self) -> dict[str, str]:
        """Returns a dictionary of completed files with their status."""
        return self.completed_files.copy()
