"""STM32 Serial Monitor - Main application entry point

This module contains the main window and UI setup for the serial monitor application.
The refactored code is split into modules for better maintainability:
  - models.py: Data classes and enums
  - parsers.py: Serial message parsing
  - formatters.py: Display formatting and colors
  - commands.py: Device command builders
  - serial_worker.py: Serial communication worker thread
  - styles.py: UI theme and styling
  - config.py: Application configuration constants
"""

import os
import sys
import serial.tools.list_ports
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabWidget, QTextEdit, QPushButton, QComboBox, QLabel, QStatusBar,
    QFrame, QCheckBox, QGridLayout, QLineEdit, QScrollArea, QTableWidget, QTableWidgetItem,
    QMessageBox, QSizePolicy, QFileDialog
)
from PyQt6.QtGui import QIntValidator, QColor, QFont
from PyQt6.QtCore import QDateTime, QTimer, Qt, QProcess

try:
    from .config import UIConfig, DeviceConfig
    from .serial_worker import SerialWorker
    from .parsers import TelemetryParser, SettingsParser
    from .commands import Command
    from .styles import DARK_STYLESHEET
    from .telemetry_table import TelemetryTableView
    from .graph_view import GraphView
    from .logger import setup_logging
except ImportError:
    from config import UIConfig, DeviceConfig
    from serial_worker import SerialWorker
    from parsers import TelemetryParser, SettingsParser
    from commands import Command
    from styles import DARK_STYLESHEET
    from telemetry_table import TelemetryTableView
    from graph_view import GraphView
    from logger import setup_logging

logger = setup_logging()

_STYLE_BTN_BLUE = (
    "QPushButton { background-color: #1a3d7a; color: #7ec8f8; border: 1px solid #2d6abf;"
    " border-radius: 5px; font-weight: 700; padding: 4px 10px; }"
    "QPushButton:hover { background-color: #2055a0; }"
    "QPushButton:pressed { background-color: #122d5a; }"
    "QPushButton:disabled { background-color: #1e2128; color: #444; border-color: #252930; }"
)
_STYLE_BTN_RED = (
    "QPushButton { background-color: #5a1a1a; color: #f88080; border: 1px solid #902020;"
    " border-radius: 5px; font-weight: 700; padding: 4px 10px; }"
    "QPushButton:hover { background-color: #6a2020; }"
    "QPushButton:pressed { background-color: #3a1010; }"
    "QPushButton:disabled { background-color: #1e2128; color: #444; border-color: #252930; }"
)
_STYLE_BTN_GREEN = (
    "QPushButton { background-color: #1a4a1a; color: #7ef87e; border: 1px solid #2dbf2d;"
    " border-radius: 5px; font-weight: 700; padding: 4px 10px; }"
    "QPushButton:hover { background-color: #205520; }"
    "QPushButton:pressed { background-color: #122d12; }"
    "QPushButton:disabled { background-color: #1e2128; color: #444; border-color: #252930; }"
)
_STYLE_SECTION_LABEL = (
    "font-size: 11px; font-weight: 700; color: #7a8a9a; letter-spacing: 1px;"
    " padding: 6px 0 2px 0; text-transform: uppercase;"
)
_STYLE_DIVIDER = "background-color: #252930; max-height: 1px; margin: 2px 0;"


class SerialMonitorUI(QMainWindow):
    """Main application window for STM32 serial monitor"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("STM32 Serial Monitor")
        self.setGeometry(100, 100, UIConfig.WINDOW_WIDTH, UIConfig.WINDOW_HEIGHT)

        self.serial_worker = None
        self.autoscroll_enabled = True
        self.telemetry_view = None
        self.graph_view = None
        self.accumulated_settings = {}
        self._connection_widgets = []
        self._flash_process = None

        self.find_ports_timer = QTimer()
        self.find_ports_timer.timeout.connect(self.update_port_list)
        self.find_ports_timer.start(UIConfig.PORT_SCAN_INTERVAL)

        self.init_ui()
        self.update_port_list()

    # -------------------------------------------------------------------------
    # UI construction
    # -------------------------------------------------------------------------

    def init_ui(self):
        """Initialize the user interface"""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QHBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        main_layout.addLayout(self._build_left_panel(), stretch=1)
        main_layout.addWidget(self._build_right_panel())

        central_widget.setLayout(main_layout)
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Ready")

    def _build_left_panel(self):
        left_layout = QVBoxLayout()
        left_layout.setContentsMargins(10, 8, 10, 8)
        left_layout.setSpacing(6)

        left_layout.addLayout(self._build_connection_bar())
        left_layout.addLayout(self._build_programming_bar())
        left_layout.addWidget(self._build_tabs())
        left_layout.addLayout(self._build_command_bar())

        return left_layout

    def _build_connection_bar(self):
        layout = QHBoxLayout()
        layout.addWidget(QLabel("Port:"))

        self.port_combo = QComboBox()
        layout.addWidget(self.port_combo)

        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self.toggle_connection)
        layout.addWidget(self.connect_btn)

        self.clear_btn = QPushButton("Clear")
        self.clear_btn.clicked.connect(self.clear_output)
        layout.addWidget(self.clear_btn)

        self.autoscroll_checkbox = QCheckBox("Autoscroll")
        self.autoscroll_checkbox.setChecked(True)
        self.autoscroll_checkbox.stateChanged.connect(self.on_autoscroll_toggled)
        layout.addWidget(self.autoscroll_checkbox)

        layout.addStretch()

        self.status_label = QLabel("● Disconnected")
        self.status_label.setStyleSheet("color: #e05555; font-weight: bold; letter-spacing: 0.5px;")
        layout.addWidget(self.status_label)

        return layout

    def _build_programming_bar(self):
        layout = QHBoxLayout()
        layout.addWidget(QLabel("Firmware:"))

        self.elf_path_input = QLineEdit()
        self.elf_path_input.setReadOnly(True)
        self.elf_path_input.setPlaceholderText("Select ELF file...")
        layout.addWidget(self.elf_path_input)

        self.browse_btn = QPushButton("Browse")
        self.browse_btn.clicked.connect(self.browse_elf_file)
        layout.addWidget(self.browse_btn)

        self.program_btn = QPushButton("Program")
        self.program_btn.setStyleSheet(_STYLE_BTN_GREEN)
        self.program_btn.setEnabled(False)
        self.program_btn.clicked.connect(self.program_device)
        layout.addWidget(self.program_btn)

        return layout

    def _build_tabs(self):
        self.tabs = QTabWidget()

        self.raw_text = QTextEdit()
        self.raw_text.setReadOnly(True)
        self.raw_text.setFont(QFont("Consolas", 9))
        self.tabs.addTab(self.raw_text, "Raw Data")

        telemetry_table = QTableWidget()
        telemetry_table.setColumnCount(15)
        telemetry_table.setHorizontalHeaderLabels([
            "Received", "External Sensor", "OnBoard Sensor", "RH(%)", "State",
            "Uptime", "FAN1", "FAN2", "FAN3", "FAN4", "In A(%)", "Out A(%)", "In B(%)", "Out B(%)", "Button"
        ])
        telemetry_table.setAlternatingRowColors(True)
        telemetry_table.verticalHeader().setVisible(False)
        telemetry_table.horizontalHeader().setStretchLastSection(True)

        self.telemetry_view = TelemetryTableView(telemetry_table)
        self.tabs.addTab(telemetry_table, "Telemetry")

        self.graph_view = GraphView()
        self.tabs.addTab(self.graph_view, "Graph")

        return self.tabs

    def _build_command_bar(self):
        layout = QHBoxLayout()
        layout.addWidget(QLabel("Command:"))

        self.command_input = QLineEdit()
        self.command_input.setPlaceholderText("Type command and press Enter or click Send...")
        self.command_input.returnPressed.connect(self.send_custom_command)
        self.command_input.setEnabled(False)
        layout.addWidget(self.command_input)

        self.send_command_btn = QPushButton("Send")
        self.send_command_btn.setStyleSheet(
            "QPushButton { background-color: #3a1a5a; color: #c49ef8; border: 1px solid #6030a0;"
            " border-radius: 5px; font-weight: 700; }"
            "QPushButton:hover { background-color: #4a2070; }"
            "QPushButton:pressed { background-color: #2a1040; }"
            "QPushButton:disabled { background-color: #1e2128; color: #444; border-color: #252930; }"
        )
        self.send_command_btn.setFixedWidth(80)
        self.send_command_btn.setEnabled(False)
        self.send_command_btn.clicked.connect(self.send_custom_command)
        layout.addWidget(self.send_command_btn)

        self._connection_widgets += [self.command_input, self.send_command_btn]
        return layout

    def _build_right_panel(self):
        panel = QFrame()
        panel.setStyleSheet("QFrame { border-left: 1px solid #252930; background-color: #1a1d23; }")
        panel.setFixedWidth(600)
        panel.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

        outer = QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        title = QLabel("Settings")
        title.setStyleSheet(
            "font-weight: 700; font-size: 13px; padding: 12px 16px 10px 16px;"
            " color: #c0c8d8; border-bottom: 1px solid #252930;"
            " background-color: #1e2128; letter-spacing: 1px;"
        )
        outer.addWidget(title)

        contents = QWidget()
        contents.setStyleSheet("background-color: #1a1d23;")
        grid = QGridLayout()
        grid.setContentsMargins(14, 10, 14, 10)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)

        row = 0
        row = self._add_top_buttons_row(grid, row)
        row = self._add_section_header(grid, row, "Temperature")
        row = self._add_setting_row(grid, row, "Critical (2–90 °C)", "critical",
                                    QIntValidator(2, 90), 3)
        row = self._add_setting_row(grid, row, "Throttle On (1–254 °C)", "throttle",
                                    QIntValidator(1, 254), 3)
        row = self._add_setting_row(grid, row, "Fan On (1–80 °C)", "fan_on",
                                    QIntValidator(1, 80), 3)
        row = self._add_setting_row(grid, row, "Fan Off (0–79 °C)", "fan_off",
                                    QIntValidator(0, 79), 3)
        row = self._add_section_header(grid, row, "PWM Throttle")
        row = self._add_setting_row(grid, row, "Channel A (0–100 %)", "pwm_a",
                                    QIntValidator(0, 100), 3)
        row = self._add_setting_row(grid, row, "Channel B (0–100 %)", "pwm_b",
                                    QIntValidator(0, 100), 3)
        row = self._add_firmware_version_row(grid, row)
        row = self._add_action_buttons_compact(grid, row)
        row = self._add_info_display_row(grid, row)
        grid.setRowStretch(row, 1)

        contents.setLayout(grid)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(contents)
        scroll.setStyleSheet("QScrollArea { background-color: #1a1d23; border: none; }")
        outer.addWidget(scroll)

        panel.setLayout(outer)
        return panel

    def _add_top_buttons_row(self, grid, row):
        self.read_settings_btn = QPushButton("Read Settings")
        self.read_settings_btn.setStyleSheet(_STYLE_BTN_BLUE)
        self.read_settings_btn.setEnabled(False)
        self.read_settings_btn.clicked.connect(lambda: self.send_command(Command.SETTINGS_READ))
        grid.addWidget(self.read_settings_btn, row, 0, 1, 2)

        self.set_all_btn = QPushButton("Set All")
        self.set_all_btn.setStyleSheet(_STYLE_BTN_BLUE)
        self.set_all_btn.setEnabled(False)
        self.set_all_btn.setToolTip("Set all temperature and PWM values at once")
        self.set_all_btn.clicked.connect(self.send_all_settings)
        grid.addWidget(self.set_all_btn, row, 2)

        self._connection_widgets += [self.read_settings_btn, self.set_all_btn]
        return row + 1

    def _add_firmware_version_row(self, grid, row):
        lbl = QLabel("Firmware Version")
        lbl.setStyleSheet(_STYLE_SECTION_LABEL)
        grid.addWidget(lbl, row, 0, 1, 3)
        row += 1

        self.get_fw_btn = QPushButton("Request")
        self.get_fw_btn.setStyleSheet(_STYLE_BTN_BLUE)
        self.get_fw_btn.setEnabled(False)
        self.get_fw_btn.setToolTip("Send GETFW command")
        self.get_fw_btn.clicked.connect(lambda: self.send_command(Command.GET_FW_VERSION))
        grid.addWidget(self.get_fw_btn, row, 0, 1, 3)
        row += 1

        self.fw_version_display = QLineEdit()
        self.fw_version_display.setReadOnly(True)
        self.fw_version_display.setFont(QFont("Consolas", 9))
        self.fw_version_display.setPlaceholderText("—")
        grid.addWidget(self.fw_version_display, row, 0, 1, 3)
        row += 1

        self._connection_widgets += [self.get_fw_btn]
        return row

    def _add_info_display_row(self, grid, row):
        divider = QFrame()
        divider.setStyleSheet(_STYLE_DIVIDER)
        divider.setFrameShape(QFrame.Shape.HLine)
        grid.addWidget(divider, row, 0, 1, 3)
        row += 1

        lbl = QLabel("Status")
        lbl.setStyleSheet(_STYLE_SECTION_LABEL)
        grid.addWidget(lbl, row, 0, 1, 3)
        row += 1

        self.info_display = QTextEdit()
        self.info_display.setReadOnly(True)
        self.info_display.setFont(QFont("Consolas", 8))
        self.info_display.setFixedHeight(220)
        grid.addWidget(self.info_display, row, 0, 1, 3)
        row += 1

        return row

    def _add_section_header(self, grid, row, title):
        divider = QFrame()
        divider.setStyleSheet(_STYLE_DIVIDER)
        divider.setFrameShape(QFrame.Shape.HLine)
        grid.addWidget(divider, row, 0, 1, 3)
        row += 1

        lbl = QLabel(title)
        lbl.setStyleSheet(_STYLE_SECTION_LABEL)
        grid.addWidget(lbl, row, 0, 1, 3)
        row += 1

        return row

    def _add_setting_row(self, grid, row, label_text, key, validator, max_len):
        lbl = QLabel(label_text)
        lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        grid.addWidget(lbl, row, 0)

        field = QLineEdit()
        field.setValidator(validator)
        field.setMaxLength(max_len)
        field.setEnabled(False)
        field.setFixedHeight(26)
        grid.addWidget(field, row, 1)

        btn = QPushButton("Set")
        btn.setStyleSheet(_STYLE_BTN_BLUE)
        btn.setEnabled(False)
        btn.setFixedWidth(44)
        btn.setFixedHeight(26)
        btn.clicked.connect(lambda _, k=key: self._dispatch_set(k))
        grid.addWidget(btn, row, 2)

        setattr(self, f"ctrl_{key}", field)
        setattr(self, f"ctrl_{key}_btn", btn)
        self._connection_widgets += [field, btn]
        return row + 1

    def _add_action_buttons_compact(self, grid, row):
        self.set_default_btn = QPushButton("Restore Defaults")
        self.set_default_btn.setStyleSheet(_STYLE_BTN_BLUE)
        self.set_default_btn.setEnabled(False)
        self.set_default_btn.clicked.connect(self.confirm_and_send_default)
        grid.addWidget(self.set_default_btn, row, 0, 1, 2)

        self.reset_defaults_btn = QPushButton("Reset Device")
        self.reset_defaults_btn.setStyleSheet(_STYLE_BTN_RED)
        self.reset_defaults_btn.setEnabled(False)
        self.reset_defaults_btn.setToolTip("Reset device")
        self.reset_defaults_btn.clicked.connect(self.confirm_and_send_reset)
        grid.addWidget(self.reset_defaults_btn, row, 2)

        self._connection_widgets += [self.set_default_btn, self.reset_defaults_btn]
        return row + 1

    def _add_action_buttons(self, grid, row):
        divider = QFrame()
        divider.setStyleSheet(_STYLE_DIVIDER)
        divider.setFrameShape(QFrame.Shape.HLine)
        grid.addWidget(divider, row, 0, 1, 3)
        row += 1

        self.set_all_btn = QPushButton("Set All")
        self.set_all_btn.setStyleSheet(_STYLE_BTN_BLUE)
        self.set_all_btn.setEnabled(False)
        self.set_all_btn.setToolTip("Set all temperature and PWM values at once")
        self.set_all_btn.clicked.connect(self.send_all_settings)
        grid.addWidget(self.set_all_btn, row, 0, 1, 3)
        row += 1

        self.set_default_btn = QPushButton("Restore Defaults")
        self.set_default_btn.setStyleSheet(_STYLE_BTN_BLUE)
        self.set_default_btn.setEnabled(False)
        self.set_default_btn.clicked.connect(self.confirm_and_send_default)
        grid.addWidget(self.set_default_btn, row, 0, 1, 2)

        self.reset_defaults_btn = QPushButton("Reset")
        self.reset_defaults_btn.setStyleSheet(_STYLE_BTN_RED)
        self.reset_defaults_btn.setEnabled(False)
        self.reset_defaults_btn.setToolTip("Reset all settings to factory defaults")
        self.reset_defaults_btn.clicked.connect(self.confirm_and_send_reset)
        grid.addWidget(self.reset_defaults_btn, row, 2)

        self._connection_widgets += [self.set_all_btn, self.set_default_btn, self.reset_defaults_btn]
        return row + 1

    # -------------------------------------------------------------------------
    # Programming
    # -------------------------------------------------------------------------

    def browse_elf_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select ELF File", "", "ELF Files (*.elf);;All Files (*)"
        )
        if file_path:
            self.elf_path_input.setText(file_path)
            self.program_btn.setEnabled(True)

    def program_device(self):
        elf_path = self.elf_path_input.text().strip()
        if not elf_path or not os.path.exists(elf_path):
            self.statusBar().showMessage("Error: ELF file not found")
            return

        self.program_btn.setEnabled(False)
        self.browse_btn.setEnabled(False)
        self.log_info("Starting programming...")
        self.statusBar().showMessage("Programming...")

        self._flash_process = QProcess(self)
        self._flash_process.readyReadStandardOutput.connect(self._on_flash_stdout)
        self._flash_process.readyReadStandardError.connect(self._on_flash_stderr)
        self._flash_process.finished.connect(self._on_flash_finished)
        self._flash_process.start("STM32_Programmer_CLI", ["-c", "port=SWD", "-d", elf_path, "-rst"])

        if not self._flash_process.waitForStarted(3000):
            self.log_info("Error: STM32_Programmer_CLI not found in PATH")
            self.statusBar().showMessage("Error: STM32_Programmer_CLI not found in PATH")
            self.program_btn.setEnabled(True)
            self.browse_btn.setEnabled(True)

    def _on_flash_stdout(self):
        output = self._flash_process.readAllStandardOutput().data().decode("utf-8", errors="replace").strip()
        if output:
            self.log_info(output)

    def _on_flash_stderr(self):
        output = self._flash_process.readAllStandardError().data().decode("utf-8", errors="replace").strip()
        if output:
            self.log_info(output)

    def _on_flash_finished(self, exit_code, _exit_status):
        if exit_code == 0:
            self.log_info("Programming successful!")
            self.statusBar().showMessage("Programming successful!")
        else:
            self.log_info(f"Programming failed (exit code {exit_code})")
            self.statusBar().showMessage(f"Programming failed (exit code {exit_code})")
        self.program_btn.setEnabled(True)
        self.browse_btn.setEnabled(True)

    # -------------------------------------------------------------------------
    # Port management
    # -------------------------------------------------------------------------

    def find_stm_port(self):
        for port in serial.tools.list_ports.comports():
            if port.vid == DeviceConfig.TINYUSB_VID and port.pid == DeviceConfig.TINYUSB_CDC_PID:
                return port.device
        return None

    def get_all_ports(self):
        return [port.device for port in serial.tools.list_ports.comports()]

    def update_port_list(self):
        current = self.port_combo.currentText()
        ports = self.get_all_ports()

        if self.port_combo.count() != len(ports) or (len(ports) > 0 and current != self.port_combo.currentText()):
            self.port_combo.clear()
            self.port_combo.addItems(ports)

            stm_port = self.find_stm_port()
            if stm_port and stm_port in ports:
                self.port_combo.setCurrentText(stm_port)

    # -------------------------------------------------------------------------
    # Connection
    # -------------------------------------------------------------------------

    def toggle_connection(self):
        if self.serial_worker is None:
            self.connect()
        else:
            self.disconnect()

    def connect(self):
        port = self.port_combo.currentText()
        if not port:
            self.status_label.setText("● No port selected")
            self.status_label.setStyleSheet("color: #e05555; font-weight: bold; letter-spacing: 0.5px;")
            return

        self.serial_worker = SerialWorker(port)
        self.serial_worker.data_received.connect(self.on_data_received)
        self.serial_worker.status_changed.connect(self.on_status_changed)
        self.serial_worker.connection_state.connect(self.on_connection_state)
        self.serial_worker.finished.connect(self.on_worker_finished)
        self.serial_worker.start()
        self.status_label.setText("● Connecting...")
        self.status_label.setStyleSheet("color: #ffc107; font-weight: bold; letter-spacing: 0.5px;")

    def disconnect(self):
        if self.serial_worker:
            logger.info("Disconnect requested - stopping serial worker thread")
            self.serial_worker.stop()
            self.status_label.setText("● Disconnecting...")
            self.status_label.setStyleSheet("color: #ffc107; font-weight: bold; letter-spacing: 0.5px;")
            self.on_connection_state(False)

    def on_worker_finished(self):
        logger.info("Serial worker thread has finished")
        self.serial_worker = None
        self.on_connection_state(False)

    # -------------------------------------------------------------------------
    # Data handling
    # -------------------------------------------------------------------------

    def on_data_received(self, data):
        if not self.serial_worker:
            return

        self.raw_text.insertPlainText(data)

        if data.startswith('$01,'):
            telemetry = TelemetryParser.parse(data.rstrip('\r\n'))
            if telemetry:
                if self.telemetry_view:
                    self.telemetry_view.add_row(telemetry)
                if self.graph_view:
                    self.graph_view.add_telemetry(telemetry)

        if data.startswith('FWVER='):
            fwver = data.split('=', 1)[1].strip()
            self.fw_version_display.setText(fwver)
            self.log_info(f"Firmware: {fwver}")

        settings = SettingsParser.parse_settings_from_text(data)
        if settings:
            self.update_settings_display(settings)

        if data.startswith('OK'):
            self.log_info("Command executed successfully")
        elif data.startswith('ERR'):
            self.log_info(f"Error: {data.strip()}")

        self.autoscroll_to_bottom()

    def update_settings_display(self, settings: dict):
        self.accumulated_settings.update(settings)

        mapping = {
            'PWM_THROTTLE_A': self.ctrl_pwm_a,
            'PWM_THROTTLE_B': self.ctrl_pwm_b,
            'TEMP_THROTTLE_ON': self.ctrl_throttle,
            'TEMP_FAN_ON': self.ctrl_fan_on,
            'TEMP_FAN_OFF': self.ctrl_fan_off,
            'TEMP_CRITICAL': self.ctrl_critical,
        }
        for key, field in mapping.items():
            if key in self.accumulated_settings:
                field.setText(self.accumulated_settings[key])

        if self.graph_view:
            self.graph_view.set_settings(self.accumulated_settings)

        if self.accumulated_settings:
            self.log_info(f"Read OK - {len(self.accumulated_settings)} settings received")

        logger.info(f"Accumulated settings: {self.accumulated_settings}")

    # -------------------------------------------------------------------------
    # Command dispatch
    # -------------------------------------------------------------------------

    def _dispatch_set(self, key: str):
        if key in ("throttle", "fan_on", "fan_off", "critical"):
            self.send_temp_setting(key)
        elif key in ("pwm_a", "pwm_b"):
            channel = "A" if key == "pwm_a" else "B"
            self.send_pwm_setting(channel)

    def send_pwm_setting(self, channel: str):
        field = self.ctrl_pwm_a if channel == "A" else self.ctrl_pwm_b
        value = field.text().strip()

        if not value or not value.isdigit() or int(value) > 100:
            self.statusBar().showMessage(f"PWM {channel}: Enter a value between 0-100")
            return

        reply = QMessageBox.question(self, "Confirm Setting",
            f"Set PWM Throttle {channel} to {value}%?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)

        if reply == QMessageBox.StandardButton.Yes:
            self.send_command(Command.pwm_throttle(channel, int(value)))

    def send_temp_setting(self, setting_type: str):
        configs = {
            "throttle": (self.ctrl_throttle,  Command.temp_throttle_on, "Temp Throttle On",  "(1-254)"),
            "fan_on":   (self.ctrl_fan_on,     Command.temp_fan_on,      "Temp Fan On",        "(1-80)"),
            "fan_off":  (self.ctrl_fan_off,    Command.temp_fan_off,     "Temp Fan Off",       "(0-79)"),
            "critical": (self.ctrl_critical,   Command.temp_critical,    "Temp Critical",      "(2-90)"),
        }
        if setting_type not in configs:
            return

        field, method, label, range_text = configs[setting_type]
        value = field.text().strip()

        if not value or not value.isdigit():
            self.statusBar().showMessage(f"Enter a valid temperature {range_text}")
            return

        reply = QMessageBox.question(self, "Confirm Setting",
            f"Set {label} to {value}°C?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)

        if reply == QMessageBox.StandardButton.Yes:
            try:
                self.send_command(method(int(value)))
            except ValueError as e:
                self.statusBar().showMessage(str(e))

    def send_all_settings(self):
        fan_off = self.ctrl_fan_off.text().strip()
        fan_on = self.ctrl_fan_on.text().strip()
        throttle = self.ctrl_throttle.text().strip()
        critical = self.ctrl_critical.text().strip()
        pwm_a = self.ctrl_pwm_a.text().strip()
        pwm_b = self.ctrl_pwm_b.text().strip()

        if not all([fan_off, fan_on, throttle, critical, pwm_a, pwm_b]):
            self.statusBar().showMessage("Error: All fields must have values")
            return

        if not all(v.isdigit() for v in [fan_off, fan_on, throttle, critical, pwm_a, pwm_b]):
            self.statusBar().showMessage("Error: All fields must be valid integers")
            return

        try:
            low_temp = int(fan_off)
            high_temp = int(fan_on)
            throttle_temp = int(throttle)
            critical_temp = int(critical)
            throttle_a = int(pwm_a)
            throttle_b = int(pwm_b)

            reply = QMessageBox.question(self, "Confirm Set All",
                f"Set all settings?\n\n"
                f"Fan Off: {low_temp}°C\nFan On: {high_temp}°C\n"
                f"Throttle: {throttle_temp}°C\nCritical: {critical_temp}°C\n"
                f"PWM A: {throttle_a}%\nPWM B: {throttle_b}%",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)

            if reply == QMessageBox.StandardButton.Yes:
                command = Command.set_all_settings(low_temp, high_temp, throttle_temp,
                                                   critical_temp, throttle_a, throttle_b)
                self.send_command(command)
        except ValueError as e:
            self.statusBar().showMessage(f"Error: {str(e)}")

    def confirm_and_send_default(self):
        reply = QMessageBox.question(self, "Confirm",
            "The device will restore its internal default settings.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)

        if reply == QMessageBox.StandardButton.Yes:
            self.send_command(Command.SET_DEFAULT)

    def confirm_and_send_reset(self):
        reply = QMessageBox.question(self, "Confirm Reset",
            "Reset device?\nThis will restart the device.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)

        if reply == QMessageBox.StandardButton.Yes:
            self.send_command(Command.RESET)

    # -------------------------------------------------------------------------
    # Connection state & UI enable/disable
    # -------------------------------------------------------------------------

    def on_status_changed(self, status):
        self.statusBar().showMessage(status)

    def on_connection_state(self, connected: bool):
        """Update all connection-dependent UI elements in one pass."""
        for widget in self._connection_widgets:
            widget.setEnabled(connected)

        if connected:
            self.status_label.setText("● Connected")
            self.status_label.setStyleSheet("color: #4caf50; font-weight: bold; letter-spacing: 0.5px;")
            self.statusBar().clearMessage()
            self.connect_btn.setText("Disconnect")
            self.port_combo.setEnabled(False)
        else:
            self.status_label.setText("● Disconnected")
            self.status_label.setStyleSheet("color: #e05555; font-weight: bold; letter-spacing: 0.5px;")
            self.statusBar().showMessage("Disconnected")
            self.connect_btn.setText("Connect")
            self.connect_btn.setEnabled(True)
            self.port_combo.setEnabled(True)

    # -------------------------------------------------------------------------
    # Misc UI helpers
    # -------------------------------------------------------------------------

    def clear_output(self):
        self.raw_text.clear()

    def autoscroll_to_bottom(self):
        if self.autoscroll_enabled:
            scrollbar = self.raw_text.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    def on_autoscroll_toggled(self, state):
        self.autoscroll_enabled = self.autoscroll_checkbox.isChecked()
        if self.autoscroll_enabled:
            self.autoscroll_to_bottom()

    def send_command(self, command: str):
        if not self.serial_worker or not self.serial_worker.isRunning():
            self.statusBar().showMessage("Not connected - cannot send command")
            return

        if command == Command.SETTINGS_READ:
            self.accumulated_settings.clear()
            self.log_info("Sending settings read command...")

        self.raw_text.insertPlainText(f"\n>>> {command}\n")
        if self.serial_worker.ser and self.serial_worker.ser.is_open:
            self.serial_worker.ser.write(f"{command}\r\n".encode())
        self.autoscroll_to_bottom()

    def log_info(self, message: str):
        """Log a message to the info display with timestamp"""
        timestamp = QDateTime.currentDateTime().toString("hh:mm:ss")
        log_msg = f"[{timestamp}] {message}\n"
        current_text = self.info_display.toPlainText()
        self.info_display.setPlainText(log_msg + current_text if current_text else log_msg)

    def send_custom_command(self):
        command = self.command_input.text().strip()
        if not command:
            return

        self.send_command(command)
        self.command_input.clear()

    def closeEvent(self, event):
        self.find_ports_timer.stop()
        if self.serial_worker:
            self.disconnect()
        event.accept()


def main():
    """Application entry point"""
    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_STYLESHEET)
    window = SerialMonitorUI()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
