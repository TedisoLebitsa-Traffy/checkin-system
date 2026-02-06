#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import csv
import json
import threading
import queue
from pathlib import Path
from datetime import datetime, timedelta
import requests

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
CURRENT_STATUS = Path("current_status.json")  # NEW: Track who's IN/OUT
SENSOR_LOCK = threading.Lock()

# Notion Integration
NOTION_KEY = os.getenv("NOTION_INTEGRATION_KEY")
NOTION_ATTENDANCE_DATABASE_ID = os.getenv("NOTION_ATTENDANCE_DATABASE_ID")

# Mapping files
MAP_FILE = Path("finger_code_map.json")             # finger_id(str) -> user_code(str)
USER_FINGER_MAP_FILE = Path("user_finger_map.json") # user_code(str) -> {finger_id, code, name}

# ---- Idle animation settings ----
IDLE_FRAMES_DIR = Path("idle_frames")
IDLE_FPS = 8           # safe start; raise if stable
IDLE_STEP = 3          # frame skipping (bigger = faster animation)
IDLE_RETRIES = 3       # retry OLED display on occasional I2C glitches

# ---- Finger debouncing settings ----
FINGER_COOLDOWN = 2.0  # seconds between allowed finger scans

# ---- Notion API settings ----
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_KEY}",
    "content-type": "application/json",
    "Notion-Version": "2022-06-28",
}


# =========================
# Notion Integration Functions
# =========================
def find_todays_entry(code: str, date_str: str = None):
    """
    Find today's attendance entry for a specific employee code.
    """
    if not NOTION_KEY or not NOTION_ATTENDANCE_DATABASE_ID:
        return None
    
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    
    try:
        url = f"https://api.notion.com/v1/databases/{NOTION_ATTENDANCE_DATABASE_ID}/query"
        
        # Query for today's entry for this employee
        payload = {
            "filter": {
                "and": [
                    {"property": "Employee Code", "rich_text": {"equals": code}},
                    {"property": "Date", "date": {"equals": date_str}}
                ]
            }
        }
        
        response = requests.post(url, headers=NOTION_HEADERS, json=payload, timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            if data.get("results") and len(data["results"]) > 0:
                return data["results"][0]  # Return first matching record
        
        return None
        
    except Exception as e:
        print(f"Error querying Notion: {e}")
        return None


def calculate_hours(clock_in_str: str, clock_out_str: str) -> str:
    """
    Calculate total hours between clock in and clock out.
    Returns format like "8.5" for 8 hours 30 minutes.
    """
    try:
        # Parse the datetime strings
        fmt = "%Y-%m-%dT%H:%M:%S.%f%z" if '.' in clock_in_str else "%Y-%m-%dT%H:%M:%S%z"
        
        clock_in = datetime.strptime(clock_in_str, fmt)
        clock_out = datetime.strptime(clock_out_str, fmt)
        
        # Calculate difference in hours
        diff = clock_out - clock_in
        total_hours = diff.total_seconds() / 3600
        
        # Format to 1 decimal place
        return f"{total_hours:.1f}"
    except Exception as e:
        print(f"Error calculating hours: {e}")
        return "0.0"


def update_notion_attendance(employee_name: str, code: str, action: str, method: str):
    """
    Smart attendance update: One entry per employee per day.
    Updates Clock In time when checking IN.
    Updates Clock Out time and calculates Total Hours when checking OUT.
    REASON column gets code followed by (in) or (out) - e.g., "00000(in)" or "00000(out)"
    """
    if not NOTION_KEY or not NOTION_ATTENDANCE_DATABASE_ID:
        print("Notion credentials not configured. Skipping Notion update.")
        return False
    
    try:
        # Get current time
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        datetime_iso = now.isoformat()
        
        # Create reason text with code followed by (in) or (out)
        reason_text = f"{code}({action.lower()})"  # e.g., "00000(in)" or "00000(out)"
        
        # Find today's existing entry
        existing_entry = find_todays_entry(code, date_str)
        
        if action == "IN":
            # Clocking IN - create new entry or update if doesn't exist
            if existing_entry:
                # Entry already exists (shouldn't happen, but just in case)
                print(f"⚠️ Entry already exists for {employee_name} today")
                page_id = existing_entry["id"]
                
                # Update Clock In time and Reason
                update_data = {
                    "Clock In": {
                        "date": {"start": datetime_iso}
                    },
                    "Reason": {
                        "rich_text": [{"text": {"content": reason_text}}]
                    }
                }
                
                # Send update
                update_url = f"https://api.notion.com/v1/pages/{page_id}"
                payload = {"properties": update_data}
                response = requests.patch(update_url, headers=NOTION_HEADERS, json=payload, timeout=5)
                
            else:
                # Create new entry
                data = {
                    "Employees": {  # Your title field
                        "title": [{"text": {"content": employee_name}}]
                    },
                    "Employee Code": {
                        "rich_text": [{"text": {"content": code}}]
                    },
                    "Date": {
                        "date": {"start": date_str}
                    },
                    "Clock In": {
                        "date": {"start": datetime_iso}
                    },
                    "Reason": {
                        "rich_text": [{"text": {"content": reason_text}}]
                    },
                    # Clock Out will remain empty
                    # Day Type can be set to default if you want
                    "Day Type": {
                        "select": {"name": "Regular"}  # Adjust as needed
                    }
                }
                
                # Create new page
                url = "https://api.notion.com/v1/pages"
                payload = {
                    "parent": {"database_id": NOTION_ATTENDANCE_DATABASE_ID},
                    "properties": data
                }
                
                response = requests.post(url, headers=NOTION_HEADERS, json=payload, timeout=5)
            
            if response.status_code == 200:
                print(f"✓ Notion: {employee_name} Clocked IN at {now.strftime('%H:%M')} - Reason: {reason_text}")
                return True
                
        else:  # action == "OUT"
            # Clocking OUT - update existing entry
            if not existing_entry:
                # No entry found - create one with both times (late entry)
                print(f"⚠️ No entry found for {employee_name}, creating with both times")
                
                data = {
                    "Employees": {
                        "title": [{"text": {"content": employee_name}}]
                    },
                    "Employee Code": {
                        "rich_text": [{"text": {"content": code}}]
                    },
                    "Date": {
                        "date": {"start": date_str}
                    },
                    "Clock In": {
                        "date": {"start": f"{date_str}T09:00:00"}  # Default 9 AM
                    },
                    "Clock Out": {
                        "date": {"start": datetime_iso}
                    },
                    "Reason": {
                        "rich_text": [{"text": {"content": reason_text}}]
                    },
                    "Day Type": {
                        "select": {"name": "Regular"}
                    }
                }
                
                url = "https://api.notion.com/v1/pages"
                payload = {
                    "parent": {"database_id": NOTION_ATTENDANCE_DATABASE_ID},
                    "properties": data
                }
                
                response = requests.post(url, headers=NOTION_HEADERS, json=payload, timeout=5)
                
            else:
                # Update existing entry with Clock Out time and Reason
                page_id = existing_entry["id"]
                
                # Get Clock In time from existing entry
                clock_in_prop = existing_entry["properties"].get("Clock In", {})
                clock_in_date = clock_in_prop.get("date")
                
                update_data = {
                    "Clock Out": {
                        "date": {"start": datetime_iso}
                    },
                    "Reason": {
                        "rich_text": [{"text": {"content": reason_text}}]
                    }
                }
                
                # Only add Total Hours if we have Clock In time
                if clock_in_date and clock_in_date.get("start"):
                    clock_in_time = clock_in_date["start"]
                    
                    # Calculate total hours
                    total_hours = calculate_hours(clock_in_time, datetime_iso)
                    
                    # Add Total Hours to update data
                    update_data["Total Hours"] = {
                        "number": float(total_hours)
                    }
                
                # Send update
                update_url = f"https://api.notion.com/v1/pages/{page_id}"
                payload = {"properties": update_data}
                response = requests.patch(update_url, headers=NOTION_HEADERS, json=payload, timeout=5)
            
            if response.status_code == 200:
                print(f"✓ Notion: {employee_name} Clocked OUT at {now.strftime('%H:%M')} - Reason: {reason_text}")
                return True
        
        # If we get here, something went wrong
        if response.status_code != 200:
            print(f"✗ Notion update failed: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        print(f"✗ Error updating Notion: {e}")
        return False


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

def log_attendance(employee_name: str, code: str, method: str, action: str) -> None:
    """Log attendance with IN/OUT action to local CSV and Notion."""
    # 1. Log to local CSV file
    ATTENDANCE_LOG.parent.mkdir(parents=True, exist_ok=True)
    new_file = not ATTENDANCE_LOG.exists()
    now = datetime.now()
    with ATTENDANCE_LOG.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["date", "time", "employee_name", "code", "method", "action"])
        w.writerow([now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S"),
                    employee_name, code, method, action])
    
    # 2. Update Notion database (non-blocking in separate thread)
    def update_notion_async():
        update_notion_attendance(employee_name, code, action, method)
    
    # Start thread for Notion update to avoid blocking main thread
    thread = threading.Thread(target=update_notion_async, daemon=True)
    thread.start()

def _short(s: str, max_len: int = 21) -> str:
    s = (s or "").strip()
    return s if len(s) <= max_len else (s[: max_len - 1] + ".")

def finger_lookup(finger_id: int) -> tuple:
    """Check if finger ID is mapped to a user code."""
    finger_code_map = load_json(MAP_FILE)  # finger_id -> user_code
    code = finger_code_map.get(str(finger_id))
    if not code:
        return (False, None, None)
    name = load_code_to_name(USERS_CSV).get(code, "UNKNOWN")
    return (True, code, name)

# =========================
# NEW: IN/OUT Status Functions
# =========================
def get_user_status(user_code: str) -> str:
    """Get current IN/OUT status for a user."""
    status_data = load_json(CURRENT_STATUS)
    
    # Check if we need daily reset
    today = datetime.now().strftime("%Y-%m-%d")
    if "last_reset" not in status_data or status_data["last_reset"] != today:
        # New day, reset all statuses to OUT
        status_data = {"last_reset": today}
        save_json(CURRENT_STATUS, status_data)
        return "OUT"  # Everyone starts as OUT
    
    # Return current status or default to OUT
    return status_data.get(user_code, "OUT")

def update_user_status(user_code: str, action: str):
    """Update user's current status."""
    status_data = load_json(CURRENT_STATUS)
    
    # Ensure we have today's date
    today = datetime.now().strftime("%Y-%m-%d")
    if "last_reset" not in status_data or status_data["last_reset"] != today:
        # New day, reset
        status_data = {"last_reset": today}
    
    # Update status
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
        self.last_reported_fid = -1  # Track last finger ID
        self.last_detection_time = 0  # Track last detection time

    def stop(self):
        self._stop.set()

    def run(self):
        while not self._stop.is_set():
            got = self.lock.acquire(timeout=0.2)
            if not got:
                continue
            try:
                fid = self.sensor.verify_and_get_id(user_id=0)  # may block
                if fid >= 0:  # Only process valid finger IDs
                    now = time.time()
                    # Only report if:
                    # 1. Different finger, OR
                    # 2. Same finger but > 2 seconds since last detection
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

        # Idle animator
        self.idle = IdleAnimator(
            oled=self.oled,
            frames_dir=IDLE_FRAMES_DIR,
            fps=IDLE_FPS,
            step=IDLE_STEP,
            retries=IDLE_RETRIES
        )

        # Initialize sensor
        self.sensor = FingerVeinSensor(baud_index=3)
        ret = self.sensor.connect(SENSOR_PASSWORD)
        
        if ret != 0:
            self.oled.show_lines(["SENSOR FAIL", f"CODE:{ret}", "", ""])
            time.sleep(3)
            # Try to shutdown even if connection failed
            try:
                self.sensor.shutdown()
            except:
                pass
            raise RuntimeError(f"Sensor connect failed with code: {ret}")

        self.code_to_name = load_code_to_name(USERS_CSV)

        self.state = "IDLE"
        self.buf = ""
        self.last_ts = time.time()
        
        # Finger debouncing variables
        self.last_finger_time = 0
        self.finger_cooldown = FINGER_COOLDOWN

        self.fq = queue.Queue()
        self.fw = FingerWorker(self.sensor, self.fq, SENSOR_LOCK)
        self.fw.start()

        # Initialize current status for today
        self._init_daily_status()
        
        # Check Notion configuration
        self._check_notion_config()
        
        self.enter_idle()

    def _init_daily_status(self):
        """Initialize or reset daily status file."""
        today = datetime.now().strftime("%Y-%m-%d")
        status_data = load_json(CURRENT_STATUS)
        if "last_reset" not in status_data or status_data["last_reset"] != today:
            # New day, reset all statuses
            status_data = {"last_reset": today}
            save_json(CURRENT_STATUS, status_data)
            print(f"New day: {today}, all users set to OUT")

    def _check_notion_config(self):
        """Check if Notion is properly configured."""
        if not NOTION_KEY:
            print("⚠️ WARNING: NOTION_INTEGRATION_KEY not set in environment variables")
            self.oled.show_lines(["NOTION WARNING", "API Key Missing", "Local log only", ""])
            time.sleep(2)
        elif not NOTION_ATTENDANCE_DATABASE_ID:
            print("⚠️ WARNING: NOTION_ATTENDANCE_DATABASE_ID not set in environment variables")
            self.oled.show_lines(["NOTION WARNING", "DB ID Missing", "Local log only", ""])
            time.sleep(2)
        else:
            print("✓ Notion integration configured")
            # Test connection and get database properties
            try:
                url = f"https://api.notion.com/v1/databases/{NOTION_ATTENDANCE_DATABASE_ID}"
                response = requests.get(url, headers=NOTION_HEADERS, timeout=3)
                if response.status_code == 200:
                    print("✓ Notion connection successful")
                    
                    # Check if required properties exist
                    data = response.json()
                    properties = data.get('properties', {})
                    
                    # Check if Reason property exists (updated requirement)
                    required_props = ["Employees", "Employee Code", "Date", "Clock In", "Clock Out", "Reason"]
                    missing_props = []
                    
                    for prop in required_props:
                        if prop not in properties:
                            missing_props.append(prop)
                    
                    if missing_props:
                        print(f"⚠️ Missing properties in Notion DB: {missing_props}")
                        if "Reason" in missing_props:
                            print("⚠️ IMPORTANT: 'Reason' property is required for this system!")
                    else:
                        print("✓ All required properties found")
                        
                else:
                    print(f"⚠️ Notion connection issue: {response.status_code}")
            except Exception as e:
                print(f"⚠️ Notion connection test failed: {e}")

    def shutdown(self):
        """Clean shutdown of all components."""
        print("\nShutting down system...")
        
        # Stop finger worker thread first
        if hasattr(self, 'fw') and self.fw:
            try:
                print("Stopping finger worker thread...")
                self.fw.stop()
                self.fw.join(timeout=1.0)
                print("Finger worker stopped")
            except Exception as e:
                print(f"Error stopping finger worker: {e}")
        
        # Proper sensor shutdown
        if hasattr(self, 'sensor') and self.sensor:
            try:
                print("Shutting down sensor...")
                
                # Try manufacturer's CloseConnectDev first
                if hasattr(self.sensor, 'CloseConnectDev'):
                    print("Using CloseConnectDev...")
                    ret = self.sensor.CloseConnectDev(3000)
                    print(f"CloseConnectDev returned: {ret}")
                else:
                    # Use regular shutdown
                    self.sensor.shutdown()
                    print("Sensor shutdown complete")
                    
            except Exception as e:
                print(f"Error during sensor shutdown: {e}")
        
        # Clear OLED
        try:
            self.oled.clear()
            print("OLED cleared")
        except:
            pass
        
        print("System shutdown complete")

    def clear_finger_queue(self):
        """Clear all pending finger events from the queue."""
        try:
            while True:
                self.fq.get_nowait()
        except queue.Empty:
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

    # =========================
    # UPDATED: Handle Finger with IN/OUT logic and debouncing
    # =========================
    def handle_finger(self, finger_id: int):
        # Debounce check
        now = time.time()
        if (now - self.last_finger_time) < self.finger_cooldown:
            # Clear any queued events during cooldown
            self.clear_finger_queue()
            return
        
        self.last_finger_time = now
        self.exit_idle()
        
        enrolled, code, name = finger_lookup(finger_id)
        
        if not enrolled:
            self.oled.show_lines(["UNKNOWN FINGER", "NOT ENROLLED", "", ""])
            time.sleep(1.5)
            self.enter_idle()
            return

        # Clear any queued events after successful detection
        self.clear_finger_queue()
        
        # Determine IN/OUT action
        current_status = get_user_status(code)
        action = "OUT" if current_status == "IN" else "IN"
        
        # Log attendance (this now includes Notion update with reason text)
        log_attendance(name, code, "finger", action)
        update_user_status(code, action)
        
        # Show appropriate message
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

    # =========================
    # UPDATED: Handle Code with IN/OUT logic
    # =========================
    def handle_code_submit(self):
        # Update last finger time for code entries too (to prevent immediate finger scan after code)
        self.last_finger_time = time.time()
        self.exit_idle()
        
        code = self.buf
        name = self.code_to_name.get(code)
        
        if not name:
            log_attendance("UNKNOWN", code, "code", "DENIED")
            self.oled.show_lines(["DENIED", "Invalid code", "", ""])
            time.sleep(1.5)
            self.enter_idle()
            return

        # Determine IN/OUT action
        current_status = get_user_status(code)
        action = "OUT" if current_status == "IN" else "IN"
        
        # Log attendance (this now includes Notion update with reason text)
        log_attendance(name, code, "code", action)
        update_user_status(code, action)
        
        # Show appropriate message
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
        # Show welcome message
        self.exit_idle()
        
        # Check if Notion is configured
        if NOTION_KEY and NOTION_ATTENDANCE_DATABASE_ID:
            self.oled.show_lines(["ATTENDANCE SYSTEM", "Ready for scans", "Code or Finger", "Notion: ONLINE"])
        else:
            self.oled.show_lines(["ATTENDANCE SYSTEM", "Ready for scans", "Code or Finger", "Notion: OFFLINE"])
        
        time.sleep(2)
        self.enter_idle()
        
        while True:
            # ---- IDLE animation tick ----
            if self.state == "IDLE":
                self.idle.tick()

            # ---- Keypad events ----
            for ev, val in self.keypad.poll():
                # Normal digit entry to start typing code
                if ev == "key":
                    if self.state == "IDLE":
                        self.exit_idle()
                        self.state = "ENTERING"
                        self.buf = ""
            
                    # Only accept digits into the code buffer
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
    except KeyboardInterrupt:
        print("\nShutting down via Ctrl+C...")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        if app:
            app.shutdown()


if __name__ == "__main__":
    main()
