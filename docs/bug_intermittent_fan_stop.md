# Bug: Intermittent all-fans-stop on CRITICAL → THROTTLING exit

**Status:** Root cause unconfirmed — verification pending. No firmware changes
made. Primary suspected cause is electrical, not software.

## System overview

**MCU:** STM32C071CBT6 (48-pin LQFP, Cortex-M0+), per the schematic
(`mcu.SchDoc`, IC1) and its 48-pin net list. `flash.py` previously told
JLink `device STM32C071RB` (the 64-pin variant) — corrected to
`STM32C071CB`. Bare-metal, no RTOS — a cooperative `while(true)` loop in
`main.c`, with TIM2 input capture as the only interrupt driving application
logic.

**Power rails:** Main input is **+VIN, 24 V**, reverse-polarity protected by
a P-FET (Q5) ahead of a buck regulator (`psu.SchDoc`, AP64060WU) producing
**+3V3** for the MCU/logic. A separate buck regulator (`gate_driver_psu.SchDoc`,
SSP7903P12PR) drops +VIN down to a **+12V** rail dedicated to the fan
gate-driver ICs. The LCD module and the fans themselves are both powered
directly from the raw **24 V** rail — the LCD through a switched P-FET load
switch (Q4, gated by NPN Q18 off `LCD_PWR_EN`/PB15), the fans permanently
(only their return path is switched, see Drive stage below).

**Fans:** 4 independent units, wired through 4-pin connectors (J5–J8) that
carry `+VIN` (24 V), the switched return (`MOS_OUTn`), a tach line
(`FAN_TACHOn`), and an open-collector control/PWM line (`FAN_CTRL_PWMn`) —
i.e. the connector and drive hardware fully support 3/4-wire fans. Firmware
does not use that capability today: `fan_control_init()` **forces all 4
units into 2-wire mode** regardless of the DIP-switch (PD0–PD3) reading
(`Application/fan_control/fan_control.c`, `_fan_types[i] = FanType2Wire`),
so in the currently-shipped configuration only the switched power leg
(`MOS_OUTn`) is used for speed control; `FAN_CTRL_PWMn`/`FAN_TACHOn` are
wired but not driven/read by this firmware build.

**Speed control is not proportional PWM in practice.** TIM1 generates a
real variable-duty PWM signal in hardware, but the only two duty values ever
commanded at runtime are **0% (off)** and **100% (on)** —
`fan_control_all_off()` sets 0%, and `fan_control_sequential_open()` (the
only path that turns fans on) always commands `DC = 100`. There is currently
no code path that drives an intermediate duty cycle for fan speed. So today
the "PWM" output is functionally a binary on/off switch, not a speed
controller.

**Drive stage:** each TIM1 channel (`FAN_PWR_PWMn`, 3.3 V logic) feeds a
**dedicated low-side gate-driver IC** (UCC27517DBVR, one per fan, IC6–IC9),
powered from the +12V gate-driver rail — not the MCU pin driving the FET
gate directly. Each driver's output drives an **N-channel MOSFET**
(Q6–Q9) through a 10 Ω gate resistor, with a 10 kΩ gate pull-down for
fail-safe off if the driver output is undriven. The MOSFET switches the
fan's return path to ground (`MOS_OUTn`, low-side switching) while `+VIN`
(24 V) stays permanently connected to the fan's positive terminal. 100%
duty holds the driver output — and FET gate — statically high (fully
enhanced, fan grounded continuously); 0% holds it low (fan floating/off).
The `FAN_PWR_PWM` input to each driver also has its own 10 kΩ pull-down
directly off the MCU trace, independent of the driver-side pull-down.

**Temperature sensing:** `system_temp_get()` reports
`max(DS18B20_reading, HDC2010_reading)` in firmware, but on this board
**the DS18B20 (1-Wire, PB4/PB5/PB8) is not populated/used** — the HDC2010
(I2C2) is the only sensor actually present. The DS18B20 code path exists and
will return `INT16_MIN` (sensor-lost) permanently on this hardware, which
`system_temp_get()`'s `max()` logic correctly falls back around.

## State machine

Two independent state enums drive every output decision in `main.c`:

- **`SystemState`** — `SystemBoot` → `SystemRunning` ↔ `SystemFault`.
  `SystemFault` is entered if the temperature sensor(s) are unreadable
  (`system_temp_get() == INT16_MIN`) either at boot (after a 10 s timeout)
  or during running; it's exited back to `SystemRunning` as soon as a valid
  reading returns.
- **`ThermalState`** — `ThermalLow` → `ThermalHigh` → `ThermalThrottling` →
  `ThermalCritical`, each with its own temperature threshold from
  `Settings` and independent hysteresis on the way back down
  (`Application/thermal_control/thermal_control.c`):
  - `ThermalLow → ThermalHigh` at `temp_fan_on`, back down at `temp_fan_off`.
  - `ThermalHigh → ThermalThrottling` at `temp_throttle_on`, back down 2 °C
    below that (`THROTTLE_HYSTERESIS_DEG`).
  - `ThermalThrottling → ThermalCritical` at `temp_critical`, back down 2 °C
    below that (`CRITICAL_HYSTERESIS_DEG`).
  - Critical can only step back down to Throttling, never straight to High —
    every drop passes through Throttling first.

`app_task()` (`main.c`) calls `thermal_control_step()` once per loop
iteration, then maps the combined `(SystemState, ThermalState)` pair to fan
power, PWM throttle caps on the external repeater signal, LCD power, and the
program LED pattern — see `apply_fans()`, `apply_throttle()`,
`apply_lcd_power()`, `apply_program_led()`. Fans are commanded on for
`ThermalHigh`, `ThermalThrottling`, and `ThermalCritical` alike, and off only
for `ThermalLow` (or by the front-panel button).

### What the device does in each `ThermalState` (while `SystemRunning`)

| State | Fans (`apply_fans`) | External PWM repeater throttle cap (`apply_throttle`) | LCD power (`apply_lcd_power`) | Program LED (`apply_program_led`) |
|---|---|---|---|---|
| `ThermalLow` | Off — `fan_control_all_off()` (unless the front-panel button is held) | Forced to 100% (`pwm_set_throttle_a/b(100)`) — irrelevant while fans are off, but the repeater output itself is not gated by fan state | On | `ProgramLedLow` |
| `ThermalHigh` | On, full speed — `fan_control_sequential_open()` staggers the 4 units on 500 ms apart, each commanded to 100% duty (see "Speed control is not proportional PWM" above) | 100% — no throttling of the external PWM signal | On | `ProgramLedHigh` |
| `ThermalThrottling` | On, same as `ThermalHigh` — fan drive itself is not reduced in this state | Capped to `settings->pwm_throttle_a/b` (configurable, default ~50%) — this throttles the *external* signal repeated out on PA0/PA1, e.g. dimming the LCD backlight or throttling another connected load, not the fans | On | `ProgramLedThrottling` |
| `ThermalCritical` | On, same as above — fans are **not** turned off in Critical; `auto_on` includes Critical | Forced to 0% — the external PWM output is fully cut | **Off** — the only state where the LCD is powered down | `ProgramLedCritical` |

Two things worth calling out explicitly since they're easy to misread from
the state names:

- **"Throttling" throttles the external repeater PWM signal (`pwm_repeater`,
  PA0/PA1) — it does not reduce fan speed.** Fan drive is binary (on/off,
  see above) and is commanded identically across High/Throttling/Critical.
  The name refers to what's being throttled downstream (LCD backlight /
  external load), not the fans.
- **Fans stay on through `ThermalCritical`.** Only the LCD and the external
  PWM output are cut in Critical — cooling is expected to remain maximal
  precisely when the system is hottest. This is also why `apply_fans()`'s
  `auto_on` set includes Critical, which matters for the bug below: fan
  drive state is never touched across the entire Critical → Throttling →
  High excursion.

When `SystemState` is `SystemFault` (sensor lost), fans are forced on
unconditionally (`fans_on = ... || (state == SystemFault)`) regardless of
`ThermalState`, the LCD is off, and the program LED shows `ProgramLedError`.

## Symptom

Reported by the user:

> When the device exits from CRITICAL state and enters THROTTLING state, fans
> stop. All of them. Even if button is pressed, they will not work. Telemetry
> reports all fans are ON however. But they are OFF. When the device exits
> THROTTLING and enters HIGH_TEMP, fans are ON again. This does not happen
> often — roughly once every 100 transitions or less.

Key facts:

- All 4 fan units stop **simultaneously** — despite fully independent drive
  circuits (per-fan TIM1 channel, pin, N-MOSFET, output stage). The only
  elements shared by all four fans are: TIM1's common control bits
  (`CEN`/`ARR`/`BDTR.MOE`), the GPIOA port configuration, the 3.3 V
  gate-drive reference, the fan supply rail, and ground.
- Telemetry (`$01,...` line, fan bitfield) reports all 4 fans **ON** the entire
  time they are physically stopped.
- The `FAN_FORCE_EN` push button has no effect while stuck.
- Recovery is reliable and happens specifically at the THROTTLING → HIGH
  transition, not at a random later time.
- Frequency: roughly 1-in-100 CRITICAL → THROTTLING transitions.

## Why this is hard: the telemetry fact rules out most software explanations

`get_fan_state_str()` in `Application/telemetry/telemetry.c` does **not** read a
software "commanded" flag — it reads the live TIM1 hardware compare register
directly via `fan_control_get_power_channel_duty()` → `__HAL_TIM_GET_COMPARE`.
So "telemetry says ON" is proof that the CCR/duty register genuinely holds a
non-zero value the whole time. Any explanation has to put the fault **downstream
of CCR** — TIM1's output-enable path, the physical power/gate-drive stage, or
the fan motor itself losing sync — not "software wrote 0% by mistake."

## Investigation summary

A full static review of `Application/` (main.c orchestration, `app_state`,
`thermal_control`, `fan_control`, the TIM/GPIO BSP layer, `pwm_repeater`,
`commands`, `telemetry`, `sys_time`, board/MSP init, IWDG, temperature sensing)
found **no software code path** that clears TIM1's fan PWM output while leaving
CCR intact, and no software path that specifically re-touches fan hardware at
the THROTTLING → HIGH boundary. Ruled out along the way:

- Clock Security System break coupling — CSS is never enabled in
  `system_clock_config()`.
- A stuck `SystemFault` path — `apply_fans()` forces fans ON during
  `SystemFault`, so it can't explain fans going OFF.
- IWDG reset — timeout (~2 s) is far longer than any blocking operation found;
  a real reset would also show `fan_str="0000"` and `state_str="BOOT"` in
  telemetry for about a second post-reset, which doesn't match the report.
- Register/pin conflicts between `fan_control` (TIM1, PA2/PA3/PA8/PA9) and
  `pwm_repeater` (TIM16/TIM17, PA0/PA1) — confirmed via `stm32c0xx_hal_msp.c`
  that each timer's GPIO AF config is a separate, correctly pin-masked
  `HAL_GPIO_Init()` call. No shared timer instance, no shared variables, no
  `#include` relationship between the two modules in either direction.

### Structural software gap found

`fan_control_sequential_open()` (`Application/fan_control/fan_control.c`) is a
**set-once** state machine: once all 4 channels reach `SeqStateComplete`, every
subsequent call is a no-op. Fan duty/output state is only ever rewritten when
`fan_control_all_off()` runs, which only happens when `apply_fans()` computes
`fans_on == false`. Since `ThermalHigh`, `ThermalThrottling`, and
`ThermalCritical` are **all** members of the `auto_on` set in `apply_fans()`
(`Application/main.c`), `fans_on` never goes false across a
Critical→Throttling→High excursion — so nothing in firmware ever rewrites
TIM1's hardware state during that whole window. There was no closed-loop
self-healing: if TIM1's output-enable state were ever disturbed by anything
external, nothing would notice or correct it, and even a button press
wouldn't help (it also just calls the same no-op function).

A related latent bug follows from the same set-once design and is still live:
`FAN<1-4>=OFF` (USB command) permanently desyncs a channel from the "should be
on" state, because `apply_fans()` only ever calls the no-op
`fan_control_sequential_open()` afterwards.

No firmware change has been made for either issue — verification of the root
cause comes first.

## Leading hypothesis: LCD backlight PWM dimming as a shared-rail noise source

The user identified the key physical correlation: **the LCD power rail and
backlight brightness both change specifically at these two transitions**, and
nothing else in the firmware does.

`apply_lcd_power()` (`main.c`) is the only thing forced off during
`ThermalCritical` and turned back on the instant it's left:

```c
static void apply_lcd_power(SystemState state, ThermalState thermal)
{
    bool lcd_on = (state == SystemRunning) && (thermal != ThermalCritical);
    board_lcd_power_set(lcd_on);
}
```

`board_lcd_power_set(true)` drives PB15 high, switching on Q18 (per
`board_config.h`: "PB15 → NPN Q18 → LCD +PWR_OUT, active HIGH") — a real power
switch closing, with associated inrush as the LCD's input capacitance and
backlight driver charge up.

Simultaneously, `apply_throttle()` steps the `pwm_repeater` output
(TIM16/TIM17 → PA0/PA1), which the code's own comments identify as the LCD
backlight dimming signal (`"BJT on output inverts: PA0 HIGH → LCD_PWM LOW"`,
`"DIM_PWM"` — `pwm_repeater.c`):

| Transition | LCD power (PB15 → Q18 → Q4 load switch, on +VIN 24 V) | Backlight dimming PWM duty |
|---|---|---|
| CRITICAL → THROTTLING | OFF → ON (inrush) | 0% → `settings->pwm_throttle_a/b` (default ~50%) |
| THROTTLING → HIGH | already ON, no change | ~50% → 100% |

(Per the schematic: `LCD_PWR_EN`/PB15 drives NPN Q18, which pulls the gate
of P-FET Q4 low to turn it on — Q4 is the actual high-side load switch
connecting `+VIN` to `+PWR_OUT`, the LCD module's 24 V supply. Q18 itself
carries only gate-drive current, not the LCD's load current.)

### The mechanism

Important clarification: `DIM_PWM` is a **logic-level control signal**, not a
power line. The signal itself carries no meaningful energy. The hypothesis
therefore depends entirely on how the LCD module's backlight driver implements
dimming:

- **Direct PWM dimming** (the common implementation): the driver chops the
  actual backlight LED current on/off at the dim frequency, following the
  signal 1:1. At THROTTLING (~50%), the LCD module's supply current is then a
  pulsed load at 160 Hz — **every on-edge is its own small inrush event**,
  recurring continuously for the entire THROTTLING dwell. At 100% duty
  (HIGH/LOW thermal states) the LED current is DC and the repeated transients
  stop. In this case the mechanism below holds, with the aggressor being the
  LCD module's supply current (through Q18), not the signal trace.
- **Analog dimming** (driver filters the PWM into a brightness level): the LED
  current is smooth at any duty, there is no chopped load, and this entire
  hypothesis is **dead** — the observed correlation would need a different
  explanation.

The state pattern itself argues that only the chopped-load version can be
right, if any: fans run fine in CRITICAL (LCD fully off — zero load) and fine
in HIGH (100% dim — *maximum* average load), failing only at ~50%. No
average-load/undersized-rail story fits that; only something unique to
intermediate duty — chopping/ripple — does.

If the fan supply or gate-drive rail shares any common point with the
backlight driver's rail (common bulk capacitor, common regulator, common
return path), this single mechanism explains every observed symptom together:

1. **All 4 fans die together** — the four drive circuits are independent
   (own TIM1 channel, own pin, own N-MOSFET), so if each fan stalled
   independently with probability *p*, all four together would occur at
   ~*p*⁴ — effectively never. Simultaneity forces a single common-point
   event deep enough to take out every fan when it occurs — a shared
   rail/reference disturbance, independent of TIM1's per-channel PWM
   signals (which stay correct the whole time — matching telemetry).
2. **Telemetry stays "ON" throughout** — the fault is entirely downstream of
   the MCU's TIM1 CCR register, which this mechanism never touches.
3. **~1-in-100 rarity** — the randomness lives in the *common* event, not in
   per-fan vulnerability (per-fan randomness could not produce simultaneity):
   whether the rail/reference disturbance during a given THROTTLING dwell
   happens to reach the depth/duration needed to take the fans out, versus
   the common case where it stays within what they tolerate.
4. **Reliable recovery exactly at THROTTLING → HIGH** — once duty hits 100%,
   the repeated on/off transients simply **stop occurring** — not because
   anything gets reset or re-enabled, but because the disturbance source no
   longer exists. A stalled fan then gets a clean, uninterrupted supply window
   and restarts on its own. This is why recovery is deterministic and
   immediate at that specific transition rather than "eventually, by luck."

The coupling path does not have to be the 24 V fan supply rail itself.
Correction from schematic review: the fan MOSFET gates are **not** driven
directly by TIM1 pins — each channel has its own dedicated low-side gate
driver IC (UCC27517, IC6–IC9) powered from a separate **+12 V gate-driver
rail** (`gate_driver_psu.SchDoc`, regulated down from +VIN by IC5). 100%
duty holds each driver's output — and the corresponding FET gate — at a
solid ~12 V, which gives far more V_GS enhancement margin than a bare 3.3 V
MCU pin would; a "3.3 V has little margin" version of this argument is
**not supported by the actual circuit** and is retracted. The viable
version of a reference-bounce mechanism has to act on the shared **+12 V
gate-driver rail or its return**, not on 3.3 V logic: since all four
UCC27517 drivers share the same +12 V supply (`+12V`, C18–C27 decoupling)
and the same ground plane as the LCD load switch and fan return paths, a
chopped LCD load current returning through a shared ground segment, or
loading the shared +12 V rail, would affect all four drivers' output high
level at once — potentially enough to reduce (not necessarily lose) FET
enhancement simultaneously across all 4 channels. This is a weaker
mechanism than the bare-3.3 V-gate version originally proposed, since 12 V
gate drive has more headroom to absorb a disturbance before a MOSFET
meaningfully de-enhances, and should be weighted accordingly during
verification.

This also explains why an MOE-register theory (all 4 channels sharing one
enable bit inside the MCU) is a weaker fit on its own: nothing in the
firmware ever re-arms TIM1 after boot, so a register-level fault would stay
stuck until fans were explicitly cycled off/on — it would not self-heal
exactly at the HIGH transition the way the backlight-PWM mechanism naturally
does. Broad GPIOA corruption (MODER/AFR) is similarly disfavored: PA0/PA1
(dim output) and PA6/PA7 (I2C — HDC2010) share the same port. Confirming
that HDC2010 values stay valid in telemetry during a stuck window would rule
port-wide corruption out entirely.

### Caveat

This requires two schematic/module-level facts that are not derivable from the
firmware repo and have not been confirmed with a scope or debugger:

1. The LCD module's backlight driver must do **direct PWM dimming** (chopped
   LED current), not analog dimming.
2. The fan drive path must share some physical point with the LCD load —
   either the supply side (common bulk capacitor, common regulator) or merely
   the return side (LCD load current flowing through a ground segment shared
   with the fan MOSFET sources / MCU ground reference).

## Suggested verification (no firmware change required)

Force `pwm_throttle_a` and `pwm_throttle_b` to `100` via the `SETTINGS=` or
`PWMTHR=` USB command, so THROTTLING drives the backlight at the same steady
100% duty as HIGH. If the fan dropout stops occurring (or becomes much rarer)
over an equivalent number of CRITICAL→THROTTLING cycles, that confirms the
backlight dimming PWM is the trigger, independent of any firmware change on
the fan side.

Hardware-level confirmation, if available:

- **Most decisive single measurement:** scope the LCD supply rail (downstream
  of Q18) during a THROTTLING dwell at ~50% dim. If the LCD's supply
  current/voltage is **not** chopped at 160 Hz, the module does analog dimming
  and the entire backlight hypothesis is ruled out. If it is chopped, the
  pulsed-load mechanism is real and the remaining question is only whether it
  couples into the fan rail.
- Scope PB15 (LCD power) and the backlight dimming output (PA0/PA1) together
  with the fan supply/gate-drive rail during a THROTTLING dwell, looking for
  ripple/sag correlated with each dimming PWM edge.
- For the ground-bounce variant: measure a fan MOSFET's V_GS directly at the
  FET (gate to source pin, not to MCU ground) during a THROTTLING dwell —
  160 Hz dips in V_GS with the fan supply rail steady would confirm a shared
  ground return as the coupling path.
- If a debugger can stay attached through a live reproduction, inspect
  `TIM1->BDTR` / `TIM1->CCER` at the moment fans are reported stuck — this
  would immediately confirm or rule out the MCU-register (MOE) explanation
  versus a purely downstream/analog one.

## 2026-08-11 review notes

Additional notes from a follow-up static-analysis pass over the same
symptom, recorded for comparison against the leading hypothesis above.

- Confirms the two structural code facts this doc relies on:
  `apply_fans()` treats CRITICAL/THROTTLING/HIGH identically (fans are
  never re-commanded across that whole window), and no runtime code path
  ever calls `tim_pwm_start()`/`fan_power_init()` again after boot.
- Considered and **rejects** a TIM1 register/clock corruption theory (e.g.
  via BKIN noise or a supply brownout clearing `CEN`/`MOE`). This is the
  "MOE-register theory" already disfavored above: a register-level fault
  would stay latched until fans were cycled rather than self-heal exactly
  at THROTTLING → HIGH, and CCR would no longer read back correctly —
  contradicting the telemetry fact that `get_fan_state_str()` reads the
  live `__HAL_TIM_GET_COMPARE` register, not a software flag. It also
  doesn't explain why all 4 independent channels fail together.
- Independently corroborates the shared-rail / LCD-power-switch
  correlation (`BOARD_LCD_PWR_EN` / PB15 turning on exactly at
  CRITICAL→THROTTLING) as the most plausible lead, consistent with the
  backlight dimming chopped-load mechanism already documented above —
  which is the piece that explains why HIGH (100% dim, steady load) is
  safe while THROTTLING (~50% dim, chopped load) is not, and CRITICAL (LCD
  off, zero load) is also safe.

No firmware change has been made as a result of this pass. The chopped-load
dimming hypothesis and verification plan above remain the standing lead.
