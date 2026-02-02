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

#=================================================================================



# =========================
# Config
# =========================
KEYPAD_PORT = "/dev/ttyUSB0"      # or "/dev/ttyUSB0" if USB-serial adapter
KEYPAD_BAUD = 9600                # set to your keypad baud

FINGER_PORT = "/dev/ttyUSB1"      # example if fingerprint is another USB-serial
FINGER_BAUD = 9600                # common fingerprint baud (adjust)

LOG_PATH = Path("checkins.csv")   # This is the list for the users.

# If you have a known mapping of codes -> user_id:
VALID_CODES = {
    "1234": "USER_001",
    "5678": "USER_002",
    # add yours
}


# =========================
# OLED helpers
# =========================
class OLED:
    def __init__(self):
        serial_iface = i2c(port=1, address=0x3C)  # most OLEDs use 0x3C
        self.device = ssd1306(serial_iface)       # This is the physical LED screen
        self.font = ImageFont.load_default()

    def show_lines(self, lines):                    # This is the text display Method.
        img =  Image.new("1", self.device.size, 1)  # 1 = white background in 1-bit mode / Zero would produce a black background.
        draw = ImageDraw.Draw(img)

        y = 0
        for line in lines[:4]:                               # Creates a 12px font for each line of wording.
            draw.text((0, y), line, font=self.font, fill=0)  # 0 = black text
            y += 12

        self.device.display(img)


# =========================
# Logging
# =========================
def log_checkin(user_id, method, result):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    new_file = not LOG_PATH.exists()

    with LOG_PATH.open("a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["timestamp", "user_id", "method", "result"])
        w.writerow([datetime.now().isoformat(timespec="seconds"), user_id, method, result])


# =========================
# Keypad reader (UART)
# =========================
class KeypadUART:
    """
    This class should output key events like:
      ('key', '1'), ('key', '2'), ('enter', None), ('back', None)
    You must adapt decode_bytes_to_keys() to your keypad's protocol.
    """
    def __init__(self, port, baud):
        self.ser = serial.Serial(port, baudrate=baud, timeout=0)
        self.buffer = bytearray()

    def decode_bytes_to_keys(self, data: bytes):
        # ---- YOU WILL EDIT THIS ----
        # Generic fallback: assume keypad sends ASCII characters directly.
        # e.g. b'1', b'2', b'3', b'\n'
        events = []
        for b in data:
            if b in (10, 13):              # LF or CR
                events.append(("enter", None))
            elif b in (8, 127):            # backspace
                events.append(("back", None))
            else:
                ch = chr(b)
                # accept digits and asterisk/hash as typical keypad keys
                if ch.isdigit() or ch in ("*", "#"):
                    events.append(("key", ch))
        return events

    def poll(self):
        events = []
        n = self.ser.in_waiting
        if n > 0:
            data = self.ser.read(n)
            events.extend(self.decode_bytes_to_keys(data))
        return events


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

"""
class CheckInApp:
    def __init__(self):
        self.oled = OLED()

        self.keypad = KeypadUART(KEYPAD_PORT, KEYPAD_BAUD)

        # If you don’t have the fingerprint connected yet, comment this line and keep self.finger = None
        try:
            self.finger = FingerprintSensor(FINGER_PORT, FINGER_BAUD)
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
        masked = "*" * len(self.code)
        self.oled.show_lines([
            "ENTER CODE:",
            masked,
            "Enter = submit",
            "Back = delete",
        ])

    def process_code(self):
        user_id = VALID_CODES.get(self.code)
        if user_id:
            log_checkin(user_id, "code", "success")
            self.oled.show_lines(["SUCCESS ?", f"ID: {user_id}", "Method: CODE", ""])
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
                events += self.finger.poll()

            # Handle events
            for ev, val in events:
                if ev == "key":
                    if self.state == "IDLE":
                        self.state = "ENTERING_CODE"
                        self.code = ""
                    if self.state == "ENTERING_CODE":
                        if len(self.code) < 16:
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

""" 

