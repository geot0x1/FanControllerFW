# Bug: Intermittent all-fans-stop on CRITICAL → THROTTLING exit

**Status:** Root cause unconfirmed — verification pending. No firmware changes
made. Primary suspected cause is electrical, not software.

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

| Transition | LCD power (PB15/Q18) | Backlight dimming PWM duty |
|---|---|---|
| CRITICAL → THROTTLING | OFF → ON (inrush) | 0% → `settings->pwm_throttle_a/b` (default ~50%) |
| THROTTLING → HIGH | already ON, no change | ~50% → 100% |

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

The coupling path does not have to be the fan supply rail itself. The
N-MOSFET gates are driven directly from TIM1 pins at a constant 3.3 V (100%
duty = pin statically high), so V_GS of all four FETs rides on the difference
between the MCU's 3.3 V/ground and the power stage's source/ground. A chopped
LCD load current returning through a shared ground segment would bounce that
reference at 160 Hz for all four FETs at once — partially de-enhancing every
fan MOSFET simultaneously without the fan supply rail ever sagging. 3.3 V
gate drive typically has little enhancement margin, so a ~1 V reference
shift is already significant. This variant requires only a shared ground
return, a much weaker schematic precondition than a shared supply rail.

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
