#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time
import csv
import json
import threading
import queue
from pathlib import Path
from datetime import datetime

from PIL import Image  # <-- needed for idle frames
from oled import OLED
from keypad import KeypadUART
from fingerprint_sensor import FingerVeinSensor

# =========================
# Config
# =========================
KEYPAD_PORT = "/dev/ttyUSB0"
KEYPAD_BAUD = 9600
SENSOR_PASSWORD = "00000000"

USERS_CSV = Path("checkins.csv")
USER_NAME_COL = "Employee Name"
USER_CODE_COL = "Code"

ATTENDANCE_LOG = Path("attendance_log.csv")

# Mapping files
MAP_FILE = Path("finger_code_map.json")             # finger_id(str) -> user_code(str)
USER_FINGER_MAP_FILE = Path("user_finger_map.json") # user_code(str) -> {finger_id, code, name}

ITEMS_PER_PAGE = 2  # OLED 4 lines => 2 users shown

# ---- Idle animation settings ----
IDLE_FRAMES_DIR = Path("idle_frames")
IDLE_FPS = 8           # safe start; raise if stable
IDLE_STEP = 3          # frame skipping (bigger = faster animation)
IDLE_RETRIES = 3       # retry OLED display on occasional I2C glitches


# =========================
# Idle Animator (frames -> OLED)
# =========================
class IdleAnimator:
    """
    Non-blocking OLED animation from pre-rendered frames (PNG files).

    Usage:
      idle = IdleAnimator(oled, "idle_frames", fps=8, step=3)
      idle.enable()
      # in loop when IDLE:
      idle.tick()

    Notes:
      - step controls perceived speed more than fps
      - fps controls how often we try to push a new full frame over I2C
    """
    def __init__(self, oled: OLED, frames_dir: Path, fps=8, step=1, retries=2, retry_delay=0.03):
        self.oled = oled
        self.frames_dir = Path(frames_dir)
        self.fps = float(fps)
        self.step = max(1, int(step))
        self.retries = int(retries)
        self.retry_delay = float(retry_delay)

        self.enabled = False
        self._frames = []
        self._idx = 0
        self._last_ts = 0.0

        self.reload()

    def reload(self):
        self._frames = sorted(self.frames_dir.glob("frame_*.png"))
        if not self._frames:
            raise FileNotFoundError(
                f"No frames found in {self.frames_dir}. Expected frame_001.png etc."
            )
        self._idx = 0
        self._last_ts = 0.0

    def enable(self, reset=True):
        self.enabled = True
        if reset:
            self._idx = 0
            self._last_ts = 0.0

    def disable(self):
        self.enabled = False

    def set_fps(self, fps):
        self.fps = float(fps)

    def set_step(self, step):
        self.step = max(1, int(step))

    def _safe_display(self, img: Image.Image) -> bool:
        img = img.convert("1")
        for _ in range(self.retries):
            try:
                self.oled.device.display(img)
                return True
            except OSError:
                time.sleep(self.retry_delay)
        return False

    def tick(self) -> bool:
        if not self.enabled:
            return False

        now = time.time()
        interval = 1.0 / self.fps if self.fps > 0 else 0.0
        if (now - self._last_ts) < interval:
            return False

        fp = self._frames[self._idx]
        img = Image.open(fp)

        ok = self._safe_display(img)
        self._last_ts = now

        # Advance by step (this controls speed a lot)
        self._idx = (self._idx + self.step) % len(self._frames)
        return ok


# =========================
# Helpers
# =========================
def load_json(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {}

def save_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2))

def load_users_from_csv(csv_path: Path) -> list[dict]:
    if not csv_path.exists():
        raise FileNotFoundError(f"User list CSV not found: {csv_path}")
    with csv_path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        users = [row for row in reader]
    if not users:
        raise ValueError("CSV is empty.")
    return users

def load_code_to_name(csv_path: Path) -> dict:
    users = load_users_from_csv(csv_path)
    out = {}
    for row in users:
        code = (row.get(USER_CODE_COL) or "").strip()
        name = (row.get(USER_NAME_COL) or "").strip()
        if code:
            out[code] = name or "UNKNOWN"
    return out

def log_attendance(employee_name: str, code: str, method: str, result: str) -> None:
    ATTENDANCE_LOG.parent.mkdir(parents=True, exist_ok=True)
    new_file = not ATTENDANCE_LOG.exists()
    now = datetime.now()
    with ATTENDANCE_LOG.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["date", "time", "employee_name", "code", "method", "result"])
        w.writerow([now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S"),
                    employee_name, code, method, result])

def _short(s: str, max_len: int = 21) -> str:
    s = (s or "").strip()
    return s if len(s) <= max_len else (s[: max_len - 1] + ".")


# =========================
# Enrollment UI (OLED)
# =========================
def choose_user_oled(users: list[dict], oled: OLED, keypad: KeypadUART) -> dict:
    page = 0
    selected_abs_idx = None
    total_pages = (len(users) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE

    def render():
        nonlocal page, selected_abs_idx
        start = page * ITEMS_PER_PAGE
        end = min(start + ITEMS_PER_PAGE, len(users))
        visible = users[start:end]

        header = f"USER {page+1}/{total_pages}"
        footer = "PgUp/PgDn 1-2 Sel" if selected_abs_idx is None else "ENTER=OK BACK=CAN"

        lines = [_short(header)]
        for i in range(ITEMS_PER_PAGE):
            if i < len(visible):
                u = visible[i]
                code = (u.get(USER_CODE_COL) or "").strip()
                name = (u.get(USER_NAME_COL) or "").strip()
                label = f"{i+1}) {code}"
                if name:
                    label += f" {name}"
                abs_idx = start + i
                prefix = ">" if selected_abs_idx == abs_idx else " "
                lines.append(_short(prefix + label))
            else:
                lines.append("")
        lines.append(_short(footer))
        oled.show_lines(lines)

    render()

    while True:
        events = keypad.poll()
        if not events:
            time.sleep(0.05)
            continue

        for event, value in events:
            if event == "PgUp":
                page = (page + 1) % total_pages
                selected_abs_idx = None
                render()
            elif event == "PgDn":
                page = (page - 1) % total_pages
                selected_abs_idx = None
                render()
            elif event == "back":
                return None
            elif event == "key" and value and value.isdigit():
                pick = int(value)
                start = page * ITEMS_PER_PAGE
                end = min(start + ITEMS_PER_PAGE, len(users))
                visible_count = end - start
                if 1 <= pick <= visible_count:
                    selected_abs_idx = start + (pick - 1)
                    render()
            elif event == "enter":
                if selected_abs_idx is None:
                    oled.show_lines(["NO SELECTION", "PRESS 1-2", "", ""])
                    time.sleep(0.8)
                    render()
                    continue
                return users[selected_abs_idx]

def enroll_for_user(sensor: FingerVeinSensor, selected_user: dict, oled: OLED, keypad: KeypadUART) -> None:
    finger_code_map = load_json(MAP_FILE)
    user_finger_map = load_json(USER_FINGER_MAP_FILE)

    user_code = (selected_user.get(USER_CODE_COL) or "").strip()
    user_name = (selected_user.get(USER_NAME_COL) or "").strip()

    oled.show_lines(["ENROLL NEW", "ENTER=start", "BACK=cancel", ""])
    while True:
        for ev, _ in keypad.poll():
            if ev == "back":
                return
            if ev == "enter":
                break
        time.sleep(0.05)

    oled.show_lines(["FIND EMPTY ID", "PLEASE WAIT...", "", ""])
    finger_id = sensor.get_empty_id(start_id=0, end_id=200)

    oled.show_lines(["ENROLLING...", f"ID:{finger_id}", "FOLLOW SENSOR", ""])
    result = sensor.enroll_user(user_id=finger_id, group_id=1, temp_num=3)

    if result != 0:
        oled.show_lines(["ENROLL FAIL", f"CODE:{result}", "", ""])
        time.sleep(2)
        return

    # ? link finger_id -> CSV code
    finger_code_map[str(finger_id)] = user_code
    save_json(MAP_FILE, finger_code_map)

    user_finger_map[user_code] = {"finger_id": finger_id, "code": user_code, "name": user_name}
    save_json(USER_FINGER_MAP_FILE, user_finger_map)

    oled.show_lines(["ENROLLED", _short(user_name), f"CODE:{user_code}", ""])
    time.sleep(2)

def enrollment_flow(sensor: FingerVeinSensor, oled: OLED, keypad: KeypadUART) -> None:
    users = load_users_from_csv(USERS_CSV)
    selected = choose_user_oled(users, oled, keypad)
    if selected is None:
        # user cancelled → go back to idle
        return
    
    enroll_for_user(sensor, selected, oled, keypad)


# =========================
# Finger scan background thread
# =========================
class FingerWorker(threading.Thread):
    def __init__(self, sensor: FingerVeinSensor, out_q: queue.Queue):
        super().__init__(daemon=True)
        self.sensor = sensor
        self.out_q = out_q
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        while not self._stop.is_set():
            try:
                fid = self.sensor.verify_and_get_id(user_id=0)  # blocks until scan completes
                self.out_q.put(("finger_ok", fid))
            except Exception:
                time.sleep(0.2)


# =========================
# Main App
# =========================
class App:
    def __init__(self):
        self.oled = OLED()
        self.keypad = KeypadUART(KEYPAD_PORT, KEYPAD_BAUD)

        # Idle animator
        self.idle = IdleAnimator(
            oled=self.oled,
            frames_dir=IDLE_FRAMES_DIR,
            fps=IDLE_FPS,
            step=IDLE_STEP,
            retries=IDLE_RETRIES
        )

        self.sensor = FingerVeinSensor(baud_index=3)
        ret = self.sensor.connect(SENSOR_PASSWORD)
        if ret != 0:
            self.oled.show_lines(["SENSOR FAIL", f"CODE:{ret}", "", ""])
            raise RuntimeError("Sensor connect failed")

        self.code_to_name = load_code_to_name(USERS_CSV)

        self.state = "IDLE"
        self.buf = ""
        self.last_ts = time.time()

        self.fq = queue.Queue()
        self.fw = FingerWorker(self.sensor, self.fq)
        self.fw.start()

        self.enter_idle()

    def shutdown(self):
        try:
            self.fw.stop()
        except Exception:
            pass
        try:
            self.sensor.shutdown()
        except Exception:
            pass

    # ----- Idle control -----
    def enter_idle(self):
        self.state = "IDLE"
        self.buf = ""
        self.idle.enable(reset=False)  # keep animation position
        # Do NOT call show_lines here; the animator owns the OLED during idle

    def exit_idle(self):
        self.idle.disable()

    # ----- UI screens (disable idle first so it doesn't overwrite) -----
    def show_buf(self):
        self.exit_idle()
        self.oled.show_lines(["ENTER CODE:", self.buf, "ENTER=submit", "BACK=delete"])

    def finger_lookup(self, finger_id: int):
        finger_code_map = load_json(MAP_FILE)  # finger_id -> user_code
        code = finger_code_map.get(str(finger_id))
        if not code:
            return (False, None, None)
        name = self.code_to_name.get(code, "UNKNOWN")
        return (True, code, name)

    def prompt_enroll(self) -> bool:
        self.exit_idle()
        self.oled.show_lines(["FINGER UNKNOWN", "ENROLL NOW?", "ENTER=yes", "BACK=no"])
        start = time.time()
        while time.time() - start < 10:
            for ev, _ in self.keypad.poll():
                if ev == "enter":
                    return True
                if ev == "back":
                    return False
            time.sleep(0.05)
        return False

    def handle_finger(self, finger_id: int):
        self.exit_idle()
        enrolled, code, name = self.finger_lookup(finger_id)
        t_now = datetime.now().strftime("%H:%M:%S")

        if enrolled:
            log_attendance(name, code, "finger", "success")
            self.oled.show_lines([f"Hi {_short(name)}", "Code:", _short(code), t_now])
            time.sleep(3)
            self.enter_idle()
            return

        # not enrolled (mapped)
        if self.prompt_enroll():
            enrollment_flow(self.sensor, self.oled, self.keypad)
            self.code_to_name = load_code_to_name(USERS_CSV)
            self.oled.show_lines(["DONE", "SCAN AGAIN", "", ""])
            time.sleep(1.5)

        self.enter_idle()

    def handle_code_submit(self):
        self.exit_idle()
        code = self.buf
        name = self.code_to_name.get(code)
        t_now = datetime.now().strftime("%H:%M:%S")

        if name:
            log_attendance(name, code, "code", "success")
            self.oled.show_lines([f"Hi {_short(name)}", "You arrived:", t_now, ""])
            time.sleep(3)
        else:
            log_attendance("UNKNOWN", code, "code", "fail")
            self.oled.show_lines(["DENIED", "Invalid code", "", ""])
            time.sleep(1.5)

        self.enter_idle()

    def run(self):
        while True:
            # ---- IDLE animation tick ----
            if self.state == "IDLE":
                self.idle.tick()

            # ---- Keypad events ----
            for ev, val in self.keypad.poll():
                if ev == "key":
                    if self.state == "IDLE":
                        self.exit_idle()
                        self.state = "ENTERING"
                        self.buf = ""
                    if self.state == "ENTERING" and len(self.buf) < 5:
                        self.buf += val
                        self.last_ts = time.time()
                        self.show_buf()

                elif ev == "back":
                    if self.state == "ENTERING" and self.buf:
                        self.buf = self.buf[:-1]
                        self.last_ts = time.time()
                        self.show_buf()
                    elif self.state == "ENTERING" and not self.buf:
                        self.enter_idle()

                elif ev == "enter":
                    if self.state == "ENTERING":
                        if len(self.buf) != 5:
                            self.exit_idle()
                            self.oled.show_lines(["INVALID", "Need 5 digits", "", ""])
                            time.sleep(1.0)
                            self.enter_idle()
                        else:
                            self.handle_code_submit()

            # ---- typing timeout ----
            if self.state == "ENTERING" and (time.time() - self.last_ts) > 10:
                self.enter_idle()

            # ---- Finger events ----
            try:
                while True:
                    fev, fid = self.fq.get_nowait()
                    if fev == "finger_ok":
                        self.state = "IDLE"
                        self.buf = ""
                        self.handle_finger(int(fid))
            except queue.Empty:
                pass

            time.sleep(0.02)


def main():
    app = None
    try:
        app = App()
        app.run()
    finally:
        if app:
            app.shutdown()

if __name__ == "__main__":
    main()
