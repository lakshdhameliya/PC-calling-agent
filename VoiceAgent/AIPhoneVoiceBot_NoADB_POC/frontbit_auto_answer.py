"""Frontbit Link incoming-call detector and delayed auto-answer helper.

Frontbit Link exposes the Windows Phone Line Transport call through its
notification.  This helper only automates the visible Accept action; audio is
still routed by Frontbit Link/Bluetooth HFP and is not handled here.
"""
from __future__ import annotations

import ctypes
import json
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from comtypes import CoInitialize, CoUninitialize
from comtypes.client import CreateObject, GetModule

GetModule("UIAutomationCore.dll")
from comtypes.gen import UIAutomationClient  # noqa: E402

ROOT = Path(__file__).resolve().parent
EVENT_LOG = ROOT / "frontbit_auto_answer_status.json"

TREE_SCOPE_SUBTREE = 0x4
UIA_NAME_PROPERTY_ID = 30005
UIA_CONTROL_TYPE_PROPERTY_ID = 30003
UIA_BUTTON_CONTROL_TYPE_ID = 50000
UIA_INVOKE_PATTERN_ID = 10000


def _write_status(**values):
    payload = {"updated_at": time.time(), **values}
    try:
        EVENT_LOG.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


DAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _minutes(value):
    hours, minutes = str(value).split(":")[:2]
    return int(hours) * 60 + int(minutes)


def in_business_hours(now, days):
    current = now.hour * 60 + now.minute
    today = days.get(DAY_KEYS[now.weekday()]) or {}
    yesterday = days.get(DAY_KEYS[(now.weekday() - 1) % 7]) or {}

    def ranges(day):
        found = []
        for pair in day.get("hours") or []:
            try:
                found.append((_minutes(pair[0]), _minutes(pair[1])))
            except Exception:
                continue
        return found

    if today.get("open"):
        hours = ranges(today)
        if not hours:
            return True
        for start, end in hours:
            if (start < end and start <= current < end) or (start >= end and current >= start):
                return True
    if yesterday.get("open"):
        for start, end in ranges(yesterday):
            if start >= end and current < end:
                return True
    return False


def _get_adb_path():
    try:
        import os, shutil
        candidates = [
            str(ROOT.parent.parent / "platform-tools" / "adb.exe"),
            r"C:\Frontbit Link\platform-tools\adb.exe",
            r"C:\Users\DREAMWORLD\Desktop\platform-tools\adb.exe",
        ]
        for c in candidates:
            if os.path.exists(c):
                return c
        which = shutil.which("adb")
        if which:
            return which
    except Exception:
        pass
    return None


def _adb_env():
    import os
    env = dict(os.environ)
    user_home = Path.home() / ".android"
    project_home = ROOT.parent.parent / ".android"
    env["ANDROID_USER_HOME"] = str(user_home if (user_home / "adbkey").exists() else project_home)
    return env


def _get_adb_device():
    try:
        import subprocess, os
        adb = _get_adb_path()
        if not adb or not os.path.exists(adb):
            return None
        env = _adb_env()

        res = subprocess.run([adb, "devices"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=1.0, env=env)
        for line in res.stdout.splitlines():
            line = line.strip()
            if not line or line.startswith("List of"):
                continue
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                return parts[0]
    except Exception:
        pass
    return None


class FrontbitAutoAnswer:
    """Detect Frontbit's incoming-call notification and invoke Accept."""

    # Frontbit builds may expose Accept, Accept on PC, or Answer depending on
    # the installed notification language/version.
    ACCEPT_NAMES = (
        "Accept", "Accept on PC", "Accept call", "Answer", "Answer call",
        "Accept incoming call", "Answer incoming call",
    )

    CALL_ACTIVE_NAMES = (
        "End", "End call", "Transfer to PC", "Hold", "Mute",
        "Transfer to phone", "Resume call",
    )

    def __init__(self, delay_seconds=0.0, poll_seconds=0.20, delay_file=None, business_file=None):
        self.delay_seconds = max(0.0, float(delay_seconds))
        self.poll_seconds = max(0.10, float(poll_seconds))
        self.delay_file = Path(delay_file) if delay_file else None
        self.business_file = Path(business_file) if business_file else None
        self.delay_reason = "default"
        self.ringing = False
        self.answered_at = None
        self.in_call = False
        self.last_in_call_seen = 0.0
        self.stop_event = threading.Event()
        self.thread = None
        self._seen_since = None
        self._answered_for_ring = False
        self._last_transfer_attempt = 0.0

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, name="frontbit-auto-answer", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)

    def _refresh_delay(self, now=None):
        self.delay_reason = "default"
        if self.delay_file is not None:
            try:
                seconds = float(json.loads(self.delay_file.read_text(encoding="utf-8"))["seconds"])
                self.delay_seconds = max(0.0, min(120.0, seconds))
            except Exception:
                pass
        if self.business_file is not None:
            try:
                business = json.loads(self.business_file.read_text(encoding="utf-8"))
                if in_business_hours(now or datetime.now(), business.get("days") or {}):
                    self.delay_seconds = max(0.0, min(120.0, float(business["seconds"])))
                    self.delay_reason = "business_hours"
            except Exception:
                pass

    @staticmethod
    def _automation():
        return CreateObject(
            "{FF48DBA4-60EF-4201-AA87-54103EEF594E}",
            interface=UIAutomationClient.IUIAutomation,
        )

    @staticmethod
    def _frontbit_root(automation):
        """Return only the Frontbit Link desktop window, never another app."""
        try:
            desktop = automation.GetRootElement()
            windows = desktop.FindAll(0x2, automation.CreateTrueCondition())
            main_window = None
            for index in range(windows.Length):
                window = windows.GetElement(index)
                name = (window.CurrentName or "").strip().casefold()
                if name == "call":
                    return window
                elif name == "frontbit link":
                    main_window = window
            return main_window
        except Exception:
            pass
        return None

    @classmethod
    def _find_transfer_to_pc_element(cls, automation):
        try:
            root = cls._frontbit_root(automation)
            if not root:
                return None
            button_condition = automation.CreatePropertyCondition(
                UIA_CONTROL_TYPE_PROPERTY_ID, UIA_BUTTON_CONTROL_TYPE_ID
            )
            cond1 = automation.CreatePropertyCondition(UIA_NAME_PROPERTY_ID, "Transfer to PC")
            cond2 = automation.CreatePropertyCondition(UIA_NAME_PROPERTY_ID, "CallAudioRouteToPc")
            or_cond = automation.CreateOrCondition(cond1, cond2)
            return root.FindFirst(
                TREE_SCOPE_SUBTREE,
                automation.CreateAndCondition(button_condition, or_cond),
            )
        except Exception:
            return None

    @classmethod
    def _find_in_call_element(cls, automation):
        try:
            root = cls._frontbit_root(automation)
            if not root:
                return None
            button_condition = automation.CreatePropertyCondition(
                UIA_CONTROL_TYPE_PROPERTY_ID, UIA_BUTTON_CONTROL_TYPE_ID
            )
            names_or = None
            for name in cls.CALL_ACTIVE_NAMES:
                cond = automation.CreatePropertyCondition(UIA_NAME_PROPERTY_ID, name)
                names_or = cond if names_or is None else automation.CreateOrCondition(names_or, cond)
            if names_or is None:
                return None
            return root.FindFirst(
                TREE_SCOPE_SUBTREE,
                automation.CreateAndCondition(button_condition, names_or),
            )
        except Exception:
            return None

    @classmethod
    def _find_end_button(cls, automation):
        try:
            root = cls._frontbit_root(automation)
            if not root:
                return None
            button_condition = automation.CreatePropertyCondition(
                UIA_CONTROL_TYPE_PROPERTY_ID, UIA_BUTTON_CONTROL_TYPE_ID
            )
            names_or = None
            for name in ("End", "End call", "Disconnect"):
                cond = automation.CreatePropertyCondition(UIA_NAME_PROPERTY_ID, name)
                names_or = cond if names_or is None else automation.CreateOrCondition(names_or, cond)
            if names_or is None:
                return None
            return root.FindFirst(
                TREE_SCOPE_SUBTREE,
                automation.CreateAndCondition(button_condition, names_or),
            )
        except Exception:
            return None

    def close_stuck_call_window(self):
        try:
            automation = self._automation()
            if not automation:
                return False
            btn = self._find_end_button(automation)
            if btn:
                if self._invoke(btn) or self._mouse_click(btn):
                    self.in_call = False
                    return True
        except Exception:
            pass
        return False

    @classmethod
    def _find_accept_element(cls, automation):
        try:
            root = cls._frontbit_root(automation)
            if not root:
                return None
            button_condition = automation.CreatePropertyCondition(
                UIA_CONTROL_TYPE_PROPERTY_ID, UIA_BUTTON_CONTROL_TYPE_ID
            )
            names_or = None
            for name in cls.ACCEPT_NAMES:
                cond = automation.CreatePropertyCondition(UIA_NAME_PROPERTY_ID, name)
                names_or = cond if names_or is None else automation.CreateOrCondition(names_or, cond)
            if names_or is None:
                return None

            element = root.FindFirst(
                TREE_SCOPE_SUBTREE,
                automation.CreateAndCondition(button_condition, names_or),
            )
            if element:
                return element

            element = root.FindFirst(TREE_SCOPE_SUBTREE, names_or)
            if element:
                walker = automation.ControlViewWalker
                curr = element
                for _ in range(4):
                    if curr.CurrentControlType == UIA_BUTTON_CONTROL_TYPE_ID:
                        return curr
                    parent = walker.GetParentElement(curr)
                    if not parent:
                        break
                    curr = parent
                return element
        except Exception:
            pass
        return None

    @staticmethod
    def _mouse_click(element):
        try:
            rect = element.CurrentBoundingRectangle
            w = rect.right - rect.left
            h = rect.bottom - rect.top
            if w <= 0 or h <= 0:
                return False
            cx = (rect.left + rect.right) // 2
            cy = (rect.top + rect.bottom) // 2
            user32 = ctypes.windll.user32
            orig_pt = (ctypes.c_long * 2)()
            user32.GetCursorPos(orig_pt)
            user32.SetCursorPos(cx, cy)
            time.sleep(0.02)
            user32.mouse_event(0x0002, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTDOWN
            time.sleep(0.04)
            user32.mouse_event(0x0004, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTUP
            time.sleep(0.02)
            user32.SetCursorPos(orig_pt[0], orig_pt[1])
            return True
        except Exception:
            return False

    @classmethod
    def _invoke(cls, element):
        # 1. Direct physical mouse click at element coordinates (most reliable for Uno Platform / WinUI)
        if cls._mouse_click(element):
            return True

        # 2. Try UI Automation invoke on element and ancestors
        curr = element
        for _ in range(4):
            if curr:
                try:
                    pattern = curr.GetCurrentPattern(UIA_INVOKE_PATTERN_ID).QueryInterface(
                        UIAutomationClient.IUIAutomationInvokePattern
                    )
                    pattern.Invoke()
                    return True
                except Exception:
                    pass
                try:
                    pattern = curr.GetCurrentPattern(10018).QueryInterface(
                        UIAutomationClient.IUIAutomationLegacyIAccessiblePattern
                    )
                    pattern.DoDefaultAction()
                    return True
                except Exception:
                    pass
                try:
                    walker = cls._automation().ControlViewWalker
                    curr = walker.GetParentElement(curr)
                except Exception:
                    break
            else:
                break

        # 3. Direct mouse click on parent
        try:
            walker = cls._automation().ControlViewWalker
            parent = walker.GetParentElement(element)
            if parent and cls._mouse_click(parent):
                return True
        except Exception:
            pass

        return False

    @staticmethod
    def _keyboard_fallback(element):
        try:
            element.SetFocus()
            time.sleep(0.08)
            user32 = ctypes.windll.user32
            user32.keybd_event(0x0D, 0, 0, 0)
            user32.keybd_event(0x0D, 0, 0x0002, 0)
            return True
        except Exception:
            return False

    @staticmethod
    def _check_adb_call_state():
        dev = _get_adb_device()
        if not dev:
            return None
        try:
            import subprocess, os
            adb = _get_adb_path()
            if not adb:
                return None
            env = _adb_env()
            res = subprocess.run([adb, "-s", dev, "shell", "dumpsys", "telephony.registry"],
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=1.0, env=env)
            states = [int(line.split("=")[1].strip()) for line in res.stdout.splitlines() if "mCallState=" in line]
            if any(s == 2 for s in states):
                return "in_call"
            elif any(s == 1 for s in states):
                return "ringing"
            elif states and all(s == 0 for s in states):
                return "idle"
        except Exception:
            pass
        return None

    @staticmethod
    def _check_adb_ringing():
        dev = _get_adb_device()
        if not dev:
            return False
        try:
            import subprocess, os
            adb = _get_adb_path()
            if not adb:
                return False
            env = _adb_env()
            res = subprocess.run([adb, "-s", dev, "shell", "dumpsys", "telephony.registry"],
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=1.0, env=env)
            states = [int(line.split("=")[1].strip()) for line in res.stdout.splitlines() if "mCallState=" in line]
            return any(s == 1 for s in states)
        except Exception:
            pass
        return False

    @staticmethod
    def _answer_adb():
        dev = _get_adb_device()
        if not dev:
            return False
        try:
            import subprocess, os
            adb = _get_adb_path()
            if not adb:
                return False
            env = _adb_env()
            # Universal Android answer: wake screen, keyevent 5 (call), swipe up, and call key
            cmd = "input keyevent 224; input keyevent 5; input tap 554 1238; input swipe 554 1238 554 800 250; input keyevent 5"
            subprocess.run([adb, "-s", dev, "shell", cmd],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2.5, env=env)
            return True
        except Exception:
            return False

    @staticmethod
    def _end_adb():
        dev = _get_adb_device()
        if not dev:
            return False
        try:
            import subprocess, os
            adb = _get_adb_path()
            if not adb:
                return False
            env = _adb_env()
            cmd = "cmd telecom end-call; input keyevent 6"
            subprocess.run([adb, "-s", dev, "shell", cmd],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2.0, env=env)
            return True
        except Exception:
            return False

    def _run(self):
        CoInitialize()
        try:
            automation = self._automation()
        except Exception as exc:
            _write_status(state="error", error=f"UI Automation unavailable: {exc}")
            CoUninitialize()
            return

        _write_status(state="monitoring", delay_seconds=self.delay_seconds, method="element-first")
        try:
            while not self.stop_event.wait(self.poll_seconds):
                now = time.monotonic()

                # Track in-call status via Frontbit Link UI elements
                try:
                    in_call_element = self._find_in_call_element(automation)
                except Exception:
                    in_call_element = None

                if in_call_element:
                    # Check native call state from Sefirah C# (absolute truth)
                    try:
                        cs_path = ROOT / "call_state.json"
                        if cs_path.exists():
                            cs_data = json.loads(cs_path.read_text(encoding="utf-8"))
                            if cs_data.get("state") == "idle":
                                self.close_stuck_call_window()
                                self.in_call = False
                                in_call_element = None
                    except Exception:
                        pass

                    # Also check if call was terminated on the mobile device via ADB
                    if in_call_element and now - getattr(self, "_last_adb_state_check", 0) > 1.2:
                        self._last_adb_state_check = now
                        adb_st = self._check_adb_call_state()
                        if adb_st == "idle":
                            self.close_stuck_call_window()
                            self.in_call = False
                            in_call_element = None
                    if in_call_element:
                        self.in_call = True
                        self.last_in_call_seen = now
                        # Auto-transfer audio from phone to PC if button is visible
                        if now - getattr(self, "_last_transfer_attempt", 0) > 2.0:
                            self._last_transfer_attempt = now
                            try:
                                transfer_btn = self._find_transfer_to_pc_element(automation)
                                if transfer_btn:
                                    self._invoke(transfer_btn)
                            except Exception:
                                pass
                else:
                    if now - self.last_in_call_seen > 2.0:
                        self.in_call = False

                # When Sefirah is connected over Wi-Fi, its native call-state event
                # is authoritative. Keep this helper in monitor-only mode so an old
                # UI/ADB keyevent cannot toggle an already-answered call.
                native_call_state = None
                try:
                    cs_path = ROOT / "call_state.json"
                    if cs_path.exists():
                        native_call_state = json.loads(cs_path.read_text(encoding="utf-8")).get("state")
                except Exception:
                    native_call_state = None

                try:
                    element = self._find_accept_element(automation)
                except Exception as exc:
                    _write_status(state="monitoring", delay_seconds=self.delay_seconds, last_error=str(exc))
                    element = None

                adb_ringing = self._check_adb_ringing()
                native_ringing = native_call_state == "ringing"
                # The visible Accept button is the strongest signal. The JSON
                # bridge can briefly contain stale "talking" after a restart,
                # while the native Windows popup is already ringing. Never let
                # that stale value suppress a real incoming popup.
                is_ringing = bool(element) or native_ringing or (native_call_state is None and adb_ringing)

                now = time.monotonic()
                if not is_ringing:
                    self._seen_since = None
                    self._answered_for_ring = False
                    self.ringing = False
                elif not self._answered_for_ring:
                    if self._seen_since is None:
                        self._seen_since = now
                        self.ringing = True
                        self._refresh_delay()
                        _write_status(state="ringing", delay_seconds=self.delay_seconds,
                                      delay_reason=self.delay_reason, detected_at=time.time())
                    if self.delay_seconds - (now - self._seen_since) <= 0:
                        success = False
                        # 1. Direct ADB Answer if phone is paired (0.1s instant hardware answer)
                        if _get_adb_device():
                            if self._answer_adb():
                                success = True
                                method = "adb_direct_keyevent"

                        # 2. UI Automation fallback
                        if not success:
                            if native_call_state == "ringing" or element:
                                if element:
                                    success = self._invoke(element) or self._keyboard_fallback(element)
                                    method = "native_ui_accept"
                                if not success:
                                    success = self._answer_adb()
                                    method = "adb_ui_fallback"
                            elif adb_ringing:
                                adb_ok = self._answer_adb()
                                success = adb_ok
                                method = "adb_keyevent"
                            else:
                                method = "no_answer_method_available"
                                success = False
                        if success:
                            self._answered_for_ring = True
                            self.answered_at = time.monotonic()
                            self.ringing = False
                            _write_status(state="answered", method=method, answered_at=time.time(),
                                          detection_to_answer_seconds=round(time.monotonic() - self._seen_since, 3),
                                          configured_delay_seconds=self.delay_seconds,
                                          delay_reason=self.delay_reason)
        finally:
            CoUninitialize()


def probe():
    automation = FrontbitAutoAnswer._automation()
    element = FrontbitAutoAnswer._find_accept_element(automation)
    if not element:
        print(json.dumps({"found": False}))
        return 1
    print(json.dumps({"found": True, "name": element.CurrentName,
                      "class_name": element.CurrentClassName,
                      "process_id": element.CurrentProcessId}))
    return 0


if __name__ == "__main__":
    if "--probe" in sys.argv:
        raise SystemExit(probe())
    delay = 0.0
    if "--delay" in sys.argv:
        delay = float(sys.argv[sys.argv.index("--delay") + 1])
    watcher = FrontbitAutoAnswer(delay)
    watcher.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        watcher.stop()
