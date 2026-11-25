"""Read-only diagnostic: print Frontbit Link/Bluetooth audio session changes."""
import time

from pycaw.utils import AudioUtilities


last = None
deadline = time.time() + 180
while time.time() < deadline:
    rows = []
    for session in AudioUtilities.GetAllSessions():
        try:
            process = session.Process.name() if session.Process else None
        except Exception:
            process = None
        display = session.DisplayName or ""
        if "Hands-Free" in display or (process and "Phone" in process):
            rows.append((session.ProcessId, process, display, session.State))
    current = repr(rows)
    if current != last:
        print(time.strftime("%H:%M:%S"), current, flush=True)
        last = current
    time.sleep(0.5)
