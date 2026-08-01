# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Flash

```bash
python build.py          # CMake configure + Ninja build (Debug)
python build.py --clean  # Clean then build
python flash.py          # Flash via JLink over SWD
python run.py            # Build then flash in sequence
```

Toolchain: `gcc-arm-none-eabi`, CMake + Ninja. The build reads `cmake/gcc-arm-none-eabi.cmake` and writes artifacts to `build/`.

Flash requires `JLink` on PATH (JLink software from SEGGER). The ELF path is resolved from `build/build_info.json`.

## Off-Limits Directories

Never modify: `Drivers/` (ST HAL + CMSIS), `Core/` (CubeMX-generated startup/MSP), `tinyusb/` (USB stack submodule), `build/` (CMake output).

All application code lives under `Application/`.

## Architecture

**Target:** STM32C071 (Cortex-M0+, 128 KB flash, 24 KB RAM). Bare-metal cooperative scheduler — no RTOS, no interrupts driving application logic except TIM2 input capture in `pwm_repeater`.

### Layered structure

```
config/config.h          ← product-level tunable defaults (temperatures, PWM %, telemetry interval)
board/board_config.h     ← single porting layer: all pin/peripheral assignments for this PCB
board/board.h            ← board_init(), peripheral getters (I2C, USB handle), GPIO helpers
Application/bsp/         ← thin HAL wrappers (gpio, i2c, iwdg, lptim, tim, uart, usb)
Application/<module>/    ← feature modules (no cross-module coupling except through app_state)
Application/main.c       ← phased init + cooperative while(true) loop
```

### State machine (app_state)

Two orthogonal enums drive all output decisions in `main.c`:

- `SystemState`: `SystemBoot` → `SystemRunning` ↔ `SystemFault`
- `ThermalState`: `ThermalLow` / `ThermalHigh` / `ThermalThrottling` / `ThermalCritical`

`app_task()` calls `thermal_control_step()` each loop iteration, then maps the combined state to fan power, PWM throttle caps, LCD power, and the program LED pattern.

### Temperature sensing (system_temp)

`system_temp_get()` returns `max(DS18B20, HDC2010)` in **centidegrees** (°C × 100). Returns `INT16_MIN` if no sensor is available. The thermal control and fault logic treat `INT16_MIN` as sensor-lost.

- **DS18B20** (1-Wire on PB4): polled by `temperature_sensor_task()`. Three consecutive read failures → returns `INT16_MIN`.
- **HDC2010** (I2C2): polled by `hdc2010_task()`. Provides temperature + humidity; humidity appears in telemetry only.

### Settings & persistence

Settings are a **16-byte struct** (`Settings` in `settings.h`) stored in the last 8 KB of flash (pages 60–63 of 64, starting at `0x0801E000`). There is no NVS or wear-levelling layer — `settings.c` calls `flash_erase_page()` + `flash_write()` directly on every change.

To add a new setting:
1. Add a field to `Settings` in `settings.h` (struct is fixed at 16 bytes; use the `_pad` bytes).
2. Add a `#define SETTINGS_DEFAULT_*` backed by a `CONFIG_*` constant in `config.h`.
3. Add a setter in `settings.c` with validation, and wire a command in `commands.c`.

Temperature ordering invariant enforced by `settings_set_all()`: `fan_off < fan_on <= throttle_on < critical`. `fan_on == throttle_on` is permitted — in that configuration, the thermal state machine transitions directly from `ThermalLow` to `ThermalThrottling` at the shared threshold, skipping `ThermalHigh`.

### USB CDC command protocol

Commands arrive as ASCII lines over USB CDC (tinyusb). `commands_task()` reads from a FIFO, assembles lines, and dispatches.

| Command | Description |
|---|---|
| `SETTINGS?` | Query all settings → `SETTINGS=fan_off,fan_on,throttle_temp,critical,pwm_a,pwm_b` |
| `SETTINGS=<fan_off>,<fan_on>,<throttle_temp>,<critical>,<pwm_a>,<pwm_b>` | Atomic bulk set |
| `FANTEMPON=<1–80>` | Set fan-on temperature |
| `FANTEMPOFF=<0–79>` | Set fan-off temperature |
| `PWMTHRTEMP=<val>` | Set throttle-engage temperature |
| `TEMPCRIT=<2–90>` | Set critical temperature |
| `PWMTHR=<A\|B>,<0–100>` | Set PWM throttle cap for channel A or B |
| `FAN<1–4>=<ON\|OFF>` | Override individual fan duty |
| `SETDEFAULT` | Reset all settings to `config.h` defaults |
| `RESET` | Drain USB then `HAL_NVIC_SystemReset()` |
| `GETFW` | Report firmware version |

Responses are `OK <KEYWORD> ...` or `ERR <REASON> <KEYWORD> ...`.

### Telemetry

Sent over USB CDC every `CONFIG_TELEMETRY_INTERVAL_MS` (default 1000 ms):

```
$01,<uptime_s>,<ds18b20_deg>,<hdc_deg>,<hdc_rh_%>,<fan1234_bits>,<in_dc_a>,<out_dc_a>,<in_dc_b>,<out_dc_b>,<state_str>,<btn>
```

`state_str` is one of: `BOOT`, `FAULT`, `TEMP_LOW`, `TEMP_HIGH`, `TEMP_THROTTLE`, `TEMP_CRIT`.

### PWM repeater

`pwm_repeater` (TIM2 capture → TIM16/TIM17 output) reads an external 25 kHz PWM signal on PB10/PB11 and re-outputs it on PA0/PA1, capped to the `pwm_throttle_a/b` percent in `Settings`. TIM2 CC ISR updates `volatile` fields in `PwmChannel`; `pwm_repeater_task()` applies throttle and commits to the output timers each main-loop tick.

### Fan control

4 fan units, each independently selectable as 2-wire or 3/4-wire via DIP switches (PD0–PD3 read at runtime). TIM1 drives the power PWM (4 channels); TIM3 drives the remote/control PWM (4 channels). `fan_control_all_on/off()` and `fan_control_set_unit_duty()` are the main entry points.

### Monitor tool

`monitor_com_ui/` — Python serial monitor UI for observing telemetry and sending commands over USB CDC.
