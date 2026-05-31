# STM32C071 Thermal Controller

A safety-critical thermal management firmware for STM32C071 microcontrollers, featuring automatic fan control, PWM throttling, and comprehensive telemetry.

## Features

- **Multi-sensor temperature monitoring** - DS18B20 (1-Wire) and HDC2010 (I2C) support
- **Automatic fan control** - 4 independent fans with tacho feedback
- **PWM throttle control** - Reduces system load under thermal stress
- **USB CDC serial interface** - Configuration and telemetry via serial commands
- **Watchdog protection** - IWDG for system reliability

## Hardware

- **MCU**: STM32C071 (ARM Cortex-M0+, 32KB Flash, 12KB RAM)
- **Sensors**: DS18B20 (1-Wire), HDC2010 (I2C)
- **Fans**: 4 channels with tacho input for RPM monitoring
- **PWM**: 2 channels for throttle control

## Project Structure

```
├── Application/           # Firmware source code
│   ├── main.c            # Main application loop
│   ├── thermal_control/  # Thermal state machine
│   ├── fan_control/      # Fan PWM/tacho logic
│   ├── pwm_repeater/     # PWM input capture and repeat
│   ├── watchdog/         # IWDG management
│   ├── telemetry/        # Data reporting
│   └── ...               # Additional modules
├── Drivers/               # STM32Cube HAL drivers
├── cmake/                 # CMake configuration
├── monitor_com_ui/        # PyQt6 serial monitor
├── build.py               # Build script (Python)
└── CMakeLists.txt         # Main CMake configuration
```

## Building

### Prerequisites

- ARM GCC toolchain (`arm-none-eabi-gcc`)
- CMake 3.22+
- Ninja build system
- Python 3.12+ (for build script)

### Build Commands

```bash
# Build the firmware
python build.py

# Clean and rebuild
python build.py --clean
```

## Serial Monitor

A PyQt6-based serial monitor provides real-time telemetry and configuration:

```bash
python -m monitor_com_ui.main
```

### Key Commands

Telemetry is sent automatically every second. Query commands:

| Command | Description |
|---------|-------------|
| `SETTINGS?` | Read current configuration |
| `PWMTHR=A,75` | Set PWM throttle A to 75% |
| `PWMTHR=B,50` | Set PWM throttle B to 50% |
| `FAN1=ON` | Turn fan 1 on (or OFF) |
| `FANTEMPON=35` | Set fan-on temperature threshold |
| `FANTEMPOFF=30` | Set fan-off temperature threshold |
| `TEMPCRIT=60` | Set critical temperature |
| `GETFW` | Report firmware version |
| `RESET` | System reset |

## Configuration

Default thermal thresholds (in `Application/config/config.h`):

| Parameter | Default | Description |
|-----------|---------|-------------|
| `CONFIG_TEMP_FAN_ON` | 35°C | Fans start above this |
| `CONFIG_TEMP_FAN_OFF` | 30°C | Fans stop below this |
| `CONFIG_TEMP_THROTTLE_ON` | 40°C | PWM throttle engages |
| `CONFIG_TEMP_CRITICAL` | 60°C | Overheat protection |

