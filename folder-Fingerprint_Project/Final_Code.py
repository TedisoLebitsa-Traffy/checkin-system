#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time
import csv
from datetime import datetime
from pathlib import Path

import serial
from keypad import KeypadUART
from oled import OLED

# =========================
# Config
# =========================
KEYPAD_PORT = "/dev/ttyUSB0"
KEYPAD_BAUD = 9600

FINGER_PORT = "/dev/ttyUSB1"
FINGER_BAUD = 9600

# --- READ-ONLY USER LIST (DO NOT WRITE HERE) ---
USERS_CSV = Path("checkins.csv")          # <-- this is your employee list file
USER_NAME_COL = "Employee Name"
USER_CODE_COL = "Code"

# --- SEPARATE LOG FILE (WRITE HERE) ---
ATTENDANCE_LOG = Path("attendance_log.csv")


def load_valid_codes_from_csv(csv_path: Path) -> dict:
    """
    Loads employee names and codes from a CSV file.
    Returns dict mapping code (str) -> employee name (str).
    """
    if not csv_path.exists():
        raise FileNotFoundError(f"User list CSV not found: {csv_path}")

    codes = {}
    with csv_path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = (row.get(USER_NAME_COL) or "").strip()
            code = (row.get(USER_CODE_COL) or "").strip()
            if code:
                codes[code] = name or "UNKNOWN"
    return codes


def log_attendance(employee_name: str, code: str, method: str, result: str) -> None:
    """
    Appends to attendance_log.csv (separate from the user list).
    Includes date + time at the moment the person logs in.
    """
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


# =========================
# Fingerprint reader (placeholder)
# =========================
class FingerprintSensor:
    def __init__(self, port, baud):
        self.ser = serial.Serial(port, baudrate=baud, timeout=0)

    def poll(self):
        return []


# =========================
# Main App
# =========================
class CheckInApp:
    def __init__(self):
        self.oled = OLED()
        self.keypad = KeypadUART(KEYPAD_PORT, KEYPAD_BAUD)

        # Load user list ONCE (faster) — reload if you want live updates
        self.valid_codes = load_valid_codes_from_csv(USERS_CSV)

        # Finger sensor placeholder
        self.finger = None

        self.state = "IDLE"
        self.code = ""
        self.last_action_ts = time.time()

    def reset(self):
        self.state = "IDLE"
        self.code = ""
        self.last_action_ts = time.time()

    def show_idle(self):
        self.oled.show_lines([
            "CHECK-IN SYSTEM",
            "Enter code OR",
            "Scan finger",
            "",
        ])

    def show_code(self):
        self.oled.show_lines([
            "ENTER CODE:",
            self.code,
            "Enter = submit",
            "Back = delete",
        ])

    def process_code(self):
        # Get name from read-only list
        employee_name = self.valid_codes.get(self.code)

        now = datetime.now()
        t_now = now.strftime("%H:%M:%S")

        if employee_name:
            # Log to separate attendance file (NOT the user list)
            log_attendance(employee_name, self.code, "code", "success")

            self.oled.show_lines([
                f"Hi {employee_name}",
                "You arrived at:",
                t_now,
                "",
            ])
            time.sleep(5)

        else:
            # Still log failed attempts, but to the attendance log file
            log_attendance("UNKNOWN", self.code, "code", "fail")
            self.oled.show_lines(["DENIED", "Invalid code", "", ""])
            time.sleep(1.5)

        self.reset()

    def process_finger(self, user_code_or_id: str):
        """
        If your fingerprint returns a code/ID, you can map it to a name here.
        For now we log it directly.
        """
        now = datetime.now()
        t_now = now.strftime("%H:%M:%S")

        # If the finger sensor returns a CODE that exists in your list, map it:
        employee_name = self.valid_codes.get(str(user_code_or_id), "UNKNOWN")

        log_attendance(employee_name, str(user_code_or_id), "finger", "success")

        self.oled.show_lines([
            "SUCCESS",
            f"{employee_name}",
            t_now,
            "",
        ])
        time.sleep(1.5)
        self.reset()

    def run(self):
        self.show_idle()

        while True:
            # Timeout typing
            if self.state == "ENTERING_CODE" and (time.time() - self.last_action_ts) > 10:
                self.reset()
                self.show_idle()

            events = []
            events += self.keypad.poll()

            # Finger events would be appended here if implemented:
            # if self.finger:
            #     events += self.finger.poll()

            for ev, val in events:
                if ev == "key":
                    if self.state == "IDLE":
                        self.state = "ENTERING_CODE"
                        self.code = ""
                    if self.state == "ENTERING_CODE":
                        if len(self.code) < 5:
                            self.code += val
                            self.last_action_ts = time.time()
                            self.show_code()

                elif ev == "back":
                    if self.state == "ENTERING_CODE" and self.code:
                        self.code = self.code[:-1]
                        self.last_action_ts = time.time()
                        self.show_code()

                elif ev == "enter":
                    if self.state == "ENTERING_CODE":
                        if len(self.code) != 5:
                            self.oled.show_lines(["Invalid code", "Need 5 keys", "Try again", ""])
                            time.sleep(0.8)
                            self.show_idle()
                            self.reset()
                        else:
                            self.process_code()
                            self.show_idle()

                elif ev == "finger_ok":
                    self.process_finger(val)
                    self.show_idle()

                elif ev == "finger_fail":
                    log_attendance("UNKNOWN", "", "finger", "fail")
                    self.oled.show_lines(["TRY AGAIN", "Finger not found", "", ""])
                    time.sleep(1.2)
                    self.show_idle()

            time.sleep(0.02)


if __name__ == "__main__":
    app = CheckInApp()
    app.run()



