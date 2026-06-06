#ifndef CONFIG_H
#define CONFIG_H

/* ============================================================================
 * PRODUCT CONFIGURATION
 * Adjust these values to tune the firmware for a specific product variant.
 * ============================================================================ */

/* ── Thermal control defaults ────────────────────────────────────────────── */

#define CONFIG_TEMP_MAX                     180U  /* °C — absolute maximum allowed temperature */

/* Plausibility bounds for raw sensor readings (centidegrees, signed).
 * Readings outside this range are treated as invalid and counted as failures. */
#define CONFIG_SENSOR_TEMP_MIN_CDEG         (-4000)  /* -40.00 °C */
#define CONFIG_SENSOR_TEMP_MAX_CDEG         (15000)  /* 150.00 °C */

#define CONFIG_TEMP_CRITICAL_DEFAULT        75U   /* °C — overheat shutdown threshold */
#define CONFIG_TEMP_THROTTLE_ON_DEFAULT     58U   /* °C — PWM throttle engages above this */
#define CONFIG_TEMP_FAN_ON_DEFAULT          40U   /* °C — fans turn ON above this */
#define CONFIG_TEMP_FAN_OFF_DEFAULT         32U   /* °C — fans turn OFF below this */

/* ── PWM throttle defaults ───────────────────────────────────────────────── */

#define CONFIG_PWM_THROTTLE_A_DEFAULT       50U   /* % — throttle duty cycle, channel A */
#define CONFIG_PWM_THROTTLE_B_DEFAULT       50U   /* % — throttle duty cycle, channel B */

/* ── Telemetry ───────────────────────────────────────────────────────────── */

#define CONFIG_TELEMETRY_INTERVAL_MS        1000U     /* ms — reporting interval */


#endif /* CONFIG_H */
