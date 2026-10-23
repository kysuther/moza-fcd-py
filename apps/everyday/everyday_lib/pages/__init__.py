"""Pages of the everyday app. Each module defines CduPage subclasses; PAGES lists them for build()."""

from moza_fcd.app import CduPage

from ..util import clean


class EPage(CduPage):
    """Shared helpers: services through app.ctx, and a standard <MENU on LSK 6L."""

    def back(self, app, pg, label: str = "<MENU") -> None:
        app.line(pg, 6, label)

    status = ""
    status_until = 0.0

    def set_status(self, app, text: str, seconds: float = 3.0) -> None:
        """A short confirmation on row 11 that fades by itself (unlike show_message, it doesn't light MSG)."""
        self.status = clean(text)
        self.status_until = app.ctx.clock() + seconds

    def draw_status(self, app, pg, row: int = 11) -> None:
        if self.status and app.ctx.clock() < self.status_until:
            pg.center(row, self.status, "green", small=True)

    def footer_alert(self, app, pg) -> None:
        """First active alert, small and amber, on row 11 (line 6's label row)."""
        if app.ctx.alerts:
            pg.center(11, clean(app.ctx.alerts[0]), "amber", small=True)
