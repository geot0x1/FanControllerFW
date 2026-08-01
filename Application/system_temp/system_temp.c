#include "system_temp.h"
#include "temperature_sensor.h"
#include "hdc2010.h"

int16_t system_temp_get(void)
{
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
