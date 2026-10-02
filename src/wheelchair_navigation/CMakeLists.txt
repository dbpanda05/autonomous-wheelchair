cmake_minimum_required(VERSION 3.8)
project(wheelchair_navigation)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

find_package(ament_cmake REQUIRED)
find_package(ament_cmake_python REQUIRED)

# Install share directories (URDF, launch files, config files)
install(
  DIRECTORY urdf launch config
  DESTINATION share/${PROJECT_NAME}
)

# Install the Python module package
ament_python_install_package(${PROJECT_NAME})

# Install serial_bridge_node.py as an executable entry point
install(
  PROGRAMS
    wheelchair_navigation/serial_bridge_node.py
  DESTINATION lib/${PROJECT_NAME}
  RENAME serial_bridge_node
)

ament_package()
