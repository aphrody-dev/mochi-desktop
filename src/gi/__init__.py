"""Cross-platform pure-Python PyGObject (`gi`) fallback for Mochi Desktop.

When native PyGObject (`gi._gi`) is installed on a Linux/macOS/Windows host,
this package delegates to it. When native PyGObject is absent, it provides a
pure-Python implementation of `gi.require_version` and `gi.repository` (`GLib`,
`Gio`, `Gtk`, `Gdk`, `GdkX11`, `GdkWayland`, `Gst`, `Atspi`, `Gtk4LayerShell`,
`Pango`, `GObject`) so that Mochi's state machines, widgets, menus, timers,
and test suite run 100% cross-platform on Windows, macOS, and headless Linux.
"""

from __future__ import annotations

from . import repository as repository


def require_version(namespace: str, version: str) -> None:
    """Validate or record a required GIR namespace version."""
    if not isinstance(namespace, str) or not isinstance(version, str):
        raise ValueError("namespace and version must be strings")
