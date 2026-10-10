"""The application: wires settings, the shared Hub, the window and MPRIS together."""

from __future__ import annotations

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gst", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import APP_ID, APP_NAME, __version__  # noqa: E402
from .hub import Hub  # noqa: E402
from .mpris import Mpris  # noqa: E402
from .paths import SHARE_DIR, ensure_dirs  # noqa: E402
from .player import Status  # noqa: E402
from .settings import Settings  # noqa: E402
from .ui.widgets import load_css  # noqa: E402
from .ui.window import MainWindow  # noqa: E402


class HprApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        GLib.set_application_name(APP_NAME)
        self.add_main_option("config-url", 0, GLib.OptionFlags.NONE, GLib.OptionArg.STRING,
                             "Address of channels.json to use for this run", "URL")
        self.add_main_option("version", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Show the version and exit", None)
        self.hub: Hub | None = None
        self.window: MainWindow | None = None
        self.mpris: Mpris | None = None
        self._inhibit_cookie = 0
        self._config_url: str | None = None

    def do_handle_local_options(self, options: GLib.VariantDict) -> int:
        if options.contains("version"):
            print(f"{APP_NAME} {__version__}")
            return 0
        if options.contains("config-url"):
            self._config_url = options.lookup_value("config-url").get_string()
        return -1

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        ensure_dirs()
        settings = Settings()
        if self._config_url:
            settings.override_config_url = self._config_url
        self.hub = Hub(settings)
        self._load_styles()

        for name, fn in (("reload", self._on_reload), ("about", self._on_about),
                         ("shortcuts", self._on_shortcuts), ("quit", lambda *_: self.quit()),
                         ("play-pause", lambda *_: self.hub.player.toggle())):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", fn)
            self.add_action(a)
        self.set_accels_for_action("app.quit", ["<Control>q"])
        self.set_accels_for_action("app.reload", ["<Control>r", "F5"])

        self.mpris = Mpris(self.hub.player, self.hub.catalog, self.hub.now_art_uri,
                           on_raise=lambda: self.window and self.window.present(),
                           on_quit=self.quit)
        self.hub.connect("now-art", lambda *_: self.mpris.update())
        self.hub.player.connect("changed", self._on_player_changed)
        self.hub.catalog.refresh()

    def do_activate(self) -> None:
        if self.window is None:
            self.window = MainWindow(self, self.hub)
            self.window.connect("close-request", self._on_close)
            self.hub.start_polling()
        self.window.present()

    def do_shutdown(self) -> None:
        if self.hub:
            self.hub.stop_polling()
            self.hub.player.shutdown()
        if self.mpris:
            self.mpris.close()
        Adw.Application.do_shutdown(self)

    def _on_close(self, _win) -> bool:
        self.hub.player.shutdown()
        return False

    def _load_styles(self) -> None:
        display = Gdk.Display.get_default()
        if display is None:
            return
        base, dark = Gtk.CssProvider(), Gtk.CssProvider()
        load_css(base, (SHARE_DIR / "style.css").read_text("utf-8"))
        load_css(dark, (SHARE_DIR / "style-dark.css").read_text("utf-8"))
        Gtk.StyleContext.add_provider_for_display(display, base, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        sm = Adw.StyleManager.get_default()

        def apply(*_):
            if sm.get_dark():
                Gtk.StyleContext.add_provider_for_display(display, dark, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
            else:
                Gtk.StyleContext.remove_provider_for_display(display, dark)
        sm.connect("notify::dark", apply)
        apply()

    def _on_player_changed(self, player) -> None:
        # Keep the computer from going to sleep while something is playing.
        playing = player.status in Status.ACTIVE
        if playing and not self._inhibit_cookie and self.window is not None:
            self._inhibit_cookie = self.inhibit(self.window, Gtk.ApplicationInhibitFlags.SUSPEND,
                                                "Playing Heartland Public Radio")
        elif not playing and self._inhibit_cookie:
            self.uninhibit(self._inhibit_cookie)
            self._inhibit_cookie = 0

    def _on_reload(self, *_a) -> None:
        self.hub.catalog.refresh()
        self.hub.refresh_recent()
        if self.window:
            self.window.toast("Checking for channel updates…")

    def _on_shortcuts(self, *_a) -> None:
        text = ("Space — play / stop\n"
                "Esc or Alt+← — back\n"
                "Ctrl+↑ / Ctrl+↓ — volume up / down\n"
                "Ctrl+M — mute\n"
                "Ctrl+R or F5 — reload channel list\n"
                "Ctrl+Q — quit\n\n"
                "Media keys work too: Play/Pause, Next/Previous\n"
                "(switch channels, or skip within an episode).")
        dlg = Adw.MessageDialog(transient_for=self.window, heading="Keyboard shortcuts", body=text)
        dlg.add_response("ok", "OK")
        dlg.present()

    def _on_about(self, *_a) -> None:
        about = Adw.AboutWindow(
            transient_for=self.window,
            application_name=APP_NAME,
            application_icon=APP_ID,
            version=__version__,
            developer_name="Shawn Bolton, KG4BHR",
            comments="An unofficial desktop player for Heartland Public Radio: "
                     "Classic Country and Bluegrass Gospel, commercial-free.\n\n"
                     f"Channel list: {self.hub.catalog.source}\n{self.hub.catalog.config_url}",
            website="https://hpr.org",
            license_type=Gtk.License.MIT_X11,
        )
        about.present()


def main() -> int:
    return HprApplication().run(sys.argv)
