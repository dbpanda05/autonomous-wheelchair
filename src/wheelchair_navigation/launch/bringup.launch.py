"""
bringup.launch.py — Project Saarthi autonomous wheelchair

Launch arguments
----------------
use_hardware   [true]    When true, start serial_bridge_node (Pi ↔ ESP32 UART).
mode           [mapping] 'mapping'    — SLAM Toolbox (build a new map).
                         'navigation' — map_server + AMCL + Nav2 (navigate with saved map).
map            ['']      Absolute path to a saved .yaml map file.
                         Required when mode=navigation.

Nodes / launches included
--------------------------
Always:
  robot_state_publisher  (URDF → /robot_description, joint TFs)
  sllidar_ros2           (RPLidar A1 → /scan)

When use_hardware=true:
  serial_bridge_node     (UART bridge; publishes /odom and odom→base_link TF)

When mode=mapping:
  slam_toolbox online_async_launch.py

When mode=navigation AND map != '':
  nav2_bringup bringup_launch.py  (map_server + AMCL + full Nav2 stack)

Note: the static_transform_publisher (odom→base_link) that was present as a
workaround has been removed — the real TF now comes from serial_bridge_node.
"""

import os

from ament_index_python.packages import get_package_share_directory

from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    IncludeLaunchDescription,
    OpaqueFunction,
)
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PythonExpression
from launch_ros.actions import Node


def _generate_nodes(context, *args, **kwargs):
    """OpaqueFunction that builds the conditional node list at launch time."""
    pkg_share = get_package_share_directory('wheelchair_navigation')

    use_hardware = LaunchConfiguration('use_hardware').perform(context).lower()
    mode = LaunchConfiguration('mode').perform(context).lower()
    map_path = LaunchConfiguration('map').perform(context)

    nodes = []

    # ── serial_bridge_node (hardware only) ────────────────────────────────────
    if use_hardware in ('true', '1', 'yes'):
        nodes.append(
            Node(
                package='wheelchair_navigation',
                executable='serial_bridge_node',
                name='serial_bridge_node',
                output='screen',
                parameters=[{
                    'serial_port': '/dev/ttyUSB1',
                    'baud_rate': 115200,
                    'ticks_per_rev': 15,
                    'wheel_radius': 0.127,
                    'wheel_separation': 0.738,
                }],
            )
        )

    # ── SLAM Toolbox (mapping mode) ───────────────────────────────────────────
    if mode == 'mapping':
        slam_params_file = os.path.join(pkg_share, 'config', 'mapper_params_online.yaml')
        nodes.append(
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource([
                    os.path.join(
                        get_package_share_directory('slam_toolbox'),
                        'launch',
                        'online_async_launch.py',
                    )
                ]),
                launch_arguments={
                    'slam_params_file': slam_params_file,
                    'use_sim_time': 'false',
                }.items(),
            )
        )

    # ── Nav2 (navigation mode with saved map) ─────────────────────────────────
    elif mode == 'navigation':
        if not map_path:
            import sys
            print(
                '[bringup.launch.py] ERROR: mode=navigation requires the "map" argument '
                '(path to a saved .yaml map file). Exiting.',
                file=sys.stderr,
            )
            raise RuntimeError('map argument is required for navigation mode')

        nav2_params_file = os.path.join(pkg_share, 'config', 'nav2_params.yaml')
        nodes.append(
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource([
                    os.path.join(
                        get_package_share_directory('nav2_bringup'),
                        'launch',
                        'bringup_launch.py',
                    )
                ]),
                launch_arguments={
                    'map': map_path,
                    'use_sim_time': 'false',
                    'params_file': nav2_params_file,
                    'autostart': 'true',
                }.items(),
            )
        )
    else:
        import sys
        print(
            f'[bringup.launch.py] WARNING: unknown mode "{mode}". '
            'Valid values are "mapping" and "navigation".',
            file=sys.stderr,
        )

    return nodes


def generate_launch_description():
    pkg_share = get_package_share_directory('wheelchair_navigation')
    urdf_file = os.path.join(pkg_share, 'urdf', 'wheelchair.urdf')

    with open(urdf_file, 'r') as fh:
        robot_desc = fh.read()

    # ── Declare arguments ─────────────────────────────────────────────────────
    declare_use_hardware = DeclareLaunchArgument(
        'use_hardware',
        default_value='true',
        description='Start serial_bridge_node to communicate with the ESP32.',
    )
    declare_mode = DeclareLaunchArgument(
        'mode',
        default_value='mapping',
        description='"mapping" runs slam_toolbox; "navigation" runs Nav2 with a saved map.',
    )
    declare_map = DeclareLaunchArgument(
        'map',
        default_value='',
        description='Absolute path to a saved .yaml map file (required for mode=navigation).',
    )

    # ── Always-on nodes ───────────────────────────────────────────────────────
    robot_state_publisher_node = Node(
        package='robot_state_publisher',
        executable='robot_state_publisher',
        output='screen',
        parameters=[{'robot_description': robot_desc}],
    )

    sllidar_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([
            os.path.join(
                get_package_share_directory('sllidar_ros2'),
                'launch',
                'sllidar_a1_launch.py',
            )
        ]),
        launch_arguments={'frame_id': 'lidar_link'}.items(),
    )

    return LaunchDescription([
        declare_use_hardware,
        declare_mode,
        declare_map,
        robot_state_publisher_node,
        sllidar_launch,
        OpaqueFunction(function=_generate_nodes),
    ])
