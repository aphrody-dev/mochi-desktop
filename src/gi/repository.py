"""Pure-Python cross-platform `gi.repository` implementation for Mochi Desktop."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum, IntFlag
import os
from pathlib import Path
import webbrowser


class _GObjectBase:
    """Minimal GObject signal and property emitter."""

    def __init__(self, **kwargs) -> None:
        self._signals: dict[str, list[tuple[int, Callable, tuple]]] = {}
        self._next_handler_id = 1
        self._props: dict[str, object] = dict(kwargs)

    def connect(self, signal_name: str, callback: Callable, *user_data) -> int:
        hid = self._next_handler_id
        self._next_handler_id += 1
        self._signals.setdefault(signal_name, []).append((hid, callback, user_data))
        return hid

    def disconnect(self, handler_id: int) -> None:
        for handlers in self._signals.values():
            for idx, (hid, _, _) in enumerate(handlers):
                if hid == handler_id:
                    handlers.pop(idx)
                    return

    def emit(self, signal_name: str, *args) -> object:
        result = None
        for _hid, callback, user_data in list(self._signals.get(signal_name, ())):
            result = callback(self, *args, *user_data)
        return result


# ============================================================================
# GLib
# ============================================================================


class _GLibModule:
    SOURCE_REMOVE = False
    SOURCE_CONTINUE = True
    PRIORITY_DEFAULT = 0
    PRIORITY_LOW = 300

    class ChecksumType(IntEnum):
        MD5 = 0
        SHA1 = 1
        SHA256 = 2
        SHA512 = 3

    class UserDirectory(IntEnum):
        DIRECTORY_DESKTOP = 0
        DIRECTORY_DOCUMENTS = 1
        DIRECTORY_DOWNLOAD = 2
        DIRECTORY_MUSIC = 3
        DIRECTORY_PICTURES = 4
        DIRECTORY_PUBLIC_SHARE = 5
        DIRECTORY_TEMPLATES = 6
        DIRECTORY_VIDEOS = 7

    class Error(Exception):
        def __init__(
            self,
            message: str = "",
            domain: str = "glib-quark",
            code: int = 0,
        ) -> None:
            super().__init__(message)
            self.message = message
            self.domain = domain
            self.code = code

        @classmethod
        def new_literal(cls, domain: int | str, message: str, code: int) -> _GLibModule.Error:
            return cls(message=message, domain=str(domain), code=int(code))

        def matches(self, domain: int | str, code: int) -> bool:
            return str(self.domain) == str(domain) and int(self.code) == int(code)

    class VariantType:
        def __init__(self, type_string: str) -> None:
            self.type_string = str(type_string)

        @classmethod
        def new(cls, type_string: str) -> _GLibModule.VariantType:
            return cls(type_string)

        def dup_string(self) -> str:
            return self.type_string

    class Variant:
        def __init__(self, format_string: str, value: object) -> None:
            self.format_string = str(format_string)
            self._value = value

        @classmethod
        def _deep_unpack(cls, obj: object) -> object:
            if isinstance(obj, cls):
                return cls._deep_unpack(obj._value)
            if isinstance(obj, tuple):
                return tuple(cls._deep_unpack(item) for item in obj)
            if isinstance(obj, list):
                return [cls._deep_unpack(item) for item in obj]
            if isinstance(obj, dict):
                return {
                    cls._deep_unpack(k): cls._deep_unpack(v)
                    for k, v in obj.items()
                }
            return obj

        def unpack(self) -> object:
            return self._deep_unpack(self._value)

        def get_type_string(self) -> str:
            return self.format_string

    class MainContext:
        _default_instance: _GLibModule.MainContext | None = None

        @classmethod
        def default(cls) -> _GLibModule.MainContext:
            if cls._default_instance is None:
                cls._default_instance = cls()
            return cls._default_instance

        def pending(self) -> bool:
            return False

        def iteration(self, may_block: bool = False) -> bool:
            return False

    class MainLoop:
        def __init__(self, context: object = None) -> None:
            self._running = False

        def run(self) -> None:
            self._running = True

        def quit(self) -> None:
            self._running = False

        def is_running(self) -> bool:
            return self._running

    def __init__(self) -> None:
        self._next_source_id = 1
        self._sources: dict[int, tuple[Callable, tuple]] = {}

    def timeout_add(self, interval: int, function: Callable, *data) -> int:
        sid = self._next_source_id
        self._next_source_id += 1
        self._sources[sid] = (function, data)
        return sid

    def timeout_add_seconds(self, interval: int, function: Callable, *data) -> int:
        return self.timeout_add(int(interval) * 1000, function, *data)

    def idle_add(self, function: Callable, *data) -> int:
        return self.timeout_add(0, function, *data)

    def unix_signal_add(
        self,
        priority: int,
        signum: int,
        function: Callable,
        *data,
    ) -> int:
        return self.timeout_add(0, function, *data)

    def source_remove(self, tag: int) -> bool:
        self._sources.pop(int(tag), None)
        return True

    def markup_escape_text(self, text: str) -> str:
        return (
            str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&apos;")
        )

    def get_user_special_dir(self, directory: int) -> str | None:
        home = Path.home()
        if directory == self.UserDirectory.DIRECTORY_DOWNLOAD:
            return str(home / "Downloads")
        if directory == self.UserDirectory.DIRECTORY_DESKTOP:
            return str(home / "Desktop")
        if directory == self.UserDirectory.DIRECTORY_DOCUMENTS:
            return str(home / "Documents")
        return str(home)


GLib = _GLibModule()


# ============================================================================
# Gio
# ============================================================================


class _GioModule:
    class ApplicationFlags(IntFlag):
        FLAGS_NONE = 0
        DEFAULT_FLAGS = 0
        NON_UNIQUE = 1 << 5

    class BusType(IntEnum):
        STARTER = -1
        NONE = 0
        SYSTEM = 1
        SESSION = 2

    class BusNameOwnerFlags(IntFlag):
        NONE = 0
        ALLOW_REPLACEMENT = 1
        REPLACE = 2

    class BusNameWatcherFlags(IntFlag):
        NONE = 0
        AUTO_START = 1

    class DBusProxyFlags(IntFlag):
        NONE = 0
        DO_NOT_LOAD_PROPERTIES = 1
        DO_NOT_CONNECT_SIGNALS = 2
        DO_NOT_AUTO_START = 4

    class DBusCallFlags(IntFlag):
        NONE = 0
        NO_AUTO_START = 1

    class DBusSignalFlags(IntFlag):
        NONE = 0

    class DBusError(IntEnum):
        FAILED = 0
        NO_MEMORY = 1
        SERVICE_UNKNOWN = 2
        NAME_HAS_NO_OWNER = 3
        NO_REPLY = 4
        IO_ERROR = 5
        BAD_ADDRESS = 6
        NOT_SUPPORTED = 7
        LIMITS_EXCEEDED = 8
        ACCESS_DENIED = 9
        AUTH_FAILED = 10
        NO_SERVER = 11
        TIMEOUT = 12
        INVALID_ARGS = 16
        UNKNOWN_METHOD = 19

    class FileMonitorFlags(IntFlag):
        NONE = 0
        WATCH_MOUNTS = 1
        SEND_MOVED = 2

    class FileMonitorEvent(IntEnum):
        CHANGED = 0
        CHANGES_DONE_HINT = 1
        DELETED = 2
        CREATED = 3
        ATTRIBUTE_CHANGED = 4
        PRE_UNMOUNT = 5
        UNMOUNTED = 6
        MOVED = 7
        RENAMED = 8
        MOVED_IN = 9
        MOVED_OUT = 10

    class AppInfo:
        @staticmethod
        def launch_default_for_uri(uri: str, context: object = None) -> bool:
            try:
                webbrowser.open(uri)
                return True
            except Exception:
                return False

    class SimpleAction(_GObjectBase):
        def __init__(
            self,
            name: str,
            parameter_type: _GLibModule.VariantType | None = None,
        ) -> None:
            super().__init__()
            self.name = str(name)
            self.parameter_type = parameter_type

        @classmethod
        def new(
            cls,
            name: str,
            parameter_type: _GLibModule.VariantType | None = None,
        ) -> _GioModule.SimpleAction:
            return cls(name, parameter_type)

        def get_name(self) -> str:
            return self.name

        def get_parameter_type(self) -> _GLibModule.VariantType | None:
            return self.parameter_type

        def activate(self, parameter: object = None) -> None:
            self.emit("activate", parameter)

    class Application(_GObjectBase):
        _default_app: _GioModule.Application | None = None

        def __init__(
            self,
            application_id: str | None = None,
            flags: int = 0,
            **kwargs,
        ) -> None:
            super().__init__(application_id=application_id, flags=flags, **kwargs)
            self.application_id = application_id
            self.flags = flags
            self._actions: dict[str, _GioModule.SimpleAction] = {}
            self._windows: list[object] = []
            _GioModule.Application._default_app = self

        @classmethod
        def get_default(cls) -> _GioModule.Application | None:
            return cls._default_app

        def add_action(self, action: _GioModule.SimpleAction) -> None:
            self._actions[action.get_name()] = action

        def lookup_action(self, name: str) -> _GioModule.SimpleAction | None:
            return self._actions.get(name)

        def get_active_window(self) -> object | None:
            return self._windows[0] if self._windows else None

        def get_dbus_connection(self) -> object | None:
            return None

        def register(self, cancellable: object = None) -> bool:
            return True

        def hold(self) -> None:
            return None

        def release(self) -> None:
            return None

        def run(self, argv: list[str] | None = None) -> int:
            if hasattr(self, "do_activate"):
                self.do_activate()
            if hasattr(self, "do_shutdown"):
                self.do_shutdown()
            return 0

        def quit(self) -> None:
            if hasattr(self, "do_shutdown"):
                self.do_shutdown()

        def do_shutdown(self) -> None:
            return None

    class FileMonitor(_GObjectBase):
        def cancel(self) -> bool:
            return True

    class File(_GObjectBase):
        def __init__(self, path: str | Path) -> None:
            super().__init__()
            self._path = Path(path)

        @classmethod
        def new_for_path(cls, path: str) -> _GioModule.File:
            return cls(path)

        @classmethod
        def new_for_uri(cls, uri: str) -> _GioModule.File:
            if uri.startswith("file://"):
                from urllib.parse import unquote, urlsplit
                parsed = urlsplit(uri)
                raw = unquote(parsed.path)
                if os.name == "nt" and raw.startswith("/") and len(raw) > 2 and raw[2] == ":":
                    raw = raw[1:]
                return cls(raw)
            return cls(uri)

        def get_path(self) -> str | None:
            return str(self._path)

        def get_basename(self) -> str | None:
            return self._path.name

        def monitor_directory(
            self,
            flags: int = 0,
            cancellable: object = None,
        ) -> _GioModule.FileMonitor:
            return _GioModule.FileMonitor()

    class Settings(_GObjectBase):
        def __init__(self, schema_id: str = "") -> None:
            super().__init__()
            self.schema_id = schema_id
            self._values: dict[str, str] = {"color-scheme": "default"}

        @classmethod
        def new(cls, schema_id: str) -> _GioModule.Settings:
            return cls(schema_id)

        def get_string(self, key: str) -> str:
            return self._values.get(key, "default")

    class DBusProxy(_GObjectBase):
        @classmethod
        def new_for_bus_sync(
            cls,
            bus_type: int,
            flags: int,
            info: object,
            name: str,
            object_path: str,
            interface_name: str,
            cancellable: object = None,
        ) -> _GioModule.DBusProxy:
            raise GLib.Error(f"D-Bus unavailable on {os.name} for {name}")

    class DBus:
        session = None

    @staticmethod
    def dbus_error_quark() -> int:
        return 1

    @staticmethod
    def bus_get_sync(bus_type: int, cancellable: object = None) -> object:
        raise GLib.Error("D-Bus session bus unavailable")

    @staticmethod
    def bus_own_name_on_connection(*_args, **_kwargs) -> int:
        return 1

    @staticmethod
    def bus_unown_name(_owner_id: int) -> None:
        return None


Gio = _GioModule()


# ============================================================================
# Gdk / GdkX11 / GdkWayland
# ============================================================================


class _GdkModule:
    BUTTON_PRIMARY = 1
    BUTTON_SECONDARY = 3
    KEY_Escape = 0xFF1B
    KEY_Left = 0xFF51
    KEY_Right = 0xFF53
    KEY_KP_Left = 0xFF96
    KEY_KP_Right = 0xFF98

    class DragAction(IntFlag):
        NONE = 0
        COPY = 1
        MOVE = 2
        LINK = 4

    class ModifierType(IntFlag):
        NO_MODIFIER_MASK = 0
        SHIFT_MASK = 1 << 0
        CONTROL_MASK = 1 << 2
        ALT_MASK = 1 << 3

    @dataclass
    class Rectangle:
        x: int = 0
        y: int = 0
        width: int = 0
        height: int = 0

    @dataclass
    class RGBA:
        red: float = 0.0
        green: float = 0.0
        blue: float = 0.0
        alpha: float = 1.0

        def equal(self, other: object) -> bool:
            if not isinstance(other, _GdkModule.RGBA):
                return False
            return (
                abs(self.red - other.red) < 1e-4
                and abs(self.green - other.green) < 1e-4
                and abs(self.blue - other.blue) < 1e-4
                and abs(self.alpha - other.alpha) < 1e-4
            )

        def to_string(self) -> str:
            r = round(self.red * 255)
            g = round(self.green * 255)
            b = round(self.blue * 255)
            return f"rgba({r},{g},{b},{self.alpha:g})"

    class Monitor(_GObjectBase):
        def __init__(
            self,
            x: int = 0,
            y: int = 0,
            width: int = 1920,
            height: int = 1080,
        ) -> None:
            super().__init__()
            self._geometry = _GdkModule.Rectangle(x, y, width, height)

        def get_geometry(self) -> _GdkModule.Rectangle:
            return self._geometry

    class _MonitorList:
        def __init__(self, monitors: list[_GdkModule.Monitor] | None = None) -> None:
            self._monitors = monitors or [_GdkModule.Monitor(0, 0, 1920, 1080)]

        def get_n_items(self) -> int:
            return len(self._monitors)

        def get_item(self, index: int) -> _GdkModule.Monitor | None:
            if 0 <= index < len(self._monitors):
                return self._monitors[index]
            return None

        def __len__(self) -> int:
            return len(self._monitors)

        def __iter__(self):
            return iter(self._monitors)

    class Display(_GObjectBase):
        _default_display: _GdkModule.Display | None = None

        def __init__(self) -> None:
            super().__init__()
            self._monitors = _GdkModule._MonitorList()

        @classmethod
        def get_default(cls) -> _GdkModule.Display:
            if cls._default_display is None:
                cls._default_display = cls()
            return cls._default_display

        def get_monitors(self) -> _GdkModule._MonitorList:
            return self._monitors

    class Surface(_GObjectBase):
        def __init__(self) -> None:
            super().__init__()
            self._mapped = True
            self._scale = 1
            self._frame_sync_enabled = True
            self._skip_taskbar_hint = False

        def get_mapped(self) -> bool:
            return self._mapped

        def get_scale_factor(self) -> int:
            return self._scale

        def get_scale(self) -> float:
            return float(self._scale)

        def set_frame_sync_enabled(self, enabled: bool) -> None:
            self._frame_sync_enabled = bool(enabled)

        def set_skip_taskbar_hint(self, setting: bool) -> None:
            self._skip_taskbar_hint = bool(setting)

        def begin_move(
            self,
            device: object,
            button: int,
            x: float,
            y: float,
            timestamp: int,
        ) -> None:
            return None

    class Toplevel(Surface):
        pass

    class Texture(_GObjectBase):
        def save_to_png_bytes(self) -> bytes:
            return b""

    class FileList:
        def __init__(self, files: list[_GioModule.File] | None = None) -> None:
            self._files = list(files or [])

        def get_files(self) -> list[_GioModule.File]:
            return list(self._files)


Gdk = _GdkModule()


class _GdkX11Module:
    class X11Surface(_GdkModule.Toplevel):
        def get_xid(self) -> int:
            return 1


class _GdkWaylandModule:
    class WaylandDisplay(_GdkModule.Display):
        pass


GdkX11 = _GdkX11Module()
GdkWayland = _GdkWaylandModule()


# ============================================================================
# Gtk
# ============================================================================


class _GtkModule:
    STYLE_PROVIDER_PRIORITY_APPLICATION = 600
    STYLE_PROVIDER_PRIORITY_USER = 800

    class Align(IntEnum):
        FILL = 0
        START = 1
        END = 2
        CENTER = 3
        BASELINE = 4

    class Orientation(IntEnum):
        HORIZONTAL = 0
        VERTICAL = 1

    class Justification(IntEnum):
        LEFT = 0
        RIGHT = 1
        CENTER = 2
        FILL = 3

    class PolicyType(IntEnum):
        ALWAYS = 0
        AUTOMATIC = 1
        NEVER = 2
        EXTERNAL = 3

    class PositionType(IntEnum):
        LEFT = 0
        RIGHT = 1
        TOP = 2
        BOTTOM = 3

    class PropagationPhase(IntEnum):
        NONE = 0
        CAPTURE = 1
        BUBBLE = 2
        TARGET = 3

    class StackTransitionType(IntEnum):
        NONE = 0
        CROSSFADE = 1

    class WrapMode(IntEnum):
        NONE = 0
        CHAR = 1
        WORD = 2
        WORD_CHAR = 3

    class _SettingsProps:
        def __init__(self, owner: _GtkModule.Settings) -> None:
            object.__setattr__(self, "_owner", owner)

        def __getattr__(self, name: str) -> object:
            key = name.replace("_", "-")
            return self._owner._props.get(key, False)

        def __setattr__(self, name: str, value: object) -> None:
            key = name.replace("_", "-")
            self._owner._props[key] = value

    class Settings(_GObjectBase):
        _default_settings: _GtkModule.Settings | None = None

        def __init__(self) -> None:
            super().__init__()
            self._props["gtk-application-prefer-dark-theme"] = False
            self.props = _GtkModule._SettingsProps(self)

        @classmethod
        def get_default(cls) -> _GtkModule.Settings:
            if cls._default_settings is None:
                cls._default_settings = cls()
            return cls._default_settings

        def find_property(self, name: str) -> object | None:
            return self._props.get(name)

        def set_property(self, name: str, value: object) -> None:
            self._props[name] = value

        def get_property(self, name: str) -> object:
            return self._props.get(name)

    class CssProvider(_GObjectBase):
        def __init__(self) -> None:
            super().__init__()
            self.css = ""

        def load_from_string(self, css: str) -> None:
            self.css = str(css)

        def load_from_data(self, data: bytes | str, length: int = -1) -> None:
            self.css = data.decode("utf-8", errors="ignore") if isinstance(data, bytes) else str(data)

    class StyleContext:
        _STANDARD_COLORS = {
            "theme_bg_color",
            "theme_fg_color",
            "theme_base_color",
            "theme_text_color",
            "theme_selected_bg_color",
            "theme_selected_fg_color",
            "insensitive_bg_color",
            "insensitive_fg_color",
            "insensitive_base_color",
            "borders",
            "unfocused_borders",
            "warning_color",
            "error_color",
            "success_color",
        }

        @staticmethod
        def add_provider_for_display(
            display: object,
            provider: object,
            priority: int,
        ) -> None:
            return None

        def lookup_color(self, color_name: str) -> tuple[bool, _GdkModule.RGBA | None]:
            if color_name not in self._STANDARD_COLORS:
                return (False, None)
            dark = bool(
                _GtkModule.Settings.get_default().props.gtk_application_prefer_dark_theme
            )
            if "fg" in color_name or "text" in color_name:
                val = 0.95 if dark else 0.12
            else:
                val = 0.14 if dark else 0.96
            return (True, _GdkModule.RGBA(val, val, val, 1.0))

    class _EventController(_GObjectBase):
        def __init__(self) -> None:
            super().__init__()
            self._phase = _GtkModule.PropagationPhase.BUBBLE
            self._button = 0
            self._widget: _GtkModule.Widget | None = None

        @classmethod
        def new(cls, *args, **kwargs):
            return cls(*args, **kwargs)

        def set_propagation_phase(self, phase: int) -> None:
            self._phase = int(phase)

        def set_button(self, button: int) -> None:
            self._button = int(button)

        def get_current_event(self) -> object | None:
            return None

    class GestureClick(_EventController):
        pass

    class GestureDrag(_EventController):
        pass

    class EventControllerMotion(_EventController):
        pass

    class EventControllerKey(_EventController):
        pass

    class DropTarget(_EventController):
        def __init__(self, gtype: object = None, actions: int = 0) -> None:
            super().__init__()
            self.gtype = gtype
            self.actions = actions
            self.gtypes: tuple = ()

        @classmethod
        def new(cls, gtype: object = None, actions: int = 0) -> _GtkModule.DropTarget:
            return cls(gtype, actions)

        def set_gtypes(self, types: list | tuple) -> None:
            self.gtypes = tuple(types)

    class Widget(_GObjectBase):
        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            self._parent: _GtkModule.Widget | None = None
            self._children: list[_GtkModule.Widget] = []
            self._css_classes: set[str] = set()
            self._controllers: list[object] = []
            self._visible = True
            self._sensitive = True
            self._opacity = 1.0
            self._width = 112
            self._height = 112
            self._scale_factor = 1
            self._focusable = True
            self._can_focus = True
            self._can_target = True
            self._hexpand = False
            self._vexpand = False
            self._halign = _GtkModule.Align.FILL
            self._valign = _GtkModule.Align.FILL
            self._tooltip_text: str | None = None
            self._cursor_name: str | None = None
            self._margins = {"top": 0, "bottom": 0, "start": 0, "end": 0}

        def add_css_class(self, css_class: str) -> None:
            self._css_classes.add(str(css_class))

        def remove_css_class(self, css_class: str) -> None:
            self._css_classes.discard(str(css_class))

        def has_css_class(self, css_class: str) -> bool:
            return str(css_class) in self._css_classes

        def get_css_classes(self) -> list[str]:
            return sorted(self._css_classes)

        def add_controller(self, controller: object) -> None:
            if hasattr(controller, "_widget"):
                controller._widget = self
            self._controllers.append(controller)

        def observe_controllers(self) -> list[object]:
            return list(self._controllers)

        def set_visible(self, visible: bool) -> None:
            self._visible = bool(visible)

        def get_visible(self) -> bool:
            return self._visible

        def show(self) -> None:
            self._visible = True

        def hide(self) -> None:
            self._visible = False

        def set_sensitive(self, sensitive: bool) -> None:
            self._sensitive = bool(sensitive)

        def get_sensitive(self) -> bool:
            return self._sensitive

        def set_opacity(self, opacity: float) -> None:
            self._opacity = max(0.0, min(1.0, float(opacity)))

        def get_opacity(self) -> float:
            return self._opacity

        def set_size_request(self, width: int, height: int) -> None:
            if width > 0:
                self._width = int(width)
            if height > 0:
                self._height = int(height)

        def get_width(self) -> int:
            return self._width

        def get_height(self) -> int:
            return self._height

        def get_allocated_width(self) -> int:
            return self._width

        def get_allocated_height(self) -> int:
            return self._height

        def get_scale_factor(self) -> int:
            return self._scale_factor

        def set_focusable(self, focusable: bool) -> None:
            self._focusable = bool(focusable)

        def set_can_focus(self, can_focus: bool) -> None:
            self._can_focus = bool(can_focus)

        def set_can_target(self, can_target: bool) -> None:
            self._can_target = bool(can_target)

        def set_hexpand(self, expand: bool) -> None:
            self._hexpand = bool(expand)

        def set_vexpand(self, expand: bool) -> None:
            self._vexpand = bool(expand)

        def set_halign(self, align: int) -> None:
            self._halign = int(align)

        def set_valign(self, align: int) -> None:
            self._valign = int(align)

        def set_margin_top(self, margin: int) -> None:
            self._margins["top"] = int(margin)

        def set_margin_bottom(self, margin: int) -> None:
            self._margins["bottom"] = int(margin)

        def set_margin_start(self, margin: int) -> None:
            self._margins["start"] = int(margin)

        def set_margin_end(self, margin: int) -> None:
            self._margins["end"] = int(margin)

        def set_tooltip_text(self, text: str | None) -> None:
            self._tooltip_text = text

        def get_tooltip_text(self) -> str | None:
            return self._tooltip_text

        def set_cursor_from_name(self, name: str | None) -> None:
            self._cursor_name = name

        def get_display(self) -> _GdkModule.Display:
            return Gdk.Display.get_default()

        def get_parent(self) -> _GtkModule.Widget | None:
            return self._parent

        def set_parent(self, parent: _GtkModule.Widget | None) -> None:
            self._parent = parent

        def unparent(self) -> None:
            self._parent = None

        def get_first_child(self) -> _GtkModule.Widget | None:
            return self._children[0] if self._children else None

        def get_next_sibling(self) -> _GtkModule.Widget | None:
            if self._parent is None:
                return None
            siblings = self._parent._children
            try:
                idx = siblings.index(self)
            except ValueError:
                return None
            return siblings[idx + 1] if idx + 1 < len(siblings) else None

        def queue_draw(self) -> None:
            return None

        def get_style_context(self) -> _GtkModule.StyleContext:
            return _GtkModule.StyleContext()

        def get_color(self) -> _GdkModule.RGBA:
            _found, color = self.get_style_context().lookup_color("theme_fg_color")
            return color or _GdkModule.RGBA(0.12, 0.12, 0.12, 1.0)

    class Box(Widget):
        def __init__(
            self,
            orientation: int = 0,
            spacing: int = 0,
            **kwargs,
        ) -> None:
            super().__init__(**kwargs)
            self.orientation = orientation
            self.spacing = spacing

        def append(self, child: _GtkModule.Widget) -> None:
            child._parent = self
            self._children.append(child)

        def prepend(self, child: _GtkModule.Widget) -> None:
            child._parent = self
            self._children.insert(0, child)

        def insert_child_after(
            self,
            child: _GtkModule.Widget,
            sibling: _GtkModule.Widget | None,
        ) -> None:
            child._parent = self
            if sibling is None:
                self._children.insert(0, child)
                return
            try:
                idx = self._children.index(sibling)
                self._children.insert(idx + 1, child)
            except ValueError:
                self._children.append(child)

        def remove(self, child: _GtkModule.Widget) -> None:
            if child in self._children:
                self._children.remove(child)
                child._parent = None

    class Grid(Box):
        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            self._row_spacing = 0
            self._col_spacing = 0

        def set_row_spacing(self, spacing: int) -> None:
            self._row_spacing = int(spacing)

        def set_column_spacing(self, spacing: int) -> None:
            self._col_spacing = int(spacing)

        def attach(
            self,
            child: _GtkModule.Widget,
            column: int,
            row: int,
            width: int = 1,
            height: int = 1,
        ) -> None:
            self.append(child)

    class GridView(Box):
        pass

    class HeaderBar(Box):
        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            self._title_widget: _GtkModule.Widget | None = None

        def set_title_widget(self, widget: _GtkModule.Widget | None) -> None:
            self._title_widget = widget

        def set_show_title_buttons(self, setting: bool) -> None:
            return None

        def pack_start(self, child: _GtkModule.Widget) -> None:
            self.prepend(child)

        def pack_end(self, child: _GtkModule.Widget) -> None:
            self.append(child)

    class DrawingArea(Widget):
        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            self._draw_func: Callable | None = None

        def set_content_width(self, width: int) -> None:
            self._width = int(width)

        def get_content_width(self) -> int:
            return self._width

        def set_content_height(self, height: int) -> None:
            self._height = int(height)

        def get_content_height(self) -> int:
            return self._height

        def set_draw_func(self, draw_func: Callable | None, *user_data) -> None:
            self._draw_func = draw_func

    class Label(Widget):
        def __init__(self, label: str = "", **kwargs) -> None:
            super().__init__(**kwargs)
            self._text = str(label)
            self._markup = ""
            self._xalign = 0.5
            self._yalign = 0.5
            self._wrap = False
            self._wrap_mode = _GtkModule.WrapMode.WORD
            self._max_width_chars = -1
            self._width_chars = -1
            self._justify = _GtkModule.Justification.LEFT
            self._selectable = False

        def set_text(self, text: str) -> None:
            self._text = str(text)

        def get_text(self) -> str:
            return self._text

        def set_label(self, text: str) -> None:
            self._text = str(text)

        def get_label(self) -> str:
            return self._text

        def set_markup(self, markup: str) -> None:
            self._markup = str(markup)
            self._text = str(markup)

        def set_xalign(self, xalign: float) -> None:
            self._xalign = float(xalign)

        def set_yalign(self, yalign: float) -> None:
            self._yalign = float(yalign)

        def set_wrap(self, wrap: bool) -> None:
            self._wrap = bool(wrap)

        def set_wrap_mode(self, mode: int) -> None:
            self._wrap_mode = int(mode)

        def set_max_width_chars(self, n_chars: int) -> None:
            self._max_width_chars = int(n_chars)

        def set_width_chars(self, n_chars: int) -> None:
            self._width_chars = int(n_chars)

        def set_justify(self, jtype: int) -> None:
            self._justify = int(jtype)

        def set_selectable(self, selectable: bool) -> None:
            self._selectable = bool(selectable)

        def set_ellipsize(self, mode: int) -> None:
            self._ellipsize = int(mode)

    class Image(Widget):
        def __init__(self, icon_name: str = "", **kwargs) -> None:
            super().__init__(**kwargs)
            self.icon_name = icon_name
            self._pixel_size = 16

        @classmethod
        def new_from_icon_name(cls, icon_name: str) -> _GtkModule.Image:
            return cls(icon_name=icon_name)

        def set_from_icon_name(self, icon_name: str) -> None:
            self.icon_name = icon_name

        def set_pixel_size(self, size: int) -> None:
            self._pixel_size = int(size)

    class Picture(Widget):
        pass

    class Separator(Widget):
        def __init__(self, orientation: int = 0, **kwargs) -> None:
            super().__init__(**kwargs)
            self.orientation = orientation

    class Button(Widget):
        def __init__(self, label: str = "", **kwargs) -> None:
            super().__init__(**kwargs)
            self._label = str(label)
            self._child: _GtkModule.Widget | None = None
            self._icon_name: str | None = None

        @classmethod
        def new_from_icon_name(cls, icon_name: str) -> _GtkModule.Button:
            btn = cls()
            btn._icon_name = icon_name
            return btn

        def set_label(self, label: str) -> None:
            self._label = str(label)

        def get_label(self) -> str:
            return self._label

        def set_icon_name(self, icon_name: str) -> None:
            self._icon_name = str(icon_name)

        def set_child(self, child: _GtkModule.Widget | None) -> None:
            self._child = child
            if child is not None:
                child._parent = self
                self._children = [child]
            else:
                self._children = []

        def get_child(self) -> _GtkModule.Widget | None:
            return self._child

        def clicked(self) -> None:
            self.emit("clicked")

    class CheckButton(Button):
        def __init__(self, label: str = "", **kwargs) -> None:
            super().__init__(label=label, **kwargs)
            self._active = False

        def set_active(self, active: bool) -> None:
            changed = self._active != bool(active)
            self._active = bool(active)
            if changed:
                self.emit("toggled")
                self.emit("notify::active", None)

        def get_active(self) -> bool:
            return self._active

    class Switch(Widget):
        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            self._active = False
            self._state = False

        def set_active(self, is_active: bool) -> None:
            changed = self._active != bool(is_active)
            self._active = bool(is_active)
            self._state = self._active
            if changed:
                self.emit("notify::active", None)

        def get_active(self) -> bool:
            return self._active

        def set_state(self, state: bool) -> None:
            self._state = bool(state)

    class Scale(Widget):
        def __init__(
            self,
            orientation: int = 0,
            minimum: float = 0.0,
            maximum: float = 100.0,
            step: float = 1.0,
            **kwargs,
        ) -> None:
            super().__init__(**kwargs)
            self.orientation = orientation
            self.minimum = float(minimum)
            self.maximum = float(maximum)
            self.step = float(step)
            self._value = float(minimum)
            self._draw_value = False

        @classmethod
        def new_with_range(
            cls,
            orientation: int,
            minimum: float,
            maximum: float,
            step: float,
        ) -> _GtkModule.Scale:
            return cls(
                orientation=orientation,
                minimum=minimum,
                maximum=maximum,
                step=step,
            )

        def set_value(self, value: float) -> None:
            self._value = float(value)
            self.emit("value-changed")

        def get_value(self) -> float:
            return self._value

        def set_draw_value(self, draw_value: bool) -> None:
            self._draw_value = bool(draw_value)

    class SpinButton(Scale):
        def get_value_as_int(self) -> int:
            return int(round(self._value))

        def set_digits(self, digits: int) -> None:
            return None

        def set_numeric(self, numeric: bool) -> None:
            return None

    class DropDown(Widget):
        def __init__(self, strings: list[str] | tuple[str, ...] = (), **kwargs) -> None:
            super().__init__(**kwargs)
            self._strings = tuple(strings)
            self._selected = 0

        @classmethod
        def new_from_strings(cls, strings: list[str] | tuple[str, ...]) -> _GtkModule.DropDown:
            return cls(strings=strings)

        def set_selected(self, position: int) -> None:
            self._selected = int(position)
            self.emit("notify::selected", None)

        def get_selected(self) -> int:
            return self._selected

    class ProgressBar(Widget):
        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            self._fraction = 0.0
            self._text = ""
            self._show_text = False

        def set_fraction(self, fraction: float) -> None:
            self._fraction = max(0.0, min(1.0, float(fraction)))

        def get_fraction(self) -> float:
            return self._fraction

        def set_text(self, text: str | None) -> None:
            self._text = text or ""

        def set_show_text(self, show_text: bool) -> None:
            self._show_text = bool(show_text)

        def pulse(self) -> None:
            return None

    class Revealer(Widget):
        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            self._reveal_child = False
            self._child: _GtkModule.Widget | None = None

        def set_reveal_child(self, reveal: bool) -> None:
            self._reveal_child = bool(reveal)

        def get_reveal_child(self) -> bool:
            return self._reveal_child

        def set_child(self, child: _GtkModule.Widget | None) -> None:
            self._child = child

    class Stack(Widget):
        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            self._named: dict[str, _GtkModule.Widget] = {}
            self._visible_name: str | None = None

        def set_transition_type(self, transition: int) -> None:
            return None

        def set_transition_duration(self, duration: int) -> None:
            return None

        def add_named(self, child: _GtkModule.Widget, name: str) -> None:
            self._named[name] = child
            self._children.append(child)
            if self._visible_name is None:
                self._visible_name = name

        def set_visible_child_name(self, name: str) -> None:
            self._visible_name = str(name)

        def get_visible_child_name(self) -> str | None:
            return self._visible_name

    class ScrolledWindow(Widget):
        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            self._child: _GtkModule.Widget | None = None
            self._policy = (
                _GtkModule.PolicyType.AUTOMATIC,
                _GtkModule.PolicyType.AUTOMATIC,
            )

        def set_policy(self, hpolicy: int, vpolicy: int) -> None:
            self._policy = (int(hpolicy), int(vpolicy))

        def set_min_content_height(self, height: int) -> None:
            self._height = max(self._height, int(height))

        def set_max_content_height(self, height: int) -> None:
            return None

        def set_propagate_natural_height(self, propagate: bool) -> None:
            return None

        def set_child(self, child: _GtkModule.Widget | None) -> None:
            self._child = child
            if child is not None:
                child._parent = self
                self._children = [child]
            else:
                self._children = []

        def get_child(self) -> _GtkModule.Widget | None:
            return self._child

    class _TextBuffer:
        def __init__(self) -> None:
            self._text = ""

        def set_text(self, text: str, length: int = -1) -> None:
            self._text = str(text)

        def get_bounds(self) -> tuple[int, int]:
            return (0, len(self._text))

        def get_text(self, *_args) -> str:
            return self._text

    class TextView(Widget):
        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            self._buffer = _GtkModule._TextBuffer()
            self._editable = True
            self._cursor_visible = True
            self._wrap_mode = _GtkModule.WrapMode.NONE

        def get_buffer(self) -> _GtkModule._TextBuffer:
            return self._buffer

        def set_editable(self, setting: bool) -> None:
            self._editable = bool(setting)

        def get_editable(self) -> bool:
            return self._editable

        def set_cursor_visible(self, setting: bool) -> None:
            self._cursor_visible = bool(setting)

        def get_cursor_visible(self) -> bool:
            return self._cursor_visible

        def set_wrap_mode(self, wrap_mode: int) -> None:
            self._wrap_mode = int(wrap_mode)

        def set_monospace(self, monospace: bool) -> None:
            return None

    class Popover(Widget):
        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            self._visible = False
            self._autohide = True
            self._has_arrow = True
            self._position = _GtkModule.PositionType.TOP
            self._offset = (0, 0)
            self._pointing_to = Gdk.Rectangle(0, 0, 1, 1)
            self._child: _GtkModule.Widget | None = None

        def set_autohide(self, autohide: bool) -> None:
            self._autohide = bool(autohide)

        def set_has_arrow(self, has_arrow: bool) -> None:
            self._has_arrow = bool(has_arrow)

        def set_position(self, position: int) -> None:
            self._position = int(position)

        def set_offset(self, x_offset: int, y_offset: int) -> None:
            self._offset = (int(x_offset), int(y_offset))

        def set_pointing_to(self, rect: _GdkModule.Rectangle) -> None:
            self._pointing_to = rect

        def set_child(self, child: _GtkModule.Widget | None) -> None:
            self._child = child
            if child is not None:
                child._parent = self
                self._children = [child]

        def popup(self) -> None:
            self._visible = True

        def popdown(self) -> None:
            if self._visible:
                self._visible = False
                self.emit("closed")

    class Window(Widget):
        def __init__(
            self,
            application: _GioModule.Application | None = None,
            title: str = "",
            **kwargs,
        ) -> None:
            super().__init__(**kwargs)
            self.application = application
            if application is not None:
                application._windows.append(self)
            self._title = str(title)
            self._decorated = True
            self._resizable = True
            self._modal = False
            self._hide_on_close = False
            self._destroy_with_parent = False
            self._transient_for: _GtkModule.Window | None = None
            self._child: _GtkModule.Widget | None = None
            self._titlebar: _GtkModule.Widget | None = None
            self._surface: _GdkModule.Surface | None = None
            self._realized = False
            self._active = True
            self._visible = False

        def set_title(self, title: str) -> None:
            self._title = str(title)

        def get_title(self) -> str:
            return self._title

        def set_decorated(self, setting: bool) -> None:
            self._decorated = bool(setting)

        def set_resizable(self, resizable: bool) -> None:
            self._resizable = bool(resizable)

        def set_modal(self, modal: bool) -> None:
            self._modal = bool(modal)

        def set_hide_on_close(self, setting: bool) -> None:
            self._hide_on_close = bool(setting)

        def get_hide_on_close(self) -> bool:
            return self._hide_on_close

        def set_destroy_with_parent(self, setting: bool) -> None:
            self._destroy_with_parent = bool(setting)

        def set_transient_for(self, parent: _GtkModule.Window | None) -> None:
            self._transient_for = parent

        def get_transient_for(self) -> _GtkModule.Window | None:
            return self._transient_for

        def set_default_size(self, width: int, height: int) -> None:
            self._width = int(width)
            self._height = int(height)

        def get_default_size(self) -> tuple[int, int]:
            return (self._width, self._height)

        def set_titlebar(self, titlebar: _GtkModule.Widget | None) -> None:
            self._titlebar = titlebar

        def set_child(self, child: _GtkModule.Widget | None) -> None:
            self._child = child
            if child is not None:
                child._parent = self
                self._children = [child]
            else:
                self._children = []

        def get_child(self) -> _GtkModule.Widget | None:
            return self._child

        def realize(self) -> None:
            if not self._realized:
                self._realized = True
                if self._surface is None:
                    self._surface = GdkX11.X11Surface()
                self.emit("realize")

        def get_realized(self) -> bool:
            return self._realized

        def get_surface(self) -> _GdkModule.Surface | None:
            return self._surface

        def is_active(self) -> bool:
            return self._active

        def present(self) -> None:
            self.realize()
            self._visible = True
            self._active = True
            self.emit("map")

        def close(self) -> None:
            handled = self.emit("close-request")
            if not handled:
                self._visible = False

        def destroy(self) -> None:
            self._visible = False
            if self.application is not None and self in self.application._windows:
                self.application._windows.remove(self)

    class ApplicationWindow(Window):
        pass

    class Dialog(Window):
        pass

    class Application(Gio.Application):
        pass


Gtk = _GtkModule()


# ============================================================================
# Gtk4LayerShell / Gst / Atspi / Pango / GObject
# ============================================================================


class _Gtk4LayerShellModule:
    class Layer(IntEnum):
        BACKGROUND = 0
        BOTTOM = 1
        TOP = 2
        OVERLAY = 3

    class Edge(IntEnum):
        LEFT = 0
        RIGHT = 1
        TOP = 2
        BOTTOM = 3

    class KeyboardMode(IntEnum):
        NONE = 0
        EXCLUSIVE = 1
        ON_DEMAND = 2

    @staticmethod
    def is_supported() -> bool:
        return False

    @staticmethod
    def init_for_window(_window: object) -> None:
        return None

    @staticmethod
    def set_namespace(_window: object, _namespace: str) -> None:
        return None

    @staticmethod
    def set_layer(_window: object, _layer: int) -> None:
        return None

    @staticmethod
    def set_keyboard_mode(_window: object, _mode: int) -> None:
        return None

    @staticmethod
    def set_exclusive_zone(_window: object, _zone: int) -> None:
        return None

    @staticmethod
    def set_anchor(_window: object, _edge: int, _anchor: bool) -> None:
        return None

    @staticmethod
    def set_margin(_window: object, _edge: int, _margin: int) -> None:
        return None

    @staticmethod
    def get_monitor(_window: object) -> object | None:
        return None


class _GstModule:
    @staticmethod
    def init(_argv: object = None) -> None:
        return None


class _AtspiModule:
    class Device:
        pass

    class DeviceA11yManager:
        pass

    class EventListener:
        @classmethod
        def new(cls, _callback: Callable) -> _AtspiModule.EventListener:
            return cls()

        def register(self, _event: str) -> bool:
            return False

        def deregister(self, _event: str) -> bool:
            return False

    @staticmethod
    def init() -> int:
        return 1


class _PangoModule:
    pass


class _GObjectModule:
    Object = _GObjectBase


Gtk4LayerShell = _Gtk4LayerShellModule()
Gst = _GstModule()
Atspi = _AtspiModule()
Pango = _PangoModule()
GObject = _GObjectModule()
