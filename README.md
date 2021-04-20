# heated

Scripts to turn [Tinkerforge](https://www.tinkerforge.com/en/) bricklets into a general-purpose
PID controlled heater.

----------------------------------------

Assumes we've got a running [brickd](https://www.tinkerforge.com/en/doc/Software/Brickd.html) 
instance and the following bricklets connected:

 - [Thermocouple 2.0](https://www.tinkerforge.com/en/doc/Hardware/Bricklets/Thermocouple_V2.html#thermocouple-v2-bricklet)
   to connect to a thermocouple probe to read system temperature
 - [Solid State Relay 2.0](https://www.tinkerforge.com/en/doc/Hardware/Bricklets/Solid_State_Relay_V2.html) to control
   a heater or other or output in PWM
 - [LCD 128x64](https://www.tinkerforge.com/en/doc/Hardware/Bricklets/LCD_128x64.html) 
   to provide an interactive GUI

Also requires:

 - [Tinkerforge Python Bindings](https://www.tinkerforge.com/en/doc/Software/API_Bindings_Python.html#api-bindings-python)
 - [simple-pid](https://github.com/m-lundberg/simple-pid) (for `regulated.py` script)

Tested and working on a Raspberry Pi Zero with a 
[HAT Zero Brick](https://www.tinkerforge.com/en/doc/Hardware/Bricks/HAT_Zero_Brick.html#hat-zero-brick)
but theoretically would work fine with any other master brick.

## Scripts

There are currently two scripts available:

### `unregulated.py`

This provides a GUI which allows heater power to be set directly. The thermocouple temperature
is visible and tracked over time via a graph.

If power is set to 0 or 100%, the relay is turned permanently off or on respectively. For any
values in between, we use PWM with a fixed frequency (1Hz by default).

### `regulated.py`

Provides a GUI which allows a desired target temperature to be set. A PID loop then dynamically
updates heater power according to thermocouple temperature and tuning parameters.

Thermocouple temperature can again be visualised via a graph to verify stability.

PID parameters are set via the separate `tuning.json` file which is read on initialisation.
If `Heater.tuning_mode` is set to `True`, this file is read on every PID iteration. This is
useful for live PID tuning.

## Usage

After installing requirements and ensuring that `brickd` is running, simply execute your desired
script:

```shell
./unregulated.py
```

or

```shell
./regulated.py
```

For long-term use, you can use your preferred method of autostarting this script on boot. On a Pi
Zero, I've had success using a [systemd service](https://www.raspberrypi.org/documentation/linux/usage/systemd.md)
set to start as soon as `brickd` is up.

The "Settings" tab in the GUI for both scripts provides a "Shut Down" button that executes a 
graceful shutdown of the host device. This may need to be updated as appropriate for your system.
