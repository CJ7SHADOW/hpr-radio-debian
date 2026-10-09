"""A show's page: about the show, then recent episodes, then the archive by year."""

from __future__ import annotations

from typing import Callable

from gi.repository import Gio, GLib, Gtk

from .. import episodes, net
from ..player import Status
from .widgets import Art, PlayButton, clamp, clear, format_remaining, label, scrolled


class ShowPage(Gtk.Box):
    def __init__(self, hub, show, open_player: Callable, open_url: Callable) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.hub = hub
        self.show = show
        self.title = show.title
        self.open_player = open_player
        self.open_url = open_url

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                       margin_top=18, margin_bottom=24, margin_start=16, margin_end=16)
        self.append(scrolled(clamp(body)))

        head = Gtk.Box(spacing=16)
        art = Art(hub.images, 112, radius=16)
        art.set(show.image_url, show.badge, "#8A4B1F")
        head.append(art)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.CENTER, hexpand=True)
        texts.append(label("ON DEMAND", "eyebrow"))
        texts.append(label(show.title, "page-title", wrap=True))
        if show.host:
            texts.append(label(f"with {show.host}", "subtitle"))
        if show.schedule:
            texts.append(label(show.schedule, "dim", wrap=True))
        head.append(texts)
        body.append(head)
        if show.description:
            d = label(show.description, wrap=True)
            d.set_margin_top(10)
            body.append(d)

        self.continue_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.append(self.continue_box)
        self.sections = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        body.append(self.sections)
        foot = label("Episodes stream from the Internet Archive, courtesy of Heartland Public Radio.",
                     "footer", wrap=True)
        foot.set_margin_top(16)
        body.append(foot)

        self.recent_eps = episodes.cached(show.recent_source)
        self.archive_eps = episodes.cached(show.archive_source) if show.archive_source else None
        self.recent_failed = self.archive_failed = False
        self._rows: list[_EpisodeRow] = []
        self._handlers = [
            (hub.player, hub.player.connect("changed", lambda *_: self.refresh())),
            (hub, hub.connect("progress", lambda *_: self.refresh())),
        ]
        self.rebuild()
        self.load()

    def teardown(self) -> None:
        for obj, hid in self._handlers:
            obj.disconnect(hid)
        self._handlers = []

    def load(self) -> None:
        self.recent_failed = self.archive_failed = False
        net.run_async(episodes.load, self.show.recent_source, callback=self._got_recent)
        if self.show.archive_source:
            net.run_async(episodes.load, self.show.archive_source, callback=self._got_archive)

    def _got_recent(self, eps) -> None:
        if eps is not None:
            self.recent_eps = eps
        else:
            self.recent_failed = self.recent_eps is None
        self.rebuild()

    def _got_archive(self, eps) -> None:
        if eps is not None:
            self.archive_eps = eps
        else:
            self.archive_failed = self.archive_eps is None
        self.rebuild()

    def rebuild(self) -> None:
        clear(self.sections)
        self._rows = []

        def section(title: str, eps) -> None:
            self.sections.append(label(title, "section"))
            lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            lb.add_css_class("boxed-list")
            lb.connect("row-activated", lambda _l, row: self._activate(row.episode))
            for ep in eps:
                row = _EpisodeRow(self, ep)
                self._rows.append(row)
                lb.append(row)
            self.sections.append(lb)

        if self.recent_eps is not None:
            section("Recent episodes", self.recent_eps)
        else:
            self.sections.append(label("Recent episodes", "section"))
            self.sections.append(self._loading_or_error(self.recent_failed))

        if self.show.archive_source:
            if self.archive_eps is not None:
                recent_keys = {e.key for e in self.recent_eps or []}
                by_year: dict[int, list] = {}
                for e in self.archive_eps:
                    if e.key not in recent_keys:
                        by_year.setdefault(e.year, []).append(e)
                for year in sorted(by_year, reverse=True):
                    section(f"Archive · {year}" if year else "Archive", by_year[year])
            elif self.recent_eps is not None:
                self.sections.append(label("Archive", "section"))
                self.sections.append(self._loading_or_error(self.archive_failed, "Loading the archive…"))
        self.refresh()

    def _loading_or_error(self, failed: bool, text: str = "Loading episodes…") -> Gtk.Widget:
        box = Gtk.Box(spacing=10, margin_top=6)
        if failed:
            box.append(label("Couldn't load episodes from hpr.org.", "dim"))
            b = Gtk.Button(label="Try again")
            b.connect("clicked", lambda *_: (self.load(), self.rebuild()))
            box.append(b)
        else:
            box.append(Gtk.Spinner(spinning=True))
            box.append(label(text, "dim"))
        return box

    def refresh(self) -> None:
        for row in self._rows:
            row.refresh()
        clear(self.continue_box)
        last = self.hub.progress.last
        p = self.hub.player
        if last and last[0] == self.show.id and not (p.episode and p.episode.key == last[1]):
            ep = next((e for e in (self.recent_eps or []) + (self.archive_eps or []) if e.key == last[1]), None)
            if ep is not None:
                self.continue_box.append(self._continue_card(ep))

    def _continue_card(self, ep) -> Gtk.Widget:
        card = Gtk.Box(spacing=12, margin_top=14)
        card.add_css_class("continue-card")
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
        texts.append(label("CONTINUE LISTENING", "eyebrow"))
        texts.append(label(ep.title, "row-title"))
        pr = self.hub.progress.get(ep.key)
        if pr and pr.duration > 0:
            texts.append(label(format_remaining(pr.remaining), "dim"))
            bar = Gtk.ProgressBar(fraction=pr.fraction)
            bar.add_css_class("thin")
            texts.append(bar)
        card.append(texts)
        b = Gtk.Button(valign=Gtk.Align.CENTER)
        b.add_css_class("suggested-action")
        b.add_css_class("pill")
        b.set_child(Gtk.Label(label="Resume"))
        b.connect("clicked", lambda *_: (self.hub.player.play_episode(self.show, ep), self.open_player()))
        card.append(b)
        return card

    def _activate(self, ep) -> None:
        p = self.hub.player
        if p.episode and p.episode.key == ep.key:
            self.open_player()
        else:
            p.play_episode(self.show, ep)


class _EpisodeRow(Gtk.ListBoxRow):
    def __init__(self, page: ShowPage, ep) -> None:
        super().__init__(activatable=True)
        self.page = page
        self.episode = ep
        hub = page.hub

        box = Gtk.Box(spacing=8)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.CENTER, hexpand=True)
        self.title = label(ep.title, "row-title")
        texts.append(self.title)
        if ep.note:
            texts.append(label(ep.note, "dim", wrap=True, lines=2))
        self.progress_box = Gtk.Box(spacing=8)
        self.bar = Gtk.ProgressBar(valign=Gtk.Align.CENTER, width_request=96)
        self.bar.add_css_class("thin")
        self.progress_label = label("", "time")
        self.progress_box.append(self.bar)
        self.progress_box.append(self.progress_label)
        texts.append(self.progress_box)
        box.append(texts)

        self.button = PlayButton(36, suggested=False)
        self.button.add_css_class("flat")
        self.button.connect("clicked", self._on_play)
        box.append(self.button)

        # ⋮ menu: play from the beginning, playlist PDF, played / unplayed
        group = Gio.SimpleActionGroup()
        for name, fn in (("restart", self._restart), ("playlist", self._playlist),
                         ("played", self._played), ("unplayed", self._unplayed)):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", lambda *_a, f=fn: f())
            group.add_action(a)
        self.insert_action_group("ep", group)
        self._actions = group
        self.menu_button = Gtk.MenuButton(icon_name="view-more-symbolic", valign=Gtk.Align.CENTER)
        self.menu_button.add_css_class("flat")
        self.menu_button.set_tooltip_text("More options")
        box.append(self.menu_button)
        self.set_child(box)
        self.hub = hub

    def refresh(self) -> None:
        hub, ep = self.hub, self.episode
        p = hub.player
        current = p.episode is not None and p.episode.key == ep.key
        if current:
            self.title.add_css_class("current")
        else:
            self.title.remove_css_class("current")
        self.button.update(p.status if current else Status.STOPPED, pauses=True)

        pr = hub.progress.get(ep.key)
        if pr and pr.done:
            self.bar.set_visible(False)
            self.progress_label.set_label("✓ Played")
            self.progress_box.set_visible(True)
        elif pr and pr.duration > 0:
            self.bar.set_visible(True)
            self.bar.set_fraction(pr.fraction)
            self.progress_label.set_label(format_remaining(pr.remaining))
            self.progress_box.set_visible(True)
        else:
            self.progress_box.set_visible(False)

        menu = Gio.Menu()
        if pr and not pr.done and pr.position > 0:
            menu.append("Play from the beginning", "ep.restart")
        if ep.playlist_url:
            menu.append("View playlist (PDF)", "ep.playlist")
        menu.append("Mark as unplayed" if pr and pr.done else "Mark as played",
                    "ep.unplayed" if pr and pr.done else "ep.played")
        self.menu_button.set_menu_model(menu)

    def _on_play(self, _b) -> None:
        p = self.hub.player
        if p.episode and p.episode.key == self.episode.key and p.is_active:
            p.pause()
        else:
            p.play_episode(self.page.show, self.episode)

    def _restart(self) -> None:
        self.hub.player.play_episode(self.page.show, self.episode, from_start=True)
        self.hub.emit("progress")

    def _playlist(self) -> None:
        if self.episode.playlist_url:
            self.page.open_url(self.episode.playlist_url)

    def _played(self) -> None:
        self.hub.progress.mark_played(self.episode.key)
        GLib.idle_add(lambda: (self.hub.emit("progress"), False)[1])

    def _unplayed(self) -> None:
        self.hub.progress.clear(self.episode.key)
        GLib.idle_add(lambda: (self.hub.emit("progress"), False)[1])
