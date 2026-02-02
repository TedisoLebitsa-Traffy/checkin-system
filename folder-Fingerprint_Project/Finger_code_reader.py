import csv
import json
from pathlib import Path
from fingerprint_sensor import FingerVeinSensor


class FingerCodeReader:
    """
    - Continuously scans until a correct/enrolled finger is detected
    - Returns the user's Code from the CSV file (NOT from JSON)

    Files:
      1) user_finger_map.json:
         {
           "<CSV_CODE>": {"finger_id": <int>, "code": "...", "name": "..."},
           ...
         }

      2) checkins.csv (or your file):
         Must contain columns like: "Code", "Employee Name"
    """

    NO_MATCH_STATUS = 1
    NO_MATCH_REASON = 12  # your 'no match' case

    def __init__(
        self,
        users_csv: str | Path = "checkins.csv",
        user_key_col: str = "Code",
        user_name_col: str = "Employee Name",
        finger_map_path: str | Path = "user_finger_map.json",
        baud_index: int = 3,
        password: str = "00000000",
    ):
        self.users_csv = Path(users_csv)
        self.user_key_col = user_key_col
        self.user_name_col = user_name_col
        self.finger_map_path = Path(finger_map_path)
        self.baud_index = baud_index
        self.password = password

        # Load CSV once (fast lookups)
        self._csv_index = self._load_csv_index()

    # ---------- CSV + JSON helpers ----------
    def _load_csv_index(self) -> dict:
        """
        Build an index: code -> row dict (full row).
        """
        if not self.users_csv.exists():
            raise FileNotFoundError(f"CSV not found: {self.users_csv}")

        with self.users_csv.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        if not rows:
            raise ValueError("CSV is empty or has no rows.")

        if self.user_key_col not in rows[0]:
            raise ValueError(
                f"CSV missing required column '{self.user_key_col}'. "
                f"Found: {list(rows[0].keys())}"
            )

        index = {}
        for r in rows:
            code = (r.get(self.user_key_col) or "").strip()
            if code:
                index[code] = r
        return index

    def _load_finger_map(self) -> dict:
        """
        Load user_finger_map.json.
        """
        if self.finger_map_path.exists():
            return json.loads(self.finger_map_path.read_text())
        return {}

    @staticmethod
    def _is_no_match_error(exc: Exception) -> bool:
        """
        Detect: RuntimeError("Verify failed, status=1, reason=12")
        """
        msg = str(exc)
        return ("status=1" in msg) and ("reason=12" in msg)

    # ---------- Public API ----------
    def scan_code_until_correct(self) -> str:
        """
        Keeps asking to scan again until:
          - finger is recognized AND
          - finger_id is linked in user_finger_map.json AND
          - that linked key exists in the CSV

        Returns:
          - the Code (from CSV) as a string

        Notes:
          - Prints "finger is wrong" when not enrolled / no match.
        """
        finger_map = self._load_finger_map()
        if not finger_map:
            raise RuntimeError(f"No enrolled fingers found in {self.finger_map_path}")

        sensor = FingerVeinSensor(baud_index=self.baud_index)

        try:
            ret = sensor.connect(self.password)
            if ret != 0:
                raise RuntimeError(f"Sensor connect failed (code {ret})")

            while True:
                try:
                    finger_id = sensor.verify_and_get_id(user_id=0)
                except Exception as e:
                    if self._is_no_match_error(e):
                        print("? Finger is wrong / not enrolled. Please scan again.")
                        continue
                    # other errors: re-raise
                    raise

                # Find which CSV "Code" is linked to this finger_id (JSON keys are the CSV codes)
                matched_code_key = None
                for code_key, info in finger_map.items():
                    if info.get("finger_id") == finger_id:
                        matched_code_key = str(code_key).strip()
                        break

                if matched_code_key is None:
                    print("? Finger recognized but not linked to any user. Scan again.")
                    continue

                # Ensure that code exists in the CSV
                if matched_code_key not in self._csv_index:
                    print("? Finger linked, but user not found in CSV. Scan again.")
                    continue

                # Return the Code from CSV (this is literally the key column)
                return matched_code_key

        finally:
            sensor.shutdown()

    def scan_user_until_correct(self) -> dict:
        """
        Optional: returns full CSV row (including name, department, etc.)
        """
        code = self.scan_code_until_correct()
        return self._csv_index[code]
