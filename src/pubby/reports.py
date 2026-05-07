"""Report generation (CSV / HTML) and background export runnable."""

from __future__ import annotations

import csv
import os
from datetime import datetime

from PySide6.QtCore import QRunnable, QObject, Signal

from pubby.utils import format_size, format_duration, VIDEO_EXTENSIONS


# ---------------------------------------------------------------------------
# JobManager report methods (moved out of workers.py)
# ---------------------------------------------------------------------------

def export_csv(job_manager, file_path: str):
    """Export completed files data to a CSV report."""
    completed = job_manager.get_completed_files()
    if not completed:
        print("No completed files to export.")
        return

    try:
        with open(file_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([
                "Name", "Source Folder", "File Type", "File Size",
                "Creation Date", "Hash Values", "Volumes", "Verification State",
            ])

            for source_path, data in completed.items():
                size_bytes = data.get('size', 0)
                start_time = data.get('start_time', 0)
                file_hash = data.get('hash', 'N/A')
                dest_volume = data.get('dest_path', 'Unknown Volume')
                source_folder = data.get('source_folder', 'Unknown Folder')

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

                writer.writerow([
                    filename, source_folder, ext.upper(), format_size(size_bytes),
                    creation_date_str, file_hash, dest_volume, verification_state,
                ])

        print(f"DEBUG JobManager: CSV exported successfully to {file_path}")
    except Exception as e:
        print(f"ERROR JobManager: Failed to export CSV: {e}")


def _aggregate_folder_data(completed_files):
    """Aggregate completed files grouped by source folder.

    Returns a dict keyed by folder name with aggregated stats.
    """
    folder_data = {}

    for source_path, data in completed_files.items():
        folder_name = os.path.basename(data.get('source_folder', 'Unknown'))

        if folder_name not in folder_data:
            folder_data[folder_name] = {
                'files': [],
                'total_clips': 0,
                'total_files': 0,
                'total_duration_secs': 0,
                'total_size_bytes': 0,
            }

        video_meta = data.get('video_meta', {})
        size = data.get('size', 0)
        duration = video_meta.get('duration', 0)
        ext = os.path.splitext(source_path)[1].lower()

        folder_data[folder_name]['total_files'] += 1

        if ext in VIDEO_EXTENSIONS:
            folder_data[folder_name]['total_clips'] += 1

        folder_data[folder_name]['files'].append({
            'name': os.path.basename(source_path),
            'size': format_size(size),
            'duration': format_duration(duration),
            'resolution': video_meta.get('resolution', 'N/A'),
            'fps': video_meta.get('fps', 0),
            'format': video_meta.get('format', 'UNKNOWN'),
            'hash': data.get('hash', 'N/A'),
        })

        folder_data[folder_name]['total_duration_secs'] += duration
        folder_data[folder_name]['total_size_bytes'] += size

    return dict(sorted(folder_data.items()))


def export_html(job_manager, file_path: str, progress_callback=None):
    """Generate an HTML report using Jinja2."""
    from jinja2 import Template

    completed = job_manager.get_completed_files()
    if not completed:
        print("No completed files to export.")
        return

    try:
        if progress_callback:
            progress_callback(10, "Aggregating metadata...")

        sorted_folders = _aggregate_folder_data(completed)

        if progress_callback:
            progress_callback(40, "Sorting folders...")

        if progress_callback:
            progress_callback(60, "Rendering HTML template...")

        template_str = """\
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
                <th>Duration</th>
                <th>Files</th>
                <th>Size</th>
            </tr>
        </thead>
        <tbody>
            {% for folder_name, data in folders.items() %}
            <tr class="folder-header">
                <td>{{ folder_name }}</td>
                <td>{{ data.total_clips }}</td>
                <td>{{ _format_duration(data.total_duration_secs) }}</td>
                <td>{{ data.total_files }}</td>
                <td>{{ _format_size(data.total_size_bytes) }}</td>
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
</html>"""

        template = Template(template_str)

        html_content = template.render(
            report_date=datetime.now().strftime("%Y-%m-%d %H:%M"),
            folders=sorted_folders,
            _format_size=format_size,
            _format_duration=format_duration,
        )

        if progress_callback:
            progress_callback(90, "Saving to disk...")

        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(html_content)

        print(f"DEBUG JobManager: HTML Report exported successfully to {file_path}")

    except Exception as e:
        import traceback
        print(f"ERROR JobManager: Failed to export HTML report: {e}")
        traceback.print_exc()


# ---------------------------------------------------------------------------
# Background runnable for HTML export (progress dialog integration)
# ---------------------------------------------------------------------------

class HtmlExportRunnableSignals(QObject):
    progress = Signal(int, str)
    finished = Signal()
    error = Signal(str)


class HtmlExportRunnable(QRunnable):
    """Runs report generation in a background thread."""

    def __init__(self, job_manager, file_path: str):
        super().__init__()
        self.setAutoDelete(True)
        self.job_manager = job_manager
        self.file_path = file_path
        self.signals = HtmlExportRunnableSignals()

    def run(self):
        try:
            export_html(
                self.job_manager,
                self.file_path,
                progress_callback=self.signals.progress.emit,
            )
            self.signals.finished.emit()
        except Exception as e:
            import traceback
            print(f"ERROR HtmlExportRunnable: Failed to export HTML report: {e}")
            traceback.print_exc()
            self.signals.error.emit(str(e))
