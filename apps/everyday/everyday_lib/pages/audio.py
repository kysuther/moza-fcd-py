"""Audio: master volume and mute, microphone mute, per-app mixer, output device."""

from . import EPage
from ..util import bar, clean, parse_percent

APPS_PER_PAGE = 5


class AudioPage(EPage):
    title = "AUDIO"

    def draw(self, app, pg):
        ctx = app.ctx
        a = ctx.audio.snapshot
        if not ctx.audio.available:
            pg.center(6, "AUDIO NOT AVAILABLE", "amber")
            self.back(app, pg)
            return
        vol = a.get("volume")
        muted = a.get("muted")
        vol_txt = "--" if vol is None else f"{vol:3d}% {bar(vol)}"
        app.line(pg, 1, vol_txt, "UNMUTE>" if muted else "MUTE>", left_label="MASTER (TYPE %, LSK)",
                 left_color="amber" if muted else "green", right_color="amber" if muted else "white")
        step = ctx.config["audio"]["volume_step"]
        app.line(pg, 2, f"<-{step}", f"+{step}>", left_label="STEP")
        mic = a.get("mic_muted")
        if mic is None:
            app.line(pg, 3, "NO MICROPHONE", left_label="MICROPHONE", color="grey")
        else:
            app.line(pg, 3, "MUTED" if mic else "LIVE", "UNMUTE>" if mic else "MUTE>", left_label="MICROPHONE",
                     left_color="amber" if mic else "green")
        default = next((d["name"] for d in a.get("devices", []) if d["default"]), "")
        app.line(pg, 4, "<MIXER", "OUTPUT>", right_label=clean(default, 22))
        self.back(app, pg)
        self.footer_alert(app, pg)

    def on_lsk(self, app, side, n):
        audio = app.ctx.audio
        step = app.ctx.config["audio"]["volume_step"]
        if (side, n) == ("L", 1):
            if not app.scratchpad:
                app.show_message("TYPE 0-100, THEN LSK")
            else:
                pct = parse_percent(app.scratchpad)
                if pct is None:
                    app.show_message("INVALID ENTRY")
                else:
                    app.take_scratchpad()
                    audio.set_volume(pct)
        elif (side, n) == ("R", 1):
            audio.toggle_mute()
        elif (side, n) == ("L", 2):
            audio.step_volume(-step)
        elif (side, n) == ("R", 2):
            audio.step_volume(step)
        elif (side, n) == ("R", 3):
            audio.toggle_mic()
        elif (side, n) == ("L", 4):
            app.go("mixer")
        elif (side, n) == ("R", 4):
            app.go("output")
        elif (side, n) == ("L", 6):
            app.go("menu")
        else:
            return False
        return True


class MixerPage(EPage):
    """Per-app volume. Type a % then the app's LSK to set it; LSK with an empty scratchpad mutes."""

    title = "MIXER"

    @property
    def page_count(self):
        return max(1, -(-len(self._apps) // APPS_PER_PAGE))

    def __init__(self):
        super().__init__()
        self._apps = []

    def _visible(self, app):
        self._apps = app.ctx.audio.snapshot.get("apps", [])
        self.page_index = min(self.page_index, self.page_count - 1)
        start = self.page_index * APPS_PER_PAGE
        return self._apps[start:start + APPS_PER_PAGE]

    def draw(self, app, pg):
        apps = self._visible(app)
        if not apps:
            pg.center(6, "NO APPS PLAYING AUDIO", "grey")
        for i, a in enumerate(apps, start=1):
            col = "amber" if a["muted"] else "green"
            right = "MUTED" if a["muted"] else f"{a['volume']}%"
            app.line(pg, i, clean(a["name"], 14), right, left_label=bar(a["volume"]), left_color="white",
                     right_color=col, label_color="grey")
        app.line(pg, 6, "<AUDIO", "TYPE %+LSK SETS", right_color="grey")

    def on_lsk(self, app, side, n):
        if (side, n) == ("L", 6):
            app.go("audio")
            return True
        apps = self._visible(app)
        if 1 <= n <= len(apps):
            key = apps[n - 1]["key"]
            if app.scratchpad:
                pct = parse_percent(app.scratchpad)
                if pct is None:
                    app.show_message("INVALID ENTRY")
                else:
                    app.take_scratchpad()
                    app.ctx.audio.set_app_volume(key, pct)
            else:
                app.ctx.audio.toggle_app_mute(key)
            return True
        return False


class OutputPage(EPage):
    title = "OUTPUT DEVICE"

    def __init__(self):
        super().__init__()
        self._devices = []

    @property
    def page_count(self):
        return max(1, -(-len(self._devices) // APPS_PER_PAGE))

    def _visible(self, app):
        self._devices = app.ctx.audio.snapshot.get("devices", [])
        self.page_index = min(self.page_index, self.page_count - 1)
        start = self.page_index * APPS_PER_PAGE
        return self._devices[start:start + APPS_PER_PAGE]

    def draw(self, app, pg):
        devices = self._visible(app)
        if not devices:
            pg.center(6, "NO OUTPUT DEVICES", "grey")
        for i, d in enumerate(devices, start=1):
            app.line(pg, i, f"<{clean(d['name'], 23)}", left_label="ACTIVE" if d["default"] else "",
                     left_color="green" if d["default"] else "white", label_color="green")
        app.line(pg, 6, "<AUDIO")

    def on_lsk(self, app, side, n):
        if (side, n) == ("L", 6):
            app.go("audio")
            return True
        devices = self._visible(app)
        if side == "L" and 1 <= n <= len(devices):
            app.ctx.audio.set_default_device(devices[n - 1]["id"])
            app.show_message("SWITCHING OUTPUT")
            return True
        return False
