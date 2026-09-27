cmake_minimum_required(VERSION 3.5)

set(TARGET_NAME csv_inhaler)

set(EXTENSION_NAME ${TARGET_NAME}_extension)
set(LOADABLE_EXTENSION_NAME ${TARGET_NAME}_loadable_extension)

project(${TARGET_NAME})

set(CMAKE_CXX_STANDARD "17" CACHE STRING "C++ standard to enforce")
set(CMAKE_CXX_STANDARD_REQUIRED ON)

include_directories(src/include)
# DuckDB ships header-only cpp-httplib (namespace duckdb_httplib) and yyjson; we reuse both.
include_directories(${CMAKE_CURRENT_SOURCE_DIR}/duckdb/third_party/httplib)
include_directories(${CMAKE_CURRENT_SOURCE_DIR}/duckdb/third_party/yyjson/include)

set(EXTENSION_SOURCES
    src/csv_inhaler_extension.cpp
    src/csv_split.cpp
    src/ollaya_client.cpp
    src/repair.cpp)

build_static_extension(${TARGET_NAME} ${EXTENSION_SOURCES})
build_loadable_extension(${TARGET_NAME} " " ${EXTENSION_SOURCES})

install(
  TARGETS ${EXTENSION_NAME}
  EXPORT "${DUCKDB_EXPORT_SET}"
  LIBRARY DESTINATION "${INSTALL_LIB_DIR}"
  ARCHIVE DESTINATION "${INSTALL_LIB_DIR}")
