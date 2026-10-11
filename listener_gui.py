"""
Window for the capture (Listener) — what the ForzaListener.exe build runs: pick the folder
the .bin files go to, the UDP port, Start / Stop, and a journal of the messages the console
version prints. Stdlib only (tkinter), like Listener; never imports the tuner.

    .venv/Scripts/python.exe listener_gui.py        # from source
    build_listener.bat                               # -> dist/ForzaListener/ForzaListener.exe
"""

from __future__ import annotations

import json
import os
import queue
import socket
import sys
import tempfile
import threading
import tkinter as tk
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Optional

import Listener

APP_NAME = "ForzaListener"
DEFAULT_PORT = 49152
BIND_ALL = "0.0.0.0"          # listen on every network adapter: no IP to type in here
LOG_FILE_NAME = "listener_log.txt"
POLL_MS = 200                 # journal / counters refresh
STOP_WAIT_S = 3.0             # Listener.run wakes up every SOCKET_TIMEOUT_S (0.5 s)


def frozen() -> bool:
    return getattr(sys, "frozen", False)


def default_output_dir() -> Path:
    """The exe writes to Documents/ForzaTelemetry; from source, the project's telemetry/."""
    if frozen():
        return Path.home() / "Documents" / "ForzaTelemetry"
    return Path(__file__).resolve().with_name("telemetry")


def settings_path() -> Path:
    base = Path(os.environ.get("APPDATA") or Path.home())
    return base / APP_NAME / "settings.json"


@dataclass
class Settings:
    output_dir: str
    port: int = DEFAULT_PORT

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Settings":
        path = path or settings_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            port = int(data.get("port", DEFAULT_PORT))
            return cls(output_dir=str(data["output_dir"]), port=port if 1 <= port <= 65535 else DEFAULT_PORT)
        except (OSError, ValueError, KeyError, TypeError):
            return cls(output_dir=str(default_output_dir()))

    def save(self, path: Optional[Path] = None) -> None:
        path = path or settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")


def local_ipv4() -> list:
    """This PC's IPv4 addresses (what goes into the game's Data Out IP), primary first."""
    found = []
    try:   # the adapter that would route outside; UDP connect sends nothing
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            found.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(info[4][0])
    except OSError:
        pass
    out = []
    for ip in found:
        if not ip.startswith("127.") and ip not in out:
            out.append(ip)
    return out


class CaptureSession:
    """Listener running in a background thread. Messages go to `messages` (a queue the
    window drains) with a timestamp, and to listener_log.txt in the output folder."""

    def __init__(self, output_dir: Path, port: int, messages: queue.Queue):
        self.output_dir = Path(output_dir)
        self.messages = messages
        self._lock = threading.Lock()     # log() is called from both threads
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._log_file = open(self.output_dir / LOG_FILE_NAME, "a", encoding="utf-8")
        try:   # raises OSError when the port is taken — the window shows it
            self.listener = Listener.Listener(BIND_ALL, port, self.output_dir, log=self.log)
        except BaseException:
            self._close_log()
            raise
        self.thread = threading.Thread(target=self._run, name="listener", daemon=True)

    def log(self, message: str) -> None:
        line = f"{datetime.now():%H:%M:%S}  {message}"
        self.messages.put(line)
        with self._lock:
            if self._log_file is not None:
                self._log_file.write(f"{datetime.now():%Y-%m-%d} {line}\n")
                self._log_file.flush()

    def _close_log(self) -> None:
        with self._lock:
            if self._log_file is not None:
                self._log_file.close()
                self._log_file = None

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.listener.stop()

    @property
    def running(self) -> bool:
        return self.thread.is_alive()

    def _run(self) -> None:
        try:
            self.listener.run()
        except Exception as exc:          # never die silently in a windowed exe
            self.log(f"Помилка: {exc!r}")
        finally:
            self.listener.close()         # writes what is buffered
            self.log(self.listener.summary())
            self.log("Запис зупинено.")
            self._close_log()


class ListenerWindow:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.settings = Settings.load()
        self.messages: queue.Queue = queue.Queue()
        self.session: Optional[CaptureSession] = None
        # The window's own state, not read back from the widgets: ttk's widget["state"] is a
        # Tcl object, so `== "disabled"` was never true and the window stayed in "recording"
        # after Stop (Start / Change… disabled until a restart).
        self.running = False

        root.title("Forza Listener — запис телеметрії FH6")
        root.minsize(620, 420)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        frame = ttk.Frame(root, padding=10)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="Тека для записів:").grid(row=0, column=0, sticky="w")
        self.dir_var = tk.StringVar(value=self.settings.output_dir)
        ttk.Entry(frame, textvariable=self.dir_var, state="readonly").grid(row=0, column=1, sticky="ew", padx=6)
        self.change_btn = ttk.Button(frame, text="Змінити…", command=self.change_dir)
        self.change_btn.grid(row=0, column=2)
        ttk.Button(frame, text="Відкрити теку", command=self.open_dir).grid(row=0, column=3, padx=(6, 0))

        ttk.Label(frame, text="Порт:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.port_var = tk.StringVar(value=str(self.settings.port))
        self.port_entry = ttk.Entry(frame, textvariable=self.port_var, width=8)
        self.port_entry.grid(row=1, column=1, sticky="w", padx=6, pady=(8, 0))

        ips = ", ".join(local_ipv4()) or "не знайдено"
        ttk.Label(frame, text=f"У грі (Data Out) вкажіть IP цього ПК: {ips} і той самий порт.",
                  foreground="#555").grid(row=2, column=0, columnspan=4, sticky="w", pady=(4, 8))

        buttons = ttk.Frame(frame)
        buttons.grid(row=3, column=0, columnspan=4, sticky="w")
        self.start_btn = ttk.Button(buttons, text="▶ Старт", command=self.start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(buttons, text="■ Стоп", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=6)
        self.state_var = tk.StringVar(value="Зупинено")
        ttk.Label(buttons, textvariable=self.state_var).pack(side="left", padx=12)

        self.stats_var = tk.StringVar(value="")
        ttk.Label(frame, textvariable=self.stats_var).grid(row=4, column=0, columnspan=4, sticky="w", pady=(8, 4))

        self.journal = ScrolledText(frame, height=14, state="disabled", wrap="word")
        self.journal.grid(row=5, column=0, columnspan=4, sticky="nsew")
        frame.rowconfigure(5, weight=1)

        self.note(f"Готово. Формат пакета: {len(Listener.load_layout())} полів. Натисніть «Старт».")

        root.after(POLL_MS, self.poll)

    def note(self, message: str) -> None:
        """A journal line from the window itself (the session logs its own)."""
        self.messages.put(f"{datetime.now():%H:%M:%S}  {message}")

    # --- actions
    def change_dir(self) -> None:
        chosen = filedialog.askdirectory(initialdir=self.dir_var.get(), title="Куди зберігати записи")
        if chosen:
            self.dir_var.set(str(Path(chosen)))
            self.save_settings()

    def open_dir(self) -> None:
        path = Path(self.dir_var.get())
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(path)

    def start(self) -> None:
        try:
            port = int(self.port_var.get())
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            messagebox.showerror(APP_NAME, "Порт має бути числом від 1 до 65535.")
            return
        self.save_settings()
        try:
            self.session = CaptureSession(Path(self.dir_var.get()), port, self.messages)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Не вдалося почати запис на порту {port}:\n{exc}\n\n"
                                           "Можливо, порт уже зайнятий іншою програмою, "
                                           "або немає доступу до теки для записів.")
            return
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Не вдалося почати запис:\n{exc!r}")
            return
        self.session.start()
        self.session.log(f"Очікування телеметрії Forza на порту {port}… Записи: {self.dir_var.get()}")
        self.set_running(True)

    def stop(self) -> None:
        if self.session is not None:
            self.session.log("Зупинка збору даних…")
            self.session.stop()
            self.stop_btn.configure(state="disabled")

    def on_close(self) -> None:
        if self.session is not None and self.session.running:
            self.session.stop()
            self.session.thread.join(STOP_WAIT_S)
        self.root.destroy()

    # --- helpers
    def save_settings(self) -> None:
        self.settings.output_dir = self.dir_var.get()
        try:
            self.settings.port = int(self.port_var.get())
        except ValueError:
            pass
        try:
            self.settings.save()
        except OSError:
            pass

    def set_running(self, running: bool) -> None:
        self.running = running
        self.start_btn.configure(state="disabled" if running else "normal")
        self.stop_btn.configure(state="normal" if running else "disabled")
        self.change_btn.configure(state="disabled" if running else "normal")
        self.port_entry.configure(state="disabled" if running else "normal")
        self.state_var.set("● Йде запис" if running else "Зупинено")

    def poll(self) -> None:
        lines = []
        while True:
            try:
                lines.append(self.messages.get_nowait())
            except queue.Empty:
                break
        if lines:
            self.journal.configure(state="normal")
            self.journal.insert("end", "\n".join(lines) + "\n")
            self.journal.see("end")
            self.journal.configure(state="disabled")
        session = self.session
        if session is not None:
            stats = session.listener.recorder.stats
            current = session.listener.recorder.current_file
            self.stats_var.set(f"Отримано пакетів: {stats['received']}   збережено: {stats['stored']}   "
                               f"файл: {current.name if current else '—'}")
            if self.running and not session.running:     # the capture thread has finished
                self.set_running(False)
        self.root.after(POLL_MS, self.poll)


def self_check(report: Path) -> int:
    """`ForzaListener.exe --check <file>`: the bundle can build a Recorder from the packet layout
    and open a UDP socket. Writes "ok …" or the error to <file> (a windowed exe has no console);
    build_listener.bat runs it after every build. Exit code 0 = ok."""
    try:
        layout = Listener.load_layout()
        with tempfile.TemporaryDirectory() as tmp:
            listener = Listener.Listener("127.0.0.1", 0, tmp, log=lambda m: None)
            fields = len(listener.recorder.fields)
            listener.close()
        report.write_text(f"ok: packet {len(layout)} fields, record {fields} fields\n", encoding="utf-8")
        return 0
    except Exception as exc:
        report.write_text(f"error: {exc!r}\n", encoding="utf-8")
        return 1


def main() -> None:
    if len(sys.argv) >= 3 and sys.argv[1] == "--check":
        sys.exit(self_check(Path(sys.argv[2])))
    root = tk.Tk()
    ListenerWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
