add_library(system_temp STATIC system_temp.c)
target_include_directories(system_temp PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(system_temp PUBLIC temperature_sensor hdc2010 bsp_i2c)
