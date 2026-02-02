import json
from pathlib import Path
from fingerprint_sensor import FingerVeinSensor

USER_FINGER_MAP_FILE = Path("user_finger_map.json")

# These match your earlier error: status=1 (FAIL), reason=12 (0x0C VERIFY failed)
NO_MATCH_STATUS = 1
NO_MATCH_REASON = 12


def load_user_finger_map() -> dict:
    if USER_FINGER_MAP_FILE.exists():
        return json.loads(USER_FINGER_MAP_FILE.read_text())
    return {}


def find_person_by_finger_id(finger_id: int, user_finger_map: dict) -> dict | None:
    for user_key, info in user_finger_map.items():
        if info.get("finger_id") == finger_id:
            return {
                "user_key": user_key,
                "name": info.get("name"),
                "code": info.get("code")
            }
    return None


def is_no_match_error(exc: Exception) -> bool:
    """
    Detect your specific 'no match' case from the exception text:
    RuntimeError: Verify failed, status=1, reason=12
    """
    msg = str(exc)
    return f"status={NO_MATCH_STATUS}" in msg and f"reason={NO_MATCH_REASON}" in msg


def main():
    user_finger_map = load_user_finger_map()
    if not user_finger_map:
        print("? No enrolled users found in user_finger_map.json.")
        return

    sensor = FingerVeinSensor(baud_index=3)

    try:
        ret = sensor.connect("00000000")
        if ret != 0:
            print(f"Connect failed, code: {ret}")
            return

        print("=================================")
        print("  PLACE YOUR FINGER ON THE SENSOR ")
        print("  (Press q then ENTER to quit)    ")
        print("=================================")

        while True:
            quit_choice = input("\nReady? Press ENTER to scan (or 'q' to quit): ").strip().lower()
            if quit_choice == "q":
                print("Exiting.")
                return

            try:
                # 1:N identification
                finger_id = sensor.verify_and_get_id(user_id=0)

                # Lookup person
                person = find_person_by_finger_id(finger_id, user_finger_map)

                if person:
                    print("\n? PERSON IDENTIFIED")
                    print(f"Name : {person['name']}")
                    print(f"Code : {person['user_key']}")
                    print(f"PIN  : {person['code']}")
                    return  # stop after success
                else:
                    print("\n?? Finger recognized but NOT linked to any person in user_finger_map.json")
                    print(f"Finger ID: {finger_id}")
                    again = input("Try another finger? (y/n): ").strip().lower()
                    if again != "y":
                        return

            except Exception as e:
                if is_no_match_error(e):
                    print("\n? No match found (finger not enrolled).")
                    print("Please try another finger.")
                    continue  # loop again for a new attempt

                # Any other error is important (comm issues, timeout, etc.)
                print("\n? Identification failed (unexpected error)")
                print("Reason:", e)
                again = input("Try again? (y/n): ").strip().lower()
                if again == "y":
                    continue
                return

    finally:
        sensor.shutdown()


if __name__ == "__main__":
    main()
