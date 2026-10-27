"""Windows audio via pycaw: master volume/mute, microphone mute, per-app mixer, output device.

COM safety: comtypes objects must not be released on a thread with the wrong COM apartment, or the
process can crash. So COM runs multi-threaded (sys.coinit_flags = 0 before comtypes is imported),
no COM objects are kept between polls (actions look sessions up again), and garbage is collected on
this thread after every poll/action.
"""

from __future__ import annotations

import gc
import sys
import warnings

from . import Worker

EDATAFLOW_RENDER = 0
DEVICE_STATE_ACTIVE = 1
COINIT_MULTITHREADED = 0


def _app_key(session) -> str:
    try:
        name = session.Process.name() if session.Process else "System sounds"
    except Exception:
        name = "?"
    return name.lower().removesuffix(".exe")


class AudioService(Worker):
    name = "audio"
    interval = 0.5

    def __init__(self, hide=()) -> None:
        super().__init__()
        self.hide = {h.lower().removesuffix(".exe") for h in hide}
        self._polls = 0
        self.snapshot = {"volume": None, "muted": False, "mic_muted": None, "apps": [], "devices": []}

    def setup(self) -> None:
        warnings.filterwarnings("ignore", category=FutureWarning)  # comtypes on new Pythons
        if "comtypes" not in sys.modules:
            sys.coinit_flags = COINIT_MULTITHREADED  # read by comtypes when it initialises COM on import
        import comtypes
        from pycaw.utils import AudioUtilities  # noqa: F401  (ImportError -> unavailable)
        comtypes.CoInitializeEx(COINIT_MULTITHREADED)

    def teardown(self) -> None:
        gc.collect()

    # --- helpers (service thread only) ------------------------------------------------

    @staticmethod
    def _speakers_volume():
        from pycaw.utils import AudioUtilities
        return AudioUtilities.GetSpeakers().EndpointVolume

    @staticmethod
    def _mic_volume():
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import IAudioEndpointVolume
        from pycaw.utils import AudioUtilities
        mic = AudioUtilities.GetMicrophone()
        if mic is None:
            return None
        iface = mic.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        # QueryInterface, not ctypes.cast: a cast makes a second COM pointer without AddRef, both release
        # on garbage collection, and the double Release crashes the process (access violation).
        return iface.QueryInterface(IAudioEndpointVolume)

    @staticmethod
    def _sessions_for(key: str):
        from pycaw.utils import AudioUtilities
        return [s for s in AudioUtilities.GetAllSessions() if _app_key(s) == key]

    def poll(self) -> None:
        try:
            self._poll()
        finally:
            gc.collect()  # release COM objects here, on the thread that made them

    def _poll(self) -> None:
        from pycaw.utils import AudioUtilities
        snap = dict(self.snapshot)
        ev = self._speakers_volume()
        snap["volume"] = int(round(ev.GetMasterVolumeLevelScalar() * 100))
        snap["muted"] = bool(ev.GetMute())
        try:
            mic = self._mic_volume()
            snap["mic_muted"] = bool(mic.GetMute()) if mic is not None else None
        except Exception:
            snap["mic_muted"] = None

        apps = {}
        for s in AudioUtilities.GetAllSessions():
            key = _app_key(s)
            if key in self.hide or key in apps:
                continue
            vol = s.SimpleAudioVolume
            apps[key] = {"key": key, "name": key.upper(), "volume": int(round(vol.GetMasterVolume() * 100)),
                         "muted": bool(vol.GetMute())}
        snap["apps"] = [apps[k] for k in sorted(apps)]

        if self._polls % 10 == 0:  # devices change rarely
            default_id = AudioUtilities.GetSpeakers().id
            snap["devices"] = [{"id": d.id, "name": d.FriendlyName or "?", "default": d.id == default_id}
                               for d in AudioUtilities.GetAllDevices(data_flow=EDATAFLOW_RENDER,
                                                                     device_state=DEVICE_STATE_ACTIVE)]
        self._polls += 1
        self.snapshot = snap

    def _act(self, fn) -> None:
        def run():
            try:
                fn()
            finally:
                gc.collect()
        self.do(run)

    # --- actions (queued onto the service thread) -------------------------------------

    def set_volume(self, percent: int) -> None:
        self._act(lambda: self._speakers_volume().SetMasterVolumeLevelScalar(max(0, min(100, percent)) / 100, None))

    def step_volume(self, delta: int) -> None:
        def act():
            ev = self._speakers_volume()
            now = ev.GetMasterVolumeLevelScalar() * 100
            ev.SetMasterVolumeLevelScalar(max(0.0, min(100.0, now + delta)) / 100, None)
        self._act(act)

    def toggle_mute(self) -> None:
        def act():
            ev = self._speakers_volume()
            ev.SetMute(0 if ev.GetMute() else 1, None)
        self._act(act)

    def toggle_mic(self) -> None:
        def act():
            mic = self._mic_volume()
            if mic is not None:
                mic.SetMute(0 if mic.GetMute() else 1, None)
        self._act(act)

    def set_app_volume(self, key: str, percent: int) -> None:
        def act():
            for s in self._sessions_for(key):
                s.SimpleAudioVolume.SetMasterVolume(max(0, min(100, percent)) / 100, None)
        self._act(act)

    def toggle_app_mute(self, key: str) -> None:
        def act():
            sessions = self._sessions_for(key)
            if sessions:
                mute = 0 if sessions[0].SimpleAudioVolume.GetMute() else 1
                for s in sessions:
                    s.SimpleAudioVolume.SetMute(mute, None)
        self._act(act)

    def set_default_device(self, device_id: str) -> None:
        def act():
            from pycaw.constants import ERole
            from pycaw.utils import AudioUtilities
            AudioUtilities.SetDefaultDevice(device_id, roles=[ERole.eConsole, ERole.eMultimedia])
            self._polls = 0  # re-read the device list on the next poll
        self._act(act)
