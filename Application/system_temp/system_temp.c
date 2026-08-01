#include "system_temp.h"
#include "temperature_sensor.h"
#include "hdc2010.h"
#include "temp_simulator.h"

int16_t system_temp_get(void)
{
    /* Check if temperature simulator is active */
    int16_t sim_temp = temp_simulator_get();
    if (sim_temp != INT16_MIN)
    {
        return sim_temp;
    }

    int16_t ds = get_temperature();
    int16_t hdc = hdc2010_get_temp();

    if (hdc == INT16_MIN)
    {
        return ds;
    }
    if (ds == INT16_MIN)
    {
        return hdc;
    }
    return ds > hdc ? ds : hdc;
}
