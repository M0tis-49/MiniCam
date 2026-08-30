from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Event, Thread
from tkinter import BooleanVar, DoubleVar, IntVar, Label, PhotoImage, StringVar, Tk, Toplevel
from tkinter import ttk


APP_NAME = "MiniCam"
DEFAULT_CAMERA_INDEX = 1
DEFAULT_WIDTH = 320
DEFAULT_HEIGHT = 240
MIN_WIDTH = 220
MIN_HEIGHT = 180
FRAME_DELAY_MS = 16

APP_DIR = Path(__file__).resolve().parent
LOG_PATH = APP_DIR / "minicam.log"
SETTINGS_PATH = APP_DIR / "minicam_settings.json"

SIZE_PRESETS = {
    "Petit": (320, 240),
    "Moyen": (480, 360),
    "Grand": (640, 480),
}

DEFAULT_SETTINGS = {
    "camera_index": DEFAULT_CAMERA_INDEX,
    "window_width": DEFAULT_WIDTH,
    "window_height": DEFAULT_HEIGHT,
    "resizable": True,
    "always_on_top": True,
    "mirror": True,
    "fullscreen": False,
    "mini_mode": False,
    "drag_mini": True,
    "brightness": 0,
    "contrast": 100,
    "saturation": 100,
    "zoom": 100,
    "target_fps": 30,
    "save_settings": True,
    "live_preview": True,
}

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


def bounded_int(value, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def get_var_value(variable, default):
    try:
        return variable.get()
    except Exception:
        return default


def load_settings() -> dict:
    settings = DEFAULT_SETTINGS.copy()
    if not SETTINGS_PATH.exists():
        return settings

    try:
        saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        logging.exception("Settings could not be loaded")
        return settings

    if isinstance(saved, dict):
        settings.update(saved)

    return clean_settings(settings)


def clean_settings(settings: dict) -> dict:
    cleaned = DEFAULT_SETTINGS.copy()
    cleaned["camera_index"] = bounded_int(
        settings.get("camera_index"),
        DEFAULT_CAMERA_INDEX,
        0,
        9,
    )
    cleaned["window_width"] = bounded_int(
        settings.get("window_width"),
        DEFAULT_WIDTH,
        MIN_WIDTH,
        1920,
    )
    cleaned["window_height"] = bounded_int(
        settings.get("window_height"),
        DEFAULT_HEIGHT,
        MIN_HEIGHT,
        1080,
    )
    cleaned["brightness"] = bounded_int(settings.get("brightness"), 0, -100, 100)
    cleaned["contrast"] = bounded_int(settings.get("contrast"), 100, 50, 200)
    cleaned["saturation"] = bounded_int(settings.get("saturation"), 100, 0, 200)
    cleaned["zoom"] = bounded_int(settings.get("zoom"), 100, 100, 300)
    cleaned["target_fps"] = bounded_int(settings.get("target_fps"), 30, 5, 60)

    cleaned["resizable"] = bool(settings.get("resizable", True))
    cleaned["always_on_top"] = bool(settings.get("always_on_top", True))
    cleaned["mirror"] = bool(settings.get("mirror", True))
    cleaned["fullscreen"] = bool(settings.get("fullscreen", False))
    cleaned["mini_mode"] = bool(settings.get("mini_mode", False))
    cleaned["drag_mini"] = bool(settings.get("drag_mini", True))
    cleaned["save_settings"] = bool(settings.get("save_settings", True))
    cleaned["live_preview"] = bool(settings.get("live_preview", True))
    return cleaned


class MiniCamApp:
    def __init__(self, camera_index: int | None = None) -> None:
        self.settings = load_settings()
        if camera_index is not None:
            self.settings["camera_index"] = camera_index

        self.camera_index = int(self.settings["camera_index"])
        self.always_on_top = bool(self.settings["always_on_top"])
        self.mirror = bool(self.settings["mirror"])
        self.fullscreen = bool(self.settings["fullscreen"])
        self.mini_mode = bool(self.settings["mini_mode"])
        self.drag_mini = bool(self.settings["drag_mini"])
        self.brightness = int(self.settings["brightness"])
        self.contrast = int(self.settings["contrast"])
        self.saturation = int(self.settings["saturation"])
        self.zoom = int(self.settings["zoom"])
        self.target_fps = int(self.settings["target_fps"])
        self.drag_start = (0, 0)

        self.root = Tk()
        self.root.title(APP_NAME)
        self.root.geometry(f"{self.settings['window_width']}x{self.settings['window_height']}")
        self.root.minsize(MIN_WIDTH, MIN_HEIGHT)
        self.root.configure(bg="#111111")

        self.video_label = Label(
            self.root,
            anchor="center",
            background="#0b0b0b",
            foreground="#f2f2f2",
            text="Ouverture de la camera...",
        )
        self.video_label.pack(fill="both", expand=True)

        self.cv2 = None
        self.np = None
        self.photo = None
        self.frame_queue: Queue = Queue(maxsize=1)
        self.message_queue: Queue = Queue()
        self.app_stop_event = Event()
        self.camera_stop_event: Event | None = None
        self.camera_thread: Thread | None = None
        self.camera_generation = 0
        self.settings_window: Toplevel | None = None

        self._bind_events()
        self._apply_window_settings()
        self._start_camera()
        self._tick()

    def _bind_events(self) -> None:
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.bind("<Escape>", lambda _event: self._close())
        self.root.bind("<F10>", lambda _event: self._open_settings())
        self.root.bind("<F11>", lambda _event: self._toggle_fullscreen())
        self.root.bind("<p>", lambda _event: self._open_settings())
        self.root.bind("<P>", lambda _event: self._open_settings())
        self.video_label.bind("<Double-Button-1>", lambda _event: self._open_settings())
        self.video_label.bind("<ButtonPress-1>", self._start_drag)
        self.video_label.bind("<B1-Motion>", self._drag_window)

    def _start_drag(self, event) -> None:
        self.drag_start = (event.x, event.y)

    def _drag_window(self, event) -> None:
        if not self.mini_mode or not self.drag_mini or self.fullscreen:
            return

        x = self.root.winfo_pointerx() - self.drag_start[0]
        y = self.root.winfo_pointery() - self.drag_start[1]
        self.root.geometry(f"+{x}+{y}")

    def _start_camera(self) -> None:
        if self.camera_stop_event is not None:
            self.camera_stop_event.set()

        self._clear_queues()
        self.video_label.configure(image="", text="Ouverture de la camera...")

        self.camera_generation += 1
        camera_stop_event = Event()
        self.camera_stop_event = camera_stop_event
        self.camera_thread = Thread(
            target=self._camera_loop,
            args=(self.camera_index, camera_stop_event, self.camera_generation),
            daemon=True,
        )
        self.camera_thread.start()

    def _clear_queues(self) -> None:
        for queue in (self.frame_queue, self.message_queue):
            while True:
                try:
                    queue.get_nowait()
                except Empty:
                    break

    def _camera_loop(self, camera_index: int, camera_stop_event: Event, generation: int) -> None:
        try:
            import cv2
            import numpy as np
        except ModuleNotFoundError:
            logging.exception("Camera dependencies are not installed")
            self.message_queue.put((generation, "Installation incomplete : lance install.bat"))
            return
        except Exception:
            logging.exception("Camera dependencies failed to load")
            self.message_queue.put((generation, "Erreur au chargement de la camera"))
            return

        self.cv2 = cv2
        self.np = np
        capture = self._open_camera(cv2, camera_index, camera_stop_event, generation)
        if capture is None:
            return

        try:
            while not self.app_stop_event.is_set() and not camera_stop_event.is_set():
                started_at = time.perf_counter()
                ok, frame = capture.read()
                if ok:
                    frame = self._process_frame(cv2, np, frame)
                    self._put_latest_frame(generation, frame)
                else:
                    self.message_queue.put((generation, "Camera indisponible"))
                    time.sleep(0.2)

                delay = max(0.0, (1.0 / max(1, self.target_fps)) - (time.perf_counter() - started_at))
                if delay > 0:
                    time.sleep(min(delay, 0.2))
        except Exception:
            logging.exception("Camera loop failed")
            self.message_queue.put((generation, "Erreur camera, voir minicam.log"))
        finally:
            capture.release()
            logging.info("Camera %s released", camera_index)

    def _open_camera(self, cv2, camera_index: int, camera_stop_event: Event, generation: int):
        logging.info("Opening camera %s", camera_index)

        backends = [cv2.CAP_DSHOW, cv2.CAP_ANY]
        for backend in backends:
            if self.app_stop_event.is_set() or camera_stop_event.is_set():
                return None

            capture = cv2.VideoCapture(camera_index, backend)
            if not capture.isOpened():
                capture.release()
                continue

            capture.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            capture.set(cv2.CAP_PROP_FPS, self.target_fps)
            capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)

            self.message_queue.put((generation, ""))
            logging.info("Camera %s opened with backend %s", camera_index, backend)
            return capture

        logging.error("Camera %s not found", camera_index)
        self.message_queue.put((generation, f"Camera {camera_index} introuvable"))
        return None

    def _process_frame(self, cv2, np, frame):
        if self.mirror:
            frame = cv2.flip(frame, 1)

        if self.zoom > 100:
            image_height, image_width = frame.shape[:2]
            crop_width = max(1, int(image_width * 100 / self.zoom))
            crop_height = max(1, int(image_height * 100 / self.zoom))
            left = (image_width - crop_width) // 2
            top = (image_height - crop_height) // 2
            frame = frame[top : top + crop_height, left : left + crop_width]

        alpha = self.contrast / 100
        beta = self.brightness
        if alpha != 1 or beta != 0:
            frame = np.clip(frame.astype(np.float32) * alpha + beta, 0, 255).astype(np.uint8)

        if self.saturation != 100:
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV).astype(np.float32)
            hsv[:, :, 1] = np.clip(hsv[:, :, 1] * (self.saturation / 100), 0, 255)
            frame = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)

        return frame

    def _put_latest_frame(self, generation: int, frame) -> None:
        item = (generation, frame)
        try:
            self.frame_queue.put_nowait(item)
        except Full:
            try:
                self.frame_queue.get_nowait()
            except Empty:
                pass
            self.frame_queue.put_nowait(item)

    def _tick(self) -> None:
        self._show_pending_messages()

        latest_frame = None
        while True:
            try:
                generation, frame = self.frame_queue.get_nowait()
            except Empty:
                break

            if generation == self.camera_generation:
                latest_frame = frame

        if latest_frame is not None:
            self._show_frame(latest_frame)

        if not self.app_stop_event.is_set():
            self.root.after(FRAME_DELAY_MS, self._tick)

    def _show_pending_messages(self) -> None:
        while True:
            try:
                generation, message = self.message_queue.get_nowait()
            except Empty:
                return

            if generation != self.camera_generation:
                continue

            if message:
                self.video_label.configure(image="", text=message)
            else:
                self.video_label.configure(text="")

    def _show_frame(self, frame) -> None:
        cv2 = self.cv2
        if cv2 is None:
            return

        target_width = max(1, self.video_label.winfo_width())
        target_height = max(1, self.video_label.winfo_height())

        image_height, image_width = frame.shape[:2]
        scale = min(target_width / image_width, target_height / image_height)
        width = max(1, int(image_width * scale))
        height = max(1, int(image_height * scale))

        interpolation = cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
        resized = cv2.resize(frame, (width, height), interpolation=interpolation)

        left = (target_width - width) // 2
        right = target_width - width - left
        top = (target_height - height) // 2
        bottom = target_height - height - top
        padded = cv2.copyMakeBorder(
            resized,
            top,
            bottom,
            left,
            right,
            cv2.BORDER_CONSTANT,
            value=(11, 11, 11),
        )

        ok, encoded = cv2.imencode(".ppm", padded)
        if not ok:
            logging.error("Frame encoding failed")
            return

        self.photo = PhotoImage(data=encoded.tobytes(), format="PPM")
        self.video_label.configure(image=self.photo, text="")

    def _open_settings(self) -> None:
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.focus_force()
            return

        self._capture_window_size()

        window = Toplevel(self.root)
        self.settings_window = window
        window.title("Parametres")
        window.resizable(False, False)
        window.transient(self.root)
        window.attributes("-topmost", self.always_on_top)
        window.protocol("WM_DELETE_WINDOW", lambda: self._close_settings(window))

        camera_var = IntVar(value=self.camera_index)
        width_var = IntVar(value=int(self.settings["window_width"]))
        height_var = IntVar(value=int(self.settings["window_height"]))
        resizable_var = BooleanVar(value=bool(self.settings["resizable"]))
        topmost_var = BooleanVar(value=self.always_on_top)
        mirror_var = BooleanVar(value=self.mirror)
        fullscreen_var = BooleanVar(value=self.fullscreen)
        mini_var = BooleanVar(value=self.mini_mode)
        drag_mini_var = BooleanVar(value=self.drag_mini)
        brightness_var = DoubleVar(value=self.brightness)
        contrast_var = DoubleVar(value=self.contrast)
        saturation_var = DoubleVar(value=self.saturation)
        zoom_var = DoubleVar(value=self.zoom)
        fps_var = DoubleVar(value=self.target_fps)
        save_var = BooleanVar(value=bool(self.settings["save_settings"]))
        live_var = BooleanVar(value=bool(self.settings["live_preview"]))

        notebook = ttk.Notebook(window)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)

        camera_tab = ttk.Frame(notebook, padding=12)
        window_tab = ttk.Frame(notebook, padding=12)
        image_tab = ttk.Frame(notebook, padding=12)
        screen_tab = ttk.Frame(notebook, padding=12)
        notebook.add(camera_tab, text="Camera")
        notebook.add(window_tab, text="Fenetre")
        notebook.add(image_tab, text="Image")
        notebook.add(screen_tab, text="Ecran")

        self._build_camera_tab(camera_tab, camera_var, fps_var)
        self._build_window_tab(
            window_tab,
            width_var,
            height_var,
            resizable_var,
            topmost_var,
            fullscreen_var,
            mini_var,
            drag_mini_var,
        )
        self._build_image_tab(image_tab, mirror_var, brightness_var, contrast_var, saturation_var, zoom_var)
        self._build_screen_tab(screen_tab)

        buttons = ttk.Frame(window, padding=(10, 0, 10, 10))
        buttons.pack(fill="x")

        def apply(restart_camera: bool = False) -> None:
            self._apply_settings(
                camera_var,
                width_var,
                height_var,
                resizable_var,
                topmost_var,
                mirror_var,
                fullscreen_var,
                mini_var,
                drag_mini_var,
                brightness_var,
                contrast_var,
                saturation_var,
                zoom_var,
                fps_var,
                save_var,
                live_var,
                restart_camera,
            )

        live_after = {"id": None}

        def apply_live(*_args) -> None:
            live_after["id"] = None
            if live_var.get():
                self._apply_live_settings(
                    width_var,
                    height_var,
                    resizable_var,
                    topmost_var,
                    mirror_var,
                    fullscreen_var,
                    mini_var,
                    drag_mini_var,
                    brightness_var,
                    contrast_var,
                    saturation_var,
                    zoom_var,
                    save_var,
                    live_var,
                )
            else:
                self.settings["live_preview"] = False
                self._save_settings()

        def schedule_live_apply(*_args) -> None:
            if not window.winfo_exists():
                return

            if live_after["id"] is not None:
                window.after_cancel(live_after["id"])
            live_after["id"] = window.after(120, apply_live)

        for traced_var in (
            width_var,
            height_var,
            resizable_var,
            topmost_var,
            mirror_var,
            fullscreen_var,
            mini_var,
            drag_mini_var,
            brightness_var,
            contrast_var,
            saturation_var,
            zoom_var,
            save_var,
            live_var,
        ):
            traced_var.trace_add("write", schedule_live_apply)

        def reset_all() -> None:
            camera_var.set(DEFAULT_SETTINGS["camera_index"])
            width_var.set(DEFAULT_SETTINGS["window_width"])
            height_var.set(DEFAULT_SETTINGS["window_height"])
            resizable_var.set(DEFAULT_SETTINGS["resizable"])
            topmost_var.set(DEFAULT_SETTINGS["always_on_top"])
            mirror_var.set(DEFAULT_SETTINGS["mirror"])
            fullscreen_var.set(DEFAULT_SETTINGS["fullscreen"])
            mini_var.set(DEFAULT_SETTINGS["mini_mode"])
            drag_mini_var.set(DEFAULT_SETTINGS["drag_mini"])
            brightness_var.set(DEFAULT_SETTINGS["brightness"])
            contrast_var.set(DEFAULT_SETTINGS["contrast"])
            saturation_var.set(DEFAULT_SETTINGS["saturation"])
            zoom_var.set(DEFAULT_SETTINGS["zoom"])
            fps_var.set(DEFAULT_SETTINGS["target_fps"])
            save_var.set(DEFAULT_SETTINGS["save_settings"])
            live_var.set(DEFAULT_SETTINGS["live_preview"])
            apply(restart_camera=True)

        ttk.Checkbutton(buttons, text="Enregistrer", variable=save_var).pack(side="left")
        ttk.Checkbutton(buttons, text="Modification en direct", variable=live_var).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="Reinitialiser", command=reset_all).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="Fermer MiniCam", command=self._close).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="Fermer", command=lambda: self._close_settings(window)).pack(side="right")
        ttk.Button(buttons, text="Appliquer", command=apply).pack(side="right", padx=(0, 8))
        ttk.Button(buttons, text="Redemarrer camera", command=lambda: apply(True)).pack(side="right", padx=(0, 8))

    def _build_camera_tab(self, tab: ttk.Frame, camera_var: IntVar, fps_var: DoubleVar) -> None:
        ttk.Label(tab, text="Numero de camera").grid(row=0, column=0, sticky="w", pady=(0, 8))
        ttk.Spinbox(tab, from_=0, to=9, textvariable=camera_var, width=8).grid(
            row=0,
            column=1,
            sticky="w",
            pady=(0, 8),
        )

        self._add_slider(tab, "Fluidite FPS", fps_var, 5, 60, 1, 1)
        tab.columnconfigure(0, minsize=150)
        tab.columnconfigure(1, minsize=220)

    def _build_window_tab(
        self,
        tab: ttk.Frame,
        width_var: IntVar,
        height_var: IntVar,
        resizable_var: BooleanVar,
        topmost_var: BooleanVar,
        fullscreen_var: BooleanVar,
        mini_var: BooleanVar,
        drag_mini_var: BooleanVar,
    ) -> None:
        ttk.Label(tab, text="Largeur").grid(row=0, column=0, sticky="w", pady=(0, 8))
        ttk.Spinbox(tab, from_=MIN_WIDTH, to=1920, increment=20, textvariable=width_var, width=8).grid(
            row=0,
            column=1,
            sticky="w",
            pady=(0, 8),
        )

        ttk.Label(tab, text="Hauteur").grid(row=1, column=0, sticky="w", pady=(0, 8))
        ttk.Spinbox(tab, from_=MIN_HEIGHT, to=1080, increment=20, textvariable=height_var, width=8).grid(
            row=1,
            column=1,
            sticky="w",
            pady=(0, 8),
        )

        presets = ttk.Frame(tab)
        presets.grid(row=2, column=0, columnspan=2, sticky="w", pady=(0, 10))
        for label, size in SIZE_PRESETS.items():
            ttk.Button(
                presets,
                text=label,
                command=lambda selected=size: self._set_size_preset(selected, width_var, height_var),
            ).pack(side="left", padx=(0, 6))

        ttk.Checkbutton(tab, text="Fenetre redimensionnable", variable=resizable_var).grid(
            row=3,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(0, 6),
        )
        ttk.Checkbutton(tab, text="Toujours au-dessus", variable=topmost_var).grid(
            row=4,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(0, 6),
        )
        ttk.Checkbutton(tab, text="Plein ecran", variable=fullscreen_var).grid(
            row=5,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(0, 6),
        )
        ttk.Checkbutton(tab, text="Mode mini sans barre de titre", variable=mini_var).grid(
            row=6,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(0, 6),
        )
        ttk.Checkbutton(tab, text="Deplacer le mode mini avec la souris", variable=drag_mini_var).grid(
            row=7,
            column=0,
            columnspan=2,
            sticky="w",
        )
        tab.columnconfigure(0, minsize=150)
        tab.columnconfigure(1, minsize=220)

    def _build_image_tab(
        self,
        tab: ttk.Frame,
        mirror_var: BooleanVar,
        brightness_var: DoubleVar,
        contrast_var: DoubleVar,
        saturation_var: DoubleVar,
        zoom_var: DoubleVar,
    ) -> None:
        ttk.Checkbutton(tab, text="Mode miroir", variable=mirror_var).grid(
            row=0,
            column=0,
            columnspan=3,
            sticky="w",
            pady=(0, 8),
        )
        self._add_slider(tab, "Luminosite", brightness_var, -100, 100, 1, 1)
        self._add_slider(tab, "Contraste", contrast_var, 50, 200, 1, 2)
        self._add_slider(tab, "Saturation", saturation_var, 0, 200, 1, 3)
        self._add_slider(tab, "Zoom", zoom_var, 100, 300, 1, 4)
        tab.columnconfigure(0, minsize=120)
        tab.columnconfigure(1, minsize=220)

    def _build_screen_tab(self, tab: ttk.Frame) -> None:
        ttk.Label(
            tab,
            text="L'affichage de l'ecran est reserve pour la prochaine etape.",
            wraplength=360,
        ).pack(anchor="w")
        ttk.Label(
            tab,
            text="Le menu est deja pret pour accueillir ce mode.",
            wraplength=360,
        ).pack(anchor="w", pady=(8, 0))

    def _add_slider(
        self,
        parent: ttk.Frame,
        label: str,
        variable: DoubleVar,
        minimum: int,
        maximum: int,
        increment: int,
        row: int,
    ) -> None:
        value = StringVar(value=str(int(round(variable.get()))))

        def update_value(*_args) -> None:
            rounded = int(round(variable.get() / increment) * increment)
            value.set(str(rounded))

        variable.trace_add("write", update_value)
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=(0, 8))
        ttk.Scale(parent, from_=minimum, to=maximum, variable=variable, orient="horizontal").grid(
            row=row,
            column=1,
            sticky="ew",
            pady=(0, 8),
        )
        ttk.Label(parent, textvariable=value, width=5, anchor="e").grid(row=row, column=2, sticky="e", pady=(0, 8))

    def _set_size_preset(self, size: tuple[int, int], width_var: IntVar, height_var: IntVar) -> None:
        width, height = size
        width_var.set(width)
        height_var.set(height)

    def _apply_settings(
        self,
        camera_var: IntVar,
        width_var: IntVar,
        height_var: IntVar,
        resizable_var: BooleanVar,
        topmost_var: BooleanVar,
        mirror_var: BooleanVar,
        fullscreen_var: BooleanVar,
        mini_var: BooleanVar,
        drag_mini_var: BooleanVar,
        brightness_var: DoubleVar,
        contrast_var: DoubleVar,
        saturation_var: DoubleVar,
        zoom_var: DoubleVar,
        fps_var: DoubleVar,
        save_var: BooleanVar,
        live_var: BooleanVar,
        restart_camera: bool = False,
    ) -> None:
        old_camera_index = self.camera_index
        old_target_fps = self.target_fps
        new_settings = clean_settings(
            {
                "camera_index": get_var_value(camera_var, self.camera_index),
                "window_width": get_var_value(width_var, self.settings["window_width"]),
                "window_height": get_var_value(height_var, self.settings["window_height"]),
                "resizable": get_var_value(resizable_var, self.settings["resizable"]),
                "always_on_top": get_var_value(topmost_var, self.always_on_top),
                "mirror": get_var_value(mirror_var, self.mirror),
                "fullscreen": get_var_value(fullscreen_var, self.fullscreen),
                "mini_mode": get_var_value(mini_var, self.mini_mode),
                "drag_mini": get_var_value(drag_mini_var, self.drag_mini),
                "brightness": get_var_value(brightness_var, self.brightness),
                "contrast": get_var_value(contrast_var, self.contrast),
                "saturation": get_var_value(saturation_var, self.saturation),
                "zoom": get_var_value(zoom_var, self.zoom),
                "target_fps": get_var_value(fps_var, self.target_fps),
                "save_settings": get_var_value(save_var, self.settings["save_settings"]),
                "live_preview": get_var_value(live_var, self.settings["live_preview"]),
            }
        )

        self.settings = new_settings
        self.camera_index = int(new_settings["camera_index"])
        self.always_on_top = bool(new_settings["always_on_top"])
        self.mirror = bool(new_settings["mirror"])
        self.fullscreen = bool(new_settings["fullscreen"])
        self.mini_mode = bool(new_settings["mini_mode"])
        self.drag_mini = bool(new_settings["drag_mini"])
        self.brightness = int(new_settings["brightness"])
        self.contrast = int(new_settings["contrast"])
        self.saturation = int(new_settings["saturation"])
        self.zoom = int(new_settings["zoom"])
        self.target_fps = int(new_settings["target_fps"])

        self._apply_window_settings()
        self._save_settings()

        if restart_camera or self.camera_index != old_camera_index or self.target_fps != old_target_fps:
            self._start_camera()

    def _apply_live_settings(
        self,
        width_var: IntVar,
        height_var: IntVar,
        resizable_var: BooleanVar,
        topmost_var: BooleanVar,
        mirror_var: BooleanVar,
        fullscreen_var: BooleanVar,
        mini_var: BooleanVar,
        drag_mini_var: BooleanVar,
        brightness_var: DoubleVar,
        contrast_var: DoubleVar,
        saturation_var: DoubleVar,
        zoom_var: DoubleVar,
        save_var: BooleanVar,
        live_var: BooleanVar,
    ) -> None:
        new_settings = clean_settings(
            {
                "camera_index": self.camera_index,
                "window_width": get_var_value(width_var, self.settings["window_width"]),
                "window_height": get_var_value(height_var, self.settings["window_height"]),
                "resizable": get_var_value(resizable_var, self.settings["resizable"]),
                "always_on_top": get_var_value(topmost_var, self.always_on_top),
                "mirror": get_var_value(mirror_var, self.mirror),
                "fullscreen": get_var_value(fullscreen_var, self.fullscreen),
                "mini_mode": get_var_value(mini_var, self.mini_mode),
                "drag_mini": get_var_value(drag_mini_var, self.drag_mini),
                "brightness": get_var_value(brightness_var, self.brightness),
                "contrast": get_var_value(contrast_var, self.contrast),
                "saturation": get_var_value(saturation_var, self.saturation),
                "zoom": get_var_value(zoom_var, self.zoom),
                "target_fps": self.target_fps,
                "save_settings": get_var_value(save_var, self.settings["save_settings"]),
                "live_preview": get_var_value(live_var, self.settings["live_preview"]),
            }
        )

        self.settings = new_settings
        self.always_on_top = bool(new_settings["always_on_top"])
        self.mirror = bool(new_settings["mirror"])
        self.fullscreen = bool(new_settings["fullscreen"])
        self.mini_mode = bool(new_settings["mini_mode"])
        self.drag_mini = bool(new_settings["drag_mini"])
        self.brightness = int(new_settings["brightness"])
        self.contrast = int(new_settings["contrast"])
        self.saturation = int(new_settings["saturation"])
        self.zoom = int(new_settings["zoom"])

        self._apply_window_settings()
        self._save_settings()

    def _apply_window_settings(self) -> None:
        can_resize = bool(self.settings["resizable"]) and not self.fullscreen and not self.mini_mode
        self.root.resizable(can_resize, can_resize)
        self.root.attributes("-topmost", self.always_on_top)
        self.root.attributes("-fullscreen", self.fullscreen)
        self.root.overrideredirect(self.mini_mode and not self.fullscreen)

        if not self.fullscreen:
            self.root.geometry(f"{self.settings['window_width']}x{self.settings['window_height']}")

        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.attributes("-topmost", self.always_on_top)

    def _toggle_fullscreen(self) -> None:
        self.fullscreen = not self.fullscreen
        self.settings["fullscreen"] = self.fullscreen
        self._apply_window_settings()
        self._save_settings()

    def _close_settings(self, window: Toplevel) -> None:
        if window.winfo_exists():
            window.destroy()
        self.settings_window = None

    def _capture_window_size(self) -> None:
        if self.fullscreen:
            return

        width = max(MIN_WIDTH, self.root.winfo_width())
        height = max(MIN_HEIGHT, self.root.winfo_height())
        self.settings["window_width"] = width
        self.settings["window_height"] = height

    def _save_settings(self) -> None:
        try:
            if not bool(self.settings.get("save_settings", True)):
                if SETTINGS_PATH.exists():
                    SETTINGS_PATH.unlink()
                return

            self._capture_window_size()
            SETTINGS_PATH.write_text(
                json.dumps(clean_settings(self.settings), indent=2),
                encoding="utf-8",
            )
        except Exception:
            logging.exception("Settings could not be saved")

    def _close(self) -> None:
        self.app_stop_event.set()
        if self.camera_stop_event is not None:
            self.camera_stop_event.set()
        self._save_settings()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    camera_index = None
    if len(sys.argv) > 1:
        try:
            camera_index = int(sys.argv[1])
        except ValueError:
            print("Usage: python minicam.py [numero_camera]", file=sys.stderr)
            raise SystemExit(2)

    try:
        MiniCamApp(camera_index).run()
    except Exception:
        logging.exception("MiniCam crashed")
        raise


if __name__ == "__main__":
    main()
