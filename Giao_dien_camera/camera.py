"""
Sony FCB-EV9520L Camera Control Module
Uses VISCA protocol over serial connection
"""
import serial
import time
import threading
import logging
from typing import Optional

# Setup logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class SonyCamera:
    """Control Sony FCB-EV9520L camera via VISCA protocol"""
    
    # VISCA command constants
    VISCA_HEADER = 0x81
    VISCA_TERMINATOR = 0xFF
    
    # Focus modes
    FOCUS_AUTO = 0x02
    FOCUS_MANUAL = 0x03
    
    # AE modes
    AE_FULL_AUTO = 0x00
    AE_MANUAL = 0x03
    AE_SHUTTER_PRI = 0x0A
    AE_IRIS_PRI = 0x0B
    
    # Iris F-stops and values
    IRIS_F_STOPS = ["CLOSE", "F14", "F11", "F9.6", "F8.0", "F6.8", "F5.6", 
                    "F4.8", "F4.0", "F3.4", "F2.8", "F2.4", "F2.0", "F1.6"]
    IRIS_HEX_VALUES = [0x00, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B, 
                       0x0C, 0x0D, 0x0E, 0x0F, 0x10, 0x11]
    
    # Shutter speeds
    SHUTTER_SPEEDS = ["1/1", "1/2", "1/4", "1/8", "1/15", "1/30", "1/60", "1/90",
                      "1/100", "1/125", "1/180", "1/250", "1/350", "1/500", 
                      "1/725", "1/1000", "1/1500", "1/2000", "1/3000", "1/4000", 
                      "1/6000", "1/10000"]
    SHUTTER_HEX_VALUES = [0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 
                          0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F, 0x10, 0x11, 0x12, 0x13, 
                          0x14, 0x15]
    
    # Gain dB values
    GAIN_DB_OFF = [0.0, 3.6, 7.1, 10.7, 14.3, 17.9, 21.4, 25.0, 28.6, 32.1, 35.7, 39.3, 42.9, 46.4, 50.0]
    GAIN_DB_ON = [0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 35.0, 40.0, 45.0, 50.0, 55.0, 60.0, 65.0, 70.0]
    
    def __init__(self, port: str = 'COM3', baudrate: int = 9600):
        self.port = port
        self.baudrate = baudrate
        self.serial: Optional[serial.Serial] = None
        self.is_connected = False
        self.read_timeout = 5000  # milliseconds
        
    def connect(self) -> bool:
        """Connect to camera via serial port"""
        try:
            self.serial = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=1,
                bytesize=serial.EIGHTBITS,
                stopbits=serial.STOPBITS_ONE,
                parity=serial.PARITY_NONE
            )
            self.is_connected = True
            time.sleep(0.5)
            return True
        except Exception as e:
            print(f"Connection failed: {e}")
            self.is_connected = False
            return False
    
    def disconnect(self):
        """Disconnect from camera"""
        if self.serial:
            self.serial.close()
            self.is_connected = False
    
    def _send_command(self, cmd: bytes) -> Optional[bytes]:
        """Send VISCA command and receive response"""
        if not self.is_connected or not self.serial:
            return None
        
        try:
            # Flush old data to avoid reading leftover completions from previous commands
            self.serial.reset_input_buffer()
            
            self.serial.write(cmd)
            
            response = bytearray()
            start_time = time.time()
            is_inquiry = (len(cmd) > 1 and cmd[1] == 0x09)
            
            while time.time() - start_time < 0.5:  # 500ms timeout
                if self.serial.in_waiting > 0:
                    chunk = self.serial.read(self.serial.in_waiting)
                    response.extend(chunk)
                    
                    if response and response[-1] == 0xFF:
                        try:
                            last_start = response.rindex(0x90)
                            if len(response) > last_start + 1:
                                msg_type = response[last_start + 1]
                                if is_inquiry:
                                    # For inquiry, wait for Completion (0x50)
                                    if (msg_type & 0xF0) == 0x50:
                                        return bytes(response)
                                else:
                                    # For control, ACK (0x40) or Completion (0x50) is fine
                                    if (msg_type & 0xF0) in (0x40, 0x50):
                                        return bytes(response)
                        except ValueError:
                            pass
                else:
                    time.sleep(0.005)
                    
            return bytes(response)
        except Exception as e:
            print(f"Command error: {e}")
            return None
    
    # ============ Focus Control ============
    
    def set_focus_auto(self) -> bool:
        """Set Auto Focus mode"""
        cmd = bytes([self.VISCA_HEADER, 0x01, 0x04, 0x38, self.FOCUS_AUTO, self.VISCA_TERMINATOR])
        return self._send_command(cmd) is not None
    
    def set_focus_manual(self) -> bool:
        """Set Manual Focus mode"""
        cmd = bytes([self.VISCA_HEADER, 0x01, 0x04, 0x38, self.FOCUS_MANUAL, self.VISCA_TERMINATOR])
        return self._send_command(cmd) is not None
    
    def get_focus_mode(self) -> Optional[int]:
        """Get current focus mode (2=AF, 3=MF)"""
        cmd = bytes([self.VISCA_HEADER, 0x09, 0x04, 0x38, self.VISCA_TERMINATOR])
        response = self._send_command(cmd)
        if response:
            try:
                # Find completion message (0x50 | socket_id)
                for i, byte in enumerate(response):
                    if (byte & 0xF0) == 0x50:
                        if i + 1 < len(response):
                            return response[i+1]
                        break
            except (ValueError, IndexError):
                pass
        return None
    
    def focus_near(self, step: int = 0x400):
        """Move focus closer (manual mode)"""
        cmd = bytes([self.VISCA_HEADER, 0x01, 0x04, 0x08, 0x00, 0x00, 0x00, 0x01, self.VISCA_TERMINATOR])
        return self._send_command(cmd) is not None
    
    def focus_far(self, step: int = 0x400):
        """Move focus farther (manual mode)"""
        cmd = bytes([self.VISCA_HEADER, 0x01, 0x04, 0x08, 0x00, 0x00, 0x00, 0x02, self.VISCA_TERMINATOR])
        return self._send_command(cmd) is not None
    
    def focus_direct(self, position: int) -> bool:
        """Focus directly to a specific position (0x0000 - 0xF000)"""
        position = max(0, min(0xF000, position))
        p = (position >> 12) & 0x0F
        q = (position >> 8) & 0x0F
        r = (position >> 4) & 0x0F
        s = position & 0x0F
        cmd = bytes([self.VISCA_HEADER, 0x01, 0x04, 0x48, p, q, r, s, self.VISCA_TERMINATOR])
        return self._send_command(cmd) is not None
        
    def get_focus_position(self) -> Optional[int]:
        """Get current focus position (0-0xF000)"""
        cmd = bytes([self.VISCA_HEADER, 0x09, 0x04, 0x48, self.VISCA_TERMINATOR])
        response = self._send_command(cmd)
        if response:
            try:
                # Find completion message (0x50 | socket_id)
                for i, byte in enumerate(response):
                    if (byte & 0xF0) == 0x50:
                        if i + 5 < len(response):
                            p = response[i+1] & 0x0F
                            q = response[i+2] & 0x0F
                            r = response[i+3] & 0x0F
                            s = response[i+4] & 0x0F
                            pos = (p << 12) | (q << 8) | (r << 4) | s
                            return pos
                        break
            except (ValueError, IndexError):
                pass
        return None

    def one_push_af(self) -> bool:
        """Trigger one-push autofocus"""
        cmd = bytes([self.VISCA_HEADER, 0x01, 0x04, 0x18, 0x01, self.VISCA_TERMINATOR])
        return self._send_command(cmd) is not None
    
    # ============ Zoom Control ============
    
    def get_zoom_position(self) -> Optional[int]:
        """Get current zoom position (0-0x4000)"""
        cmd = bytes([self.VISCA_HEADER, 0x09, 0x04, 0x47, self.VISCA_TERMINATOR])
        response = self._send_command(cmd)
        if response:
            try:
                # Find completion message (0x50 | socket_id)
                for i, byte in enumerate(response):
                    if (byte & 0xF0) == 0x50:
                        if i + 5 < len(response):
                            p = response[i+1] & 0x0F
                            q = response[i+2] & 0x0F
                            r = response[i+3] & 0x0F
                            s = response[i+4] & 0x0F
                            pos = (p << 12) | (q << 8) | (r << 4) | s
                            return pos
                        break
            except (ValueError, IndexError):
                pass
        return None
    
    def zoom_direct(self, position: int) -> bool:
        """Zoom directly to a specific position (0x0000 - 0x4000)"""
        position = max(0, min(0x4000, position))
        p = (position >> 12) & 0x0F
        q = (position >> 8) & 0x0F
        r = (position >> 4) & 0x0F
        s = position & 0x0F
        cmd = bytes([self.VISCA_HEADER, 0x01, 0x04, 0x47, p, q, r, s, self.VISCA_TERMINATOR])
        return self._send_command(cmd) is not None
    
    def zoom_in(self, speed: int = 0x02):
        """Zoom in"""
        cmd = bytes([self.VISCA_HEADER, 0x01, 0x04, 0x07, 0x20 | speed, self.VISCA_TERMINATOR])
        return self._send_command(cmd) is not None
    
    def zoom_out(self, speed: int = 0x02):
        """Zoom out"""
        cmd = bytes([self.VISCA_HEADER, 0x01, 0x04, 0x07, 0x30 | speed, self.VISCA_TERMINATOR])
        return self._send_command(cmd) is not None
    
    def zoom_stop(self):
        """Stop zoom"""
        cmd = bytes([self.VISCA_HEADER, 0x01, 0x04, 0x07, 0x00, self.VISCA_TERMINATOR])
        return self._send_command(cmd) is not None
    
    # ============ Exposure Control ============
    
    def set_ae_mode_full_auto(self) -> bool:
        """Set Full Auto Exposure mode"""
        cmd = bytes([self.VISCA_HEADER, 0x01, 0x04, 0x39, self.AE_FULL_AUTO, self.VISCA_TERMINATOR])
        result = self._send_command(cmd) is not None
        logger.debug(f"Set AE mode to Full Auto, result: {result}")
        return result
    
    def set_ae_mode_manual(self) -> bool:
        """Set Manual Exposure mode"""
        cmd = bytes([self.VISCA_HEADER, 0x01, 0x04, 0x39, self.AE_MANUAL, self.VISCA_TERMINATOR])
        result = self._send_command(cmd) is not None
        logger.debug(f"Set AE mode to Manual (0x{self.AE_MANUAL:02X}), result: {result}")
        return result
    
    def set_ae_mode_shutter_priority(self) -> bool:
        """Set Shutter Priority AE mode"""
        cmd = bytes([self.VISCA_HEADER, 0x01, 0x04, 0x39, self.AE_SHUTTER_PRI, self.VISCA_TERMINATOR])
        result = self._send_command(cmd) is not None
        logger.debug(f"Set AE mode to Shutter Priority (0x{self.AE_SHUTTER_PRI:02X}), result: {result}")
        return result
    
    def set_ae_mode_iris_priority(self) -> bool:
        """Set Iris Priority AE mode"""
        cmd = bytes([self.VISCA_HEADER, 0x01, 0x04, 0x39, self.AE_IRIS_PRI, self.VISCA_TERMINATOR])
        result = self._send_command(cmd) is not None
        logger.debug(f"Set AE mode to Iris Priority (0x{self.AE_IRIS_PRI:02X}), result: {result}")
        return result
    
    def get_ae_mode(self) -> Optional[int]:
        """Get current AE mode"""
        cmd = bytes([self.VISCA_HEADER, 0x09, 0x04, 0x39, self.VISCA_TERMINATOR])
        response = self._send_command(cmd)
        if response:
            try:
                # Find completion message (0x50 | socket_id)
                for i, byte in enumerate(response):
                    if (byte & 0xF0) == 0x50:
                        if i + 1 < len(response):
                            mode = response[i+1]
                            logger.debug(f"Get AE mode: 0x{mode:02X}")
                            return mode
                        break
            except (ValueError, IndexError):
                pass
        logger.debug("Get AE mode failed")
        return None
    
    def set_shutter(self, shutter_hex: int) -> bool:
        """Set shutter speed by hex value"""
        cmd = bytes([self.VISCA_HEADER, 0x01, 0x04, 0x4A, 0x00, shutter_hex, self.VISCA_TERMINATOR])
        result = self._send_command(cmd) is not None
        logger.debug(f"Set shutter to 0x{shutter_hex:02X}, result: {result}")
        return result
    
    def set_iris(self, iris_hex: int) -> bool:
        """Set iris (F-stop) by hex value"""
        cmd = bytes([self.VISCA_HEADER, 0x01, 0x04, 0x4B, 0x00, iris_hex, self.VISCA_TERMINATOR])
        result = self._send_command(cmd) is not None
        logger.debug(f"Set iris to 0x{iris_hex:02X}, result: {result}")
        return result
    
    def set_gain(self, gain_value: int) -> bool:
        """Set gain (0x01-0x0F)"""
        gain_value = max(1, min(15, gain_value))
        cmd = bytes([self.VISCA_HEADER, 0x01, 0x04, 0x4C, 0x00, gain_value, self.VISCA_TERMINATOR])
        result = self._send_command(cmd) is not None
        logger.debug(f"Set gain to {gain_value}, result: {result}")
        return result
    
    def get_shutter_position(self) -> Optional[int]:
        """Get current shutter value"""
        cmd = bytes([self.VISCA_HEADER, 0x09, 0x04, 0x4A, self.VISCA_TERMINATOR])
        response = self._send_command(cmd)
        if response:
            try:
                # Find completion message (0x50 | socket_id)
                for i, byte in enumerate(response):
                    if (byte & 0xF0) == 0x50:
                        if i + 4 < len(response):
                            return response[i+4]
                        break
            except (ValueError, IndexError):
                pass
        return None
    
    def get_iris_position(self) -> Optional[int]:
        """Get current iris value"""
        cmd = bytes([self.VISCA_HEADER, 0x09, 0x04, 0x4B, self.VISCA_TERMINATOR])
        response = self._send_command(cmd)
        if response:
            try:
                # Find completion message (0x50 | socket_id)
                for i, byte in enumerate(response):
                    if (byte & 0xF0) == 0x50:
                        if i + 4 < len(response):
                            return response[i+4]
                        break
            except (ValueError, IndexError):
                pass
        return None
    
    def get_gain_position(self) -> Optional[int]:
        """Get current gain value"""
        cmd = bytes([self.VISCA_HEADER, 0x09, 0x04, 0x4C, self.VISCA_TERMINATOR])
        response = self._send_command(cmd)
        if response:
            try:
                # Find completion message (0x50 | socket_id)
                for i, byte in enumerate(response):
                    if (byte & 0xF0) == 0x50:
                        if i + 4 < len(response):
                            return response[i+4]
                        break
            except (ValueError, IndexError):
                pass
        return None
    
    # ============ Camera Info ============
    
    def get_power_status(self) -> Optional[int]:
        """Get power status (0=Off, 1=On)"""
        cmd = bytes([self.VISCA_HEADER, 0x09, 0x04, 0x00, self.VISCA_TERMINATOR])
        response = self._send_command(cmd)
        if response:
            try:
                # Find completion message (0x50 | socket_id)
                for i, byte in enumerate(response):
                    if (byte & 0xF0) == 0x50:
                        if i + 1 < len(response):
                            return response[i+1]
                        break
            except (ValueError, IndexError):
                pass
        return None
    
    def power_on(self) -> bool:
        """Turn camera on"""
        cmd = bytes([self.VISCA_HEADER, 0x01, 0x04, 0x00, 0x02, self.VISCA_TERMINATOR])
        return self._send_command(cmd) is not None
    
    def power_off(self) -> bool:
        """Turn camera off"""
        cmd = bytes([self.VISCA_HEADER, 0x01, 0x04, 0x00, 0x03, self.VISCA_TERMINATOR])
        return self._send_command(cmd) is not None
    
    def get_temperature(self) -> Optional[int]:
        """Get camera temperature"""
        cmd = bytes([self.VISCA_HEADER, 0x09, 0x04, 0x32, self.VISCA_TERMINATOR])
        response = self._send_command(cmd)
        if response:
            try:
                # Find completion message (0x50 | socket_id)
                for i, byte in enumerate(response):
                    if (byte & 0xF0) == 0x50:
                        if i + 1 < len(response):
                            return response[i+1]
                        break
            except (ValueError, IndexError):
                pass
        return None
