#ifndef TEMP_SIMULATOR_H
#define TEMP_SIMULATOR_H

#ifdef __cplusplus
extern "C"
{
#endif

#include <stdint.h>
#include <stdbool.h>

    /**
     * @brief Initialize the temperature simulator.
     *        Call once at startup.
     */
    void temp_simulator_init(void);

    /**
     * @brief Enable or disable temperature simulation.
     */
    void temp_simulator_set_enabled(bool enabled);

    /**
     * @brief Check if temperature simulation is currently enabled.
     */
    bool temp_simulator_is_enabled(void);

    /**
     * @brief Get the simulated temperature in centidegrees (°C × 100).
     *        Returns INT16_MIN if simulator is disabled.
     *
     * Cycles through: Critical (75°C) → Throttling (58°C) → High (40°C) → Low (32°C)
     *                 → High (40°C) → Throttling (58°C) → Critical (75°C) → repeat
     *
     * Each state lasts 500ms, so a full cycle is 3.5 seconds.
     */
    int16_t temp_simulator_get(void);

#ifdef __cplusplus
}
#endif

#endif /* TEMP_SIMULATOR_H */
