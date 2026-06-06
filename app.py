"""
Smart File Organizer Agent — Flask Web App
==========================================
Workflow: Observe → Decide → Act
Supports both server-folder scanning and browser file uploads.
Streams real-time log events via SSE; lets users download a ZIP of results.
"""

import io
import os
import json
import uuid
import shutil
import logging
import queue
import threading
import zipfile
from pathlib import Path
from datetime import datetime
from flask import (Flask, render_template, request, jsonify,
                   Response, stream_with_context, send_file)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # 500 MB upload limit

SESSIONS_DIR = Path("/tmp/organizer_sessions")
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# File-type → folder mapping
# ---------------------------------------------------------------------------

FILE_TYPE_MAP: dict[str, str] = {
    ".pdf": "Documents", ".doc": "Documents", ".docx": "Documents",
    ".txt": "Documents", ".xlsx": "Documents", ".xls": "Documents",
    ".pptx": "Documents", ".ppt": "Documents", ".csv": "Documents",
    ".md": "Documents", ".rtf": "Documents", ".odt": "Documents",
    ".jpg": "Images", ".jpeg": "Images", ".png": "Images",
    ".gif": "Images", ".bmp": "Images", ".svg": "Images",
    ".webp": "Images", ".ico": "Images", ".tiff": "Images", ".tif": "Images",
    ".mp4": "Videos", ".mov": "Videos", ".avi": "Videos",
    ".mkv": "Videos", ".wmv": "Videos", ".flv": "Videos", ".webm": "Videos",
    ".zip": "Archives", ".rar": "Archives", ".7z": "Archives",
    ".tar": "Archives", ".gz": "Archives", ".bz2": "Archives",
}

MANAGED_FOLDERS = {"Documents", "Images", "Videos", "Archives", "Other"}

FOLDER_ICONS = {
    "Documents": "📄",
    "Images":    "🖼️",
    "Videos":    "🎬",
    "Archives":  "📦",
    "Other":     "📁",
}

# ---------------------------------------------------------------------------
# Logger factory
# ---------------------------------------------------------------------------

def make_logger(log_path: Path) -> logging.Logger:
    logger = logging.getLogger(f"organizer_{log_path.stem}")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(logging.Formatter(
        "%(asctime)s  [%(levelname)s]  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(fh)
    return logger

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def classify(name: str) -> tuple[str, str]:
    """Return (folder_name, icon) for a filename."""
    ext = Path(name).suffix.lower()
    folder = FILE_TYPE_MAP.get(ext, "Other")
    return folder, FOLDER_ICONS.get(folder, "📁")


def build_plan(target: Path) -> tuple[list[dict], list[dict], list[str]]:
    """
    Inspect *target* and return (files_info, plan_info, skipped).
    Does NOT move anything.
    """
    files_info, plan_info, skipped = [], [], []
    for item in sorted(target.iterdir()):
        if not item.is_file():
            continue
        folder, icon = classify(item.name)
        files_info.append({
            "name":   item.name,
            "ext":    item.suffix.lower() or "(none)",
            "size":   item.stat().st_size,
            "folder": folder,
            "icon":   icon,
        })
        dest_dir  = target / folder
        dest_file = dest_dir / item.name
        if item.parent == dest_dir:
            skipped.append(item.name)
            continue
        counter = 1
        while dest_file.exists():
            dest_file = dest_dir / f"{item.stem}_{counter}{item.suffix}"
            counter += 1
        plan_info.append({
            "src": item.name, "dst": dest_file.name,
            "folder": folder, "icon": icon,
        })
    return files_info, plan_info, skipped

# ---------------------------------------------------------------------------
# Agent — pure logic; emits SSE events to a queue
# ---------------------------------------------------------------------------

class SmartFileOrganizerAgent:
    def __init__(self, target_dir: Path, event_q: queue.Queue,
                 logger: logging.Logger):
        self.target_dir = target_dir
        self.q = event_q
        self.logger = logger
        self._files: list[Path] = []
        self._plan: list[dict] = []
        self.stats: dict[str, int] = {}
        self.skipped: list[str] = []

    def _push(self, phase: str, message: str, extra: dict | None = None):
        payload = {"phase": phase, "message": message}
        if extra:
            payload.update(extra)
        self.q.put(payload)
        self.logger.info("[%s] %s", phase, message)

    # ── OBSERVE ─────────────────────────────────────────────────────────────

    def observe(self) -> list[Path]:
        self._push("OBSERVE", f"Scanning: {self.target_dir}")
        files = []
        for item in sorted(self.target_dir.iterdir()):
            if item.is_file():
                folder, icon = classify(item.name)
                files.append(item)
                self._push("OBSERVE", f"Found: {item.name}",
                           {"file": {"name": item.name,
                                     "ext": item.suffix.lower() or "(none)",
                                     "size": item.stat().st_size,
                                     "folder": folder, "icon": icon}})
            elif item.is_dir() and item.name not in MANAGED_FOLDERS:
                self._push("OBSERVE", f"Skipping sub-folder: {item.name}/")
        self._files = files
        self._push("OBSERVE", f"Total files found: {len(files)}",
                   {"total": len(files)})
        return files

    # ── DECIDE ──────────────────────────────────────────────────────────────

    def decide(self) -> list[dict]:
        self._push("DECIDE", "Building move plan …")
        plan, skipped = [], []
        for f in self._files:
            folder, icon = classify(f.name)
            dest_dir  = self.target_dir / folder
            dest_file = dest_dir / f.name
            if f.parent == dest_dir:
                skipped.append(f.name)
                self._push("DECIDE", f"Already in place — skip: {f.name}")
                continue
            counter = 1
            while dest_file.exists():
                dest_file = dest_dir / f"{f.stem}_{counter}{f.suffix}"
                counter += 1
            entry = {"src": f.name, "dst": dest_file.name,
                     "folder": folder, "icon": icon}
            plan.append({"src_path": f, "dst_path": dest_file, **entry})
            self._push("DECIDE", f"{f.name} → {folder}/{dest_file.name}",
                       {"move": entry})
        self._plan, self.skipped = plan, skipped
        self._push("DECIDE",
                   f"Plan ready: {len(plan)} move(s), {len(skipped)} skip(s)",
                   {"plan_count": len(plan), "skip_count": len(skipped)})
        return plan

    # ── ACT ─────────────────────────────────────────────────────────────────

    def act(self) -> dict[str, int]:
        self._push("ACT", "Executing move plan …")
        total = len(self._plan)
        stats: dict[str, int] = {}
        for i, entry in enumerate(self._plan, 1):
            src: Path = entry["src_path"]
            dst: Path = entry["dst_path"]
            folder = entry["folder"]
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            stats[folder] = stats.get(folder, 0) + 1
            self._push("ACT", f"[{i}/{total}] Moved: {src.name} → {folder}/",
                       {"current": i, "total": total,
                        "folder": folder, "file": src.name})
        self.stats = stats
        self._push("ACT", "Organisation complete!",
                   {"stats": stats, "done": True})
        return stats

# ---------------------------------------------------------------------------
# SSE streaming helper
# ---------------------------------------------------------------------------

def _run_and_stream(target: Path, log_path: Path,
                    extra_done: dict | None = None) -> Response:
    event_q: queue.Queue = queue.Queue()
    logger = make_logger(log_path)

    def run_agent():
        try:
            agent = SmartFileOrganizerAgent(target, event_q, logger)
            agent.observe()
            agent.decide()
            agent.act()
            done_payload = {"phase": "DONE", "log_file": str(log_path)}
            if extra_done:
                done_payload.update(extra_done)
            event_q.put(done_payload)
        except Exception as exc:
            logger.exception("Agent error")
            event_q.put({"phase": "ERROR", "message": str(exc)})
        finally:
            event_q.put(None)

    threading.Thread(target=run_agent, daemon=True).start()

    def stream():
        while True:
            item = event_q.get()
            if item is None:
                break
            yield f"data: {json.dumps(item)}\n\n"

    return Response(
        stream_with_context(stream()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

# ---------------------------------------------------------------------------
# Routes — folder-based (existing)
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/scan", methods=["POST"])
def scan():
    """Observe phase for a server-side folder — JSON response."""
    data = request.get_json(force=True)
    raw_path = (data.get("path") or "").strip()
    if not raw_path:
        return jsonify({"error": "No path provided."}), 400
    target = Path(raw_path).expanduser().resolve()
    if not target.exists() or not target.is_dir():
        return jsonify({"error": f"Directory not found: {target}"}), 404
    files_info, plan_info, skipped = build_plan(target)
    return jsonify({"path": str(target), "files": files_info,
                    "plan": plan_info, "skipped": skipped})


@app.route("/organize", methods=["POST"])
def organize():
    """Decide + Act for a server-side folder — SSE stream."""
    data = request.get_json(force=True)
    raw_path = (data.get("path") or "").strip()
    if not raw_path:
        return jsonify({"error": "No path provided."}), 400
    target = Path(raw_path).expanduser().resolve()
    if not target.exists() or not target.is_dir():
        return jsonify({"error": f"Directory not found: {target}"}), 404
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path  = target / f"organizer_log_{timestamp}.txt"
    return _run_and_stream(target, log_path)


@app.route("/demo", methods=["POST"])
def create_demo():
    """Populate a demo folder with sample files of various types."""
    demo_dir = Path("/home/runner/workspace/demo_files")
    demo_dir.mkdir(exist_ok=True)
    samples = [
        ("report_Q1.pdf",     b"%PDF-1.4 demo"),
        ("notes.txt",         b"Meeting notes"),
        ("presentation.pptx", b"PK demo pptx"),
        ("data.csv",          b"col1,col2\n1,2"),
        ("photo.jpg",         bytes([0xFF, 0xD8, 0xFF, 0xE0]) + b"demo jpg"),
        ("screenshot.png",    bytes([0x89, 0x50, 0x4E, 0x47]) + b"demo png"),
        ("avatar.webp",       b"RIFF demo webp"),
        ("clip.mp4",          b"\x00\x00\x00\x18ftyp" + b"demo mp4"),
        ("archive.zip",       bytes([0x50, 0x4B, 0x03, 0x04]) + b"demo zip"),
        ("backup.tar.gz",     bytes([0x1F, 0x8B]) + b"demo tar.gz"),
        ("invoice.docx",      b"PK demo docx"),
        ("budget.xlsx",       b"PK demo xlsx"),
    ]
    created = []
    for name, content in samples:
        p = demo_dir / name
        if not p.exists():
            p.write_bytes(content)
            created.append(name)
    return jsonify({"path": str(demo_dir), "created": created,
                    "message": f"Demo folder ready at {demo_dir}"})

# ---------------------------------------------------------------------------
# Routes — upload-based (new)
# ---------------------------------------------------------------------------

@app.route("/upload", methods=["POST"])
def upload():
    """
    Accept one or more uploaded files.
    Saves them into a fresh session folder and returns the scan preview.
    """
    files = request.files.getlist("files")
    if not files or all(f.filename == "" for f in files):
        return jsonify({"error": "No files received."}), 400

    session_id = uuid.uuid4().hex
    session_dir = SESSIONS_DIR / session_id
    session_dir.mkdir(parents=True)

    saved = []
    for f in files:
        if f.filename:
            safe_name = Path(f.filename).name  # strip any path components
            dest = session_dir / safe_name
            # Avoid overwriting duplicates
            counter = 1
            while dest.exists():
                dest = session_dir / f"{Path(safe_name).stem}_{counter}{Path(safe_name).suffix}"
                counter += 1
            f.save(str(dest))
            saved.append(dest.name)

    files_info, plan_info, skipped = build_plan(session_dir)
    return jsonify({
        "session_id": session_id,
        "files":      files_info,
        "plan":       plan_info,
        "skipped":    skipped,
        "saved":      saved,
    })


@app.route("/upload-organize", methods=["POST"])
def upload_organize():
    """
    Run the organizer agent on an existing upload session — SSE stream.
    When done, sends a DONE event with the session_id for the download link.
    """
    data = request.get_json(force=True)
    session_id = (data.get("session_id") or "").strip()
    if not session_id:
        return jsonify({"error": "No session_id provided."}), 400

    session_dir = SESSIONS_DIR / session_id
    if not session_dir.exists():
        return jsonify({"error": "Session not found."}), 404

    log_path = session_dir / "organizer_log.txt"
    return _run_and_stream(
        session_dir, log_path,
        extra_done={"session_id": session_id, "download_ready": True}
    )


@app.route("/download/<session_id>")
def download(session_id: str):
    """
    Zip the organised session folder and stream it to the browser.
    """
    # Sanitise — session IDs are hex only
    if not session_id.isalnum():
        return "Invalid session ID", 400

    session_dir = SESSIONS_DIR / session_id
    if not session_dir.exists():
        return "Session not found", 404

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(session_dir.rglob("*")):
            if path.is_file() and path.name != "organizer_log.txt":
                arcname = path.relative_to(session_dir)
                zf.write(path, arcname)
    buf.seek(0)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"organized_files_{timestamp}.zip",
    )

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
