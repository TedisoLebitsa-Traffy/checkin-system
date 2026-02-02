from fingerprint_sensor import FingerVeinSensor

def main():
    sensor = FingerVeinSensor(baud_index=3)  # 57600 baud (same as your setup)

    try:
        # 1. Connect to the device
        ret = sensor.connect("00000000")
        if ret != 0:
            print(f"Failed to connect to sensor (error {ret})")
            return

        print("=================================")
        print(" PLACE YOUR FINGER ON THE SENSOR ")
        print("=================================")

        # 2. 1:N identification (ID = 0)
        user_id = sensor.verify_and_get_id(user_id=0)

        # 3. Show the ID
        print("\n? FINGER RECOGNIZED")
        print(f"?? Your User ID is: {user_id}")

    except Exception as e:
        # Happens if finger is not enrolled or no match
        print("\n? FINGER NOT RECOGNIZED")
        print("Reason:", e)

    finally:
        sensor.shutdown()

if __name__ == "__main__":
    main()