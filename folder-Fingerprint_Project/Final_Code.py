#!/usr/bin/env python3


#============================Imports===============================================
import time             
import csv
from datetime import datetime
from pathlib import Path

# ---------- OLED (luma.oled) ----------
from luma.core.interface.serial import i2c
from luma.oled.device import ssd1306,ssd1309  # change if you use sh1106 etc.
from PIL import Image, ImageDraw, ImageFont



# ---------- UART ----------
import serial
from keypad import KeypadUART

# -------- OLED ---------
from luma.core.interface.serial import i2c
from luma.oled.device import ssd1306,ssd1309  # change if you use sh1106 etc.
from PIL import Image, ImageDraw, ImageFont
from oled import OLED
t = datetime.now().strftime("%H:%M:%S")


# =========================
# Config
# =========================
KEYPAD_PORT = "/dev/ttyUSB0"      # or "/dev/ttyUSB0" if USB-serial adapter
KEYPAD_BAUD = 9600                # set to your keypad baud

FINGER_PORT = "/dev/ttyUSB1"      # example if fingerprint is another USB-serial
FINGER_BAUD = 9600                # common fingerprint baud (adjust)

LOG_PATH = Path("checkins.csv")   # This is the list for the users.

def load_valid_codes_from_csv(csv_path):
    """
    Loads employee names and codes from a CSV file.
    Returns a dict mapping code (str) -> employee name (str).
    """
    codes = {}

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row["Employee Name"].strip()
            code = row["Code"].strip()
            codes[code] = name

    return codes
# =========================
# Logging
# =========================
def log_checkin(user_id, method, result):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    new_file = not LOG_PATH.exists()                  # This line checks if we need a new file

    with LOG_PATH.open("a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["timestamp", "user_id", "method", "result"])
        w.writerow([datetime.now().isoformat(timespec="seconds"), user_id, method, result])


# =========================
# Fingerprint reader (placeholder)
# =========================
class FingerprintSensor:
    """
    Fingerprint sensors vary a lot.

    Goal: produce events like:
      ('finger_ok', 'USER_001')
      ('finger_fail', None)

    Replace poll() with the library/protocol for your sensor.
    """
    def __init__(self, port, baud):
        # If your fingerprint sensor is not connected yet, you can comment this out temporarily.
        self.ser = serial.Serial(port, baudrate=baud, timeout=0)

    def poll(self):
        # ---- YOU WILL EDIT THIS ----
        # Placeholder: no events unless you implement sensor protocol.
        # If your sensor has a python library, use it here and return events.
        return []


# =========================
# Main App
# =========================

class CheckInApp:
    def __init__(self):
        self.oled = OLED()

        self.keypad = KeypadUART(KEYPAD_PORT, KEYPAD_BAUD)

        # If you don’t have the fingerprint connected yet, comment this line and keep self.finger = None
        try:
            self.finger = reader.scan_code_until_correct()
        except Exception:
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
        masked = self.code
        self.oled.show_lines([
            "ENTER CODE:",
            masked,
            "Enter = submit",
            "Back = delete",
        ])

    def process_code(self):
        VALID_CODES = load_valid_codes_from_csv("Time.csv")
        user_id = VALID_CODES.get(self.code)
        if user_id:
            log_checkin(user_id, "code", "success")
            
            self.oled.show_lines([
                f"Hi {user_id}",
                "You arrived at:",
                t,""
                ])




            time.sleep(5)
        else:
            log_checkin(self.code, "code", "fail")  # logs raw code as user_id field; you can change
            self.oled.show_lines(["DENIED ?", "Invalid code", "", ""])
            time.sleep(1.5)
        self.reset()

    def process_finger(self, user_id):
        # user_id should come from the fingerprint match
        log_checkin(user_id, "finger", "success")
        self.oled.show_lines(["SUCCESS ?", f"ID: {user_id}", "Method: FINGER", ""])
        time.sleep(1.5)
        self.reset()

    def run(self):
        self.show_idle()

        while True:
            # Timeout: if user started typing then stops, reset after 10s
            if self.state == "ENTERING_CODE" and (time.time() - self.last_action_ts) > 10:
                self.reset()
                self.show_idle()

            # Gather input events
            events = []
            events += self.keypad.poll()

            if self.finger:
                self.oled.show_lines(["Invalid code", "Need 5 keys", "Try again", ""])
                time.sleep(5)

            # Handle events
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

                elif ev == "enter" :
                    if self.state == "ENTERING_CODE":
                        if len(self.code) != 5 :
                            self.oled.show_lines(["Invalid code", "Need 5 keys", "Try again", ""])
                            time.sleep(0.5)
                            self.show_idle()

                        else :
                            self.process_code()
                            self.show_idle()

                elif ev == "finger_ok":
                    # val should be matched user_id
                    self.process_finger(val)
                    self.show_idle()

                elif ev == "finger_fail":
                    self.oled.show_lines(["TRY AGAIN", "Finger not found", "", ""])
                    time.sleep(1.2)
                    self.show_idle()

            time.sleep(0.02)

if __name__ == "__main__":
    app = CheckInApp()
    app.run()

