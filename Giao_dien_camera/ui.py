"""
PyQt5 GUI for Sony Camera Control
Refactored according to Sony FCB-EV9520L SDK best practices
"""
import cv2
import sys
import serial
import logging
from threading import Thread, Lock
from datetime import datetime
from pathlib import Path
from enum import Enum

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QSlider, QSpinBox, QRadioButton, QButtonGroup, QGroupBox,
    QCheckBox, QMessageBox, QFileDialog, QSplitter, QStatusBar, QGridLayout
)
from PyQt5.QtGui import QImage, QPixmap, QFont, QColor
from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt5.QtCore import QSize

from camera import SonyCamera

logger = logging.getLogger(__name__)

# Enum for AE Mode tracking (SDK best practice)
class AEModeType(Enum):
    FULL_AUTO = 0x00
    MANUAL = 0x03
    SHUTTER_PRI = 0x0A
    IRIS_PRI = 0x0B


class VideoThread(QThread):
    """Thread for video capture"""
    frame_ready = pyqtSignal(QImage)
    
    def __init__(self, camera_index=0):
        super().__init__()
        self.camera_index = camera_index
        self.is_running = False
        self.cap = None
    
    def run(self):
        self.camera_index = 1
        self.cap = cv2.VideoCapture(self.camera_index)
        if not self.cap.isOpened():
            return
        
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
        self.is_running = True
        
        while self.is_running:
            ret, frame = self.cap.read()
            if ret:
                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                h, w, c = rgb_frame.shape
                bytes_per_line = 3 * w
                q_img = QImage(rgb_frame.data, w, h, bytes_per_line, QImage.Format_RGB888).copy()
                self.frame_ready.emit(q_img)
            else:
                break
    
    def stop(self):
        self.is_running = False
        if self.cap:
            self.cap.release()
        self.wait()


class CameraControlApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Sony FCB-EV9520L Camera Controller")
        self.setGeometry(100, 100, 1600, 1000)
        
        # Camera instance
        self.camera = SonyCamera(port='COM3', baudrate=9600)
        self.video_thread = None
        
        # State flags (SDK best practice)
        self.is_live_view_active = False
        self.is_zoom_busy = False
        self.is_focus_busy = False
        self.is_syncing = False  # Prevent sending commands during sync
        self.current_ae_mode = AEModeType.FULL_AUTO
        
        # Thread lock for state protection
        self.state_lock = Lock()
        
        # Sync timeout (milliseconds)
        self.sync_timeout = 5000
        
        # Status update timer
        self.status_timer = QTimer()
        self.status_timer.timeout.connect(self.update_camera_status)
        self.status_timer.setInterval(500)
        
        # Initialize UI
        self.init_ui()
        
        # Update UI after init
        self.update_ui()
        
    def init_ui(self):
        """Initialize UI components"""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        main_layout = QHBoxLayout()
        
        # Left side: Video and controls
        left_layout = QVBoxLayout()
        
        # Video display
        self.video_label = QLabel("Live View")
        self.video_label.setStyleSheet("background-color: black; color: white;")
        self.video_label.setMinimumSize(800, 600)
        self.video_label.setAlignment(Qt.AlignCenter)
        left_layout.addWidget(self.video_label)
        
        # Video control buttons
        video_btn_layout = QHBoxLayout()
        self.btn_start_live = QPushButton("▶ Start Live View")
        self.btn_start_live.clicked.connect(self.start_live_view)
        self.btn_stop_live = QPushButton("⏹ Stop Live View")
        self.btn_stop_live.clicked.connect(self.stop_live_view)
        self.btn_stop_live.setEnabled(False)
        self.btn_capture = QPushButton("📷 Capture")
        self.btn_capture.clicked.connect(self.capture_image)
        self.btn_capture.setEnabled(False)
        
        video_btn_layout.addWidget(self.btn_start_live)
        video_btn_layout.addWidget(self.btn_stop_live)
        video_btn_layout.addWidget(self.btn_capture)
        left_layout.addLayout(video_btn_layout)
        
        # Right side: Controls
        right_layout = QVBoxLayout()
        
        # Connection group
        conn_group = self.create_connection_group()
        right_layout.addWidget(conn_group)
        
        # Focus control group
        focus_group = self.create_focus_group()
        right_layout.addWidget(focus_group)
        
        # Zoom control group
        zoom_group = self.create_zoom_group()
        right_layout.addWidget(zoom_group)
        
        # Exposure control group
        exposure_group = self.create_exposure_group()
        right_layout.addWidget(exposure_group)
        
        right_layout.addStretch()
        
        # Combine layouts
        main_layout.addLayout(left_layout, 3)
        main_layout.addLayout(right_layout, 1)
        central_widget.setLayout(main_layout)
        
        # Status bar
        self.statusBar().showMessage("Ready")
        
    def create_connection_group(self) -> QGroupBox:
        """Create connection control group"""
        group = QGroupBox("Connection")
        layout = QVBoxLayout()
        
        # COM port selection
        port_layout = QHBoxLayout()
        port_layout.addWidget(QLabel("COM Port:"))
        self.combo_port = QComboBox()
        self.combo_port.addItems(self.get_com_ports())
        port_layout.addWidget(self.combo_port)
        layout.addLayout(port_layout)
        
        # Baud rate selection
        baud_layout = QHBoxLayout()
        baud_layout.addWidget(QLabel("Baud Rate:"))
        self.combo_baud = QComboBox()
        self.combo_baud.addItems(["9600", "19200", "38400", "115200"])
        self.combo_baud.setCurrentText("9600")
        baud_layout.addWidget(self.combo_baud)
        layout.addLayout(baud_layout)
        
        # Connection buttons
        btn_layout = QHBoxLayout()
        self.btn_connect = QPushButton("🔗 Connect")
        self.btn_connect.clicked.connect(self.connect_camera)
        self.btn_disconnect = QPushButton("🔌 Disconnect")
        self.btn_disconnect.clicked.connect(self.disconnect_camera)
        self.btn_disconnect.setEnabled(False)
        
        btn_layout.addWidget(self.btn_connect)
        btn_layout.addWidget(self.btn_disconnect)
        layout.addLayout(btn_layout)
        
        # Status indicator
        self.status_label = QLabel("Disconnected")
        self.status_label.setStyleSheet("color: red; font-weight: bold;")
        layout.addWidget(self.status_label)
        
        group.setLayout(layout)
        return group
    
    def create_focus_group(self) -> QGroupBox:
        """Create focus control group"""
        group = QGroupBox("Focus Control")
        layout = QVBoxLayout()
        
        # Focus mode
        mode_layout = QHBoxLayout()
        mode_layout.addWidget(QLabel("Mode:"))
        
        self.focus_mode_group = QButtonGroup()
        self.radio_af = QRadioButton("Auto Focus")
        self.radio_mf = QRadioButton("Manual Focus")
        self.radio_af.setChecked(True)
        self.radio_af.clicked.connect(lambda: self.camera.set_focus_auto())
        self.radio_mf.clicked.connect(lambda: self.camera.set_focus_manual())
        
        self.focus_mode_group.addButton(self.radio_af)
        self.focus_mode_group.addButton(self.radio_mf)
        
        mode_layout.addWidget(self.radio_af)
        mode_layout.addWidget(self.radio_mf)
        layout.addLayout(mode_layout)
        
        # Focus buttons
        btn_layout = QHBoxLayout()
        self.btn_focus_near = QPushButton("◀ Near")
        self.btn_focus_near.clicked.connect(self.handle_focus_near)
        self.btn_focus_far = QPushButton("Far ▶")
        self.btn_focus_far.clicked.connect(self.handle_focus_far)
        self.btn_one_push_af = QPushButton("One Push AF")
        self.btn_one_push_af.clicked.connect(self.handle_one_push_af)
        
        btn_layout.addWidget(self.btn_focus_near)
        btn_layout.addWidget(self.btn_focus_far)
        btn_layout.addWidget(self.btn_one_push_af)
        layout.addLayout(btn_layout)
        
        group.setLayout(layout)
        return group
    
    def create_zoom_group(self) -> QGroupBox:
        """Create zoom control group"""
        group = QGroupBox("Zoom Control")
        layout = QVBoxLayout()
        
        # Zoom level display
        self.zoom_label = QLabel("ZOOM: 1.0x")
        self.zoom_label.setFont(QFont("Arial", 10, QFont.Bold))
        layout.addWidget(self.zoom_label)
        
        # Zoom buttons
        btn_layout = QHBoxLayout()
        self.btn_zoom_out = QPushButton("🔍- Zoom Out")
        self.btn_zoom_out.clicked.connect(self.handle_zoom_out)
        self.btn_zoom_in = QPushButton("🔍+ Zoom In")
        self.btn_zoom_in.clicked.connect(self.handle_zoom_in)
        
        btn_layout.addWidget(self.btn_zoom_out)
        btn_layout.addWidget(self.btn_zoom_in)
        layout.addLayout(btn_layout)
        
        group.setLayout(layout)
        return group
    
    def create_exposure_group(self) -> QGroupBox:
        """Create exposure control group"""
        group = QGroupBox("Exposure Control")
        layout = QVBoxLayout()
        
        # AE Mode
        ae_layout = QHBoxLayout()
        ae_layout.addWidget(QLabel("AE Mode:"))
        
        self.ae_mode_group = QButtonGroup()
        self.radio_ae_full = QRadioButton("Full Auto")
        self.radio_ae_manual = QRadioButton("Manual")
        self.radio_ae_shutter = QRadioButton("Shutter Pri")
        self.radio_ae_iris = QRadioButton("Iris Pri")
        
        self.radio_ae_full.setChecked(True)
        self.radio_ae_full.clicked.connect(self.on_ae_mode_changed)
        self.radio_ae_manual.clicked.connect(self.on_ae_mode_changed)
        self.radio_ae_shutter.clicked.connect(self.on_ae_mode_changed)
        self.radio_ae_iris.clicked.connect(self.on_ae_mode_changed)
        
        self.ae_mode_group.addButton(self.radio_ae_full)
        self.ae_mode_group.addButton(self.radio_ae_manual)
        self.ae_mode_group.addButton(self.radio_ae_shutter)
        self.ae_mode_group.addButton(self.radio_ae_iris)
        
        ae_layout.addWidget(self.radio_ae_full)
        ae_layout.addWidget(self.radio_ae_manual)
        layout.addLayout(ae_layout)
        
        ae_layout2 = QHBoxLayout()
        ae_layout2.addWidget(self.radio_ae_shutter)
        ae_layout2.addWidget(self.radio_ae_iris)
        layout.addLayout(ae_layout2)
        
        # Shutter slider
        shutter_layout = QHBoxLayout()
        shutter_layout.addWidget(QLabel("Shutter:"))
        self.shutter_label = QLabel("1/60")
        shutter_layout.addWidget(self.shutter_label)
        self.slider_shutter = QSlider(Qt.Horizontal)
        self.slider_shutter.setMinimum(0)
        self.slider_shutter.setMaximum(len(SonyCamera.SHUTTER_HEX_VALUES) - 1)
        self.slider_shutter.setValue(6)
        self.slider_shutter.valueChanged.connect(self.on_shutter_changed)
        shutter_layout.addWidget(self.slider_shutter)
        layout.addLayout(shutter_layout)
        
        # Iris slider
        iris_layout = QHBoxLayout()
        iris_layout.addWidget(QLabel("Iris:"))
        self.iris_label = QLabel("F14")
        iris_layout.addWidget(self.iris_label)
        self.slider_iris = QSlider(Qt.Horizontal)
        self.slider_iris.setMinimum(0)
        self.slider_iris.setMaximum(len(SonyCamera.IRIS_HEX_VALUES) - 1)
        self.slider_iris.setValue(0)
        self.slider_iris.valueChanged.connect(self.on_iris_changed)
        iris_layout.addWidget(self.slider_iris)
        layout.addLayout(iris_layout)
        
        # Gain slider
        gain_layout = QHBoxLayout()
        gain_layout.addWidget(QLabel("Gain:"))
        self.gain_label = QLabel("Step 0 (0.0dB)")
        gain_layout.addWidget(self.gain_label)
        self.slider_gain = QSlider(Qt.Horizontal)
        self.slider_gain.setMinimum(0)
        self.slider_gain.setMaximum(14)
        self.slider_gain.setValue(0)
        self.slider_gain.valueChanged.connect(self.on_gain_changed)
        gain_layout.addWidget(self.slider_gain)
        layout.addLayout(gain_layout)
        
        group.setLayout(layout)
        return group
    
    def get_com_ports(self):
        """Get available COM ports"""
        try:
            ports = serial.tools.list_ports.comports()
            return [port.device for port in ports] or ["COM1","COM2","COM3", "COM4","COM5"]
        except:
            return ["COM1","COM2","COM3", "COM4","COM5"]
    
    def update_ui(self):
        """Update UI state based on connection status (SDK best practice)"""
        is_connected = self.camera.is_connected
        
        # Connection buttons
        self.btn_connect.setEnabled(not is_connected)
        self.btn_disconnect.setEnabled(is_connected)
        self.combo_port.setEnabled(not is_connected)
        self.combo_baud.setEnabled(not is_connected)
        
        # Status indicator
        status_text = f"Connected ✓ ({self.camera.port})" if is_connected else "Disconnected"
        status_color = "green" if is_connected else "red"
        self.status_label.setText(status_text)
        self.status_label.setStyleSheet(f"color: {status_color}; font-weight: bold;")
        
        # Live view controls
        self.btn_start_live.setEnabled(is_connected and not self.is_live_view_active)
        self.btn_stop_live.setEnabled(is_connected and self.is_live_view_active)
        self.btn_capture.setEnabled(is_connected and self.is_live_view_active)
        
        # Focus controls
        self.radio_af.setEnabled(is_connected)
        self.radio_mf.setEnabled(is_connected)
        self.btn_one_push_af.setEnabled(is_connected)
        self.btn_focus_near.setEnabled(is_connected and self.radio_mf.isChecked())
        self.btn_focus_far.setEnabled(is_connected and self.radio_mf.isChecked())
        
        # AE Mode controls
        self.radio_ae_full.setEnabled(is_connected)
        self.radio_ae_manual.setEnabled(is_connected)
        self.radio_ae_shutter.setEnabled(is_connected)
        self.radio_ae_iris.setEnabled(is_connected)
        
        # Update exposure control enablement based on AE mode
        self.update_exposure_control_enablement()
    
    def update_exposure_control_enablement(self):
        """Strictly manage slider enabling based on AE Mode (SDK best practice)"""
        is_connected = self.camera.is_connected
        
        if not is_connected:
            self.slider_shutter.setEnabled(False)
            self.slider_iris.setEnabled(False)
            self.slider_gain.setEnabled(False)
            return
        
        # Shutter: enabled in Manual or Shutter Priority
        self.slider_shutter.setEnabled(
            self.current_ae_mode in [AEModeType.MANUAL, AEModeType.SHUTTER_PRI]
        )
        
        # Iris: enabled in Manual or Iris Priority
        self.slider_iris.setEnabled(
            self.current_ae_mode in [AEModeType.MANUAL, AEModeType.IRIS_PRI]
        )
        
        # Gain: enabled only in Manual
        self.slider_gain.setEnabled(self.current_ae_mode == AEModeType.MANUAL)
    
    def connect_camera(self):
        """Connect to camera"""
        port = self.combo_port.currentText()
        baudrate = int(self.combo_baud.currentText())
        
        self.camera.port = port
        self.camera.baudrate = baudrate
        
        # Show connecting status
        self.status_label.setText("Connecting...")
        self.status_label.setStyleSheet("color: orange; font-weight: bold;")
        
        if self.camera.connect():
            self.status_timer.start()
            self.statusBar().showMessage(f"Connected to {port}")
            logger.info(f"Successfully connected to {port}")
            self.update_ui()
            # Sync camera parameters in background after connecting
            Thread(target=self.sync_camera_parameters, daemon=True).start()
        else:
            QMessageBox.critical(self, "Connection Failed", 
                f"Failed to connect to {port}.\n\n"
                "Please check:\n"
                "• Camera is powered on\n"
                "• Correct COM port is selected\n"
                "• Baud rate is correct (default: 9600)\n"
                "• No other application is using the port")
            self.update_ui()
    
    def disconnect_camera(self):
        """Disconnect from camera"""
        self.stop_live_view()
        self.status_timer.stop()
        self.camera.disconnect()
        self.statusBar().showMessage("Disconnected")
        logger.info("Disconnected from camera")
        self.update_ui()
    
    def start_live_view(self):
        """Start live view from USB camera"""
        if not self.camera.is_connected:
            QMessageBox.warning(self, "Warning", "Camera not connected!")
            return
        
        self.video_thread = VideoThread(camera_index=0)
        self.video_thread.frame_ready.connect(self.display_frame)
        self.video_thread.start()
        
        self.is_live_view_active = True
        self.btn_start_live.setEnabled(False)
        self.btn_stop_live.setEnabled(True)
        self.btn_capture.setEnabled(True)
        self.statusBar().showMessage("Live view active")
    
    def stop_live_view(self):
        """Stop live view"""
        if self.video_thread:
            self.video_thread.stop()
            self.video_thread = None
        
        self.is_live_view_active = False
        self.btn_start_live.setEnabled(True)
        self.btn_stop_live.setEnabled(False)
        self.btn_capture.setEnabled(False)
        self.video_label.setText("Live View Stopped")
        self.statusBar().showMessage("Live view stopped")
    
    def display_frame(self, q_img: QImage):
        """Display video frame"""
        self.current_frame = q_img
        pixmap = QPixmap.fromImage(q_img)
        scaled_pixmap = pixmap.scaledToWidth(800, Qt.SmoothTransformation)
        self.video_label.setPixmap(scaled_pixmap)
    
    def capture_image(self):
        """Capture current frame"""
        if not hasattr(self, 'current_frame') or self.current_frame is None:
            QMessageBox.warning(self, "Warning", "No image to capture")
            return
        
        filename, _ = QFileDialog.getSaveFileName(
            self, "Save Image", "",
            "PNG Image (*.png);;JPEG Image (*.jpg);;BMP Image (*.bmp)"
        )
        
        if filename:
            self.current_frame.save(filename)
            QMessageBox.information(self, "Success", f"Image saved: {filename}")
    
    def on_shutter_changed(self, value):
        """Handle shutter slider change (SDK best practice: skip if syncing)"""
        if self.is_syncing:  # Don't send command while reading from camera
            return
            
        if value < len(SonyCamera.SHUTTER_SPEEDS):
            self.shutter_label.setText(SonyCamera.SHUTTER_SPEEDS[value])
            
            if self.camera.is_connected:
                self.camera.set_shutter(SonyCamera.SHUTTER_HEX_VALUES[value])
                logger.debug(f"Set shutter to {SonyCamera.SHUTTER_SPEEDS[value]}")
    
    def on_iris_changed(self, value):
        """Handle iris slider change (SDK best practice: skip if syncing)"""
        if self.is_syncing:  # Don't send command while reading from camera
            return
            
        if value < len(SonyCamera.IRIS_F_STOPS):
            self.iris_label.setText(SonyCamera.IRIS_F_STOPS[value])
            
            if self.camera.is_connected:
                self.camera.set_iris(SonyCamera.IRIS_HEX_VALUES[value])
                logger.debug(f"Set iris to {SonyCamera.IRIS_F_STOPS[value]}")
    
    def on_gain_changed(self, value):
        """Handle gain slider change (SDK best practice: skip if syncing)"""
        if self.is_syncing:  # Don't send command while reading from camera
            return
            
        gain_db = SonyCamera.GAIN_DB_OFF[value]
        self.gain_label.setText(f"Step {value * 2} ({gain_db:.1f}dB)")
        
        if self.camera.is_connected:
            self.camera.set_gain(value + 1)
            logger.debug(f"Set gain to Step {value * 2} ({gain_db:.1f}dB)")
    
    def on_ae_mode_changed(self):
        """Handle AE Mode radio button change (SDK best practice)"""
        if not self.camera.is_connected:
            return
        
        try:
            if self.radio_ae_full.isChecked():
                self.camera.set_ae_mode_full_auto()
                self.current_ae_mode = AEModeType.FULL_AUTO
                logger.debug("Set AE mode to Full Auto")
            elif self.radio_ae_manual.isChecked():
                self.camera.set_ae_mode_manual()
                self.current_ae_mode = AEModeType.MANUAL
                logger.debug("Set AE mode to Manual")
            elif self.radio_ae_shutter.isChecked():
                self.camera.set_ae_mode_shutter_priority()
                self.current_ae_mode = AEModeType.SHUTTER_PRI
                logger.debug("Set AE mode to Shutter Priority")
            elif self.radio_ae_iris.isChecked():
                self.camera.set_ae_mode_iris_priority()
                self.current_ae_mode = AEModeType.IRIS_PRI
                logger.debug("Set AE mode to Iris Priority")
            
            # Update exposure control enablement based on new mode
            self.update_exposure_control_enablement()
            
        except Exception as ex:
            logger.error(f"AE mode change error: {ex}")
            self.statusBar().showMessage(f"⚠️ AE mode change failed")
    
    def update_camera_status(self):
        """Update camera status timer callback (SDK best practice)"""
        if not self.camera.is_connected:
            return
        
        try:
            # Get focus mode in background thread
            focus_mode = self.camera.get_focus_mode()
            if focus_mode is not None:
                mode_text = "AF" if focus_mode == 2 else "MF"
                mode_color = "green" if focus_mode == 2 else "orange"
                # Update UI from main thread
                self.statusBar().showMessage(f"Focus: {mode_text}")
        except Exception as ex:
            logger.debug(f"Status update error: {ex}")
    
    def sync_camera_parameters(self):
        """Sync all camera parameters from device in a thread-safe way"""
        if not self.camera.is_connected:
            return
        
        self.is_syncing = True
        self.statusBar().showMessage("⌛ Reading camera settings...")
        
        try:
            ae_mode = self.camera.get_ae_mode()
            shutter = self.camera.get_shutter_position()
            iris = self.camera.get_iris_position()
            gain = self.camera.get_gain_position()
            zoom = self.camera.get_zoom_position()
            
            def apply_settings():
                try:
                    if ae_mode is not None:
                        if ae_mode == 0x00:
                            self.radio_ae_full.setChecked(True)
                            self.current_ae_mode = AEModeType.FULL_AUTO
                        elif ae_mode == 0x03:
                            self.radio_ae_manual.setChecked(True)
                            self.current_ae_mode = AEModeType.MANUAL
                        elif ae_mode == 0x0A:
                            self.radio_ae_shutter.setChecked(True)
                            self.current_ae_mode = AEModeType.SHUTTER_PRI
                        elif ae_mode == 0x0B:
                            self.radio_ae_iris.setChecked(True)
                            self.current_ae_mode = AEModeType.IRIS_PRI
                        self.update_exposure_control_enablement()

                    if shutter is not None:
                        try:
                            idx = list(SonyCamera.SHUTTER_HEX_VALUES).index(shutter)
                            self.slider_shutter.setValue(idx)
                        except ValueError:
                            logger.warning(f"Unknown shutter value: 0x{shutter:02X}")

                    if iris is not None:
                        try:
                            idx = list(SonyCamera.IRIS_HEX_VALUES).index(iris)
                            self.slider_iris.setValue(idx)
                        except ValueError:
                            logger.warning(f"Unknown iris value: 0x{iris:02X}")

                    if gain is not None and gain > 0:
                        self.slider_gain.setValue(gain - 1)

                    if zoom is not None:
                        self.update_zoom_display(zoom)

                    self.statusBar().showMessage("✓ Camera settings synced")
                    logger.info("Camera parameters synced successfully")
                finally:
                    self.is_syncing = False

            QTimer.singleShot(0, apply_settings)

        except Exception as ex:
            logger.error(f"Sync error: {ex}")
            def show_error():
                self.statusBar().showMessage(f"❌ Sync failed: {ex}")
                self.is_syncing = False
            QTimer.singleShot(0, show_error)

    def handle_zoom_in(self):
        """Handle zoom in step"""
        if not self.camera.is_connected or self.is_zoom_busy:
            return
        
        self.is_zoom_busy = True
        try:
            current_pos = self.camera.get_zoom_position()
            if current_pos is not None:
                target_pos = min(0x4000, current_pos + 0x200)
                self.camera.zoom_direct(target_pos)
                self.update_zoom_display(target_pos)
        finally:
            self.is_zoom_busy = False

    def handle_zoom_out(self):
        """Handle zoom out step"""
        if not self.camera.is_connected or self.is_zoom_busy:
            return
        
        self.is_zoom_busy = True
        try:
            current_pos = self.camera.get_zoom_position()
            if current_pos is not None:
                target_pos = max(0x0000, current_pos - 0x200)
                self.camera.zoom_direct(target_pos)
                self.update_zoom_display(target_pos)
        finally:
            self.is_zoom_busy = False

    def update_zoom_display(self, zoom_pos):
        """Update zoom label display"""
        multiplier = 1.0 + (zoom_pos / 16384.0) * 29.0
        self.zoom_label.setText(f"ZOOM: {min(30.0, multiplier):.1f}x")

    def handle_focus_far(self):
        """Handle focus far step (SDK best practice: double check MF after async query)"""
        if not self.camera.is_connected or self.is_focus_busy:
            return
        
        # Must be in Manual Focus mode
        if not self.radio_mf.isChecked():
            logger.warning("Focus adjustment only available in Manual Focus mode")
            return
        
        self.is_focus_busy = True
        try:
            current_pos = self.camera.get_focus_position()
            
            # Double-check: verify still in MF after query (user may click AF during wait)
            if not self.radio_mf.isChecked() or current_pos is None:
                logger.debug("Focus mode changed during operation, cancelling")
                return
            
            target_pos = min(0xF000, current_pos + 0x400)
            self.camera.focus_direct(target_pos)
            logger.debug(f"Focus Far to position: 0x{target_pos:04X}")
        finally:
            self.is_focus_busy = False

    def handle_focus_near(self):
        """Handle focus near step (SDK best practice: double check MF after async query)"""
        if not self.camera.is_connected or self.is_focus_busy:
            return
        
        # Must be in Manual Focus mode
        if not self.radio_mf.isChecked():
            logger.warning("Focus adjustment only available in Manual Focus mode")
            return
        
        self.is_focus_busy = True
        try:
            current_pos = self.camera.get_focus_position()
            
            # Double-check: verify still in MF after query (user may click AF during wait)
            if not self.radio_mf.isChecked() or current_pos is None:
                logger.debug("Focus mode changed during operation, cancelling")
                return
            
            target_pos = max(0x0000, current_pos - 0x400)
            self.camera.focus_direct(target_pos)
            logger.debug(f"Focus Near to position: 0x{target_pos:04X}")
        finally:
            self.is_focus_busy = False

    def handle_one_push_af(self):
        """Handle One Push Auto Focus"""
        if not self.camera.is_connected:
            return
        
        if self.radio_af.isChecked():
            self.radio_mf.setChecked(True)
            self.camera.set_focus_manual()
            # Wait 100ms before sending one_push_af
            QTimer.singleShot(100, self.camera.one_push_af)
        else:
            self.camera.one_push_af()
            
    def closeEvent(self, event):
        """Handle window close"""
        self.stop_live_view()
        self.disconnect_camera()
        event.accept()
