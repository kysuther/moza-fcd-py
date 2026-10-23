"""Discord: connection, mute/deafen, your voice channel and who's in it (page 1); recent notifications
(page 2 - opening it turns the MSG light off)."""

import time

from . import EPage
from ..util import clean, wrap_lines

STATE_COLORS = {"CONNECTED": "green", "APPROVE IN DISCORD": "amber", "NOT APPROVED": "amber", "ERROR": "amber"}


def ago(then, now) -> str:
    mins = int((now - then) // 60)
    return "NOW" if mins < 1 else f"{mins}M" if mins < 60 else f"{mins // 60}H"


class DiscordPage(EPage):
    page_count = 2

    def __init__(self, wall_clock=time.time):
        super().__init__()
        self.wall_clock = wall_clock

    @property
    def title(self):
        return "DISCORD" if self.page_index == 0 else "NOTIFICATIONS"

    def draw(self, app, pg):
        d = app.ctx.discord
        s = d.snapshot
        if not d.available:
            pg.center(6, "DISCORD N/A", "amber")
        elif not s.get("configured"):
            pg.center(3, "NOT SET UP", "amber")
            for r, text in enumerate(("ADD CLIENT_ID AND", "CLIENT_SECRET UNDER", "[DISCORD] IN CONFIG.TOML,",
                                      "THEN RESTART THE APP"), start=5):
                pg.center(r, text, "grey", small=True)
        elif self.page_index == 0:
            self._voice(app, pg, s)
        else:
            self._notifications(app, pg, s)
            if s.get("unseen"):
                d.mark_seen()
        if self.page_index == 0:
            unseen = s.get("unseen", 0)
            app.line(pg, 6, "<MENU", "NOTIFICATIONS>", right_label=f"{unseen} NEW" if unseen else "",
                     label_color="amber")
        else:
            app.line(pg, 6, "<VOICE", "CLEAR>" if s.get("notifications") else "")
        if s.get("error") and s.get("state") != "CONNECTED":
            pg.center(11, clean(s["error"]), "amber", small=True)
        else:
            self.draw_status(app, pg)

    def _voice(self, app, pg, s):
        state = s.get("state", "")
        connected = state == "CONNECTED"
        app.line(pg, 1, clean(state, 18 if connected else 24), clean(s.get("user"), 12) if connected else "",
                 left_label="DISCORD", right_label="SIGNED IN AS" if connected else "",
                 left_color=STATE_COLORS.get(state, "grey"), right_color="cyan", label_color="grey")
        if not connected:
            if state == "APPROVE IN DISCORD":
                pg.center(5, "CHECK THE DISCORD APP", "white", small=True)
                pg.center(6, "AND PRESS AUTHORIZE", "white", small=True)
            app.line(pg, 5, right="RECONNECT>")
            return
        mute, deaf = s.get("mute"), s.get("deaf")
        app.line(pg, 2, "<UNMUTE" if mute else "<MUTE", "UNDEAFEN>" if deaf else "DEAFEN>",
                 left_label="MIC MUTED" if mute else "MIC LIVE", right_label="DEAFENED" if deaf else "SOUND ON",
                 label_color="amber" if (mute or deaf) else "green")
        ch = s.get("channel")
        if not ch:
            app.line(pg, 3, "NOT IN VOICE", left_label="VOICE CHANNEL", left_color="grey", label_color="grey")
            return
        app.line(pg, 3, clean(ch["name"], 16), "LEAVE>", left_label="VOICE CHANNEL",
                 right_label=f"{len(ch['members'])} IN CALL", left_color="cyan", label_color="grey")
        for i, m in enumerate(ch["members"][:8]):
            row, col = 7 + i // 2, 0 if i % 2 == 0 else 12
            mark = " D" if m["deaf"] else " M" if m["mute"] else ""
            pg.write(row, col, clean(m["name"], 11 - len(mark)) + mark, "grey" if (m["mute"] or m["deaf"]) else "green",
                     small=True)

    def _notifications(self, app, pg, s):
        notes = s.get("notifications", [])[:5]
        if not notes:
            pg.center(5, "NO NOTIFICATIONS YET", "grey")
            pg.center(7, "DISCORD SENDS ONLY THOSE", "grey", small=True)
            pg.center(8, "IT WOULD SHOW ON SCREEN", "grey", small=True)
        now = self.wall_clock()
        for n, note in enumerate(notes, start=1):
            age = ago(note["at"], now)
            body = wrap_lines(note["body"], 24, 1)
            app.line(pg, n, body[0] if body else "", left_label=clean(note["title"], 22 - len(age)),
                     right_label=age, left_color="white", label_color="cyan")

    def on_lsk(self, app, side, n):
        d = app.ctx.discord
        s = d.snapshot
        if self.page_index == 0:
            if (side, n) == ("L", 6):
                app.go("menu")
            elif (side, n) == ("R", 6):
                self.page_index = 1
            elif not s.get("configured"):
                return False
            elif (side, n) == ("R", 5) and s.get("state") != "CONNECTED":
                d.reconnect()
                self.set_status(app, "CONNECTING")
            elif (side, n) == ("L", 2) and s.get("state") == "CONNECTED":
                d.toggle_mute()
            elif (side, n) == ("R", 2) and s.get("state") == "CONNECTED":
                d.toggle_deaf()
            elif (side, n) == ("R", 3) and s.get("channel"):
                d.leave_channel()
                self.set_status(app, "LEFT VOICE CHANNEL")
            else:
                return False
            return True
        if (side, n) == ("L", 6):
            self.page_index = 0
        elif (side, n) == ("R", 6) and s.get("notifications"):
            d.clear_notifications()
        else:
            return False
        return True
