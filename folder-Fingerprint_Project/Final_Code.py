#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time
import csv
import json
import threading
import queue
from pathlib import Path
from datetime import datetime

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

MAP_FILE = Path("finger_code_map.json")          # finger_id(str) -> user_code(str)
USER_FINGER_MAP_FILE = Path("user_finger_map.json")  # user_code(str) -> {finger_id, code, name}

ITEMS_PER_PAGE = 2  # OLED has 4 lines


# =========================
# JSON + CSV helpers
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
        raise ValueError("CSV is empty or has no rows.")

    if USER_CODE_COL not in users[0]:
        raise ValueError(f"CSV missing required column '{USER_CODE_COL}'")

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
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M:%S")

    with ATTENDANCE_LOG.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["date", "time", "employee_name", "code", "method", "result"])
        w.writerow([date_str, time_str, employee_name, code, method, result])

def _short(s: str, max_len: int = 21) -> str:
    s = (s or "").strip()
    return s if len(s) <= max_len else (s[: max_len - 1] + ".")


# =========================
# OLED Enrollment UI
# =========================
def choose_user_oled(users: list[dict], oled: OLED, keypad: KeypadUART) -> dict:
    if not users:
        raise ValueError("No users in CSV.")

    page = 0
    selected_abs_idx = None
    total_pages = (len(users) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE

    def render():
        nonlocal page, selected_abs_idx
        start = page * ITEMS_PER_PAGE
        end = min(start + ITEMS_PER_PAGE, len(users))
        visible = users[start:end]

        header = f"USER {page+1}/{total_pages}"
        footer = "PgUp/PgDn 1-2 Sel"
        if selected_abs_idx is not None:
            footer = "ENTER=OK BACK=CAN"

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
                prefix = ">" if (selected_abs_idx == abs_idx) else " "
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
                raise RuntimeError("User selection cancelled.")

            elif event == "key" and value and value.isdigit():
                pick = int(value)
                start = page * ITEMS_PER_PAGE
                end = min(start + ITEMS_PER_PAGE, len(users))
                visible_count = end - start

                if 1 <= pick <= visible_count and pick <= ITEMS_PER_PAGE:
                    selected_abs_idx = start + (pick - 1)
                    render()

            elif event == "enter":
                if selected_abs_idx is None:
                    oled.show_lines(["NO SELECTION", "PRESS 1-2", "", ""])
                    time.sleep(0.8)
                    render()
                    continue
                return users[selected_abs_idx]


def enroll_finger_for_selected_user(
    sensor: FingerVeinSensor,
    selected_user: dict,
    oled: OLED,
    keypad: KeypadUART,
    start_id=0,
    end_id=200
) -> tuple[int, str]:
    """
    Enroll a NEW finger and link that finger_id to the user's CSV Code.
    Saves:
      - MAP_FILE: finger_id -> user_code
      - USER_FINGER_MAP_FILE: user_code -> {finger_id, code, name}
    """
    finger_code_map = load_json(MAP_FILE)
    user_finger_map = load_json(USER_FINGER_MAP_FILE)

    user_code = (selected_user.get(USER_CODE_COL) or "").strip()
    user_name = (selected_user.get(USER_NAME_COL) or "").strip()

    # If user already linked
    if user_code in user_finger_map:
        existing = user_finger_map[user_code]
        oled.show_lines([
            "ALREADY LINKED",
            _short(user_code),
            f"FID:{existing.get('finger_id')}",
            "ENTER=NEW BACK=KEEP"
        ])
        while True:
            for ev, _ in keypad.poll():
                if ev == "back":
                    return int(existing["finger_id"]), str(existing["code"])
                if ev == "enter":
                    break
            time.sleep(0.05)

    oled.show_lines(["ENROLL NEW", "ENTER=start", "BACK=cancel", ""])
    while True:
        for ev, _ in keypad.poll():
            if ev == "back":
                raise RuntimeError("Enrollment cancelled.")
            if ev == "enter":
                break
        time.sleep(0.05)

    oled.show_lines(["FIND EMPTY ID", "PLEASE WAIT...", "", ""])
    finger_id = sensor.get_empty_id(start_id=start_id, end_id=end_id)

    oled.show_lines(["ENROLLING...", f"ID:{finger_id}", "FOLLOW SENSOR", ""])
    result = sensor.enroll_user(user_id=finger_id, group_id=1, temp_num=3)

    if result == 0:
        # Link finger_id directly to CSV code
        finger_code_map[str(finger_id)] = user_code
        save_json(MAP_FILE, finger_code_map)

        user_finger_map[user_code] = {"finger_id": finger_id, "code": user_code, "name": user_name}
        save_json(USER_FINGER_MAP_FILE, user_finger_map)

        oled.show_lines(["ENROLLED", _short(user_name or user_code), f"CODE:{user_code}", ""])
        time.sleep(2)
        return finger_id, user_code

    if result == 10:
        oled.show_lines(["FINGER EXISTS", "TRY ANOTHER", "ENTER=retry", "BACK=stop"])
        while True:
            for ev, _ in keypad.poll():
                if ev == "back":
                    raise RuntimeError("Enrollment cancelled (duplicate finger).")
                if ev == "enter":
                    return enroll_finger_for_selected_user(sensor, selected_user, oled, keypad, start_id, end_id)
            time.sleep(0.05)

    raise RuntimeError(f"Enrollment failed with error code: {result}")


def enrollment_flow(sensor: FingerVeinSensor, oled: OLED, keypad: KeypadUART) -> None:
    users = load_users_from_csv(USERS_CSV)
    selected_user = choose_user_oled(users, oled, keypad)
    enroll_finger_for_selected_user(sensor, selected_user, oled, keypad)


# =========================
# Finger scanning worker (non-blocking for keypad)
# =========================
class FingerScanWorker(threading.Thread):
    """
    Runs verify_and_get_id(0) continuously in the background.
    Pushes events into a queue:
      ("finger_ok", finger_id_int)
      ("finger_fail", None)
    """
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
                fid = self.sensor.verify_and_get_id(user_id=0)  # blocking
                if isinstance(fid, int):
                    self.out_q.put(("finger_ok", fid))
            except Exception:
                # Don't spam fail events too fast
                self.out_q.put(("finger_fail", None))
                time.sleep(0.3)


# =========================
# Main integrated app
# =========================
class IntegratedApp:
    def __init__(self):
        self.oled = OLED()
        self.keypad = KeypadUART(KEYPAD_PORT, KEYPAD_BAUD)

        self.sensor = FingerVeinSensor(baud_index=3)
        ret = self.sensor.connect(SENSOR_PASSWORD)
        if ret != 0:
            self.oled.show_lines(["SENSOR FAIL", f"CODE:{ret}", "", ""])
            time.sleep(2)
            raise RuntimeError(f"Sensor connect failed: {ret}")

        self.code_to_name = load_code_to_name(USERS_CSV)

        self.state = "IDLE"
        self.code_buf = ""
        self.last_action_ts = time.time()

        self.finger_q = queue.Queue()
        self.finger_worker = FingerScanWorker(self.sensor, self.finger_q)
        self.finger_worker.start()

    def shutdown(self):
        try:
            self.finger_worker.stop()
        except Exception:
            pass
        try:
            self.sensor.shutdown()
        except Exception:
            pass

    def show_idle(self):
        self.oled.show_lines(["CHECK-IN SYSTEM", "ENTER CODE OR", "SCAN FINGER", ""])

    def show_code(self):
        self.oled.show_lines(["ENTER CODE:", self.code_buf, "ENTER=submit", "BACK=delete"])

    def reset_code_entry(self):
        self.state = "IDLE"
        self.code_buf = ""
        self.last_action_ts = time.time()

    def handle_code_submit(self):
        code = self.code_buf
        name = self.code_to_name.get(code)
        t_now = datetime.now().strftime("%H:%M:%S")

        if name:
            log_attendance(name, code, "code", "success")
            self.oled.show_lines([f"Hi {_short(name)}", "You arrived at:", t_now, ""])
            time.sleep(3)
        else:
            log_attendance("UNKNOWN", code, "code", "fail")
            self.oled.show_lines(["DENIED", "Invalid code", "", ""])
            time.sleep(1.5)

        self.reset_code_entry()
        self.show_idle()

    def finger_lookup(self, finger_id: int):
        finger_code_map = load_json(MAP_FILE)  # finger_id(str) -> user_code(str)
        code = finger_code_map.get(str(finger_id))
        if not code:
            return (False, None, None)
        name = self.code_to_name.get(code, "UNKNOWN")
        return (True, code, name)

    def prompt_enroll(self) -> bool:
        """
        Enter=yes, Back=no, timeout 10s
        """
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

    def handle_finger_ok(self, finger_id: int):
        enrolled, code, name = self.finger_lookup(finger_id)
        t_now = datetime.now().strftime("%H:%M:%S")

        if enrolled:
            log_attendance(name or "UNKNOWN", code or "", "finger", "success")
            self.oled.show_lines([f"Hi {_short(name)}", "Code:", _short(code), t_now])
            time.sleep(3)
            self.show_idle()
            return

        # not enrolled
        if self.prompt_enroll():
            try:
                enrollment_flow(self.sensor, self.oled, self.keypad)
                # refresh mapping for names
                self.code_to_name = load_code_to_name(USERS_CSV)
                self.oled.show_lines(["ENROLL DONE", "SCAN AGAIN", "", ""])
                time.sleep(1.5)
            except Exception as e:
                self.oled.show_lines(["ENROLL FAIL", _short(str(e)), "", ""])
                time.sleep(2)

        self.show_idle()

    def run(self):
        self.show_idle()

        while True:
            # -------------------------
            # 1) Handle keypad always
            # -------------------------
            events = self.keypad.poll()
            for ev, val in events:
                if ev == "key":
                    if self.state == "IDLE":
                        self.state = "ENTERING_CODE"
                        self.code_buf = ""
                    if self.state == "ENTERING_CODE":
                        if len(self.code_buf) < 5:
                            self.code_buf += val
                            self.last_action_ts = time.time()
                            self.show_code()

                elif ev == "back":
                    if self.state == "ENTERING_CODE" and self.code_buf:
                        self.code_buf = self.code_buf[:-1]
                        self.last_action_ts = time.time()
                        self.show_code()
                    elif self.state == "ENTERING_CODE" and not self.code_buf:
                        self.reset_code_entry()
                        self.show_idle()

                elif ev == "enter":
                    if self.state == "ENTERING_CODE":
                        if len(self.code_buf) != 5:
                            self.oled.show_lines(["INVALID CODE", "Need 5 digits", "", ""])
                            time.sleep(1.0)
                            self.reset_code_entry()
                            self.show_idle()
                        else:
                            self.handle_code_submit()

            # typing timeout
            if self.state == "ENTERING_CODE" and (time.time() - self.last_action_ts) > 10:
                self.reset_code_entry()
                self.show_idle()

            # -------------------------
            # 2) Handle finger events
            # -------------------------
            try:
                while True:
                    fev, fval = self.finger_q.get_nowait()
                    if fev == "finger_ok":
                        # If someone is typing, finger still takes priority (you can change this if you want)
                        self.reset_code_entry()
                        self.handle_finger_ok(int(fval))
                    # ignore finger_fail to avoid spamming OLED
            except queue.Empty:
                pass

            time.sleep(0.05)


def main():
    app = None
    try:
        app = IntegratedApp()
        app.run()
    finally:
        if app:
            app.shutdown()


if __name__ == "__main__":
    main()




