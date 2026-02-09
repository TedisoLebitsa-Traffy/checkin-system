#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import json
import threading
import queue
from pathlib import Path
from datetime import datetime
import requests

from PIL import Image
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

CURRENT_STATUS = Path("current_status.json")  # Track who's IN/OUT
SENSOR_LOCK = threading.Lock()

# Notion Integration
NOTION_KEY = "ntn_v79048340066HxNnLJZyxkEvbZ993r0IwEHqYB8F3lg4aE"
NOTION_DATABASE_ID = "2fe1d72a31d680ba9408faba0e8c1d9f"

# Mapping files
MAP_FILE = Path("finger_code_map.json")
USER_FINGER_MAP_FILE = Path("user_finger_map.json")

# ---- Idle animation settings ----
IDLE_FRAMES_DIR = Path("idle_frames")
IDLE_FPS = 8
IDLE_STEP = 3
IDLE_RETRIES = 3

# ---- Finger debouncing settings ----
FINGER_COOLDOWN = 2.0

# ---- Notion API settings ----
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_KEY}",
    "content-type": "application/json",
    "Notion-Version": "2022-06-28",
}

# =========================
# ULTRA SIMPLIFIED Notion Function
# =========================
def write_to_notion_reason_only(code: str, action: str):
    """
    ULTRA SIMPLIFIED: Write ONLY to Reason column in Notion.
    NO Clock In, NO Clock Out, NO Date - JUST the Reason.
    Format in Reason column: "code(in)" or "code(out)"
    """
    if not NOTION_KEY or not NOTION_DATABASE_ID:
        print("Notion credentials not configured. Skipping Notion update.")
        return False
    
    try:
        # Create reason text with code followed by (in) or (out)
        reason_text = f"{code}({action.lower()})"  # e.g., "00000(in)" or "00000(out)"
        
        # ONLY write to Reason column (title type)
        # No Date, no Clock In, no Clock Out, no other properties
        data = {
            "Reason": {  # Title field (your Reason column is title type)
                "title": [{"text": {"content": reason_text}}]
            }
            # NO other properties - JUST Reason
        }
        
        # Create new page
        url = "https://api.notion.com/v1/pages"
        payload = {
            "parent": {"database_id": NOTION_DATABASE_ID},
            "properties": data
        }
        
        response = requests.post(url, headers=NOTION_HEADERS, json=payload, timeout=5)
        
        if response.status_code == 200:
            now = datetime.now()
            print(f"Notion: {code} {action} at {now.strftime('%H:%M')} - Reason: {reason_text}")
            return True
        else:
            print(f"Notion update failed: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        print(f"Error updating Notion: {e}")
        return False


# =========================
# Idle Animator
# =========================
class IdleAnimator:
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
            raise FileNotFoundError(f"No frames found in {self.frames_dir}")
        self._idx = 0
        self._last_ts = 0.0

    def enable(self, reset=True):
        self.enabled = True
        if reset:
            self._idx = 0
            self._last_ts = 0.0

    def disable(self):
        self.enabled = False

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
    import csv
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

def log_to_notion_reason_only(code: str, action: str) -> None:
    """Log ONLY to Notion Reason column, nothing else."""
    def write_notion_async():
        write_to_notion_reason_only(code, action)
    thread = threading.Thread(target=write_notion_async, daemon=True)
    thread.start()

def _short(s: str, max_len: int = 21) -> str:
    s = (s or "").strip()
    return s if len(s) <= max_len else (s[: max_len - 1] + ".")

def finger_lookup(finger_id: int) -> tuple:
    finger_code_map = load_json(MAP_FILE)
    code = finger_code_map.get(str(finger_id))
    if not code:
        return (False, None, None)
    name = load_code_to_name(USERS_CSV).get(code, "UNKNOWN")
    return (True, code, name)

# =========================
# IN/OUT Status Functions
# =========================
def get_user_status(user_code: str) -> str:
    status_data = load_json(CURRENT_STATUS)
    today = datetime.now().strftime("%Y-%m-%d")
    if "last_reset" not in status_data or status_data["last_reset"] != today:
        status_data = {"last_reset": today}
        save_json(CURRENT_STATUS, status_data)
        return "OUT"
    return status_data.get(user_code, "OUT")

def update_user_status(user_code: str, action: str):
    status_data = load_json(CURRENT_STATUS)
    today = datetime.now().strftime("%Y-%m-%d")
    if "last_reset" not in status_data or status_data["last_reset"] != today:
        status_data = {"last_reset": today}
    status_data[user_code] = action
    save_json(CURRENT_STATUS, status_data)


# =========================
# Finger scan background thread
# =========================
class FingerWorker(threading.Thread):
    def __init__(self, sensor: FingerVeinSensor, out_q: queue.Queue, lock: threading.Lock):
        super().__init__(daemon=True)
        self.sensor = sensor
        self.out_q = out_q
        self.lock = lock
        self._stop = threading.Event()
        self.last_reported_fid = -1
        self.last_detection_time = 0

    def stop(self):
        self._stop.set()

    def run(self):
        while not self._stop.is_set():
            got = self.lock.acquire(timeout=0.2)
            if not got:
                continue
            try:
                fid = self.sensor.verify_and_get_id(user_id=0)
                if fid >= 0:
                    now = time.time()
                    if fid != self.last_reported_fid or (now - self.last_detection_time) > 2.0:
                        self.last_reported_fid = fid
                        self.last_detection_time = now
                        self.out_q.put(("finger_ok", fid))
            except Exception:
                time.sleep(0.2)
            finally:
                try:
                    self.lock.release()
                except RuntimeError:
                    pass


# =========================
# Main App
# =========================
class App:
    def __init__(self):
        self.oled = OLED()
        self.keypad = KeypadUART(KEYPAD_PORT, KEYPAD_BAUD)
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
            time.sleep(3)
            try:
                self.sensor.shutdown()
            except:
                pass
            raise RuntimeError(f"Sensor connect failed with code: {ret}")

        self.code_to_name = load_code_to_name(USERS_CSV)
        self.state = "IDLE"
        self.buf = ""
        self.last_ts = time.time()
        self.last_finger_time = 0
        self.finger_cooldown = FINGER_COOLDOWN
        self.fq = queue.Queue()
        self.fw = FingerWorker(self.sensor, self.fq, SENSOR_LOCK)
        self.fw.start()
        self._init_daily_status()
        self._check_notion_config()
        self.enter_idle()

    def _init_daily_status(self):
        today = datetime.now().strftime("%Y-%m-%d")
        status_data = load_json(CURRENT_STATUS)
        if "last_reset" not in status_data or status_data["last_reset"] != today:
            status_data = {"last_reset": today}
            save_json(CURRENT_STATUS, status_data)
            print(f"New day: {today}, all users set to OUT")

    def _check_notion_config(self):
        if not NOTION_KEY:
            print("WARNING: NOTION_KEY not set")
            self.oled.show_lines(["NOTION WARNING", "API Key Missing", "Logging disabled", ""])
            time.sleep(2)
        elif not NOTION_DATABASE_ID:
            print("WARNING: DATABASE_ID not set")
            self.oled.show_lines(["NOTION WARNING", "DB ID Missing", "Logging disabled", ""])
            time.sleep(2)
        else:
            print("Notion integration configured")
            try:
                url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}"
                response = requests.get(url, headers=NOTION_HEADERS, timeout=3)
                if response.status_code == 200:
                    print("Notion connection successful")
                else:
                    print(f"Notion connection issue: {response.status_code}")
            except Exception as e:
                print(f"Notion connection test failed: {e}")

    def shutdown(self):
        print("\nShutting down system...")
        if hasattr(self, 'fw') and self.fw:
            try:
                print("Stopping finger worker thread...")
                self.fw.stop()
                self.fw.join(timeout=1.0)
                print("Finger worker stopped")
            except Exception as e:
                print(f"Error stopping finger worker: {e}")
        if hasattr(self, 'sensor') and self.sensor:
            try:
                print("Shutting down sensor...")
                if hasattr(self.sensor, 'CloseConnectDev'):
                    ret = self.sensor.CloseConnectDev(3000)
                    print(f"CloseConnectDev returned: {ret}")
                else:
                    self.sensor.shutdown()
                    print("Sensor shutdown complete")
            except Exception as e:
                print(f"Error during sensor shutdown: {e}")
        try:
            self.oled.clear()
            print("OLED cleared")
        except:
            pass
        print("System shutdown complete")

    def clear_finger_queue(self):
        try:
            while True:
                self.fq.get_nowait()
        except queue.Empty:
            pass

    def enter_idle(self):
        self.state = "IDLE"
        self.buf = ""
        self.idle.enable(reset=False)

    def exit_idle(self):
        self.idle.disable()

    def show_buf(self):
        self.exit_idle()
        self.oled.show_lines(["ENTER CODE:", self.buf, "ENTER=submit", "BACK=delete"])

    def handle_finger(self, finger_id: int):
        now = time.time()
        if (now - self.last_finger_time) < self.finger_cooldown:
            self.clear_finger_queue()
            return
        
        self.last_finger_time = now
        self.exit_idle()
        
        enrolled, code, name = finger_lookup(finger_id)
        
        if not enrolled:
            #self.oled.show_lines(["UNKNOWN FINGER", "NOT ENROLLED", "", ""])
            time.sleep(1.5)
            self.enter_idle()
            return

        self.clear_finger_queue()
        current_status = get_user_status(code)
        action = "OUT" if current_status == "IN" else "IN"
        
        # ULTRA SIMPLIFIED: Write ONLY to Notion Reason column, nothing else
        log_to_notion_reason_only(code, action)
        update_user_status(code, action)
        
        t_now = datetime.now().strftime("%H:%M")
        if action == "IN":
            self.oled.show_lines([
                f"WELCOME {_short(name)}!",
                f"Code: {code}",
                f"Time: {t_now}",
                "Status: CLOCKED IN"
            ])
        else:
            self.oled.show_lines([
                f"GOODBYE {_short(name)}!",
                f"Code: {code}",
                f"Time: {t_now}",
                "Status: CLOCKED OUT"
            ])
        
        time.sleep(3)
        self.enter_idle()

    def handle_code_submit(self):
        self.last_finger_time = time.time()
        self.exit_idle()
        
        code = self.buf
        name = self.code_to_name.get(code)
        
        if not name:
            self.oled.show_lines(["DENIED", "Invalid code", "", ""])
            time.sleep(1.5)
            self.enter_idle()
            return

        current_status = get_user_status(code)
        action = "OUT" if current_status == "IN" else "IN"
        
        # ULTRA SIMPLIFIED: Write ONLY to Notion Reason column, nothing else
        log_to_notion_reason_only(code, action)
        update_user_status(code, action)
        
        t_now = datetime.now().strftime("%H:%M")
        if action == "IN":
            self.oled.show_lines([
                f"WELCOME {_short(name)}!",
                f"Code: {code}",
                f"Time: {t_now}",
                "Status: CLOCKED IN"
            ])
        else:
            self.oled.show_lines([
                f"GOODBYE {_short(name)}!",
                f"Code: {code}",
                f"Time: {t_now}",
                "Status: CLOCKED OUT"
            ])
        
        time.sleep(3)
        self.enter_idle()

    def run(self):
        self.exit_idle()
        if NOTION_KEY and NOTION_DATABASE_ID:
            self.oled.show_lines(["ATTENDANCE SYSTEM", "Ready for scans", "Code or Finger", "Notion: ONLINE"])
        else:
            self.oled.show_lines(["ATTENDANCE SYSTEM", "Ready for scans", "Code or Finger", "Notion: OFFLINE"])
        
        time.sleep(2)
        self.enter_idle()
        
        while True:
            if self.state == "IDLE":
                self.idle.tick()

            for ev, val in self.keypad.poll():
                if ev == "key":
                    if self.state == "IDLE":
                        self.exit_idle()
                        self.state = "ENTERING"
                        self.buf = ""
                    if self.state == "ENTERING" and val and str(val).isdigit() and len(self.buf) < 5:
                        self.buf += str(val)
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

            if self.state == "ENTERING" and (time.time() - self.last_ts) > 10:
                self.enter_idle()

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
    except KeyboardInterrupt:
        print("\nShutting down via Ctrl+C...")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        if app:
            app.shutdown()


if __name__ == "__main__":
    main()
