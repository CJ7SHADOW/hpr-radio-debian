"""Shared widgets: cover art with a colored placeholder, play button, formatting helpers."""

from __future__ import annotations

import re
import time

from gi.repository import Gtk, Pango

from ..player import Status

_DEFAULT_COLOR = "#5B4636"
_color_classes: set[str] = set()
_dynamic_css: list[str] = []
_dynamic_provider: Gtk.CssProvider | None = None


def load_css(provider: Gtk.CssProvider, css: str) -> None:
    """CssProvider.load_from_string arrived in GTK 4.12; Debian 12 has 4.8."""
    if hasattr(provider, "load_from_string"):
        provider.load_from_string(css)
        return
    try:
        provider.load_from_data(css.encode())          # GTK 4.8–4.10
    except TypeError:
        provider.load_from_data(css, -1)               # GTK 4.12+ without load_from_string


def color_class(hex_color: str | None) -> str:
    """CSS class giving a placeholder tile this color (as a gradient)."""
    global _dynamic_provider
    c = hex_color if hex_color and re.fullmatch(r"#[0-9a-fA-F]{6}", hex_color) else _DEFAULT_COLOR
    name = "ph-" + c[1:].lower()
    if name not in _color_classes:
        _color_classes.add(name)
        r, g, b = (int(c[i:i + 2], 16) for i in (1, 3, 5))
        dark = "#%02x%02x%02x" % (int(r * 0.55), int(g * 0.55), int(b * 0.55))
        _dynamic_css.append(f".{name} {{ background-image: linear-gradient(135deg, {c}, {dark}); }}")
        from gi.repository import Gdk
        display = Gdk.Display.get_default()
        if _dynamic_provider is None and display is not None:
            _dynamic_provider = Gtk.CssProvider()
            Gtk.StyleContext.add_provider_for_display(display, _dynamic_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        if _dynamic_provider is not None:
            load_css(_dynamic_provider, "\n".join(_dynamic_css))
    return name


class Art(Gtk.Overlay):
    """A square cover image over a colored tile with a label, so a missing cover still looks deliberate."""

    def __init__(self, images, size: int, radius: int = 12) -> None:
        super().__init__()
        self.images = images
        self._size = size
        self._url: str | None = None
        self.set_size_request(size, size)
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)
        self.set_overflow(Gtk.Overflow.HIDDEN)
        # Fixed size: don't let the tile inside ask the parent rows to stretch.
        self.set_hexpand(False)
        self.set_vexpand(False)
        self.add_css_class("art")
        self.add_css_class(f"radius-{radius}")

        self._tile = Gtk.Box()
        self._tile.add_css_class("art-tile")
        self._label = Gtk.Label(hexpand=True, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self._label.add_css_class("art-label")
        bucket = "xs" if size <= 56 else "s" if size <= 80 else "m" if size <= 140 else "l"
        self._label.add_css_class(f"art-label-{bucket}")
        self._tile.append(self._label)
        self.set_child(self._tile)

        self._picture = Gtk.Picture(can_shrink=True)
        if hasattr(self._picture, "set_content_fit"):
            self._picture.set_content_fit(Gtk.ContentFit.COVER)
        self._picture.set_size_request(size, size)
        self.add_overlay(self._picture)
        self._color_class: str | None = None

    def set(self, url: str | None, label: str, color: str | None) -> None:
        self._label.set_label(label)
        cls = color_class(color)
        if cls != self._color_class:
            if self._color_class:
                self._tile.remove_css_class(self._color_class)
            self._tile.add_css_class(cls)
            self._color_class = cls
        if url == self._url:
            return
        self._url = url
        self._picture.set_paintable(None)
        if url:
            self.images.get(url, lambda tex, u=url: self._loaded(u, tex))

    def _loaded(self, url: str, tex) -> None:
        if url == self._url:
            self._picture.set_paintable(tex)


def label(text: str = "", *css: str, wrap: bool = False, lines: int = 1, xalign: float = 0.0,
          selectable: bool = False) -> Gtk.Label:
    lab = Gtk.Label(label=text, xalign=xalign, selectable=selectable)
    for c in css:
        lab.add_css_class(c)
    if wrap:
        lab.set_wrap(True)
        lab.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        if lines > 1:
            lab.set_lines(lines)
            lab.set_ellipsize(Pango.EllipsizeMode.END)
    else:
        lab.set_ellipsize(Pango.EllipsizeMode.END)
    return lab


class PlayButton(Gtk.Button):
    """Round play / stop (or pause, for episodes) button with a 'connecting' spinner."""

    def __init__(self, size: int = 48, suggested: bool = True) -> None:
        super().__init__(valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        self.add_css_class("circular")
        if suggested:
            self.add_css_class("suggested-action")
        self.add_css_class("play-button")
        self.set_size_request(size, size)
        self._stack = Gtk.Stack()
        self._icon = Gtk.Image(pixel_size=max(16, size // 2 - 4))
        self._spinner = Gtk.Spinner(spinning=True, width_request=size // 2, height_request=size // 2)
        self._stack.add_named(self._icon, "icon")
        self._stack.add_named(self._spinner, "spin")
        self.set_child(self._stack)
        self.update(Status.STOPPED, pauses=False)

    def update(self, status: str, pauses: bool) -> None:
        if status in (Status.CONNECTING, Status.RECONNECTING):
            self._stack.set_visible_child_name("spin")
            self.set_tooltip_text("Pause" if pauses else "Stop")
        else:
            self._stack.set_visible_child_name("icon")
            active = status == Status.PLAYING
            self._icon.set_from_icon_name(
                ("media-playback-pause-symbolic" if pauses else "media-playback-stop-symbolic")
                if active else "media-playback-start-symbolic")
            self.set_tooltip_text(("Pause" if pauses else "Stop") if active else "Play")


def status_line(status: str, episode: bool = False) -> str | None:
    return {
        Status.CONNECTING: "Connecting…",
        Status.RECONNECTING: "Connection dropped, reconnecting…",
        Status.ERROR: ("Couldn't load this episode. Press play to try again." if episode
                       else "Couldn't reach this channel. Press play to try again."),
    }.get(status)


def format_clock(seconds: float) -> str:
    s = max(0, int(seconds))
    h, m, s = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def format_remaining(seconds: float) -> str:
    mins = max(1, int((seconds + 59) // 60))
    h, m = divmod(mins, 60)
    if h and m:
        return f"{h} hr {m} min left"
    if h:
        return f"{h} hr left"
    return f"{m} min left"


def format_time_of_day(unix: int) -> str:
    if unix <= 0:
        return ""
    t = time.localtime(unix)
    return time.strftime("%-I:%M %p", t)


def clamp(child: Gtk.Widget, width: int = 640) -> Gtk.Widget:
    from gi.repository import Adw
    c = Adw.Clamp(maximum_size=width, tightening_threshold=width - 40)
    c.set_child(child)
    return c


def scrolled(child: Gtk.Widget) -> Gtk.ScrolledWindow:
    sw = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
    sw.set_child(child)
    return sw


def clear(box: Gtk.Widget) -> None:
    child = box.get_first_child()
    while child is not None:
        nxt = child.get_next_sibling()
        box.remove(child)
        child = nxt
