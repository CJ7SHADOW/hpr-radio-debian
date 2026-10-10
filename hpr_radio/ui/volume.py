"""Volume control for the header bar: a speaker button that opens a slider.

The slider is perceptual (like the desktop's own volume sliders): position p sets
a gain of p³, so 50% on the slider is about -18 dB rather than barely quieter.
This is the app's own level, separate from the system volume.
"""

from __future__ import annotations

from gi.repository import Gtk

STEP = 0.05


def to_slider(gain: float) -> float:
    return max(0.0, min(1.0, gain)) ** (1 / 3)


def to_gain(pos: float) -> float:
    return max(0.0, min(1.0, pos)) ** 3


class VolumeButton(Gtk.MenuButton):
    def __init__(self, player) -> None:
        super().__init__(tooltip_text="Volume")
        self.player = player
        self._updating = False

        self.mute_button = Gtk.ToggleButton(tooltip_text="Mute", valign=Gtk.Align.CENTER)
        self.mute_button.add_css_class("flat")
        self.mute_button.connect("toggled", self._on_mute)

        self.scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.scale.set_draw_value(False)
        self.scale.set_hexpand(True)
        self.scale.set_size_request(200, -1)
        for mark in (25, 50, 75):
            self.scale.add_mark(mark, Gtk.PositionType.BOTTOM, None)
        self.scale.connect("value-changed", self._on_scale)

        self.percent = Gtk.Label(width_chars=4, xalign=1)
        self.percent.add_css_class("numeric")

        row = Gtk.Box(spacing=6, margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        row.append(self.mute_button)
        row.append(self.scale)
        row.append(self.percent)
        pop = Gtk.Popover(child=row)
        self.set_popover(pop)
        pop.connect("show", lambda *_: self.scale.grab_focus())

        # Scrolling over the speaker icon changes the volume without opening it.
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", self._on_scroll)
        self.add_controller(scroll)

        player.connect("changed", lambda *_: self.sync())
        self.sync()

    # ---- player → widgets -----------------------------------------------------------

    def sync(self) -> None:
        pos = to_slider(self.player.volume)
        muted = self.player.muted
        self._updating = True
        try:
            if abs(self.scale.get_value() - pos * 100) > 0.5:
                self.scale.set_value(round(pos * 100))
            self.mute_button.set_active(muted)
        finally:
            self._updating = False
        pct = round(pos * 100)
        self.percent.set_label(f"{pct}%")
        if muted or pct == 0:
            icon = "audio-volume-muted-symbolic"
        elif pct < 34:
            icon = "audio-volume-low-symbolic"
        elif pct < 67:
            icon = "audio-volume-medium-symbolic"
        else:
            icon = "audio-volume-high-symbolic"
        self.set_icon_name(icon)
        self.mute_button.set_icon_name("audio-volume-muted-symbolic" if muted else "audio-volume-high-symbolic")
        self.mute_button.set_tooltip_text("Unmute" if muted else "Mute")
        self.set_tooltip_text("Volume: muted" if muted else f"Volume: {pct}%")

    # ---- widgets → player -----------------------------------------------------------

    def _on_scale(self, scale) -> None:
        if self._updating:
            return
        self.player.set_volume(to_gain(scale.get_value() / 100))
        if self.player.muted:
            self.player.set_muted(False)  # moving the slider unmutes, like the desktop does

    def _on_mute(self, button) -> None:
        if not self._updating:
            self.player.set_muted(button.get_active())

    def _on_scroll(self, _ctl, _dx, dy) -> bool:
        self.nudge(-STEP if dy > 0 else STEP)
        return True

    def nudge(self, delta: float) -> None:
        pos = to_slider(self.player.volume) + delta
        self.player.set_volume(to_gain(round(pos / STEP) * STEP))
        if self.player.muted and delta > 0:
            self.player.set_muted(False)
