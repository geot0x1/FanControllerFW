# Bug: Intermittent all-fans-stop on CRITICAL → THROTTLING exit

**Status:** Root cause unconfirmed (requires hardware verification). One defensive
firmware mitigation applied. Primary suspected cause is electrical, not software.

## Symptom

Reported by the user:

> When the device exits from CRITICAL state and enters THROTTLING state, fans
> stop. All of them. Even if button is pressed, they will not work. Telemetry
> reports all fans are ON however. But they are OFF. When the device exits
> THROTTLING and enters HIGH_TEMP, fans are ON again. This does not happen
> often — roughly once every 100 transitions or less.

Key facts:

- All 4 fan units stop **simultaneously**.
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

### Structural software gap found (fixed regardless of root cause)

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

**Fix applied:** `fan_control.c` now periodically re-arms each channel (via
`tim_pwm_start()`, which re-enables the channel/MOE, not just the duty
register) every ~1 s while fans are supposed to be on, even after
`SeqStateComplete` is reached. This closes the robustness gap and, as a side
effect, also fixes a related latent bug where `FAN<1-4>=OFF` (USB command)
could permanently desync a channel from the "should be on" state.

This fix defends against an MCU-register-level disturbance (e.g. TIM1's shared
`BDTR.MOE` bit — a single point that, if cleared, would explain all 4 channels
dying together instantly). It does **not** address a fault that lives entirely
downstream of the MCU (external gate driver / motor stage), if that turns out
to be the real mechanism — see below.

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

At THROTTLING, the backlight's dimming PWM is actively duty-cycling — its
driver/boost stage is being repeatedly switched on and off at the dimming
frequency, and **every on-edge is its own small inrush event**, recurring
continuously for the entire THROTTLING dwell. At 100% duty (HIGH/LOW thermal
states), the dimming PWM is effectively DC — the driver runs continuously with
no more repeated on/off transients.

If the fan supply or gate-drive rail shares any common point with the
backlight driver's rail (common bulk capacitor, common regulator, common
return path), this single mechanism explains every observed symptom together:

1. **All 4 fans die together** — a shared rail disturbance hits all 4 fan
   channels' power stage at once, independent of TIM1's per-channel PWM
   signals (which stay correct the whole time — matching telemetry).
2. **Telemetry stays "ON" throughout** — the fault is entirely downstream of
   the MCU's TIM1 CCR register, which this mechanism never touches.
3. **~1-in-100 rarity** — not a single rare digital glitch, but a statistical
   threshold: whether the cumulative rail ripple during a given THROTTLING
   dwell happens to be deep/long enough, at a moment a fan is vulnerable
   (e.g. mid-commutation on a 2-wire fan with no hall feedback), to actually
   stall it — rather than the more common case where fans just tolerate the
   ripple.
4. **Reliable recovery exactly at THROTTLING → HIGH** — once duty hits 100%,
   the repeated on/off transients simply **stop occurring** — not because
   anything gets reset or re-enabled, but because the disturbance source no
   longer exists. A stalled fan then gets a clean, uninterrupted supply window
   and restarts on its own. This is why recovery is deterministic and
   immediate at that specific transition rather than "eventually, by luck."

This also explains why an MOE-register theory (all 4 channels sharing one
enable bit inside the MCU) is a weaker fit on its own: nothing in the
*original* (pre-fix) firmware ever re-armed TIM1 after boot, so a
register-level fault would have stayed stuck until fans were explicitly
cycled off/on — it would not have self-healed exactly at the HIGH transition
the way the backlight-PWM mechanism naturally does.

### Caveat

This requires the fan supply/gate-drive rail to actually share some physical
point with the backlight driver's rail — that's a schematic-level fact, not
something derivable from the firmware repo. It has not been confirmed with a
scope or debugger.

## Suggested verification (no firmware change required)

Force `pwm_throttle_a` and `pwm_throttle_b` to `100` via the `SETTINGS=` or
`PWMTHR=` USB command, so THROTTLING drives the backlight at the same steady
100% duty as HIGH. If the fan dropout stops occurring (or becomes much rarer)
over an equivalent number of CRITICAL→THROTTLING cycles, that confirms the
backlight dimming PWM is the trigger, independent of any firmware change on
the fan side.

Hardware-level confirmation, if available:

- Scope PB15 (LCD power) and the backlight dimming output (PA0/PA1) together
  with the fan supply/gate-drive rail during a THROTTLING dwell, looking for
  ripple/sag correlated with each dimming PWM edge.
- If a debugger can stay attached through a live reproduction, inspect
  `TIM1->BDTR` / `TIM1->CCER` at the moment fans are reported stuck — this
  would immediately confirm or rule out the MCU-register (MOE) explanation
  versus a purely downstream/analog one.

## If confirmed: fix direction

The mitigation already applied (periodic TIM1 re-arm in `fan_control.c`) is
worth keeping regardless — it closes a real self-healing gap and an unrelated
`FAN=OFF` command bug — but it does not address a downstream/analog fault. If
the shared-rail/backlight-PWM mechanism is confirmed, the actual fix is
hardware-side: separate or better-decouple the fan power/gate-drive rail from
the backlight driver's rail, add bulk capacitance at the point of common
coupling, or soften/slow the backlight dimming PWM's transition rate to
reduce the repeated inrush during THROTTLING.
