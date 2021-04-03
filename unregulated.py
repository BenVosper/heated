from logging import getLogger, INFO, StreamHandler
from time import sleep

from tinkerforge.ip_connection import IPConnection
from tinkerforge.ip_connection import Error as TFConnectionError
from tinkerforge.bricklet_lcd_128x64 import BrickletLCD128x64
from tinkerforge.bricklet_thermocouple_v2 import BrickletThermocoupleV2
from tinkerforge.bricklet_solid_state_relay_v2 import BrickletSolidStateRelayV2

LOGGER = getLogger(__name__)
LOGGER.setLevel(INFO)
LOGGER.addHandler(StreamHandler())

HOST = "localhost"
PORT = 4223

THERMOCOUPLE_READ_PERIOD = 1000
GUI_READ_PERIOD = 100
PWM_PERIOD = 1000


class Heater:

    ipcon = None
    lcd = None
    thermocouple = None
    relay = None

    heater_power = 0

    def __init__(self):
        self.ipcon = IPConnection()
        while True:
            try:
                self.ipcon.connect(HOST, PORT)
                break
            except TFConnectionError as error:
                LOGGER.error("Connection Error: " + str(error.description))
                sleep(1)

        self.ipcon.register_callback(IPConnection.CALLBACK_ENUMERATE, self.cb_enumerate)
        self.ipcon.register_callback(IPConnection.CALLBACK_CONNECTED, self.cb_connected)

        while True:
            try:
                self.ipcon.enumerate()
                return
            except TFConnectionError as error:
                LOGGER.error("Enumerate Error: " + str(error.description))
                sleep(1)

    def _init_lcd(self, uid):
        try:
            self.lcd = BrickletLCD128x64(uid, self.ipcon)
            self.lcd.clear_display()
            self.lcd.remove_all_gui()
            LOGGER.info("LCD128x64 initialized")
        except TFConnectionError as error:
            LOGGER.error("LCD128x64 init failed: " + str(error.description))
            return

        self.lcd.set_gui_button(0, 2, 25, 60, 15, "-1%")
        self.lcd.set_gui_button(1, 64, 25, 60, 15, "+1%")
        self.lcd.set_gui_button(2, 2, 45, 60, 15, "-10%")
        self.lcd.set_gui_button(3, 64, 45, 60, 15, "+10%")
        self.lcd.set_gui_button_pressed_callback_configuration(GUI_READ_PERIOD, True)
        self.lcd.register_callback(
            BrickletLCD128x64.CALLBACK_GUI_BUTTON_PRESSED, self.cb_button
        )

        self.write_power()

    def _init_thermocouple(self, uid):
        try:
            self.thermocouple = BrickletThermocoupleV2(uid, self.ipcon)
            LOGGER.info("Thermocouple initialized")
        except TFConnectionError as error:
            LOGGER.error("Thermocouple init failed: " + str(error.description))
            return

        self.thermocouple.set_temperature_callback_configuration(
            THERMOCOUPLE_READ_PERIOD, False, "x", 0, 0
        )
        self.thermocouple.register_callback(
            BrickletThermocoupleV2.CALLBACK_TEMPERATURE, self.cb_thermocouple
        )

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
        self.relay.set_monoflop(False, 0)

    def write_temp(self, value):
        if self.lcd is None:
            return
        self.lcd.draw_box(0, 0, 127, 10, True, BrickletLCD128x64.COLOR_WHITE)
        celcius = int(value) / 100
        string = f"Temp: {celcius:6.2f}\xDFC"
        self.lcd.draw_text(0, 0, BrickletLCD128x64.FONT_6X8, True, string)

    def write_power(self):
        if self.lcd is None:
            return
        self.lcd.draw_box(0, 12, 127, 20, True, BrickletLCD128x64.COLOR_WHITE)
        string = f"Power: {self.heater_power}%"
        self.lcd.draw_text(0, 12, BrickletLCD128x64.FONT_6X8, True, string)

    def cb_thermocouple(self, value):
        self.write_temp(value)

    def cb_button(self, index, value):
        if value is False:
            return
        if index == 0:
            self.heater_power = max(self.heater_power - 1, 0)
            self.write_power()
        elif index == 1:
            self.heater_power = min(self.heater_power + 1, 100)
            self.write_power()
        elif index == 2:
            self.heater_power = max(self.heater_power - 10, 0)
            self.write_power()
        elif index == 3:
            self.heater_power = min(self.heater_power + 10, 100)
            self.write_power()

    def cb_relay_flop(self, state):
        on_time = round((self.heater_power / 100) * PWM_PERIOD)
        off_time = PWM_PERIOD - on_time

        # I don't understand why this is. The docs say the value here
        # should be the state *after* the monoflop, but it seems to
        # just return the opposite of the state we flopped to.
        # eg. Flopping to False from False will return True here,
        # where by the docs you'd expect False.
        previous_state = not state

        if on_time and previous_state is False:
            self.relay.set_monoflop(True, on_time)
        else:
            self.relay.set_monoflop(False, off_time)

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


if __name__ == "__main__":
    LOGGER.info("Heater starting...")

    heater = Heater()

    input("Press key to exit\n")

    heater.lcd.clear_display()
    heater.lcd.remove_all_gui()

    if heater.ipcon is not None:
        heater.ipcon.disconnect()

    LOGGER.info("Heater shut down")
