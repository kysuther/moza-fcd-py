"""System monitor (2 pages: overview, disks) and the top-processes page with EXEC-confirmed end."""

from . import EPage
from ..util import clean, duration_words, rate


def _pct(v):
    return "--" if v is None else f"{v:.0f}%"


class SystemPage(EPage):
    title = "SYSTEM"
    page_count = 2

    def draw(self, app, pg):
        s = app.ctx.system.snapshot
        if not app.ctx.system.available:
            pg.center(6, "SYSTEM INFO N/A", "amber")
        elif self.page_index == 0:
            ram_label = f"RAM {s.get('ram_used', 0):.1f}/{s.get('ram_total', 0):.0f}G" if s.get("ram") is not None else "RAM"
            app.line(pg, 1, _pct(s.get("cpu")), _pct(s.get("ram")), left_label="CPU", right_label=ram_label,
                     color="green")
            g = s.get("gpu")
            if g:
                app.line(pg, 2, f"{g['util']:.0f}%", f"{g['temp']:.0f}°C", left_label="GPU", right_label="GPU TEMP",
                         left_color="green", right_color="amber" if g["temp"] >= app.ctx.config["alerts"]["gpu_temp_c"]
                         else "green")
                app.line(pg, 3, f"{g['vram_used']:.1f}/{g['vram_total']:.0f}G", left_label="VRAM", color="green")
            else:
                app.line(pg, 2, "NO NVIDIA GPU", left_label="GPU", color="grey")
            ping = s.get("ping")
            app.line(pg, 3, None if g else "", "--" if ping is None else f"{ping:.0f} MS",
                     right_label=clean(f"PING {app.ctx.system.ping_host}", 16), right_color="green" if ping is not None else "grey")
            app.line(pg, 4, rate(s.get("net_down")) + "B/S", rate(s.get("net_up")) + "B/S", left_label="DOWN",
                     right_label="UP", color="green")
            app.line(pg, 5, duration_words(s.get("uptime")), left_label="UPTIME", color="white")
        else:
            disks = s.get("disks", [])[:5]
            if not disks:
                pg.center(6, "NO DISKS FOUND", "grey")
            for i, d in enumerate(disks, start=1):
                low = d["free_pct"] <= app.ctx.config["alerts"]["disk_free_percent"]
                app.line(pg, i, f"{d['free']:.0f}G FREE", f"{d['free_pct']:.0f}%", left_label=f"{d['name']} {d['total']:.0f}G",
                         right_label="FREE", color="amber" if low else "green")
        app.line(pg, 6, "<MENU", "PROCESSES>")
        self.footer_alert(app, pg)

    def on_lsk(self, app, side, n):
        if (side, n) == ("L", 6):
            app.go("menu")
        elif (side, n) == ("R", 6):
            app.go("processes")
        else:
            return False
        return True


class ProcessesPage(EPage):
    """Top 5 processes by CPU. LSK selects one; EXEC ends it; <CANCEL backs out."""

    title = "PROCESSES"

    def __init__(self):
        super().__init__()
        self.target = None

    def on_enter(self, app):
        self.target = None

    def draw(self, app, pg):
        procs = app.ctx.system.snapshot.get("procs", [])
        for i, p in enumerate(procs[:5], start=1):
            selected = self.target and self.target["pid"] == p["pid"]
            app.line(pg, i, f"<{clean(p['name'], 16)}", f"{p['cpu']:.1f}%", left_label=f"PID {p['pid']}",
                     left_color="amber" if selected else "white", right_color="green", label_color="grey")
        if self.target and app.exec_pending:
            pg.center(11, clean(f"EXEC ENDS {self.target['name']}"), "amber", small=True)
            app.line(pg, 6, "<CANCEL")
        else:
            app.line(pg, 6, "<SYSTEM", "SELECT TO END", right_color="grey")

    def on_lsk(self, app, side, n):
        if (side, n) == ("L", 6):
            if app.exec_pending:
                self.target = None
                app.set_exec_pending(False)
            else:
                app.go("system")
            return True
        procs = app.ctx.system.snapshot.get("procs", [])[:5]
        if side == "L" and 1 <= n <= len(procs):
            self.target = dict(procs[n - 1])
            app.set_exec_pending(True)
            return True
        return False

    def on_exec(self, app):
        if self.target:
            app.ctx.system.terminate(self.target["pid"])
            app.show_message(clean(f"ENDING {self.target['name']}"))
            self.target = None
