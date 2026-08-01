#include "app_state.h"
#include "board.h"
#include "board/board_config.h"
#include "commands.h"
#include "hdc2010.h"
#include "delay.h"
#include "fan_control.h"
#include "program_led.h"
#include "push_button.h"
#include "pwm_repeater.h"
#include "settings.h"
#include "stm32c0xx_hal.h"
#include "sys_time.h"
#include "system_temp.h"
#include "telemetry.h"
#include "temp_simulator.h"
#include "thermal_control.h"
#include "temperature_sensor.h"
#include "timers/timers.h"
#include "usb.h"
#include "watchdog.h"
#include <stdbool.h>
#include <stdint.h>

/* Tunables */
#define APP_FAN_PWM_FREQ_HZ  25000U
#define APP_INIT_TIMEOUT_MS  10000U

static Hdc2010 hdc2010_dev;

static millis_t boot_start_ms;

static void app_boot_init(void)
{
    boot_start_ms = millis();
}

static void handle_boot(const Settings *settings)
{
    int16_t t = system_temp_get();
    if (t != INT16_MIN)
    {
        app_state_enter_running(thermal_control_initial(t, settings));
        return;
    }
    if ((millis() - boot_start_ms) >= APP_INIT_TIMEOUT_MS)
    {
        app_state_enter_fault();
    }
}

static void handle_running(const Settings *settings)
{
    int16_t t = system_temp_get();
    if (t == INT16_MIN)
    {
        app_state_enter_fault();
        return;
    }
    ThermalState next = thermal_control_step(app_get_thermal_state(), t, settings);
    app_state_update_thermal(next);
}

static void handle_fault(const Settings *settings)
{
    int16_t t = system_temp_get();
    if (t != INT16_MIN)
    {
        app_state_enter_running(thermal_control_initial(t, settings));
    }
}

static void apply_throttle(SystemState state, ThermalState thermal, const Settings *settings)
{
    if (state != SystemRunning)
    {
        pwm_set_throttle_a(0U);
        pwm_set_throttle_b(0U);
        return;
    }

    switch (thermal)
    {
        case ThermalCritical:
            pwm_set_throttle_a(0U);
            pwm_set_throttle_b(0U);
            break;

        case ThermalThrottling:
            pwm_set_throttle_a(settings->pwm_throttle_a);
            pwm_set_throttle_b(settings->pwm_throttle_b);
            break;

        case ThermalLow:
        case ThermalHigh:
        default:
            pwm_set_throttle_a(100U);
            pwm_set_throttle_b(100U);
            break;
    }
}

static void apply_fans(SystemState state, ThermalState thermal, bool button_pressed)
{
    bool auto_on = ((state == SystemRunning) || (state == SystemFault)) &&
                   ((thermal == ThermalHigh) || (thermal == ThermalThrottling) || (thermal == ThermalCritical));
    bool fans_on = button_pressed || auto_on || (state == SystemFault);

    if (fans_on)
    {
        fan_control_sequential_open();
    }
    else
    {
        fan_control_all_off();
    }
}

static void apply_lcd_power(SystemState state, ThermalState thermal)
{
    bool lcd_on = (state == SystemRunning) && (thermal != ThermalCritical);
    board_lcd_power_set(lcd_on);
}

static void apply_program_led(SystemState state, ThermalState thermal)
{
    ProgramLedState led_state;

    if (state == SystemBoot)
    {
        led_state = ProgramLedLow;
    }
    else if (state == SystemFault)
    {
        led_state = ProgramLedError;
    }
    else
    {
        switch (thermal)
        {
            case ThermalCritical:
                led_state = ProgramLedCritical;
                break;
            case ThermalThrottling:
                led_state = ProgramLedThrottling;
                break;
            case ThermalHigh:
                led_state = ProgramLedHigh;
                break;
            case ThermalLow:
            default:
                led_state = ProgramLedLow;
                break;
        }
    }

    program_led_set_state(led_state);
}

static void app_task(void)
{
    const Settings *settings = settings_get();
    if (settings == NULL)
    {
        return;
    }

    switch (app_get_state())
    {
        case SystemBoot:
            handle_boot(settings);
            break;

        case SystemRunning:
            handle_running(settings);
            break;

        case SystemFault:
            handle_fault(settings);
            break;

        default:
            break;
    }

    SystemState  state   = app_get_state();
    ThermalState thermal = app_get_thermal_state();
    bool button_pressed  = push_button_is_pressed();

    apply_throttle(state, thermal, settings);
    apply_fans(state, thermal, button_pressed);
    apply_lcd_power(state, thermal);
    apply_program_led(state, thermal);
}

int main(void)
{
    /* Phase 1: MCU + basic services */
    HAL_Init();
    board_init();
    timers_init();
    delay_init(timers_get_sys_timer());
    watchdog_init();

    /* Phase 2: Persisted settings */
    settings_init();

    /* Phase 3: USB up early so enumeration can start; nothing below blocks */
    usb_init();
    telemetry_init();
    commands_init();
    hdc2010_init(&hdc2010_dev, board_get_i2c(), HDC2010_ADDR_LOW);

    /* Phase 4: I/O */
    program_led_init();
    push_button_init();

    pwm_repeater_init(timers_get_capture(), timers_get_repeater_a(), timers_get_repeater_b());

    board_onewire_power_set(true);
    board_onewire_pullup_set(true);
    temperature_sensor_init();
    temp_simulator_init();

    fan_control_init(timers_get_fan_power(), timers_get_fan_remote());
    fan_init(APP_FAN_PWM_FREQ_HZ);

    /* Start with all fans off; thermal SM takes over from first iteration */
    fan_control_all_off();

    app_state_init();
    thermal_control_init();
    app_boot_init();

    /* Phase 5: Cooperative main loop */
    while (true)
    {
        watchdog_kick();
        usb_task();
        commands_task();
        push_button_task();
        pwm_repeater_task();
        temperature_sensor_task();
        hdc2010_task();
        program_led_task();
        telemetry_task();

        app_task();
    }
}
