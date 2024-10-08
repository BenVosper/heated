#!/usr/bin/env python3
import asyncio
import atexit
import json
import socket

from collections import deque
from datetime import datetime, timedelta
from logging import getLogger, INFO, StreamHandler, FileHandler
from time import sleep
from subprocess import run
from os import path

from simple_pid import PID

from tinkerforge.ip_connection import IPConnection
from tinkerforge.ip_connection import Error as TFConnectionError
from tinkerforge.bricklet_lcd_128x64 import BrickletLCD128x64
from tinkerforge.bricklet_thermocouple_v2 import BrickletThermocoupleV2
from tinkerforge.bricklet_solid_state_relay_v2 import BrickletSolidStateRelayV2


def last_n_values(n, iterable):
    for i in range(n, 0, -1):
        yield iterable[-i]


LOGGER = getLogger(__name__)
LOGGER.setLevel(INFO)
STDOUT_HANDLER = StreamHandler()
LOGGER.addHandler(STDOUT_HANDLER)

DATETIME_FMT = "%d/%m/%Y %H:%M:%S"

PID_TUNING_FILE_PATH = "tuning.json"

HOST = "localhost"
PORT = 4223

THERMOCOUPLE_READ_PERIOD = 1000
GUI_READ_PERIOD = 100
PWM_PERIOD = 1000
N_SMOOTHING_POINTS = 5

# If thermocouple is in error state for more than this long,
# deactivate output until it comes back online.
DEACTIVATE_POWER_DELAY = timedelta(seconds=11)


# fmt: off
CONTROL_ICON = [
    0,0,0,0,0,0,0,1,0,0,0,1,0,0,0,1,0,0,0,1,0,0,0,0,0,0,0,0,
    0,0,0,0,0,0,1,1,1,0,0,1,0,0,0,1,0,0,1,1,1,0,0,0,0,0,0,0,
    0,0,0,0,0,0,1,1,1,0,1,1,1,0,0,1,0,0,1,1,1,0,0,0,0,0,0,0,
    0,0,0,0,0,0,0,1,0,0,1,1,1,0,1,1,1,0,0,1,0,0,0,0,0,0,0,0,
    0,0,0,0,0,0,0,1,0,0,0,1,0,0,1,1,1,0,0,1,0,0,0,0,0,0,0,0,
    0,0,0,0,0,0,0,1,0,0,0,1,0,0,0,1,0,0,0,1,0,0,0,0,0,0,0,0,
]

GRAPH_ICON = [
    1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,
    1,0,0,0,0,0,1,1,1,0,0,0,0,0,0,0,0,0,0,0,0,0,1,1,0,0,0,0,
    1,0,0,0,1,1,0,0,0,1,0,0,1,0,0,0,1,0,0,1,1,1,0,0,1,0,0,0,
    1,0,1,1,0,0,0,0,0,0,1,1,0,1,1,1,0,1,1,0,0,0,0,0,0,1,1,1,
    1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,
    1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,
]

SETTINGS_ICON = [
    0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,
    0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,
    0,0,0,0,0,0,0,0,0,0,0,1,0,0,0,0,0,1,0,0,0,0,0,0,0,0,0,0,
    0,0,0,0,0,0,0,0,0,1,0,0,0,0,0,0,0,0,0,1,0,0,0,0,0,0,0,0,
    0,0,0,0,0,0,0,0,0,0,1,1,1,1,1,1,1,1,1,0,0,0,0,0,0,0,0,0,
    0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,
]
# fmt: on


class Heater:

    # These are the bricklet objects which are populated
    # as the hardware responds to enumeration request.
    ipcon = None
    lcd = None
    thermocouple = None
    relay = None
    pid = None

    # Current PID setpoint
    setpoint = 0

    # PWM power for output. 0 - 100
    heater_power = 0

    # Current state of output. Boolean
    heater_active = False

    # Current state of thermocouple.
    thermocouple_in_error_state = False
    error_state_start = None

    # Current active GUI tab index
    active_tab = 0

    # Number of readings to keep in state. This is set to match graph width
    n_temp_points = 107

    starting_temp = 20
    initial_temp_data = [starting_temp] * N_SMOOTHING_POINTS
    temp_data = deque(initial_temp_data, n_temp_points)

    # Min and max for graph Y axis. Updated automatically with data
    axis_min = starting_temp - 10
    axis_max = starting_temp + 10

    # Set true to read tuning parameters every iteration
    tuning_mode = False

    # Set true to log data to a file
    logging_mode = False

    # Current target tunings
    tunings = {"p": 0, "i": 0, "d": 0, "bias": 0, "proportional_on_measurement": False}

    def __init__(self):
        LOGGER.info("Heater starting...")

        self._init_pid()

        self.ipcon = IPConnection()
        while True:
            try:
                self.ipcon.connect(HOST, PORT)
                break
            except TFConnectionError as error:
                LOGGER.error("Connection Error: " + str(error.description))
                sleep(1)
            except socket.error as error:
                LOGGER.error("Socket Error: " + str(error))
                sleep(1)

        self.ipcon.register_callback(IPConnection.CALLBACK_ENUMERATE, self.cb_enumerate)
        self.ipcon.register_callback(IPConnection.CALLBACK_CONNECTED, self.cb_connected)

        if self.logging_mode:
            logfile_path = f"{datetime.now().timestamp():0f}_heated_data.csv"
            self.data_logger = getLogger("data-logger")
            self.data_logger.setLevel(INFO)
            self.data_logger.addHandler(FileHandler(logfile_path))
            self.data_logger.addHandler(STDOUT_HANDLER)
            self.data_logger.info(
                f"Timestamp, Temp (°C), Setpoint (°C), Power (%), Kp, Ki, Kd, Cp, Ci, Cd"
            )

        while True:
            try:
                self.ipcon.enumerate()
                return
            except TFConnectionError as error:
                LOGGER.error("Enumerate Error: " + str(error.description))
                sleep(1)

    def _init_pid(self):
        self.pid = PID(setpoint=self.setpoint, output_limits=(0, 100))
        self._read_pid_tunings_from_file()
        self._set_pid_tuning(self.tunings)

    def _read_pid_tunings_from_file(self):
        if not path.exists(PID_TUNING_FILE_PATH):
            LOGGER.info(
                f"{PID_TUNING_FILE_PATH} does not exist. Using default tunings."
            )
            return

        with open(PID_TUNING_FILE_PATH, "r") as f:
            self.tunings = json.load(f)

    def _set_pid_tuning(self, tuning_dict):
        tunings = (tuning_dict.get(parameter, 0) for parameter in ("p", "i", "d"))
        self.pid.tunings = tunings
        self.pid.proportional_on_measurement = tuning_dict.get(
            "proportional_on_measurement", False
        )
        self.pid.bias = tuning_dict.get("bias", 0)

    def _init_lcd(self, uid):
        try:
            self.lcd = BrickletLCD128x64(uid, self.ipcon)
            self.lcd.clear_display()
            self.lcd.remove_all_gui()
            LOGGER.info("LCD128x64 initialized")
        except TFConnectionError as error:
            LOGGER.error("LCD128x64 init failed: " + str(error.description))
            return

        self.lcd.set_gui_tab_selected_callback_configuration(GUI_READ_PERIOD, True)
        self.lcd.register_callback(
            BrickletLCD128x64.CALLBACK_GUI_TAB_SELECTED, self.cb_tab
        )
        self.lcd.set_gui_tab_configuration(self.lcd.CHANGE_TAB_ON_CLICK_AND_SWIPE, True)
        self.lcd.set_gui_tab_icon(0, CONTROL_ICON)
        self.lcd.set_gui_tab_icon(1, GRAPH_ICON)
        self.lcd.set_gui_tab_icon(2, SETTINGS_ICON)

        self.lcd.set_gui_button_pressed_callback_configuration(GUI_READ_PERIOD, True)
        self.lcd.register_callback(
            BrickletLCD128x64.CALLBACK_GUI_BUTTON_PRESSED, self.cb_button
        )

        # Set initial tab
        self.cb_tab(self.active_tab)

    def cb_tab(self, index):
        self.active_tab = index
        self.lcd.clear_display()
        if index == 0:
            self.write_temp()
            self.write_setpoint()
            self.write_power()
            self.lcd.set_gui_button(0, 2, 18, 61, 11, "-1\xDFC")
            self.lcd.set_gui_button(1, 66, 18, 61, 11, "+1\xDFC")
            self.lcd.set_gui_button(2, 2, 30, 61, 11, "-10\xDFC")
            self.lcd.set_gui_button(3, 66, 30, 61, 11, "+10\xDFC")
            self.lcd.set_gui_button(4, 2, 42, 61, 11, "-100\xDFC")
            self.lcd.set_gui_button(5, 66, 42, 61, 11, "+100\xDFC")

        elif index == 1:
            self.lcd.set_gui_graph_configuration(
                0, BrickletLCD128x64.GRAPH_TYPE_LINE, 20, 0, 107, 52, "", ""
            )
            self.update_graph()
            self.lcd.draw_text(0, 23, BrickletLCD128x64.FONT_6X8, True, "\xDFC")
            self.update_axis()

        elif index == 2:
            self.lcd.draw_text(0, 0, BrickletLCD128x64.FONT_6X8, True, "BV21")
            self.lcd.set_gui_button(6, 0, 10, 80, 20, "Shut Down")

    def _cb_set_button(self, setpoint):
        self.setpoint = setpoint
        self.pid.setpoint = setpoint
        self.write_setpoint()

    def cb_button(self, index, value):
        if value is False:
            return
        if index == 0:
            self._cb_set_button(max(self.setpoint - 1, 0))
        elif index == 1:
            self._cb_set_button(min(self.setpoint + 1, 1500))
        elif index == 2:
            self._cb_set_button(max(self.setpoint - 10, 0))
        elif index == 3:
            self._cb_set_button(min(self.setpoint + 10, 1500))
        elif index == 4:
            self._cb_set_button(max(self.setpoint - 100, 0))
        elif index == 5:
            self._cb_set_button(min(self.setpoint + 100, 1500))
        elif index == 6:
            self.close()
            self.shutdown_host()

    def _init_thermocouple(self, uid):
        try:
            self.thermocouple = BrickletThermocoupleV2(uid, self.ipcon)
            LOGGER.info("Thermocouple initialized")
        except TFConnectionError as error:
            LOGGER.error("Thermocouple init failed: " + str(error.description))
            return

        self.thermocouple.set_configuration(
            BrickletThermocoupleV2.AVERAGING_16,
            BrickletThermocoupleV2.TYPE_K,
            BrickletThermocoupleV2.FILTER_OPTION_50HZ,
        )
        self.thermocouple.set_temperature_callback_configuration(
            THERMOCOUPLE_READ_PERIOD, False, "x", 0, 0
        )
        self.thermocouple.register_callback(
            BrickletThermocoupleV2.CALLBACK_ERROR_STATE, self.cb_thermocouple_error
        )
        self.thermocouple.register_callback(
            BrickletThermocoupleV2.CALLBACK_TEMPERATURE, self.cb_thermocouple_reading
        )
        over_under, open_circuit = self.thermocouple.get_error_state()
        if any((over_under, open_circuit)):
            self.thermocouple_in_error_state = True

    def cb_thermocouple_error(self, over_under, open_circuit):
        if any((over_under, open_circuit)):
            self.error_state_start = datetime.now()
        else:
            self.error_state_start = None

        LOGGER.info(
            f"Thermocouple reports: "
            f"over/under voltage {over_under}, open-circuit {open_circuit}"
        )

    def get_pid_value(self):
        current_temp = (
            sum(last_n_values(N_SMOOTHING_POINTS, self.temp_data)) / N_SMOOTHING_POINTS
        )

        if self.tuning_mode:
            self._read_pid_tunings_from_file()
            self._set_pid_tuning(self.tunings)

        return self.pid(current_temp)

    def cb_thermocouple_reading(self, value):
        if (
            self.error_state_start
            and (datetime.now() - self.error_state_start) > DEACTIVATE_POWER_DELAY
        ):
            self.thermocouple_in_error_state = True
        else:
            self.thermocouple_in_error_state = False

        if self.thermocouple_in_error_state:
            power = 0
            LOGGER.info("Thermocouple in error state. Output deactivated.")
        else:
            current_temp = value / 100
            self.temp_data.append(current_temp)
            power = self.get_pid_value()

        old_power = self.heater_power
        sticky_state_active = old_power == 100 or old_power == 0
        self.heater_power = power

        if power == 100:
            self.relay.set_state(True)
            self.heater_active = True
        elif power == 0:
            self.relay.set_state(False)
            self.heater_active = False
        elif 0 < power < 100 and sticky_state_active:
            # If we're coming out of a sticky state, kick off the
            # flop loop for PWM.
            self.relay.set_state(False)
            self.heater_active = False
            self.relay.set_monoflop(False, 0)

        self.write_temp()
        self.write_power()
        self.update_graph()

        if self.logging_mode:
            self.log_line()

    def log_line(self):
        timestamp = datetime.now().strftime(DATETIME_FMT)
        current_temp = self.temp_data[-1]
        kp, ki, kd = self.pid.tunings
        cp, ci, cd = self.pid.components
        log_line = ", ".join(
            str(value)
            for value in (
                timestamp,
                current_temp,
                self.setpoint,
                self.heater_power,
                kp,
                ki,
                kd,
                cp,
                ci,
                cd,
            )
        )
        self.data_logger.info(log_line)

    def _init_relay(self, uid):
        try:
            self.relay = BrickletSolidStateRelayV2(uid, self.ipcon)
            LOGGER.info("Relay initialized")
        except TFConnectionError as error:
            LOGGER.error("Relay init failed: " + str(error.description))
            return

        self.relay.register_callback(
            BrickletSolidStateRelayV2.CALLBACK_MONOFLOP_DONE, self.cb_relay_flop
        )
        self.relay.set_state(False)

    def cb_relay_flop(self, _):
        on_time = round((self.heater_power / 100) * PWM_PERIOD)
        off_time = PWM_PERIOD - on_time

        if self.heater_power < 100:
            if self.heater_active:
                self.relay.set_monoflop(False, off_time)
                self.heater_active = False
            else:
                self.relay.set_monoflop(True, on_time)
                self.heater_active = True
        # If power is 0 or 100, we're not using the flop loop

    def write_temp(self):
        if self.lcd is None:
            return
        if self.active_tab != 0:
            return
        current_temp = self.temp_data[-1]
        temp_string = (
            f"T: {current_temp:2.0f}\xDFC"
            if not self.thermocouple_in_error_state
            else "T: ERR!"
        )
        self.lcd.draw_box(0, 0, 59, 10, True, BrickletLCD128x64.COLOR_WHITE)
        self.lcd.draw_text(0, 0, BrickletLCD128x64.FONT_6X8, True, temp_string)

    def write_power(self):
        if self.lcd is None:
            return
        if self.active_tab != 0:
            return
        self.lcd.draw_box(0, 10, 127, 19, True, BrickletLCD128x64.COLOR_WHITE)
        string = f"Power: {self.heater_power:3.1f}%"
        self.lcd.draw_text(0, 10, BrickletLCD128x64.FONT_6X8, True, string)

    def write_setpoint(self):
        if self.lcd is None:
            return
        if self.active_tab != 0:
            return

        set_string = f"S: {self.setpoint}\xDFC"
        self.lcd.draw_box(60, 0, 127, 10, True, BrickletLCD128x64.COLOR_WHITE)
        self.lcd.draw_text(60, 0, BrickletLCD128x64.FONT_6X8, True, set_string)

    def update_axis(self):
        self.lcd.draw_box(0, 0, 20, 10, True, BrickletLCD128x64.COLOR_WHITE)
        self.lcd.draw_box(0, 45, 20, 55, True, BrickletLCD128x64.COLOR_WHITE)
        self.lcd.draw_text(
            0, 0, BrickletLCD128x64.FONT_6X8, True, f"{self.axis_max:3.0f}"
        )
        self.lcd.draw_text(
            0, 45, BrickletLCD128x64.FONT_6X8, True, f"{self.axis_min:3.0f}"
        )
        self.lcd.draw_text(0, 107, BrickletLCD128x64.FONT_6X8, True, f"")

    def update_graph(self):
        if self.lcd is None:
            return
        if self.active_tab != 1:
            return

        max_temp = max(self.temp_data)
        min_temp = min(self.temp_data)

        # Pad a little bit for looks
        max_temp *= 1.1
        min_temp *= 0.9

        diff = max_temp - min_temp
        if diff == 0:
            # This probably means we don't have any data yet
            return

        scaled_data = [((value - min_temp) / diff) * 255 for value in self.temp_data]

        # This gets rid of any randomness which apparently sometimes occurs when
        # the thermocouple bricklet is physically bumped.
        scaled_data = map(lambda value: max(min(value, 255), 0), scaled_data)

        if max_temp != self.axis_max or min_temp != self.axis_min:
            self.axis_max = max_temp
            self.axis_min = min_temp
            self.update_axis()

        self.lcd.set_gui_graph_data(0, scaled_data)

    def cb_enumerate(self, uid, _, __, ___, ____, device_identifier, enumeration_type):
        if (
            enumeration_type == IPConnection.ENUMERATION_TYPE_CONNECTED
            or enumeration_type == IPConnection.ENUMERATION_TYPE_AVAILABLE
        ):
            if device_identifier == BrickletLCD128x64.DEVICE_IDENTIFIER:
                self._init_lcd(uid)
            elif device_identifier == BrickletThermocoupleV2.DEVICE_IDENTIFIER:
                self._init_thermocouple(uid)
            elif device_identifier == BrickletSolidStateRelayV2.DEVICE_IDENTIFIER:
                self._init_relay(uid)

    def cb_connected(self, connected_reason):
        if connected_reason == IPConnection.CONNECT_REASON_AUTO_RECONNECT:
            LOGGER.info("Auto Reconnect")

            while True:
                try:
                    self.ipcon.enumerate()
                    break
                except TFConnectionError as error:
                    LOGGER.error("Enumerate Error: " + str(error.description))
                    sleep(1)

    def close(self):
        if self.lcd:
            self.lcd.clear_display()
            self.lcd.remove_all_gui()
        if self.relay:
            self.relay.set_state(False)
        if self.ipcon is not None:
            self.ipcon.disconnect()
        LOGGER.info("Heater shut down")

    def shutdown_host(self):
        run("sudo shutdown now", shell=True)


if __name__ == "__main__":
    heater = Heater()
    atexit.register(heater.close)

    loop = asyncio.get_event_loop()
    try:
        loop.run_forever()
    finally:
        loop.close()
