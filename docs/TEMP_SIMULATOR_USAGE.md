# Temperature Simulator for Bug Reproduction

## Overview

The temperature simulator is a built-in testing module that cycles through thermal states to reproduce the intermittent fan-stop bug described in [bug_intermittent_fan_stop.md](bug_intermittent_fan_stop.md).

## How It Works

The simulator is **hardcoded to run by default** and continuously cycles through the following sequence every 500ms per state (total 3.5 second cycles):

```
Critical (75°C) 
    ↓ [500ms]
Throttling (58°C) 
    ↓ [500ms]
High (40°C) 
    ↓ [500ms]
Low (32°C) 
    ↓ [500ms]
High (40°C) 
    ↓ [500ms]
Throttling (58°C) 
    ↓ [500ms]
[Back to Critical]
```

### Temperature Values (in centidegrees)

| State | Temperature |
|-------|-------------|
| Critical | 7510 (75.10°C) |
| Throttling | 5850 (58.50°C) |
| High | 4500 (45.00°C) |
| Low | 3100 (31.00°C) |

## Why This Helps Reproduce the Bug

The bug occurs intermittently during CRITICAL → THROTTLING transitions. The simulator forces **continuous** state transitions to increase the probability of the bug occurrence within a reasonable testing window.

### The Critical Transition

When the system transitions from CRITICAL to THROTTLING:

1. **LCD power (PB15)** switches from OFF to ON (inrush current)
2. **Backlight PWM (PA0/PA1)** steps from 0% to ~50% duty
3. The 160 Hz chopped LCD load current may couple into:
   - Fan gate-drive rails (shared return path)
   - Fan power supply (shared voltage reference)

The simulator stresses this transition by repeating it every 3.5 seconds rather than waiting for occasional natural thermal variations.

## Integration Points

### Files Modified

- `Application/system_temp/system_temp.c` — checks simulator before reading real sensors
- `Application/main.c` — initializes simulator during Phase 4 (I/O init)

### Files Created

- `Application/temp_simulator/temp_simulator.h` — public API
- `Application/temp_simulator/temp_simulator.c` — implementation
- `Application/temp_simulator/CMakeLists.txt` — build configuration

### Build Configuration

- `Application/CMakeLists.txt` — added `temp_simulator` subdirectory
- `CMakeLists.txt` — added `temp_simulator` to executable link libraries
- `Application/system_temp/CMakeLists.txt` — added `temp_simulator` dependency

## Disabling the Simulator

If you need to test with real sensors instead, modify `Application/temp_simulator/temp_simulator.c` line 9:

```c
/* Change from: */
} sim_ctx = {.enabled = true, .state = SimStateCritical, .state_start_ms = 0};

/* To: */
} sim_ctx = {.enabled = false, .state = SimStateCritical, .state_start_ms = 0};
```

Then rebuild and flash.

## Observation During Testing

Watch the telemetry output for:

1. **State transitions every 500ms** — confirm the cycle is working
2. **Fan status bits** — should follow the state transitions
3. **Intermittent fan-stop** — if fans drop OFF while telemetry reports ON, the bug is reproduced
4. **Correlations** — note if fan stops align with specific state transitions (especially CRITICAL→THROTTLING)

## Expected Telemetry Pattern

Normal behavior (no bug):

```
state_str=CRITICAL, fan=1111  (all on)
state_str=THROTTLING, fan=1111  (all still on)
state_str=HIGH, fan=1111
state_str=LOW, fan=0000  (all off below fan_off temp)
[cycle repeats]
```

Bug reproduction (intermittent):

```
state_str=CRITICAL, fan=1111
state_str=THROTTLING, fan=0000  ← BUG: fans reported ON but physically OFF
state_str=HIGH, fan=1111  (fans magically restart)
```

## Root Cause Hypothesis

See [bug_intermittent_fan_stop.md](bug_intermittent_fan_stop.md) section "Leading hypothesis: LCD backlight PWM dimming as a shared-rail noise source".

The simulator's continuous CRITICAL→THROTTLING cycling should trigger the bug within 10–100 cycles (~35–350 seconds) if the hardware conditions match the hypothesis.
