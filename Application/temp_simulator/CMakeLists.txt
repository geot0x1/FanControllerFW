add_library(temp_simulator STATIC)
target_sources(temp_simulator PRIVATE temp_simulator.h temp_simulator.c)
target_include_directories(temp_simulator PUBLIC .)
target_link_libraries(temp_simulator PRIVATE sys_time)
