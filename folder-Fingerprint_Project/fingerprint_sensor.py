import time
import serial
import serial_comm  # your module that returns the COM port

# -------- Constants (same as yours) --------
PREFIX_CODE = 0xAABB
XG_CMD_CONNECTION = 0x01
XG_CMD_CLOSE_CONNECTION = 0x02
XG_CMD_GET_SYSTEM_INFO = 0x03
XG_CMD_GET_EMPTY_ID = 0x13
XG_CMD_ENROLL = 0x16
XG_CMD_VERIFY = 0x17

XG_ERR_SUCCESS = 0x00
XG_ERR_DATA = 0x03
XG_ERR_TIME_OUT = 0x0B
XG_ERR_COM = 0x02

XG_INPUT_FINGER = 0x20
XG_RELEASE_FINGER = 0x21
XG_ERR_NO_VEIN = 0x11

Baudrate = {0: 9600, 1: 19200, 2: 38400, 3: 57600, 4: 115200}


class CmdPacket:
    def __init__(self, address=0x00):
        self.wPrefix = PREFIX_CODE
        self.bAddress = address
        self.bCmd = 0x00
        self.bEncode = 0x00
        self.bDataLen = 0x00
        self.bData = [0x00] * 16
        self.wCheckSum = 0x000


class RspPacket:
    def __init__(self, address=0x00):
        self.wPrefix = PREFIX_CODE
        self.bAddress = address
        self.bCmd = 0x00
        self.bEncode = 0x00
        self.bDataLen = 0x00
        self.bData = [0x00] * 16
        self.wCheckSum = 0x000


class FingerVeinSensor:
    """
    Clean wrapper for your module.
    Use it from your main project alongside OLED + keypad.
    """

    def __init__(self, port=None, baud_index=3, address=0x00, timeout=1):
        self.address = address
        self.cmd_buf = [0x00] * 24
        self.rsp_raw = [0x00] * 24
        self.CMD = CmdPacket(address=address)
        self.RSP = RspPacket(address=address)

        if port is None:
            port = serial_comm.get_serial_port()

        self.ser = serial.Serial(
            port,
            baudrate=Baudrate[baud_index],
            rtscts=False,
            dsrdtr=False,
            timeout=timeout
        )

    # ---------- Helpers ----------
    @staticmethod
    def _u32_from_bytes(b1, b2, b3, b4) -> int:
        return b1 | (b2 << 8) | (b3 << 16) | (b4 << 24)

    def _build_and_send(self, timeout=3) -> int:
        checksum = 0

        self.cmd_buf[0] = (self.CMD.wPrefix & 0xFF)
        self.cmd_buf[1] = (self.CMD.wPrefix >> 8) & 0xFF
        self.cmd_buf[2] = self.CMD.bAddress
        self.cmd_buf[3] = self.CMD.bCmd
        self.cmd_buf[4] = self.CMD.bEncode
        self.cmd_buf[5] = self.CMD.bDataLen & 0xFF

        for i in range(16):
            self.cmd_buf[6 + i] = self.CMD.bData[i]

        for i in range(22):
            checksum += self.cmd_buf[i]

        self.cmd_buf[22] = checksum & 0xFF
        self.cmd_buf[23] = (checksum >> 8) & 0xFF

        self.ser.write(bytes(self.cmd_buf))
        self.ser.flush()

        # Reset CMD for next command
        self.CMD = CmdPacket(address=self.address)
        return self._recv_packet(timeout=timeout)

    def _parse_rsp(self):
        rsp = self.rsp_raw
        self.RSP.wPrefix = rsp[0] | (rsp[1] << 8)
        self.RSP.bAddress = rsp[2]
        self.RSP.bCmd = rsp[3]
        self.RSP.bEncode = rsp[4]
        self.RSP.bDataLen = rsp[5]
        for i in range(16):
            self.RSP.bData[i] = rsp[6 + i]
        self.RSP.wCheckSum = rsp[22] | (rsp[23] << 8)

    def _recv_packet(self, timeout=3) -> int:
        start = time.time()

        while (time.time() - start) < timeout:
            try:
                if self.ser.in_waiting >= 24:
                    raw = self.ser.read(24)
                    if len(raw) != 24:
                        continue

                    self.rsp_raw = list(raw)

                    # checksum check
                    chk = 0
                    for i in range(22):
                        chk += self.rsp_raw[i]
                    chk &= 0xFFFF

                    self._parse_rsp()

                    if chk == self.RSP.wCheckSum:
                        return XG_ERR_SUCCESS
                    return XG_ERR_DATA

            except Exception:
                return XG_ERR_COM

        return XG_ERR_TIME_OUT

    # ---------- Public API ----------
    def connect(self, password="00000000") -> int:
        self.CMD.bCmd = XG_CMD_CONNECTION
        self.CMD.bAddress = self.address
        self.CMD.bDataLen = 0x08

        for i in range(16):
            self.CMD.bData[i] = ord(password[i]) if i < len(password) else 0x00

        ret = self._build_and_send(timeout=3)
        if ret == XG_ERR_SUCCESS:
            return self.RSP.bData[0]  # 0 means success
        return ret

    def close(self) -> int:
        self.CMD.bCmd = XG_CMD_CLOSE_CONNECTION
        self.CMD.bAddress = self.address
        self.CMD.bDataLen = 0x00
        ret = self._build_and_send(timeout=3)
        if ret == XG_ERR_SUCCESS:
            return self.RSP.bData[0]
        return ret

    def get_settings(self):
        self.CMD.bCmd = XG_CMD_GET_SYSTEM_INFO
        self.CMD.bAddress = self.address
        self.CMD.bDataLen = 0x00
        ret = self._build_and_send(timeout=3)
        return ret, self.RSP.bData[:]

    def get_empty_id(self, start_id=0, end_id=100) -> int:
        self.CMD.bCmd = XG_CMD_GET_EMPTY_ID
        self.CMD.bAddress = self.address
        self.CMD.bDataLen = 0x08

        # start_id (u32 LE)
        self.CMD.bData[0] = start_id & 0xFF
        self.CMD.bData[1] = (start_id >> 8) & 0xFF
        self.CMD.bData[2] = (start_id >> 16) & 0xFF
        self.CMD.bData[3] = (start_id >> 24) & 0xFF
        # end_id (u32 LE)
        self.CMD.bData[4] = end_id & 0xFF
        self.CMD.bData[5] = (end_id >> 8) & 0xFF
        self.CMD.bData[6] = (end_id >> 16) & 0xFF
        self.CMD.bData[7] = (end_id >> 24) & 0xFF

        ret = self._build_and_send(timeout=3)
        if ret != XG_ERR_SUCCESS:
            raise RuntimeError(f"GetEmptyID comm error {ret}")

        if self.RSP.bData[0] != XG_ERR_SUCCESS:
            raise RuntimeError(f"GetEmptyID device error {self.RSP.bData[0]}")

        # ID is in bData[1..4]
        return self._u32_from_bytes(self.RSP.bData[1], self.RSP.bData[2], self.RSP.bData[3], self.RSP.bData[4])

    def verify_and_get_id(self, user_id=0) -> int:
        """
        user_id=0 => 1:N identification.
        Returns verified user_id on success.
        """
        ret, data = self.get_settings()
        if ret != XG_ERR_SUCCESS or data[0] != XG_ERR_SUCCESS:
            time_out = 6
        else:
            time_out = data[6]  # device timeout in seconds

        self.CMD.bCmd = XG_CMD_VERIFY
        self.CMD.bAddress = self.address
        self.CMD.bDataLen = 0x04

        self.CMD.bData[0] = user_id & 0xFF
        self.CMD.bData[1] = (user_id >> 8) & 0xFF
        self.CMD.bData[2] = (user_id >> 16) & 0xFF  # fixed
        self.CMD.bData[3] = (user_id >> 24) & 0xFF

        ret = self._build_and_send(timeout=time_out)

        while True:
            if ret == XG_ERR_SUCCESS:
                status = self.RSP.bData[0]

                if status == XG_ERR_SUCCESS:
                    return self._u32_from_bytes(self.RSP.bData[1], self.RSP.bData[2], self.RSP.bData[3], self.RSP.bData[4])

                if status == XG_INPUT_FINGER:
                    # Keep waiting for next packet
                    pass
                elif status == XG_RELEASE_FINGER:
                    pass
                else:
                    # device-specific failure reason may be in bData[1]
                    raise RuntimeError(f"Verify failed, status={status}, reason={self.RSP.bData[1]}")

            else:
                raise RuntimeError(f"Verify comm error {ret}")

            ret = self._recv_packet(timeout=time_out)

    def enroll_user(self, user_id: int, group_id=1, temp_num=3) -> int:
        """
        Enroll the given user_id.
        Returns 0 on success.
        """
        self.CMD.bCmd = XG_CMD_ENROLL
        self.CMD.bAddress = self.address
        self.CMD.bDataLen = 0x06

        self.CMD.bData[0] = user_id & 0xFF
        self.CMD.bData[1] = (user_id >> 8) & 0xFF
        self.CMD.bData[2] = (user_id >> 16) & 0xFF
        self.CMD.bData[3] = (user_id >> 24) & 0xFF
        self.CMD.bData[4] = group_id
        self.CMD.bData[5] = temp_num

        ret = self._build_and_send(timeout=6)

        while True:
            if ret != XG_ERR_SUCCESS:
                return ret

            status = self.RSP.bData[0]
            if status == XG_ERR_SUCCESS:
                return XG_ERR_SUCCESS

            if status in (XG_INPUT_FINGER, XG_RELEASE_FINGER):
                ret = self._recv_packet(timeout=6)
                continue

            # error code usually in bData[1]
            return self.RSP.bData[1]

    def shutdown(self):
        try:
            self.close()
        except Exception:
            pass
        try:
            self.ser.close()
        except Exception:
            pass
