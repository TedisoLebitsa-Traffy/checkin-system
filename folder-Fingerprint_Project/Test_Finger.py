import json
from pathlib import Path
from fingerprint_sensor import FingerVeinSensor

MAP_FILE = Path("finger_code_map.json")

# Put the codes you want to assign here (edit to match your project)
AVAILABLE_CODES = [
    "10001", "10002", "10003", "10004", "10005",
    "10006", "10007", "10008", "10009", "10010"
]

def load_map() -> dict:
    if MAP_FILE.exists():
        return json.loads(MAP_FILE.read_text())
    return {}

def save_map(m: dict) -> None:
    MAP_FILE.write_text(json.dumps(m, indent=2))

def assign_next_code(user_id: int, mapping: dict) -> str:
    """Assign the first unused code to this user_id."""
    uid = str(user_id)
    if uid in mapping:
        return mapping[uid]

    used = set(mapping.values())
    for code in AVAILABLE_CODES:
        if code not in used:
            mapping[uid] = code
            return code

    raise RuntimeError("No available codes left. Add more to AVAILABLE_CODES.")

def enroll_new_finger(sensor: FingerVeinSensor, start_id=0, end_id=100) -> tuple[int, str]:
    """
    Enroll a brand new finger and assign a code.
    Returns (user_id, code).
    """
    mapping = load_map()

    print("\n--- NEW USER ENROLLMENT ---")
    input("Press ENTER when you're ready to enroll a new finger...")

    # 1) Get an empty ID from the sensor
    user_id = sensor.get_empty_id(start_id=start_id, end_id=end_id)
    print(f"Empty ID found: {user_id}")

    # 2) Enroll the finger into that ID
    print("Now ENROLLING. Follow prompts: place finger, lift finger, repeat until done.")
    result = sensor.enroll_user(user_id=user_id, group_id=1, temp_num=3)

    if result != 0:
        raise RuntimeError(f"Enrollment failed with error code: {result}")

    print("Enrollment successful ?")

    # 3) Assign a code for this ID and save it
    code = assign_next_code(user_id, mapping)
    save_map(mapping)

    print(f"Assigned code for UserID {user_id}: {code}")
    return user_id, code

def main():
    sensor = FingerVeinSensor(baud_index=3)  # 3=57600 (your current setting)
    try:
        # Connect (default password)
        ret = sensor.connect("00000000")
        if ret != 0:
            print(f"Connect failed, code: {ret}")
            return

        # Enroll and assign
        user_id, code = enroll_new_finger(sensor, start_id=0, end_id=200)

        print("\nDONE.")
        print(f"UserID = {user_id}")
        print(f"Code   = {code}")
        print(f"Saved mapping -> {MAP_FILE}")

    finally:
        sensor.shutdown()

if __name__ == "__main__":
    main()
