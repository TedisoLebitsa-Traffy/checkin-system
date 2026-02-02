
# -*- coding: utf-8 -*-

import time
import csv
import json
from pathlib import Path
from fingerprint_sensor import FingerVeinSensor
from oled import OLED
from keypad import KeypadUART

MAP_FILE = Path("finger_code_map.json")
USER_FINGER_MAP_FILE = Path("user_finger_map.json")

USERS_CSV = Path("checkins.csv")
USER_KEY_COL = "Code"
USER_NAME_COL = "Employee Name"

AVAILABLE_CODES = [
    "10001", "10002", "10003", "10004", "10005",
    "10006", "10007", "10008", "10009", "10010"
]

# OLED is 4 lines -> show 2 users per page + header + footer
ITEMS_PER_PAGE = 2


def load_json(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {}

def save_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2))


def load_users_from_csv(csv_path: Path) -> list[dict]:
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    with csv_path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        users = [row for row in reader]

    if not users:
        raise ValueError("CSV file is empty or has no data rows.")

    if USER_KEY_COL not in users[0]:
        raise ValueError(
            f"CSV does not contain required column '{USER_KEY_COL}'. "
            f"Columns found: {list(users[0].keys())}"
        )

    return users


def _short(s: str, max_len: int) -> str:
    s = (s or "").strip()
    return s if len(s) <= max_len else (s[: max_len - 1] + ".")


def choose_user_oled(users: list[dict], oled: OLED, keypad: KeypadUART) -> dict:
    """
    OLED paging + keypad selection:
      - PgUp = next page
      - PgDn = previous page
      - digit key = choose line (1..ITEMS_PER_PAGE)
      - Enter = confirm
      - Back = cancel
    """
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
            footer = "ENTER=OK  BACK=CAN"

        lines = [_short(header, 21)]

        # user lines (2 lines)
        for i in range(ITEMS_PER_PAGE):
            if i < len(visible):
                u = visible[i]
                code = (u.get(USER_KEY_COL) or "").strip()
                name = (u.get(USER_NAME_COL) or "").strip()
                label = f"{i+1}) {code}"
                if name:
                    label += f" {name}"
                abs_idx = start + i
                prefix = ">" if (selected_abs_idx == abs_idx) else " "
                lines.append(_short(prefix + label, 21))
            else:
                lines.append("")

        lines.append(_short(footer, 21))
        oled.show_lines(lines)

    render()

    while True:
        # Poll keypad events
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

            elif event == "key":
                # only accept numeric selection keys: 1..ITEMS_PER_PAGE
                if value.isdigit():
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


def assign_next_code(user_id: int, mapping: dict) -> str:
    uid = str(user_id)
    if uid in mapping:
        return mapping[uid]

    used = set(mapping.values())
    for code in AVAILABLE_CODES:
        if code not in used:
            mapping[uid] = code
            return code

    raise RuntimeError("No available codes left. Add more to AVAILABLE_CODES.")


def enroll_finger_for_selected_user(
    sensor: FingerVeinSensor,
    selected_user: dict,
    oled: OLED,
    keypad: KeypadUART,
    start_id=0,
    end_id=200
) -> tuple[int, str]:
    finger_code_map = load_json(MAP_FILE)
    user_finger_map = load_json(USER_FINGER_MAP_FILE)

    user_key = selected_user[USER_KEY_COL].strip()
    user_name = (selected_user.get(USER_NAME_COL) or "").strip()

    # if user already linked
    if user_key in user_finger_map:
        existing = user_finger_map[user_key]
        oled.show_lines([
            "ALREADY LINKED",
            f"{_short(user_key, 21)}",
            f"FID:{existing.get('finger_id')} C:{existing.get('code')}",
            "ENTER=NEW BACK=KEEP"
        ])

        while True:
            for ev, val in keypad.poll():
                if ev == "back":
                    return int(existing["finger_id"]), existing["code"]
                if ev == "enter":
                    break
            else:
                time.sleep(0.05)
                continue
            break

    # Start enrollment
    oled.show_lines(["ENROLL NEW", "PRESS ENTER", "BACK=cancel", ""])
    while True:
        for ev, val in keypad.poll():
            if ev == "back":
                raise RuntimeError("Enrollment cancelled.")
            if ev == "enter":
                break
        else:
            time.sleep(0.05)
            continue
        break

    oled.show_lines(["FIND EMPTY ID", "PLEASE WAIT...", "", ""])
    finger_id = sensor.get_empty_id(start_id=start_id, end_id=end_id)

    oled.show_lines(["ENROLLING...", f"ID: {finger_id}", "FOLLOW SENSOR", ""])
    result = sensor.enroll_user(user_id=finger_id, group_id=1, temp_num=3)

    if result == 0:
        code = assign_next_code(finger_id, finger_code_map)
        save_json(MAP_FILE, finger_code_map)

        user_finger_map[user_key] = {"finger_id": finger_id, "code": code, "name": user_name}
        save_json(USER_FINGER_MAP_FILE, user_finger_map)

        oled.show_lines(["SUCCESS ✅", _short(user_name or user_key, 21), f"CODE: {code}", ""])
        time.sleep(2)
        return finger_id, code

    if result == 10:
        oled.show_lines(["FINGER EXISTS", "TRY ANOTHER", "ENTER=retry", "BACK=stop"])
        while True:
            for ev, val in keypad.poll():
                if ev == "back":
                    raise RuntimeError("Enrollment cancelled (duplicate finger).")
                if ev == "enter":
                    return enroll_finger_for_selected_user(sensor, selected_user, oled, keypad, start_id, end_id)
            time.sleep(0.05)

    raise RuntimeError(f"Enrollment failed with error code: {result}")


def ask_and_enroll_flow(sensor: FingerVeinSensor, oled: OLED, keypad: KeypadUART):
    oled.show_lines(["ENROLL FINGER?", "ENTER=yes", "BACK=no", ""])
    while True:
        for ev, val in keypad.poll():
            if ev == "back":
                oled.show_lines(["SKIPPING...", "", "", ""])
                time.sleep(0.8)
                return
            if ev == "enter":
                break
        else:
            time.sleep(0.05)
            continue
        break

    users = load_users_from_csv(USERS_CSV)
    selected_user = choose_user_oled(users, oled, keypad)
    enroll_finger_for_selected_user(sensor, selected_user, oled, keypad)


def main():
    # Configure these to your actual ports
    KEYPAD_PORT = "/dev/ttyUSB0"
    KEYPAD_BAUD = 9600

    oled = OLED()
    keypad = KeypadUART(KEYPAD_PORT, KEYPAD_BAUD)
    sensor = FingerVeinSensor(baud_index=3)

    try:
        ret = sensor.connect("00000000")
        if ret != 0:
            oled.show_lines(["SENSOR FAIL", f"CODE: {ret}", "", ""])
            time.sleep(2)
            return

        ask_and_enroll_flow(sensor, oled, keypad)

    finally:
        sensor.shutdown()


if __name__ == "__main__":
    main()
