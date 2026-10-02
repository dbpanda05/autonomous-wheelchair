"""
sim_launch.py — Project Saarthi Gazebo Harmonic simulation

Starts:
  1. Gazebo Harmonic with saarthi_arena.sdf world
  2. robot_state_publisher (URDF → /robot_description)
  3. ros_gz_sim entity spawner (drops wheelchair into Gazebo)
  4. ros_gz_bridge (bridges /cmd_vel, /odom, /scan, /tf, /clock)
  5. SLAM Toolbox  (when mode:=mapping, default)
  6. Nav2 bringup  (when mode:=navigation  AND map:=<path>)

Launch args
-----------
mode   [mapping]     'mapping' | 'navigation'
map    ['']          path to saved .yaml map  (required for navigation mode)

Quick start
-----------
  # Build once:
  colcon build --packages-select wheelchair_navigation
  source install/setup.bash

  # Mapping run:
  ros2 launch wheelchair_navigation sim_launch.py

  # Save map then navigate:
  ros2 run nav2_map_server map_saver_cli -f ~/saarthi_map
  ros2 launch wheelchair_navigation sim_launch.py mode:=navigation map:=/home/user/saarthi_map.yaml
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    ExecuteProcess,
    IncludeLaunchDescription,
    OpaqueFunction,
    TimerAction,
)
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def _conditional_nodes(context, *args, **kwargs):
    pkg_share = get_package_share_directory('wheelchair_navigation')
    mode = LaunchConfiguration('mode').perform(context).lower()
    map_path = LaunchConfiguration('map').perform(context)

    nodes = []

    if mode == 'mapping':
        slam_params = os.path.join(pkg_share, 'config', 'mapper_params_online.yaml')
        nodes.append(
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource([
                    os.path.join(
                        get_package_share_directory('slam_toolbox'),
                        'launch', 'online_async_launch.py',
                    )
                ]),
                launch_arguments={
                    'slam_params_file': slam_params,
                    'use_sim_time': 'true',
                }.items(),
            )
        )

    elif mode == 'navigation':
        if not map_path:
            import sys
            print(
                '[sim_launch.py] ERROR: mode=navigation requires map:=<path>',
                file=sys.stderr,
            )
            raise RuntimeError('map argument required for navigation mode')

        nav2_params = os.path.join(pkg_share, 'config', 'nav2_params.yaml')
        nodes.append(
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource([
                    os.path.join(
                        get_package_share_directory('nav2_bringup'),
                        'launch', 'bringup_launch.py',
                    )
                ]),
                launch_arguments={
                    'map': map_path,
                    'use_sim_time': 'true',
                    'params_file': nav2_params,
                    'autostart': 'true',
                }.items(),
            )
        )

    return nodes


def generate_launch_description():
    pkg_share = get_package_share_directory('wheelchair_navigation')

    urdf_file  = os.path.join(pkg_share, 'urdf', 'wheelchair.urdf')
    world_file = os.path.join(pkg_share, 'worlds', 'saarthi_arena.sdf')

    with open(urdf_file, 'r') as fh:
        robot_desc = fh.read()

    # ── Args ──────────────────────────────────────────────────────────────────
    declare_mode = DeclareLaunchArgument(
        'mode', default_value='mapping',
        description='"mapping" runs SLAM; "navigation" runs Nav2 with a saved map.',
    )
    declare_map = DeclareLaunchArgument(
        'map', default_value='',
        description='Absolute path to saved .yaml map (required for navigation mode).',
    )

    # ── 1. Gazebo Harmonic ────────────────────────────────────────────────────
    gz_sim = ExecuteProcess(
        cmd=['gz', 'sim', '-r', world_file],
        output='screen',
    )

    # ── 2. robot_state_publisher ──────────────────────────────────────────────
    rsp = Node(
        package='robot_state_publisher',
        executable='robot_state_publisher',
        output='screen',
        parameters=[{
            'robot_description': robot_desc,
            'use_sim_time': True,
        }],
    )

    # ── 3. Spawn robot in Gazebo (delayed 3 s to let Gazebo finish loading) ──
    spawn = TimerAction(
        period=3.0,
        actions=[
            Node(
                package='ros_gz_sim',
                executable='create',
                arguments=[
                    '-name', 'wheelchair',
                    '-string', robot_desc,
                    '-x', '3.5', '-y', '2.5', '-z', '0.35',
                    '-Y', '0.0',
                ],
                output='screen',
            )
        ],
    )

    # ── 4. ros_gz_bridge ──────────────────────────────────────────────────────
    bridge = Node(
        package='ros_gz_bridge',
        executable='parameter_bridge',
        arguments=[
            # Gazebo → ROS
            '/clock@rosgraph_msgs/msg/Clock[gz.msgs.Clock',
            '/odom@nav_msgs/msg/Odometry[gz.msgs.Odometry',
            '/scan@sensor_msgs/msg/LaserScan[gz.msgs.LaserScan',
            '/tf@tf2_msgs/msg/TFMessage[gz.msgs.Pose_V',
            '/joint_states@sensor_msgs/msg/JointState[gz.msgs.Model',
            # ROS → Gazebo
            '/cmd_vel@geometry_msgs/msg/Twist]gz.msgs.Twist',
        ],
        remappings=[
            ('/tf', 'tf'),
        ],
        parameters=[{'use_sim_time': True}],
        output='screen',
    )

    # ── 5. Static TF: alias Gazebo-scoped lidar frame → URDF lidar_link ─────────
    # Gazebo Harmonic names sensor frames as "{model}/{link}/{sensor}"; SLAM
    # expects the plain URDF name.  This identity transform bridges the gap.
    lidar_frame_alias = Node(
        package='tf2_ros',
        executable='static_transform_publisher',
        arguments=['0', '0', '0', '0', '0', '0',
                   'lidar_link',
                   'wheelchair/base_footprint/rplidar_a1'],
        parameters=[{'use_sim_time': True}],
        output='screen',
    )

    return LaunchDescription([
        declare_mode,
        declare_map,
        gz_sim,
        rsp,
        spawn,
        bridge,
        lidar_frame_alias,
        OpaqueFunction(function=_conditional_nodes),
    ])
