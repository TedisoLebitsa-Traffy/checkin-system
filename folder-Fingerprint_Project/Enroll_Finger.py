import csv
import json
from pathlib import Path
from fingerprint_sensor import FingerVeinSensor

MAP_FILE = Path("finger_code_map.json")
USER_FINGER_MAP_FILE = Path("user_finger_map.json")

USERS_CSV = Path("checkins.csv")          # <-- put your real filename here
USER_KEY_COL = "Code"        # <-- change to a real column in your CSV
USER_NAME_COL = "Employee Name"                 # <-- optional (for display)

AVAILABLE_CODES = [
    "10001", "10002", "10003", "10004", "10005",
    "10006", "10007", "10008", "10009", "10010"
]


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


def choose_user(users: list[dict]) -> dict:
    """
    Shows a numbered list and returns the chosen user dict.
    """
    print("\n--- CHOOSE USER FROM CSV ---")
    for i, u in enumerate(users, start=1):
        key = u.get(USER_KEY_COL, "").strip()
        name = u.get(USER_NAME_COL, "").strip()
        display = f"{key}" + (f" - {name}" if name else "")
        print(f"{i}. {display}")

    while True:
        choice = input("Enter number of user to link to this finger: ").strip()
        if not choice.isdigit():
            print("Please enter a number.")
            continue

        idx = int(choice)
        if 1 <= idx <= len(users):
            return users[idx - 1]
        print("Invalid selection. Try again.")


def assign_next_code(user_id: int, mapping: dict) -> str:
    """
    Same as your existing function (kept).
    mapping: {finger_user_id(str): code(str)}
    """
    uid = str(user_id)
    if uid in mapping:
        return mapping[uid]

    used = set(mapping.values())
    for code in AVAILABLE_CODES:
        if code not in used:
            mapping[uid] = code
            return code

    raise RuntimeError("No available codes left. Add more to AVAILABLE_CODES.")


def enroll_finger_for_selected_user(sensor: FingerVeinSensor, selected_user: dict,
                                   start_id=0, end_id=200) -> tuple[int, str]:
    """
    Enroll a finger and link it to the selected user from CSV.
    If the finger is already registered (duplicate), it asks you to try another finger.
    """
    finger_code_map = load_json(MAP_FILE)
    user_finger_map = load_json(USER_FINGER_MAP_FILE)

    user_key = selected_user[USER_KEY_COL].strip()
    user_name = selected_user.get(USER_NAME_COL, "").strip()

    # If user already has a finger linked, warn & allow overwrite
    if user_key in user_finger_map:
        existing = user_finger_map[user_key]
        print(f"\n?? This user already has a linked finger:")
        print(f"   User: {user_key} {('- ' + user_name) if user_name else ''}")
        print(f"   FingerID: {existing.get('finger_id')}  Code: {existing.get('code')}")
        overwrite = input("Overwrite link with a new finger? (y/n): ").strip().lower()
        if overwrite != "y":
            print("Cancelled enrollment.")
            return int(existing["finger_id"]), existing["code"]

    # ---- Loop until we enroll a NEW finger or user cancels ----
    while True:
        print("\n--- NEW FINGER ENROLLMENT ---")
        input("Press ENTER, then place a NEW (not enrolled) finger when prompted by the sensor...")

        # 1) get empty finger ID from sensor
        finger_id = sensor.get_empty_id(start_id=start_id, end_id=end_id)
        print(f"Empty Finger ID found: {finger_id}")

        # 2) enroll finger to that ID
        print("Enrolling now: place finger / lift finger as instructed...")
        result = sensor.enroll_user(user_id=finger_id, group_id=1, temp_num=3)

        if result == 0:
            # Success -> assign code & save
            code = assign_next_code(finger_id, finger_code_map)
            save_json(MAP_FILE, finger_code_map)

            user_finger_map[user_key] = {
                "finger_id": finger_id,
                "code": code,
                "name": user_name
            }
            save_json(USER_FINGER_MAP_FILE, user_finger_map)

            print("\n? Enrollment + Linking successful")
            print(f"User: {user_key}" + (f" - {user_name}" if user_name else ""))
            print(f"FingerID: {finger_id}")
            print(f"Assigned Code: {code}")
            return finger_id, code

        # Duplicate finger / duplicate ID
        if result == 10:  # 0x0A
            print("\n?? Finger already exists (already enrolled).")
            again = input("Do you want to enroll another DIFFERENT finger? (y/n): ").strip().lower()
            if again == "y":
                continue
            raise RuntimeError("Enrollment cancelled (finger already exists).")

        # Any other error
        raise RuntimeError(f"Enrollment failed with error code: {result}")


def ask_and_enroll_flow(sensor: FingerVeinSensor):
    """
    This is the function you asked for:
    - asks if you want to enroll
    - if yes: choose user from CSV
    - enroll finger and link to chosen user
    """
    answer = input("Do you want to enroll a new finger? (y/n): ").strip().lower()
    if answer != "y":
        print("Skipping enrollment.")
        return

    users = load_users_from_csv(USERS_CSV)
    selected_user = choose_user(users)
    enroll_finger_for_selected_user(sensor, selected_user)


def main():
    sensor = FingerVeinSensor(baud_index=3)

    try:
        ret = sensor.connect("00000000")
        if ret != 0:
            print(f"Connect failed, code: {ret}")
            return
        # New flow:
        ask_and_enroll_flow(sensor)

    finally:
        sensor.shutdown()

if __name__ == "__main__":
    main()
