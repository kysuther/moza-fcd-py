"""Template for your own CDU app.

    python -m moza_fcd gui --app examples/my_app.py     # in the emulator
    python -m moza_fcd run --app examples/my_app.py     # on the real device

Everything happens through `screen` (an FcdScreen) - the app never knows whether it's
talking to the simulator or hardware.
"""

import time

from moza_fcd.app import CduApp, CduPage


class HomePage(CduPage):
    title = "MY APP"

    def __init__(self):
        super().__init__()
        self.counter = 0
        self.name = ""

    def draw(self, app, pg):
        app.line(pg, 1, time.strftime("%H:%M:%S"), left_label="LOCAL TIME", color="green")
        app.line(pg, 2, str(self.counter), "<RESET", left_label="COUNTER", color="cyan")
        app.line(pg, 3, self.name or "-----", left_label="NAME (TYPE, THEN 3L)",
                 color="white" if self.name else "amber")
        app.line(pg, 6, "<COUNT +1", "ABOUT>")

    def on_lsk(self, app, side, n):
        if (side, n) == ("L", 6):
            self.counter += 1
        elif (side, n) == ("R", 2):
            self.counter = 0
        elif (side, n) == ("L", 3):
            if app.scratchpad:
                self.name = app.take_scratchpad()
            else:
                app.show_message("ENTER A NAME FIRST")
        elif (side, n) == ("R", 6):
            app.go("about")
        else:
            return False
        return True


class AboutPage(CduPage):
    title = "ABOUT"

    def draw(self, app, pg):
        pg.center(4, "BUILT WITH MOZA_FCD", "white")
        pg.center(6, "14 X 24 CHARACTERS", "cyan", small=True)
        app.line(pg, 6, "<BACK")

    def on_lsk(self, app, side, n):
        if (side, n) == ("L", 6):
            app.go("home")
            return True
        return False


def main(screen):
    app = CduApp(screen, home="home", function_keys={"MENU": "home"}, refresh_s=0.5)
    app.add("home", HomePage()).add("about", AboutPage())
    app.run()  # blocks; redraws every refresh_s for the clock
