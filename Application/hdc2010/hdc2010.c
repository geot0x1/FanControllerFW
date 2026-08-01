#include "hdc2010.h"
#include "config.h"
#include "sys_time.h"
#include "temp_simulator.h"
#include <stdbool.h>
#include <stddef.h>

/* ── Definitions ─────────────────────────────────────────────────────────── */

#define HDC2010_POLL_INTERVAL_MS 1000U
#define HDC2010_MAX_FAILURES     3U

/* Register map */
#define REG_TEMP_LOW             0x00U
#define REG_TEMP_HIGH            0x01U
#define REG_HUM_LOW              0x02U
#define REG_HUM_HIGH             0x03U
#define REG_INTR_DRDY            0x04U
#define REG_TEMP_MAX             0x05U
#define REG_HUM_MAX              0x06U
#define REG_INTR_EN              0x07U
#define REG_TEMP_OFFSET          0x08U
#define REG_HUM_OFFSET           0x09U
#define REG_TEMP_THR_L           0x0AU
#define REG_TEMP_THR_H           0x0BU
#define REG_RH_THR_L             0x0CU
#define REG_RH_THR_H             0x0DU
#define REG_RST_DRDY_INT         0x0EU
#define REG_MEAS_CFG             0x0FU
#define REG_MFR_ID_LOW           0xFCU
#define REG_MFR_ID_HIGH          0xFDU
#define REG_DEV_ID_LOW           0xFEU
#define REG_DEV_ID_HIGH          0xFFU

/* Expected device ID: low byte 0xD0, high byte 0x07 → combined 0x07D0 */
#define HDC2010_DEV_ID_LOW       0xD0U
#define HDC2010_DEV_ID_HIGH      0x07U

/* MEAS_CFG: one-shot, temp + humidity, 14-bit resolution */
#define MEAS_CFG_ONESHOT         0x01U

#define I2C_TIMEOUT_MS           10U
#define CONVERSION_WAIT_MS       5U

#define HAL_ADDR(a)              ((uint16_t)((a) << 1))

/* ── Typedefs ────────────────────────────────────────────────────────────── */

typedef enum
{
    Hdc2010Idle,
    Hdc2010Waiting,
} Hdc2010PollState;

/* ── Static globals ──────────────────────────────────────────────────────── */

static int16_t          cached_temp  = INT16_MIN;
static uint8_t          cached_rh    = 0xFFU;
static bool             cached_valid = false;
static uint8_t          fail_count   = 0U;

static Hdc2010         *dev_handle   = NULL;
static Hdc2010PollState poll_state   = Hdc2010Idle;
static millis_t         trigger_ms   = 0U;
static millis_t         poll_ms      = 0U;

/* ── Static function declarations ────────────────────────────────────────── */

static Hdc2010Err read_reg(Hdc2010 *dev, uint8_t reg, uint8_t *out);
static Hdc2010Err write_reg(Hdc2010 *dev, uint8_t reg, uint8_t val);
static void       reset_state(void);
static void       commit_reading(int16_t temp_cdeg, uint8_t rh_pct);
static void       note_failure(void);
static bool       poll_interval_elapsed(millis_t now);
static bool       conversion_ready(millis_t now);
static void       enter_waiting(millis_t now);
static void       enter_idle(millis_t now);
static void       handle_idle(millis_t now);
static void       handle_waiting(millis_t now);

/* ── Public API ──────────────────────────────────────────────────────────── */

Hdc2010Err hdc2010_init(Hdc2010 *dev, I2c *i2c, uint8_t addr)
{
    if (dev == NULL || i2c == NULL)
    {
        return HDC2010_ERR_ARG;
    }
    if (addr != HDC2010_ADDR_LOW && addr != HDC2010_ADDR_HIGH)
    {
        return HDC2010_ERR_ARG;
    }

    dev->i2c  = i2c;
    dev->addr = addr;

    uint8_t id_low;
    uint8_t id_high;
    if (read_reg(dev, REG_DEV_ID_LOW, &id_low) != HDC2010_OK)
    {
        return HDC2010_ERR_NOT_FOUND;
    }
    if (read_reg(dev, REG_DEV_ID_HIGH, &id_high) != HDC2010_OK)
    {
        return HDC2010_ERR_NOT_FOUND;
    }

    if (id_low != HDC2010_DEV_ID_LOW || id_high != HDC2010_DEV_ID_HIGH)
    {
        return HDC2010_ERR_NOT_FOUND;
    }

    /* Clear cache and SM state so a re-init does not leak readings or
     * timestamps from a previous session. */
    reset_state();
    dev_handle = dev;
    return HDC2010_OK;
}

Hdc2010Err hdc2010_start_measurement(Hdc2010 *dev)
{
    if (dev == NULL)
    {
        return HDC2010_ERR_ARG;
    }
    return write_reg(dev, REG_MEAS_CFG, MEAS_CFG_ONESHOT);
}

Hdc2010Err hdc2010_read(Hdc2010 *dev, int16_t *temperature_cdeg, uint8_t *humidity_pct)
{
    if (dev == NULL || temperature_cdeg == NULL || humidity_pct == NULL)
    {
        return HDC2010_ERR_ARG;
    }

    uint8_t t_lo;
    uint8_t t_hi;
    if (read_reg(dev, REG_TEMP_LOW, &t_lo) != HDC2010_OK)
    {
        return HDC2010_ERR_I2C;
    }
    if (read_reg(dev, REG_TEMP_HIGH, &t_hi) != HDC2010_OK)
    {
        return HDC2010_ERR_I2C;
    }

    uint8_t h_lo;
    uint8_t h_hi;
    if (read_reg(dev, REG_HUM_LOW, &h_lo) != HDC2010_OK)
    {
        return HDC2010_ERR_I2C;
    }
    if (read_reg(dev, REG_HUM_HIGH, &h_hi) != HDC2010_OK)
    {
        return HDC2010_ERR_I2C;
    }

    uint16_t temp_raw = (uint16_t)((uint16_t)t_hi << 8) | t_lo;
    uint16_t rh_raw   = (uint16_t)((uint16_t)h_hi << 8) | h_lo;

    /* T(cdeg) = raw * 165 * 100 / 65536 - 4000 */
    *temperature_cdeg = (int16_t)(((uint32_t)temp_raw * 16500UL) / 65536UL) - 4000;
    /* RH(%) = raw * 100 / 65536 */
    *humidity_pct     = (uint8_t)(((uint32_t)rh_raw * 100UL) / 65536UL);

    return HDC2010_OK;
}

int16_t hdc2010_get_temp(void)
{
    int16_t sim_temp = temp_simulator_get();
    if (sim_temp != INT16_MIN)
    {
        return sim_temp;
    }

    if (!cached_valid)
    {
        return INT16_MIN;
    }
    return cached_temp;
}

uint8_t hdc2010_get_rh(void)
{
    if (!cached_valid)
    {
        return 0xFFU;
    }
    return cached_rh;
}

void hdc2010_task(void)
{
    if (dev_handle == NULL)
    {
        return;
    }

    millis_t now = millis();

    switch (poll_state)
    {
        case Hdc2010Idle:
            handle_idle(now);
            break;

        case Hdc2010Waiting:
            handle_waiting(now);
            break;

        default:
            enter_idle(now);
            break;
    }
}

/* ── Static helpers ──────────────────────────────────────────────────────── */

static Hdc2010Err read_reg(Hdc2010 *dev, uint8_t reg, uint8_t *out)
{
    I2cErr err = i2c_mem_read(dev->i2c, HAL_ADDR(dev->addr),
                              reg, I2C_MEMADD_SIZE_8BIT, out, 1, I2C_TIMEOUT_MS);
    return (err == I2C_OK) ? HDC2010_OK : HDC2010_ERR_I2C;
}

static Hdc2010Err write_reg(Hdc2010 *dev, uint8_t reg, uint8_t val)
{
    I2cErr err = i2c_mem_write(dev->i2c, HAL_ADDR(dev->addr),
                               reg, I2C_MEMADD_SIZE_8BIT, &val, 1, I2C_TIMEOUT_MS);
    return (err == I2C_OK) ? HDC2010_OK : HDC2010_ERR_I2C;
}

static void reset_state(void)
{
    cached_temp  = INT16_MIN;
    cached_rh    = 0xFFU;
    cached_valid = false;
    fail_count   = 0U;
    poll_state   = Hdc2010Idle;
    trigger_ms   = 0U;
    poll_ms      = 0U;
}

static void commit_reading(int16_t temp_cdeg, uint8_t rh_pct)
{
    cached_temp  = temp_cdeg;
    cached_rh    = rh_pct;
    cached_valid = true;
    fail_count   = 0U;
}

static void note_failure(void)
{
    if (fail_count < HDC2010_MAX_FAILURES)
    {
        fail_count++;
    }
    if (fail_count >= HDC2010_MAX_FAILURES)
    {
        cached_valid = false;
        cached_temp  = INT16_MIN;
        cached_rh    = 0xFFU;
    }
}

static bool poll_interval_elapsed(millis_t now)
{
    return (now - poll_ms) >= HDC2010_POLL_INTERVAL_MS;
}

static bool conversion_ready(millis_t now)
{
    return (now - trigger_ms) >= CONVERSION_WAIT_MS;
}

static void enter_waiting(millis_t now)
{
    trigger_ms = now;
    poll_state = Hdc2010Waiting;
}

static void enter_idle(millis_t now)
{
    poll_ms    = now;
    poll_state = Hdc2010Idle;
}

static void handle_idle(millis_t now)
{
    if (!poll_interval_elapsed(now))
    {
        return;
    }

    if (hdc2010_start_measurement(dev_handle) != HDC2010_OK)
    {
        note_failure();
        poll_ms = now; /* stay in Idle; retry next interval */
        return;
    }

    enter_waiting(now);
}

static void handle_waiting(millis_t now)
{
    if (!conversion_ready(now))
    {
        return;
    }

    int16_t temp = 0;
    uint8_t rh   = 0;
    /* Only a paired (temp, rh) success counts as a valid reading, and only if
     * the temperature is within the plausible sensor range. */
    if (hdc2010_read(dev_handle, &temp, &rh) == HDC2010_OK &&
        temp >= CONFIG_SENSOR_TEMP_MIN_CDEG &&
        temp <= CONFIG_SENSOR_TEMP_MAX_CDEG)
    {
        commit_reading(temp, rh);
    }
    else
    {
        note_failure();
    }

    enter_idle(now);
}
