from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from tkinter import BooleanVar, StringVar, Tk, filedialog, messagebox, ttk
from typing import Any

import yt_dlp


APP_NAME = "VideoDownloader"
COLORS = {
    "background": "#0E1411",
    "surface": "#161F1A",
    "surface_light": "#1F2B24",
    "row_alt": "#1A241E",
    "border": "#2E3D34",
    "text": "#F1F4EB",
    "muted": "#8E9C90",
    "accent": "#C8F169",
    "accent_hover": "#D8FF8D",
    "accent_dark": "#2C4220",
    "danger": "#FF8A7A",
}
BROWSERS = {
    "Aucune": None,
    "Chrome": "chrome",
    "Edge": "edge",
    "Firefox": "firefox",
    "Brave": "brave",
}
# Ordre de priorité identique à yt-dlp.
JS_RUNTIMES = ("deno", "node", "bun", "quickjs")
CODEC_PRIORITY = {"H.264": 0, "VP9": 1, "AV1": 2}
MAX_CONCURRENT_DOWNLOADS = 3
COLUMNS = (
    ("quality", "QUALITÉ", 150, "w"),
    ("resolution", "RÉSOLUTION", 110, "center"),
    ("container", "FORMAT", 80, "center"),
    ("codec", "CODEC", 80, "center"),
    ("fps", "IPS", 60, "center"),
    ("audio", "AUDIO", 170, "center"),
    ("size", "TAILLE", 100, "e"),
)


# --------------------------------------------------------------------------- #
# Logique indépendante de l'interface
# --------------------------------------------------------------------------- #


def get_config_path() -> Path:
    config_root = Path(os.environ.get("APPDATA", Path.home()))
    return config_root / APP_NAME / "config.json"


def get_default_download_dir() -> Path:
    return Path.home() / "Downloads"


def format_size(size: int | float | None) -> str:
    if not size:
        return "Inconnue"
    value = float(size)
    for unit in ("o", "Ko", "Mo", "Go", "To"):
        if value < 1024 or unit == "To":
            return f"{value:.1f} {unit}"
        value /= 1024
    return "Inconnue"


def format_duration(seconds: int | float | None) -> str:
    if not seconds:
        return ""
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def codec_name(vcodec: str | None) -> str:
    if not vcodec:
        return "?"
    lowered = vcodec.lower()
    if lowered.startswith(("avc1", "h264")):
        return "H.264"
    if lowered.startswith(("vp09", "vp9")):
        return "VP9"
    if lowered.startswith("av01"):
        return "AV1"
    if lowered.startswith(("hev1", "hvc1", "h265")):
        return "H.265"
    return vcodec.split(".")[0].upper()


def find_js_runtimes() -> dict[str, dict[str, str]]:
    """Moteurs JavaScript installés : YouTube en a besoin pour lister tous les formats."""
    found = {}
    for name in JS_RUNTIMES:
        path = shutil.which(name)
        if path:
            found[name] = {"path": path}
    return found


def make_youtube_options(
    browser_label: str, js_runtimes: dict[str, dict[str, str]] | None = None
) -> dict[str, Any]:
    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }
    if js_runtimes:
        options["js_runtimes"] = js_runtimes
    browser = BROWSERS.get(browser_label)
    if browser:
        options["cookiesfrombrowser"] = (browser,)
    return options


def _is_video(item: dict[str, Any]) -> bool:
    return item.get("vcodec") not in (None, "none", "images") and item.get("ext") != "mhtml"


def _is_audio_only(item: dict[str, Any]) -> bool:
    return item.get("vcodec") in (None, "none") and item.get("acodec") not in (None, "none")


def _size(item: dict[str, Any] | None) -> float:
    if not item:
        return 0
    return item.get("filesize") or item.get("filesize_approx") or 0


def _best_audio(formats: list[dict[str, Any]], ext: str | None = None) -> dict[str, Any] | None:
    candidates = [
        f
        for f in formats
        if _is_audio_only(f)
        and (ext is None or f.get("ext") == ext)
        and "drc" not in str(f.get("format_id", ""))
    ]
    return max(candidates, key=lambda f: f.get("abr") or f.get("tbr") or 0, default=None)


def build_choices(
    formats: list[dict[str, Any]], has_ffmpeg: bool, show_all: bool = False
) -> list[dict[str, Any]]:
    """Transforme la liste brute de yt-dlp en choix lisibles."""
    videos = [f for f in formats if _is_video(f)]
    if not show_all:
        direct = [f for f in videos if not str(f.get("protocol", "")).startswith("m3u8")]
        videos = direct or videos
        best_by_height: dict[int, dict[str, Any]] = {}
        for item in videos:
            height = item.get("height") or 0
            current = best_by_height.get(height)
            key = (
                -CODEC_PRIORITY.get(codec_name(item.get("vcodec")), 9),
                item.get("acodec") not in (None, "none"),
                item.get("tbr") or 0,
            )
            if current is None or key > current[0]:
                best_by_height[height] = (key, item)  # type: ignore[assignment]
        videos = [entry[1] for entry in best_by_height.values()]  # type: ignore[index]

    videos.sort(
        key=lambda f: (
            f.get("height") or 0,
            f.get("fps") or 0,
            -CODEC_PRIORITY.get(codec_name(f.get("vcodec")), 9),
            f.get("tbr") or 0,
        ),
        reverse=True,
    )

    audio_m4a = _best_audio(formats, "m4a")
    audio_any = _best_audio(formats)
    choices: list[dict[str, Any]] = []

    if videos:
        with_audio = [f for f in videos if f.get("acodec") not in (None, "none")]
        top = videos[0] if has_ffmpeg or not with_audio else with_audio[0]
        if has_ffmpeg:
            selector = "bv*[ext=mp4]+ba[ext=m4a]/bv*+ba/b"
            audio = "Ajouté automatiquement"
        else:
            selector = "b[ext=mp4]/b"
            audio = "Inclus"
        choices.append(
            {
                "quality": "★  Meilleure qualité",
                "resolution": f"jusqu’à {top.get('height')}p" if top.get("height") else "auto",
                "container": "MP4",
                "codec": "auto",
                "fps": "",
                "audio": audio,
                "size": "",
                "selector": selector,
                "merge": "mp4/mkv",
                "kind": "auto",
            }
        )

    for item in videos:
        has_audio = item.get("acodec") not in (None, "none")
        ext = item.get("ext") or "?"
        width, height = item.get("width"), item.get("height")
        fps = item.get("fps")
        size = _size(item)
        if has_audio:
            selector, merge, audio = item["format_id"], None, "Inclus"
        else:
            audio_ext = "m4a" if ext == "mp4" else "webm"
            partner = _best_audio(formats, audio_ext) or audio_any
            size = size + _size(partner) if size else 0
            selector = f"{item['format_id']}+ba[ext={audio_ext}]/{item['format_id']}+ba"
            merge = f"{ext}/mkv"
            audio = "Ajouté automatiquement" if has_ffmpeg else "⚠ FFmpeg requis"
        choices.append(
            {
                "quality": f"{height}p" if height else (item.get("format_note") or "Vidéo"),
                "resolution": f"{width}×{height}" if width and height else "—",
                "container": ext.upper(),
                "codec": codec_name(item.get("vcodec")),
                "fps": f"{fps:g}" if isinstance(fps, (int, float)) else "—",
                "audio": audio,
                "size": format_size(size) if size else "—",
                "selector": selector,
                "merge": merge,
                "kind": "video",
                "needs_ffmpeg": not has_audio,
                "format_id": item["format_id"],
            }
        )

    audio_pick = audio_m4a or audio_any
    if audio_pick:
        abr = audio_pick.get("abr")
        choices.append(
            {
                "quality": "♪  Audio seulement",
                "resolution": f"{abr:.0f} kb/s" if isinstance(abr, (int, float)) else "—",
                "container": (audio_pick.get("ext") or "?").upper(),
                "codec": (audio_pick.get("acodec") or "?").split(".")[0].upper(),
                "fps": "",
                "audio": "Piste audio",
                "size": format_size(_size(audio_pick)) if _size(audio_pick) else "—",
                "selector": "ba[ext=m4a]/ba",
                "merge": None,
                "kind": "audio",
            }
        )
    return choices


def explain_download_error(error: Exception | str) -> str:
    lowered = str(error).lower()
    if "ffmpeg" in lowered:
        return (
            "FFmpeg est nécessaire pour assembler la vidéo et l’audio. "
            "Installez-le (winget install ffmpeg) ou choisissez un format avec audio inclus."
        )
    if "403" in lowered or "forbidden" in lowered:
        return (
            "YouTube a refusé le flux (HTTP 403). Actualisez les formats et "
            "réessayez. Si la vidéo est accessible dans votre navigateur, "
            "sélectionnez ce navigateur dans « Session YouTube » puis fermez-le "
            "avant de relancer."
        )
    if "unavailable" in lowered or "private video" in lowered:
        return (
            "Cette vidéo est indisponible. Elle peut être privée, supprimée, "
            "restreinte dans votre région ou nécessiter une connexion."
        )
    if "could not copy" in lowered and "cookie" in lowered:
        return "Fermez complètement le navigateur choisi dans « Session YouTube », puis réessayez."
    return f"Échec : {error}"


def open_folder(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def download_output_template(directory: Path, choice: dict[str, Any]) -> str:
    filename = "%(title)s [%(format_id)s].%(ext)s"
    if choice["kind"] != "audio":
        filename = "%(title)s [%(height)sp] [%(format_id)s].%(ext)s"
    return str(directory / filename)


def enable_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass


# --------------------------------------------------------------------------- #
# Interface
# --------------------------------------------------------------------------- #


class VideoDownloaderApp:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title("Video Desk — Téléchargeur vidéo")
        self.scale = max(1.0, root.winfo_fpixels("1i") / 96)
        screen_w, screen_h = root.winfo_screenwidth(), root.winfo_screenheight()
        width = min(self._px(1020), screen_w - self._px(40))
        height = min(self._px(740), screen_h - self._px(90))
        self.root.minsize(min(self._px(820), width), min(self._px(560), height))
        top = max(0, (screen_h - height) // 2 - self._px(30))
        self.root.geometry(f"{width}x{height}+{(screen_w - width) // 2}+{top}")

        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.fetching = False
        self.raw_formats: list[dict[str, Any]] = []
        self.choices: list[dict[str, Any]] = []
        self.current_url = ""
        self.next_job_id = 1
        self.jobs: dict[int, dict[str, str]] = {}
        self.download_slots = threading.BoundedSemaphore(MAX_CONCURRENT_DOWNLOADS)
        self.has_ffmpeg = shutil.which("ffmpeg") is not None
        self.js_runtimes = find_js_runtimes()
        self.config_path = get_config_path()
        self.download_dir = self._load_download_dir()
        self.last_output_dir: Path | None = None

        self.url_var = StringVar()
        self.directory_var = StringVar(value=str(self.download_dir))
        self.browser_var = StringVar(value="Aucune")
        self.show_all_var = BooleanVar(value=False)
        self.title_var = StringVar(value="Aucune vidéo analysée")
        self.meta_var = StringVar(value="Collez un lien puis cliquez sur « Analyser » (ou Entrée).")
        self.status_var = StringVar(value="Prêt.")
        self.progress_var = StringVar(value="")

        self._configure_theme()
        self._build_ui()
        self._show_environment_hints()
        self.root.after(100, self._process_events)
        self.url_entry.focus_set()

    def _px(self, value: float) -> int:
        return int(value * self.scale)

    # ----------------------------------------------------------------- thème
    def _configure_theme(self) -> None:
        c = COLORS
        self.root.configure(bg=c["background"])
        self.root.option_add("*TCombobox*Listbox.background", c["surface_light"])
        self.root.option_add("*TCombobox*Listbox.foreground", c["text"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", c["accent_dark"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", c["accent"])
        self.root.option_add("*TCombobox*Listbox.font", ("Segoe UI", 10))

        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", background=c["background"], foreground=c["text"], font=("Segoe UI", 10))
        style.configure("App.TFrame", background=c["background"])
        style.configure("Card.TFrame", background=c["surface"])

        def label(name: str, bg: str, fg: str, font: tuple) -> None:
            style.configure(name, background=c[bg], foreground=c[fg], font=font)

        label("Eyebrow.TLabel", "background", "accent", ("Segoe UI", 9, "bold"))
        label("Title.TLabel", "background", "text", ("Segoe UI Semibold", 19))
        label("Section.TLabel", "surface", "text", ("Segoe UI Semibold", 11))
        label("Field.TLabel", "surface", "muted", ("Segoe UI", 9, "bold"))
        label("Hint.TLabel", "surface", "muted", ("Segoe UI", 9))
        label("Warn.TLabel", "surface", "danger", ("Segoe UI", 9))
        label("Status.TLabel", "surface", "text", ("Segoe UI Semibold", 10))
        label("Progress.TLabel", "surface", "muted", ("Segoe UI", 9))

        style.configure(
            "TEntry",
            fieldbackground=c["background"],
            foreground=c["text"],
            insertcolor=c["accent"],
            bordercolor=c["border"],
            lightcolor=c["border"],
            darkcolor=c["border"],
            selectbackground=c["accent_dark"],
            selectforeground=c["accent"],
            padding=(10, 8),
        )
        style.map(
            "TEntry",
            bordercolor=[("focus", c["accent"])],
            lightcolor=[("focus", c["accent"])],
            darkcolor=[("focus", c["accent"])],
            fieldbackground=[("readonly", c["background"])],
            foreground=[("readonly", c["muted"])],
        )
        style.configure(
            "TButton",
            background=c["surface_light"],
            foreground=c["text"],
            bordercolor=c["border"],
            lightcolor=c["surface_light"],
            darkcolor=c["surface_light"],
            focuscolor=c["surface_light"],
            borderwidth=1,
            padding=(14, 8),
            font=("Segoe UI Semibold", 9),
        )
        style.map(
            "TButton",
            background=[("disabled", c["surface"]), ("active", c["border"])],
            foreground=[("disabled", c["muted"])],
        )
        style.configure(
            "Accent.TButton",
            background=c["accent"],
            foreground=c["background"],
            bordercolor=c["accent"],
            lightcolor=c["accent"],
            darkcolor=c["accent"],
            focuscolor=c["accent"],
            padding=(18, 8),
            font=("Segoe UI Semibold", 10),
        )
        style.map(
            "Accent.TButton",
            background=[("disabled", c["surface_light"]), ("active", c["accent_hover"])],
            foreground=[("disabled", c["muted"])],
            bordercolor=[("disabled", c["border"])],
            lightcolor=[("disabled", c["surface_light"])],
            darkcolor=[("disabled", c["surface_light"])],
        )
        style.configure(
            "TCombobox",
            fieldbackground=c["background"],
            background=c["surface_light"],
            foreground=c["text"],
            arrowcolor=c["accent"],
            bordercolor=c["border"],
            lightcolor=c["border"],
            darkcolor=c["border"],
            padding=(8, 6),
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", c["background"])],
            foreground=[("readonly", c["text"])],
            selectbackground=[("readonly", c["background"])],
            selectforeground=[("readonly", c["text"])],
            bordercolor=[("focus", c["accent"])],
        )
        style.configure(
            "TCheckbutton",
            background=c["surface"],
            foreground=c["muted"],
            indicatorbackground=c["background"],
            indicatorforeground=c["accent"],
            font=("Segoe UI", 9),
        )
        style.map(
            "TCheckbutton",
            background=[("active", c["surface"])],
            indicatorbackground=[("selected", c["accent_dark"])],
        )
        style.configure(
            "Formats.Treeview",
            background=c["surface"],
            fieldbackground=c["surface"],
            foreground=c["text"],
            bordercolor=c["border"],
            lightcolor=c["border"],
            darkcolor=c["border"],
            borderwidth=1,
            rowheight=self._px(34),
            font=("Segoe UI", 10),
        )
        style.configure(
            "Formats.Treeview.Heading",
            background=c["surface_light"],
            foreground=c["muted"],
            bordercolor=c["surface_light"],
            lightcolor=c["surface_light"],
            darkcolor=c["surface_light"],
            relief="flat",
            padding=(10, 8),
            font=("Segoe UI Semibold", 8),
        )
        style.map("Formats.Treeview.Heading", background=[("active", c["surface_light"])])
        style.map(
            "Formats.Treeview",
            background=[("selected", c["accent_dark"])],
            foreground=[("selected", c["accent"])],
        )
        style.layout("Formats.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        style.configure(
            "Vertical.TScrollbar",
            background=c["surface_light"],
            troughcolor=c["surface"],
            bordercolor=c["surface"],
            lightcolor=c["surface_light"],
            darkcolor=c["surface_light"],
            arrowcolor=c["muted"],
            gripcount=0,
        )
        style.map("Vertical.TScrollbar", background=[("active", c["border"])])
        style.configure(
            "Horizontal.TProgressbar",
            background=c["accent"],
            troughcolor=c["surface_light"],
            bordercolor=c["surface_light"],
            lightcolor=c["accent"],
            darkcolor=c["accent"],
            thickness=self._px(6),
        )

    # ------------------------------------------------------------ préférences
    def _load_download_dir(self) -> Path:
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
            value = data.get("download_dir")
            if value:
                return Path(value).expanduser()
        except FileNotFoundError:
            pass
        except (OSError, json.JSONDecodeError, AttributeError) as error:
            error_text = str(error)
            self.root.after(
                100,
                lambda: messagebox.showwarning(
                    "Préférences",
                    f"Impossible de lire les préférences : {error_text}",
                    parent=self.root,
                ),
            )
        return get_default_download_dir()

    # ---------------------------------------------------------------- layout
    def _build_ui(self) -> None:
        px = self._px
        container = ttk.Frame(self.root, padding=(px(24), px(16)), style="App.TFrame")
        container.pack(fill="both", expand=True)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(2, weight=1)

        # En-tête
        header = ttk.Frame(container, style="App.TFrame")
        header.grid(row=0, column=0, sticky="ew", pady=(0, px(10)))
        ttk.Label(header, text="VIDEO DESK  /  YOUTUBE", style="Eyebrow.TLabel").pack(anchor="w")
        ttk.Label(header, text="Téléchargez. À votre façon.", style="Title.TLabel").pack(anchor="w")

        # Carte source : lien, dossier, session
        source = ttk.Frame(container, padding=px(14), style="Card.TFrame")
        source.grid(row=1, column=0, sticky="ew")
        source.columnconfigure(1, weight=1)

        ttk.Label(source, text="LIEN", style="Field.TLabel").grid(row=0, column=0, sticky="w", padx=(0, px(12)))
        self.url_entry = ttk.Entry(source, textvariable=self.url_var, font=("Segoe UI", 10))
        self.url_entry.grid(row=0, column=1, sticky="ew", padx=(0, px(8)))
        self.url_entry.bind("<Return>", lambda _e: self.fetch_formats())
        self.fetch_button = ttk.Button(source, text="Analyser  →", style="Accent.TButton", command=self.fetch_formats)
        self.fetch_button.grid(row=0, column=2, sticky="ew")

        ttk.Label(source, text="DOSSIER", style="Field.TLabel").grid(row=1, column=0, sticky="w", pady=(px(10), 0))
        options_row = ttk.Frame(source, style="Card.TFrame")
        options_row.grid(row=1, column=1, columnspan=2, sticky="ew", pady=(px(10), 0))
        options_row.columnconfigure(0, weight=1)
        self.directory_entry = ttk.Entry(options_row, textvariable=self.directory_var, state="readonly")
        self.directory_entry.grid(row=0, column=0, sticky="ew", padx=(0, px(8)))
        self.folder_button = ttk.Button(options_row, text="Parcourir…", command=self.choose_directory)
        self.folder_button.grid(row=0, column=1, padx=(0, px(20)))
        ttk.Label(options_row, text="COOKIES", style="Field.TLabel").grid(row=0, column=2, padx=(0, px(8)))
        self.browser_select = ttk.Combobox(
            options_row, textvariable=self.browser_var, values=list(BROWSERS), state="readonly", width=9
        )
        self.browser_select.grid(row=0, column=3)
        self.browser_select.bind("<<ComboboxSelected>>", lambda _e: self.browser_select.selection_clear())

        # Carte formats (avec infos vidéo)
        formats_card = ttk.Frame(container, padding=px(14), style="Card.TFrame")
        formats_card.grid(row=2, column=0, sticky="nsew", pady=(px(10), 0))
        formats_card.columnconfigure(0, weight=1)
        formats_card.rowconfigure(1, weight=1)

        top = ttk.Frame(formats_card, style="Card.TFrame")
        top.grid(row=0, column=0, sticky="ew", pady=(0, px(10)))
        top.columnconfigure(0, weight=1)
        ttk.Label(top, textvariable=self.title_var, style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(top, textvariable=self.meta_var, style="Hint.TLabel").grid(row=1, column=0, sticky="w")
        ttk.Checkbutton(
            top,
            text="Afficher tous les formats",
            variable=self.show_all_var,
            command=self._refresh_choices,
        ).grid(row=0, column=1, rowspan=2, sticky="e")

        list_frame = ttk.Frame(formats_card, style="Card.TFrame")
        list_frame.grid(row=1, column=0, sticky="nsew")
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        self.format_list = ttk.Treeview(
            list_frame,
            columns=[col[0] for col in COLUMNS],
            show="headings",
            selectmode="extended",
            style="Formats.Treeview",
            height=6,
        )
        for key, title, width, anchor in COLUMNS:
            self.format_list.heading(key, text=title, anchor=anchor)
            self.format_list.column(
                key, width=px(width), minwidth=px(50), anchor=anchor, stretch=key in ("quality", "audio")
            )
        self.format_list.tag_configure("odd", background=COLORS["row_alt"])
        self.format_list.tag_configure("special", foreground=COLORS["accent"])
        self.format_list.tag_configure("warn", foreground=COLORS["danger"])
        self.format_list.grid(row=0, column=0, sticky="nsew")
        self.format_list.bind("<<TreeviewSelect>>", lambda _e: self._update_buttons())
        self.format_list.bind("<Double-1>", lambda _e: self.download_selected())
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.format_list.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.format_list.configure(yscrollcommand=scrollbar.set)

        self.env_hint = ttk.Label(formats_card, text="", style="Hint.TLabel", wraplength=px(900), justify="left")
        self.env_hint.grid(row=2, column=0, sticky="w", pady=(px(8), 0))

        # Barre d'action : statut, progression, boutons
        action = ttk.Frame(container, padding=(px(14), px(12)), style="Card.TFrame")
        action.grid(row=4, column=0, sticky="ew", pady=(px(10), 0))
        action.columnconfigure(0, weight=1)
        ttk.Label(action, textvariable=self.status_var, style="Status.TLabel", wraplength=px(640), justify="left").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(action, textvariable=self.progress_var, style="Progress.TLabel").grid(row=1, column=0, sticky="w")
        buttons = ttk.Frame(action, style="Card.TFrame")
        buttons.grid(row=0, column=1, rowspan=2, sticky="e")
        self.open_button = ttk.Button(buttons, text="Ouvrir le dossier", command=self.open_output_folder)
        self.open_button.pack(side="left", padx=(0, px(8)))
        self.download_button = ttk.Button(
            buttons,
            text="↓  Télécharger la sélection",
            style="Accent.TButton",
            command=self.download_selected,
            state="disabled",
        )
        self.download_button.pack(side="left")

        jobs_card = ttk.Frame(container, padding=px(14), style="Card.TFrame")
        jobs_card.grid(row=3, column=0, sticky="ew", pady=(px(10), 0))
        jobs_card.columnconfigure(0, weight=1)
        jobs_card.rowconfigure(1, weight=1)
        ttk.Label(jobs_card, text="TÉLÉCHARGEMENTS", style="Field.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, px(6))
        )
        self.jobs_list = ttk.Treeview(
            jobs_card,
            columns=("item", "progress", "state"),
            show="headings",
            selectmode="browse",
            style="Formats.Treeview",
            height=3,
        )
        for key, title, width, anchor in (
            ("item", "VIDÉO / FORMAT", 520, "w"),
            ("progress", "PROGRESSION", 130, "center"),
            ("state", "ÉTAT", 220, "w"),
        ):
            self.jobs_list.heading(key, text=title, anchor=anchor)
            self.jobs_list.column(key, width=px(width), minwidth=px(80), anchor=anchor, stretch=key == "item")
        self.jobs_list.tag_configure("failed", foreground=COLORS["danger"])
        self.jobs_list.tag_configure("done", foreground=COLORS["accent"])
        self.jobs_list.grid(row=1, column=0, sticky="nsew")
        jobs_scrollbar = ttk.Scrollbar(jobs_card, orient="vertical", command=self.jobs_list.yview)
        jobs_scrollbar.grid(row=1, column=1, sticky="ns")
        self.jobs_list.configure(yscrollcommand=jobs_scrollbar.set)

    def _show_environment_hints(self) -> None:
        hints = []
        style = "Hint.TLabel"
        if not self.js_runtimes:
            hints.append(
                "⚠ Aucun moteur JavaScript (Node.js ou Deno) détecté : YouTube ne proposera que "
                "très peu de formats. Installez Node.js (winget install OpenJS.NodeJS) puis relancez."
            )
            style = "Warn.TLabel"
        if not self.has_ffmpeg:
            hints.append(
                "⚠ FFmpeg absent : les qualités supérieures à 360p ne pourront pas être assemblées "
                "avec l’audio (winget install ffmpeg)."
            )
            style = "Warn.TLabel"
        hints.append("Ctrl ou Maj + clic pour choisir plusieurs formats.")
        hints.append("Enregistrez uniquement les vidéos que vous êtes autorisé(e) à télécharger.")
        self.env_hint.configure(text="\n".join(hints), style=style)

    # ------------------------------------------------------------- actions
    def _youtube_options(self) -> dict[str, Any]:
        return make_youtube_options(self.browser_var.get(), self.js_runtimes)

    def _update_buttons(self) -> None:
        self.fetch_button.configure(state="disabled" if self.fetching else "normal")
        self.folder_button.configure(state="normal")
        can_download = not self.fetching and bool(self.format_list.selection())
        self.download_button.configure(state="normal" if can_download else "disabled")
        self.open_button.configure(state="normal")

    def _set_fetching(self, fetching: bool) -> None:
        self.fetching = fetching
        self.root.configure(cursor="watch" if fetching else "")
        self._update_buttons()

    def fetch_formats(self) -> None:
        if self.fetching:
            return
        url = self.url_var.get().strip()
        if not url:
            messagebox.showerror("Lien manquant", "Collez le lien de la vidéo.", parent=self.root)
            return
        self._set_fetching(True)
        self.status_var.set("Analyse de la vidéo…")
        self.progress_var.set("Ctrl ou Maj + clic pour sélectionner plusieurs formats.")
        self.title_var.set("Analyse en cours…")
        self.meta_var.set(url)
        self.format_list.delete(*self.format_list.get_children())
        self.raw_formats = []
        self.choices = []
        self.current_url = ""
        threading.Thread(target=self._fetch_worker, args=(url, self._youtube_options()), daemon=True).start()

    def _fetch_worker(self, url: str, options: dict[str, Any]) -> None:
        try:
            with yt_dlp.YoutubeDL(options) as downloader:
                info = downloader.extract_info(url, download=False)
            self.events.put(("formats", (url, info)))
        except Exception as error:  # noqa: BLE001 - message affiché à l'utilisateur
            self.events.put(("fetch_error", str(error)))

    def _refresh_choices(self) -> None:
        self.choices = build_choices(self.raw_formats, self.has_ffmpeg, self.show_all_var.get())
        self.format_list.delete(*self.format_list.get_children())
        for index, choice in enumerate(self.choices):
            tags = ["odd"] if index % 2 else []
            if choice["kind"] != "video":
                tags.append("special")
            elif choice.get("needs_ffmpeg") and not self.has_ffmpeg:
                tags.append("warn")
            values = [f"  {choice[key]}" if key == "quality" else choice[key] for key, *_ in COLUMNS]
            self.format_list.insert("", "end", iid=str(index), values=values, tags=tags)
        if self.choices:
            self.format_list.selection_set("0")
            self.format_list.focus("0")
        self._update_buttons()

    def choose_directory(self) -> None:
        selected = filedialog.askdirectory(
            title="Choisir le dossier de téléchargement",
            initialdir=self.directory_var.get(),
            mustexist=True,
            parent=self.root,
        )
        if not selected:
            return
        new_directory = Path(selected)
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            self.config_path.write_text(json.dumps({"download_dir": str(new_directory)}, indent=2), encoding="utf-8")
        except OSError as error:
            messagebox.showerror(
                "Préférences", f"Impossible d’enregistrer le dossier par défaut :\n{error}", parent=self.root
            )
            return
        self.download_dir = new_directory
        self.directory_var.set(str(new_directory))

    def open_output_folder(self) -> None:
        target = self.last_output_dir or self.download_dir
        try:
            target.mkdir(parents=True, exist_ok=True)
            open_folder(target)
        except OSError as error:
            messagebox.showerror("Dossier", f"Impossible d’ouvrir le dossier :\n{error}", parent=self.root)

    def download_selected(self) -> None:
        if self.fetching:
            return
        selected = self.format_list.selection()
        if not selected:
            messagebox.showinfo("Format requis", "Sélectionnez une qualité.", parent=self.root)
            return
        choices = [self.choices[int(item)] for item in selected]
        if any(choice.get("needs_ffmpeg") for choice in choices) and not self.has_ffmpeg:
            proceed = messagebox.askyesno(
                "FFmpeg requis",
                "Au moins un de ces formats ne contient pas d’audio et FFmpeg n’est pas installé : "
                "la vidéo et l’audio seront enregistrés séparément.\n\nContinuer ?",
                parent=self.root,
            )
            if not proceed:
                return
        for choice in choices:
            job_id = self.next_job_id
            self.next_job_id += 1
            label = f"{self.title_var.get()} — {choice['quality'].strip('★♪ ')}"
            self.jobs[job_id] = {"label": label, "state": "En attente", "progress": "0 %"}
            self.jobs_list.insert(
                "", "end", iid=str(job_id), values=(label, "0 %", "En attente")
            )
            threading.Thread(
                target=self._download_worker,
                args=(
                    job_id,
                    self.current_url,
                    choice,
                    self.download_dir,
                    self._youtube_options(),
                ),
                daemon=True,
            ).start()
        self._refresh_job_status()

    def _download_worker(
        self, job_id: int, url: str, choice: dict[str, Any], directory: Path, options: dict[str, Any]
    ) -> None:
        with self.download_slots:
            self.events.put(("job_started", job_id))

            def progress_hook(data: dict[str, Any]) -> None:
                if data.get("status") == "downloading":
                    total = data.get("total_bytes") or data.get("total_bytes_estimate")
                    downloaded = data.get("downloaded_bytes", 0)
                    percent = downloaded * 100 / total if total else 0
                    parts = [f"{percent:.1f} %"]
                    if total:
                        parts.append(f"{format_size(downloaded)} / {format_size(total)}")
                    if data.get("speed"):
                        parts.append(f"{format_size(data['speed'])}/s")
                    if data.get("eta") is not None:
                        parts.append(f"reste {format_duration(data['eta']) or '0:00'}")
                    self.events.put(("job_progress", (job_id, percent, "  ·  ".join(parts))))
                elif data.get("status") == "finished":
                    self.events.put(("job_progress", (job_id, 100, "Fichier reçu, finalisation…")))

            try:
                directory.mkdir(parents=True, exist_ok=True)
                options = {
                    **options,
                    "format": choice["selector"],
                    "outtmpl": download_output_template(directory, choice),
                    "progress_hooks": [progress_hook],
                    "windowsfilenames": True,
                }
                if choice.get("merge") and self.has_ffmpeg:
                    options["merge_output_format"] = choice["merge"]
                with yt_dlp.YoutubeDL(options) as downloader:
                    downloader.download([url])
                self.events.put(("job_done", (job_id, directory)))
            except Exception as error:  # noqa: BLE001
                self.events.put(("job_error", (job_id, str(error))))

    def _refresh_job_status(self) -> None:
        queued_or_running = sum(job["state"] in ("En attente", "Téléchargement") for job in self.jobs.values())
        failures = sum(job["state"].startswith("Échec") for job in self.jobs.values())
        if queued_or_running:
            self.status_var.set(
                f"{queued_or_running} téléchargement(s) en cours ou en attente · "
                f"{MAX_CONCURRENT_DOWNLOADS} simultanés maximum."
            )
        elif failures:
            self.status_var.set(f"{failures} téléchargement(s) en échec — consultez la liste.")
        elif self.jobs:
            self.status_var.set("✓ Tous les téléchargements sont terminés.")

    def _update_job(self, job_id: int, state: str, progress: str | None = None) -> None:
        job = self.jobs.get(job_id)
        if not job:
            return
        job["state"] = state
        if progress is not None:
            job["progress"] = progress
        tags = ("failed",) if state.startswith("Échec") else ("done",) if state == "Terminé" else ()
        self.jobs_list.item(
            str(job_id), values=(job["label"], job["progress"], state), tags=tags
        )
        self._refresh_job_status()

    def _process_events(self) -> None:
        while True:
            try:
                event, payload = self.events.get_nowait()
            except queue.Empty:
                break

            if event == "formats":
                url, info = payload
                self.current_url = url
                self.raw_formats = info.get("formats") or []
                self.title_var.set(info.get("title") or "Vidéo")
                meta = [
                    info.get("channel") or info.get("uploader") or "",
                    format_duration(info.get("duration")),
                    f"{info['view_count']:,} vues".replace(",", " ") if info.get("view_count") else "",
                ]
                self.meta_var.set("  ·  ".join(part for part in meta if part))
                self._set_fetching(False)
                self._refresh_choices()
                count = sum(1 for c in self.choices if c["kind"] == "video")
                if any(job["state"] in ("En attente", "Téléchargement") for job in self.jobs.values()):
                    self._refresh_job_status()
                else:
                    self.status_var.set(
                        f"{count} qualité(s) disponible(s). Sélectionnez-en une puis téléchargez."
                        if self.choices
                        else "Aucun format téléchargeable trouvé."
                    )
            elif event == "job_started":
                self._update_job(payload, "Téléchargement")
            elif event == "job_progress":
                job_id, _percent, text = payload
                self._update_job(job_id, "Téléchargement", text)
            elif event == "job_done":
                job_id, directory = payload
                self.last_output_dir = directory
                self._update_job(job_id, "Terminé", "100 %")
            elif event == "fetch_error":
                self.title_var.set("Analyse impossible")
                self.meta_var.set("Vérifiez le lien ou essayez avec une session navigateur.")
                self.status_var.set(explain_download_error(payload))
                self.progress_var.set(str(payload).splitlines()[0][:160] if payload else "")
                self._set_fetching(False)
            elif event == "job_error":
                job_id, error = payload
                self._update_job(job_id, f"Échec : {explain_download_error(error)}")
        self.root.after(100, self._process_events)


def main() -> None:
    enable_dpi_awareness()
    root = Tk()
    VideoDownloaderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
