#==========================
# Imports
# =========================
import serial
from luma.core.interface.serial import i2c
from luma.oled.device import ssd1306,ssd1309  # change if you use sh1106 etc.
from PIL import Image, ImageDraw, ImageFont

import time             
import csv
from datetime import datetime
from pathlib import Path


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
            elif b in (0,1):
                events.append(("Home", None))
            elif b == 2 :
                events.append(("PgUp", None))
            elif b == 3 :
                events.append(("PgDn", None))
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


    def collect_code_from_keypad(keypad, oled, max_len=5):
        """
        Collect keys into a code buffer until ENTER is pressed.
        Updates the OLED as the user types.
        Returns the code if length == max_len, otherwise returns None.
        """
        code = ""
        oled.show_lines(["ENTER CODE:", "", "Press Enter", "Back = delete"])

        while True:
            for event, value in keypad.poll():

                if event == "key":
                    if len(code) < max_len:
                        code += value

                elif event == "back":
                    code = code[:-1]

                elif event == "enter":
                    return code if len(code) == max_len else None

                # ---- LIVE DISPLAY UPDATE (after key/back) ----
                shown = code
                oled.show_lines(["ENTER CODE:", shown, "Press Enter", "Back = delete"])

            time.sleep(0.05)

    def show_touched_key(oled, event, value):
        """
        Display the last touched key on the OLED.
        """
        if event == "key":
            oled.show_lines([
                "KEY PRESSED:",
                value,
                "",
                ""
            ])

        elif event == "enter":
            oled.show_lines([
                "KEY PRESSED:",
                "ENTER",
                "",
                ""
            ])

        elif event == "back":
            oled.show_lines([
                "KEY PRESSED:",
                "BACK",
                "",
                ""
            ])
        
        elif event == "PgUp":
            oled.show_lines([
                "KEY PRESSED:",
                "BACK",
                "",
                ""
            ])
        
        elif event == "PgUp":
            oled.show_lines([
                "KEY PRESSED:",
                "PGUP",
                "",
                ""
            ])
        
        elif event == "Menu":
            oled.show_lines([
                "KEY PRESSED:",
                "MENU",
                "",
                ""
            ])
