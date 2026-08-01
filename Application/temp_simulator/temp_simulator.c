#include "temp_simulator.h"
#include "sys_time.h"
#include <stdbool.h>

typedef enum
{
    SimStateCritical,    /* 75°C */
    SimStateThrottling,  /* 58°C */
    SimStateHigh,        /* 40°C */
    SimStateLow,         /* 32°C */
} SimState;

static struct
{
    bool enabled;
    SimState state;
    millis_t state_start_ms;
} sim_ctx = {.enabled = false, .state = SimStateCritical, .state_start_ms = 0};

/* Temperature values in centidegrees for each state */
static const int16_t sim_temps[] = {
    7510,   /* SimStateCritical */
    5850,   /* SimStateThrottling */
    4500,   /* SimStateHigh */
    3100,   /* SimStateLow */
};

#define STEP_INTERVAL_MS  500U

void temp_simulator_init(void)
{
    sim_ctx.enabled = true;
    sim_ctx.state = SimStateCritical;
    sim_ctx.state_start_ms = millis();
}

void temp_simulator_set_enabled(bool enabled)
{
    sim_ctx.enabled = enabled;
    if (enabled)
    {
        sim_ctx.state = SimStateCritical;
        sim_ctx.state_start_ms = millis();
    }
}

bool temp_simulator_is_enabled(void)
{
    return sim_ctx.enabled;
}

int16_t temp_simulator_get(void)
{
    if (!sim_ctx.enabled)
    {
        return INT16_MIN;
    }

    millis_t now = millis();
    millis_t elapsed = now - sim_ctx.state_start_ms;

    if (elapsed >= STEP_INTERVAL_MS)
    {
        /* Cycle through: Critical → Throttling → High → Low → High → Throttling → repeat */
        static const SimState cycle[] = {
            SimStateCritical,   /* 0 */
            SimStateThrottling, /* 1 */
            SimStateHigh,       /* 2 */
            SimStateLow,        /* 3 */
            SimStateHigh,       /* 4 */
            SimStateThrottling, /* 5 */
            /* Then back to Critical on the next cycle */
        };
        static const uint32_t cycle_len = sizeof(cycle) / sizeof(cycle[0]);
        static uint32_t cycle_index = 0;

        sim_ctx.state = cycle[cycle_index];
        cycle_index = (cycle_index + 1) % cycle_len;
        sim_ctx.state_start_ms = now;
    }

    return sim_temps[sim_ctx.state];
}
