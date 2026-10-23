"""Media: now playing and transport controls (works with anything in Windows' media overlay)."""

from . import EPage
from ..util import bar, clean, mmss, wrap2


class MediaPage(EPage):
    title = "MEDIA"

    def draw(self, app, pg):
        media = app.ctx.media
        m = media.snapshot
        if not media.available:
            pg.center(6, "MEDIA NOT AVAILABLE", "amber")
        elif not m.get("active"):
            pg.center(5, "NOTHING PLAYING", "grey")
        else:
            playing = m.get("status") == "Playing"
            first, second = wrap2(clean(m.get("title"), 60))
            app.line(pg, 1, first, left_label=clean(m.get("status")), color="cyan" if playing else "white",
                     label_color="green" if playing else "amber")
            if second:
                pg.write(4, 0, second, "cyan" if playing else "white")
            app.line(pg, 3, clean(m.get("artist")), left_label="ARTIST" if m.get("artist") else "", color="white")
            dur = float(m.get("duration") or 0)
            if dur > 0:
                pos = media.position()
                app.line(pg, 4, f"{mmss(pos)} {bar(pos / dur * 100, 8)} {mmss(dur)}", left_label="POSITION",
                         color="green")
        app.line(pg, 5, "<PREV", "NEXT>")
        playing = m.get("status") == "Playing"
        app.line(pg, 6, "<MENU", "PAUSE>" if playing else "PLAY>")
        self.footer_alert(app, pg)

    def on_lsk(self, app, side, n):
        media = app.ctx.media
        if (side, n) == ("L", 5):
            media.previous()
        elif (side, n) == ("R", 5):
            media.next()
        elif (side, n) == ("R", 6):
            media.play_pause()
        elif (side, n) == ("L", 6):
            app.go("menu")
        else:
            return False
        return True

    def on_key(self, app, name):
        if name == "EXEC" and not app.exec_pending:   # EXEC doubles as play/pause on this page
            app.ctx.media.play_pause()
            return True
        return False
