#==========================
# Imports
# =========================

from luma.core.interface.serial import i2c
from luma.oled.device import ssd1306,ssd1309  # change if you use sh1106 etc.
from PIL import Image, ImageDraw, ImageFont

import time             
import csv
from datetime import datetime
from pathlib import Path


# =========================
# OLED helpers
# =========================
class OLED:
    def __init__(self):
        serial_iface = i2c(port=1, address=0x3C)  # most OLEDs use 0x3C
        self.device = ssd1306(serial_iface)       # This is the physical LED screen
        self.font = ImageFont.load_default()

    def show_lines(self, lines):                    # This is the text display Method.
        img =  Image.new("1", self.device.size, 0)  # 1 = white background in 1-bit mode / Zero would produce a black background.
        draw = ImageDraw.Draw(img)

        y = 0
        for line in lines[:4]:                               # Creates a 12px font for each line of wording.
            draw.text((0, y), line, font=self.font, fill=1)  # 0 = black text
            y += 12

        self.device.display(img)

    def show_arrival_message(oled, user_name_or_id):
        """
        Displays: 'Hi <user> you arrived at <time>' on the OLED.
        """
        t = datetime.now().strftime("%H:%M:%S")
        oled.show_lines([
            f"Hi {user_name_or_id}",
            "You arrived at:",
            t,""
            ])

        time.sleep(4)
