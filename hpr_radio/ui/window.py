"""Main window: header bar, page stack with a back button, and the now-playing bar."""

from __future__ import annotations

from gi.repository import Adw, Gio, GLib, Gtk

from .. import APP_NAME
from ..player import Status
from .channel import ChannelPage
from .episode import EpisodePage
from .home import HomePage
from .show import ShowPage
from .volume import VolumeButton
from .widgets import Art, PlayButton, label


class NowPlayingBar(Gtk.Box):
    def __init__(self, hub, on_open) -> None:
        super().__init__(spacing=12)
        self.hub = hub
        self.add_css_class("np-bar")
        target = Gtk.Box(spacing=12, hexpand=True)
        target.add_css_class("np-target")
        click = Gtk.GestureClick()
        click.connect("released", lambda *_: on_open())
        target.add_controller(click)
        target.set_cursor_from_name("pointer")
        self.art = Art(hub.images, 48, radius=8)
        target.append(self.art)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True)
        self.heading = label("", "np-heading")
        self.line = label("", wrap=True, lines=2)
        texts.append(self.heading)
        texts.append(self.line)
        target.append(texts)
        self.append(target)
        self.button = PlayButton(48)
        self.button.connect("clicked", lambda *_: hub.player.toggle())
        self.append(self.button)
        hub.player.connect("changed", lambda *_: self.refresh())
        hub.connect("now-art", lambda *_: self.refresh())
        self.refresh()

    def refresh(self) -> None:
        p = self.hub.player
        if p.kind == "live" and p.station:
            st = p.station
            self.heading.set_label(st.full_name)
            line = {Status.CONNECTING: "Connecting…", Status.RECONNECTING: "Connection dropped, reconnecting…",
                    Status.ERROR: "Couldn't reach this channel"}.get(p.status)
            if line is None:
                if p.status == Status.STOPPED:
                    line = "Stopped"
                elif p.song_title:
                    line = f"{p.song_title} · {p.song_artist}" if p.song_artist else p.song_title
                else:
                    line = st.name
            self.line.set_label(line)
            art = self.hub.now_art
            self.art.set(art.image_url if art else None, st.channel, st.color)
            self.button.update(p.status, pauses=False)
        elif p.kind == "episode" and p.show and p.episode:
            self.heading.set_label(p.show.title)
            prefix = {Status.PAUSED: "Paused · ", Status.CONNECTING: "Loading · ",
                      Status.RECONNECTING: "Reconnecting · ", Status.ERROR: "Couldn't load · "}.get(p.status, "")
            self.line.set_label(prefix + p.episode.title)
            self.art.set(p.show.image_url, p.show.badge, "#8A4B1F")
            self.button.update(p.status, pauses=True)
        else:
            self.heading.set_label("Pick a channel to start listening")
            self.line.set_label("")
            self.art.set(None, "HPR", "#8A4B1F")
            self.button.update(Status.STOPPED, pauses=False)


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app, hub) -> None:
        super().__init__(application=app, title=APP_NAME)
        self.hub = hub
        self.set_default_size(460, 800)
        self.set_size_request(360, 480)

        self.header = Adw.HeaderBar()
        self.window_title = Adw.WindowTitle(title="Heartland Public Radio")
        self.header.set_title_widget(self.window_title)
        self.back_button = Gtk.Button(icon_name="go-previous-symbolic", tooltip_text="Back")
        self.back_button.connect("clicked", lambda *_: self.go_back())
        self.header.pack_start(self.back_button)
        menu = Gio.Menu()
        menu.append("Reload channel list", "app.reload")
        menu.append("Keyboard shortcuts", "app.shortcuts")
        menu.append("About HPR Radio", "app.about")
        self.header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                            tooltip_text="Main menu"))
        self.volume = VolumeButton(hub.player)
        self.header.pack_end(self.volume)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT,
                               transition_duration=180, vexpand=True)
        self.toasts = Adw.ToastOverlay(vexpand=True)
        self.toasts.set_child(self.stack)
        self.bar = NowPlayingBar(hub, self.open_now_playing)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(self.header)
        box.append(self.toasts)
        box.append(self.bar)
        self.set_content(box)

        self.pages: list[tuple[str, Gtk.Widget]] = []
        self.home = HomePage(hub, self.open_channel, self.open_show)
        self.stack.add_named(self.home, "home")
        self._update_chrome()

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)
        back_mouse = Gtk.GestureClick(button=8)  # mouse "back" button
        back_mouse.connect("pressed", lambda *_: self.go_back())
        self.add_controller(back_mouse)
        hub.player.connect("changed", lambda *_: self._update_chrome())
        hub.catalog.connect("changed", lambda *_: self._on_catalog_changed())

    # ---- navigation ----------------------------------------------------------------

    def _push(self, route: str, page: Gtk.Widget) -> None:
        if self.pages and self.pages[-1][0] == route:
            return
        name = f"p{len(self.pages)}-{GLib.get_monotonic_time()}"
        self.stack.add_named(page, name)
        self.pages.append((route, page))
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT)
        self.stack.set_visible_child(page)
        self._update_chrome()

    def go_back(self) -> None:
        if not self.pages:
            return
        _route, page = self.pages.pop()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_RIGHT)
        self.stack.set_visible_child(self.pages[-1][1] if self.pages else self.home)
        if hasattr(page, "teardown"):
            page.teardown()
        GLib.timeout_add(300, lambda: (self.stack.remove(page), False)[1])
        self._update_chrome()

    def open_channel(self, station) -> None:
        self._push(f"ch:{station.id}", ChannelPage(self.hub, station))

    def open_show(self, show) -> None:
        # Coming from the episode player's "All episodes": go back to the show page if it's below.
        for i, (route, _p) in enumerate(self.pages):
            if route == f"show:{show.id}":
                while len(self.pages) > i + 1:
                    self.go_back()
                return
        self._push(f"show:{show.id}", ShowPage(self.hub, show, self.open_episode_player, self.open_url))

    def open_episode_player(self) -> None:
        if self.hub.player.kind == "episode":
            self._push("player", EpisodePage(self.hub, self.open_show, self.open_url))

    def open_now_playing(self) -> None:
        p = self.hub.player
        if p.kind == "live" and p.station:
            self.open_channel(p.station)
        elif p.kind == "episode":
            self.open_episode_player()

    def open_url(self, url: str) -> None:
        try:
            Gio.AppInfo.launch_default_for_uri(url, None)
        except GLib.Error:
            self.toast("Couldn't open a web browser")

    def toast(self, text: str) -> None:
        self.toasts.add_toast(Adw.Toast(title=text, timeout=3))

    def _on_catalog_changed(self) -> None:
        # A channel or show removed from channels.json while its page is open: go home.
        for route, _p in list(self.pages):
            kind, _, ident = route.partition(":")
            if (kind == "ch" and self.hub.catalog.station(ident) is None) or \
                    (kind == "show" and self.hub.catalog.show(ident) is None):
                while self.pages:
                    self.go_back()
                break

    def _update_chrome(self) -> None:
        self.back_button.set_visible(bool(self.pages))
        if self.pages:
            top = self.pages[-1][1]
            self.window_title.set_title(getattr(top, "title", "Heartland Public Radio"))
            self.window_title.set_subtitle("Heartland Public Radio")
        else:
            self.window_title.set_title("Heartland Public Radio")
            self.window_title.set_subtitle("")
        # Hide the bar where the page already has the controls for what's playing.
        p = self.hub.player
        route = self.pages[-1][0] if self.pages else ""
        page_has_controls = route == "player" or (
            p.kind == "live" and p.station is not None and route == f"ch:{p.station.id}")
        self.bar.set_visible(not page_has_controls)

    def _on_key(self, _ctl, keyval, _code, state) -> bool:
        from gi.repository import Gdk
        if keyval == Gdk.KEY_Escape or (keyval == Gdk.KEY_Left and state & Gdk.ModifierType.ALT_MASK):
            if self.pages:
                self.go_back()
                return True
        ctrl = state & Gdk.ModifierType.CONTROL_MASK
        if ctrl and keyval in (Gdk.KEY_Up, Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add):
            self.volume.nudge(+0.05)
            return True
        if ctrl and keyval in (Gdk.KEY_Down, Gdk.KEY_minus, Gdk.KEY_KP_Subtract):
            self.volume.nudge(-0.05)
            return True
        if ctrl and keyval in (Gdk.KEY_m, Gdk.KEY_M):
            self.hub.player.set_muted(not self.hub.player.muted)
            return True
        if keyval == Gdk.KEY_space and not isinstance(self.get_focus(), (Gtk.Text, Gtk.Entry)):
            self.hub.player.toggle()
            return True
        return False
