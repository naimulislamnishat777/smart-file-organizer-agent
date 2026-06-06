"""
Smart File Organizer Agent
==========================
A Python agent that scans a folder, identifies file types,
creates organized subfolders, and moves files into them.

Workflow: Observe → Decide → Act
"""

import os
import shutil
import logging
import threading
import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext
from pathlib import Path
from datetime import datetime


# ---------------------------------------------------------------------------
# Configuration — file type mappings
# ---------------------------------------------------------------------------

FILE_TYPE_MAP: dict[str, str] = {
    # Documents
    ".pdf": "Documents",
    ".doc": "Documents",
    ".docx": "Documents",
    ".txt": "Documents",
    ".xlsx": "Documents",
    ".xls": "Documents",
    ".pptx": "Documents",
    ".ppt": "Documents",
    ".csv": "Documents",
    # Images
    ".jpg": "Images",
    ".jpeg": "Images",
    ".png": "Images",
    ".gif": "Images",
    ".bmp": "Images",
    ".svg": "Images",
    ".webp": "Images",
    ".ico": "Images",
    # Videos
    ".mp4": "Videos",
    ".mov": "Videos",
    ".avi": "Videos",
    ".mkv": "Videos",
    ".wmv": "Videos",
    ".flv": "Videos",
    ".webm": "Videos",
    # Archives
    ".zip": "Archives",
    ".rar": "Archives",
    ".7z": "Archives",
    ".tar": "Archives",
    ".gz": "Archives",
    ".bz2": "Archives",
}

FOLDER_COLORS = {
    "Documents": "#4A90E2",
    "Images": "#7ED321",
    "Videos": "#F5A623",
    "Archives": "#9B59B6",
    "Other": "#95A5A6",
}


# ---------------------------------------------------------------------------
# Logging setup — writes to both file and the GUI log widget
# ---------------------------------------------------------------------------


def setup_logger(log_path: Path) -> logging.Logger:
    """Configure a logger that writes to a timestamped log file."""
    logger = logging.getLogger("SmartFileOrganizer")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    # File handler
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    fmt = logging.Formatter(
        "%(asctime)s  [%(levelname)s]  %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    return logger


# ---------------------------------------------------------------------------
# Agent core — pure logic, no UI dependencies
# ---------------------------------------------------------------------------


class SmartFileOrganizerAgent:
    """
    The organizer agent.  Each public method maps to one workflow phase:

        observe()  → scan the target folder and collect file info
        decide()   → map each file to a destination folder
        act()      → create folders, move files, write log entries
    """

    def __init__(
        self,
        target_dir: Path,
        logger: logging.Logger,
        progress_callback=None,
        log_callback=None,
    ):
        self.target_dir = target_dir
        self.logger = logger
        self.progress_callback = progress_callback  # fn(current, total, file_name)
        self.log_callback = log_callback  # fn(phase, message)

        self._files: list[Path] = []
        self._plan: list[tuple[Path, Path]] = []  # (source, destination)
        self.stats: dict[str, int] = {}
        self.skipped: list[str] = []

    # ------------------------------------------------------------------
    # PHASE 1: OBSERVE
    # ------------------------------------------------------------------

    def observe(self) -> list[Path]:
        """
        Scan the target directory and collect all top-level files.
        Subdirectories that the agent itself will create are ignored.
        """
        self._emit("OBSERVE", f"Scanning: {self.target_dir}")
        managed_folders = set(FOLDER_COLORS.keys())

        files = []
        for item in sorted(self.target_dir.iterdir()):
            if item.is_file():
                files.append(item)
                self._emit("OBSERVE", f"  Found file: {item.name}")
            elif item.is_dir() and item.name not in managed_folders:
                self._emit("OBSERVE", f"  Skipping sub-folder: {item.name}/")

        self._files = files
        self._emit("OBSERVE", f"Total files found: {len(files)}")
        self.logger.info(
            "OBSERVE — %d file(s) detected in %s", len(files), self.target_dir
        )
        return files

    # ------------------------------------------------------------------
    # PHASE 2: DECIDE
    # ------------------------------------------------------------------

    def decide(self) -> list[tuple[Path, Path]]:
        """
        Map each file to its destination folder based on extension.
        Files with unknown extensions go to 'Other'.
        """
        self._emit("DECIDE", "Building move plan …")
        plan = []
        self.skipped = []

        for file in self._files:
            ext = file.suffix.lower()
            folder_name = FILE_TYPE_MAP.get(ext, "Other")
            dest_dir = self.target_dir / folder_name
            dest_file = dest_dir / file.name

            # Skip if the file is already in the right place
            if file.parent == dest_dir:
                self._emit("DECIDE", f"  Already organised — skip: {file.name}")
                self.skipped.append(file.name)
                continue

            # Handle name collisions by appending a counter
            counter = 1
            while dest_file.exists():
                stem = file.stem
                suffix = file.suffix
                dest_file = dest_dir / f"{stem}_{counter}{suffix}"
                counter += 1

            plan.append((file, dest_file))
            self._emit("DECIDE", f"  {file.name}  →  {folder_name}/{dest_file.name}")
            self.logger.info(
                "DECIDE — %s → %s/%s", file.name, folder_name, dest_file.name
            )

        self._plan = plan
        self._emit(
            "DECIDE", f"Plan ready: {len(plan)} move(s), {len(self.skipped)} skip(s)"
        )
        return plan

    # ------------------------------------------------------------------
    # PHASE 3: ACT
    # ------------------------------------------------------------------

    def act(self) -> dict[str, int]:
        """
        Execute the move plan:
          1. Create destination folders that don't yet exist.
          2. Move each file.
          3. Record statistics.
        """
        self._emit("ACT", "Executing move plan …")
        stats: dict[str, int] = {}
        total = len(self._plan)

        for index, (src, dst) in enumerate(self._plan, start=1):
            folder_name = dst.parent.name

            # Create the destination folder if it doesn't exist
            dst.parent.mkdir(parents=True, exist_ok=True)

            # Move the file
            shutil.move(str(src), str(dst))
            stats[folder_name] = stats.get(folder_name, 0) + 1

            msg = f"  Moved [{index}/{total}]: {src.name} → {folder_name}/"
            self._emit("ACT", msg)
            self.logger.info(
                "ACT    — moved %s → %s/%s", src.name, folder_name, dst.name
            )

            if self.progress_callback:
                self.progress_callback(index, total, src.name)

        self.stats = stats
        self._emit("ACT", "Done!  Summary:")
        for folder, count in stats.items():
            self._emit("ACT", f"    {folder}: {count} file(s)")
        self.logger.info("ACT    — complete. Stats: %s", stats)
        return stats

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _emit(self, phase: str, message: str) -> None:
        """Send a log line to the GUI callback (if any)."""
        if self.log_callback:
            self.log_callback(phase, message)


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------


class App(tk.Tk):
    """Main application window for the Smart File Organizer Agent."""

    def __init__(self):
        super().__init__()
        self.title("Smart File Organizer Agent")
        self.geometry("820x640")
        self.configure(bg="#1E1E2E")
        self.resizable(True, True)
        self.minsize(700, 540)

        self._selected_dir: Path | None = None
        self._agent_thread: threading.Thread | None = None

        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # ── Header ────────────────────────────────────────────────────
        header = tk.Frame(self, bg="#13131F", pady=14)
        header.pack(fill="x")

        tk.Label(
            header,
            text="🗂  Smart File Organizer Agent",
            font=("Segoe UI", 18, "bold"),
            fg="#CDD6F4",
            bg="#13131F",
        ).pack()

        tk.Label(
            header,
            text="Observe  →  Decide  →  Act",
            font=("Segoe UI", 10),
            fg="#6C7086",
            bg="#13131F",
        ).pack()

        # ── Folder selector ───────────────────────────────────────────
        selector = tk.Frame(self, bg="#1E1E2E", pady=12, padx=16)
        selector.pack(fill="x")

        tk.Label(
            selector,
            text="Target Folder:",
            font=("Segoe UI", 10, "bold"),
            fg="#BAC2DE",
            bg="#1E1E2E",
        ).pack(side="left")

        self._dir_var = tk.StringVar(value="No folder selected")
        self._dir_label = tk.Label(
            selector,
            textvariable=self._dir_var,
            font=("Consolas", 10),
            fg="#A6ADC8",
            bg="#2A2A3E",
            anchor="w",
            padx=8,
            pady=4,
            relief="flat",
            width=48,
        )
        self._dir_label.pack(side="left", padx=8)

        tk.Button(
            selector,
            text="Browse …",
            command=self._browse,
            font=("Segoe UI", 10),
            bg="#89B4FA",
            fg="#1E1E2E",
            activebackground="#74C7EC",
            relief="flat",
            padx=10,
            pady=4,
            cursor="hand2",
        ).pack(side="left")

        # ── Progress bar ──────────────────────────────────────────────
        prog_frame = tk.Frame(self, bg="#1E1E2E", padx=16)
        prog_frame.pack(fill="x")

        self._progress_var = tk.IntVar(value=0)
        self._progress_max = tk.IntVar(value=100)
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            "Custom.Horizontal.TProgressbar",
            troughcolor="#2A2A3E",
            background="#89B4FA",
            thickness=10,
        )

        self._progress_bar = ttk.Progressbar(
            prog_frame,
            variable=self._progress_var,
            maximum=100,
            length=780,
            style="Custom.Horizontal.TProgressbar",
        )
        self._progress_bar.pack(fill="x", pady=4)

        self._progress_label = tk.Label(
            prog_frame,
            text="",
            font=("Segoe UI", 9),
            fg="#6C7086",
            bg="#1E1E2E",
            anchor="w",
        )
        self._progress_label.pack(fill="x")

        # ── Stats row ─────────────────────────────────────────────────
        stats_frame = tk.Frame(self, bg="#1E1E2E", padx=16)
        stats_frame.pack(fill="x", pady=(4, 0))

        self._stat_labels: dict[str, tk.Label] = {}
        for folder, color in FOLDER_COLORS.items():
            card = tk.Frame(stats_frame, bg="#2A2A3E", padx=10, pady=6, relief="flat")
            card.pack(side="left", padx=4)
            indicator = tk.Label(
                card, text="●", font=("Segoe UI", 12), fg=color, bg="#2A2A3E"
            )
            indicator.pack(side="left")
            lbl = tk.Label(
                card,
                text=f"{folder}: 0",
                font=("Segoe UI", 9),
                fg="#CDD6F4",
                bg="#2A2A3E",
            )
            lbl.pack(side="left", padx=(4, 0))
            self._stat_labels[folder] = lbl

        # ── Log area ──────────────────────────────────────────────────
        log_frame = tk.Frame(self, bg="#1E1E2E", padx=16, pady=8)
        log_frame.pack(fill="both", expand=True)

        tk.Label(
            log_frame,
            text="Activity Log",
            font=("Segoe UI", 10, "bold"),
            fg="#BAC2DE",
            bg="#1E1E2E",
        ).pack(anchor="w")

        self._log_text = scrolledtext.ScrolledText(
            log_frame,
            bg="#13131F",
            fg="#CDD6F4",
            font=("Consolas", 9),
            wrap="word",
            relief="flat",
            state="disabled",
            insertbackground="#CDD6F4",
        )
        self._log_text.pack(fill="both", expand=True, pady=(4, 0))

        # Phase colour tags
        self._log_text.tag_config("OBSERVE", foreground="#89DCEB")
        self._log_text.tag_config("DECIDE", foreground="#F9E2AF")
        self._log_text.tag_config("ACT", foreground="#A6E3A1")
        self._log_text.tag_config("INFO", foreground="#6C7086")
        self._log_text.tag_config("ERROR", foreground="#F38BA8")

        # ── Bottom button bar ─────────────────────────────────────────
        btn_bar = tk.Frame(self, bg="#13131F", pady=10)
        btn_bar.pack(fill="x", side="bottom")

        self._run_btn = tk.Button(
            btn_bar,
            text="▶  Run Agent",
            command=self._run_agent,
            font=("Segoe UI", 11, "bold"),
            bg="#A6E3A1",
            fg="#1E1E2E",
            activebackground="#94E2D5",
            relief="flat",
            padx=20,
            pady=6,
            cursor="hand2",
        )
        self._run_btn.pack(side="left", padx=16)

        tk.Button(
            btn_bar,
            text="Clear Log",
            command=self._clear_log,
            font=("Segoe UI", 10),
            bg="#45475A",
            fg="#CDD6F4",
            activebackground="#585B70",
            relief="flat",
            padx=14,
            pady=6,
            cursor="hand2",
        ).pack(side="left", padx=4)

        tk.Button(
            btn_bar,
            text="Open Log File",
            command=self._open_log_file,
            font=("Segoe UI", 10),
            bg="#45475A",
            fg="#CDD6F4",
            activebackground="#585B70",
            relief="flat",
            padx=14,
            pady=6,
            cursor="hand2",
        ).pack(side="left", padx=4)

        self._status_var = tk.StringVar(value="Ready — select a folder to begin.")
        tk.Label(
            btn_bar,
            textvariable=self._status_var,
            font=("Segoe UI", 9),
            fg="#6C7086",
            bg="#13131F",
            anchor="e",
        ).pack(side="right", padx=16)

        # Log file path (set when agent runs)
        self._log_file_path: Path | None = None

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _browse(self) -> None:
        chosen = filedialog.askdirectory(title="Select folder to organise")
        if chosen:
            self._selected_dir = Path(chosen)
            self._dir_var.set(chosen)
            self._status_var.set("Folder selected — click 'Run Agent' to start.")
            self._log_info(f"Selected folder: {chosen}")

    def _run_agent(self) -> None:
        if not self._selected_dir:
            self._log_message("ERROR", "Please select a folder first.")
            return

        if self._agent_thread and self._agent_thread.is_alive():
            return

        # Reset stats display
        for lbl in self._stat_labels.values():
            folder = lbl.cget("text").split(":")[0]
            lbl.config(text=f"{folder}: 0")

        self._progress_var.set(0)
        self._progress_label.config(text="")
        self._run_btn.config(state="disabled", text="⏳  Running …")
        self._status_var.set("Agent running …")

        # Set up logger
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._log_file_path = self._selected_dir / f"organizer_log_{timestamp}.txt"
        logger = setup_logger(self._log_file_path)

        agent = SmartFileOrganizerAgent(
            target_dir=self._selected_dir,
            logger=logger,
            progress_callback=self._on_progress,
            log_callback=self._log_message,
        )

        def run():
            try:
                # ── OBSERVE ──────────────────────────────────────────
                self._log_phase_header("OBSERVE")
                agent.observe()

                # ── DECIDE ───────────────────────────────────────────
                self._log_phase_header("DECIDE")
                agent.decide()

                # ── ACT ──────────────────────────────────────────────
                self._log_phase_header("ACT")
                agent.act()

                # Update stat cards
                self.after(0, self._update_stats, agent.stats)
                self.after(0, self._on_done, len(agent.stats) > 0)

            except Exception as exc:
                self._log_message("ERROR", f"Agent error: {exc}")
                logger.exception("Unhandled exception in agent")
                self.after(0, self._on_done, False)

        self._agent_thread = threading.Thread(target=run, daemon=True)
        self._agent_thread.start()

    def _clear_log(self) -> None:
        self._log_text.configure(state="normal")
        self._log_text.delete("1.0", "end")
        self._log_text.configure(state="disabled")

    def _open_log_file(self) -> None:
        if self._log_file_path and self._log_file_path.exists():
            os.startfile(str(self._log_file_path))  # Windows
        elif self._log_file_path:
            self._log_message("ERROR", "Log file not found yet — run the agent first.")
        else:
            self._log_message("INFO", "No log file yet — run the agent first.")

    # ------------------------------------------------------------------
    # Callbacks from agent thread (must schedule via after() for thread safety)
    # ------------------------------------------------------------------

    def _on_progress(self, current: int, total: int, file_name: str) -> None:
        pct = int((current / total) * 100) if total else 0
        self.after(0, self._progress_var.set, pct)
        self.after(
            0,
            self._progress_label.config,
            {"text": f"Moving {current}/{total}: {file_name}"},
        )

    def _on_done(self, success: bool) -> None:
        self._run_btn.config(state="normal", text="▶  Run Agent")
        if success:
            self._status_var.set("✓ Organisation complete!")
            self._progress_var.set(100)
        else:
            self._status_var.set("Finished (check log for details).")

    def _update_stats(self, stats: dict[str, int]) -> None:
        for folder, lbl in self._stat_labels.items():
            count = stats.get(folder, 0)
            lbl.config(text=f"{folder}: {count}")

    # ------------------------------------------------------------------
    # Logging helpers
    # ------------------------------------------------------------------

    def _log_phase_header(self, phase: str) -> None:
        separator = "─" * 60
        self.after(
            0,
            self._append_log,
            f"\n{separator}\n  PHASE: {phase}\n{separator}\n",
            phase,
        )

    def _log_message(self, phase: str, message: str) -> None:
        self.after(0, self._append_log, message + "\n", phase)

    def _log_info(self, message: str) -> None:
        self.after(0, self._append_log, f"ℹ  {message}\n", "INFO")

    def _append_log(self, text: str, tag: str = "INFO") -> None:
        self._log_text.configure(state="normal")
        self._log_text.insert("end", text, tag)
        self._log_text.see("end")
        self._log_text.configure(state="disabled")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app = App()
    app.mainloop()
