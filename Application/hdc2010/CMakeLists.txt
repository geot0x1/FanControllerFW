add_library(hdc2010 STATIC)
target_sources(hdc2010 PRIVATE hdc2010.h hdc2010.c)
target_include_directories(hdc2010 PUBLIC .)
target_link_libraries(hdc2010 PRIVATE bsp_i2c config stm32cubemx sys_time temp_simulator)
