import os
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch_ros.actions import Node

def generate_launch_description():
    pkg_share = get_package_share_directory('wheelchair_navigation')

    urdf_file = os.path.join(pkg_share, 'urdf', 'wheelchair.urdf')
    slam_params_file = os.path.join(pkg_share, 'config', 'mapper_params_online.yaml')

    with open(urdf_file, 'r') as infp:
        robot_desc = infp.read()

    robot_state_publisher_node = Node(
        package='robot_state_publisher',
        executable='robot_state_publisher',
        output='screen',
        parameters=[{'robot_description': robot_desc}]
    )

    # ADD THIS: Static transform to bridge odom -> base_link for hand-held testing
    static_tf_node = Node(
        package='tf2_ros',
        executable='static_transform_publisher',
        name='static_tf_pub_odom_base',
        arguments=['0', '0', '0', '0', '0', '0', 'odom', 'base_link']
    )

    sllidar_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([
            os.path.join(get_package_share_directory('sllidar_ros2'), 'launch', 'sllidar_a1_launch.py')
        ]),
        launch_arguments={'frame_id': 'lidar_link'}.items()
    )

    slam_toolbox_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([
            os.path.join(get_package_share_directory('slam_toolbox'), 'launch', 'online_async_launch.py')
        ]),
        launch_arguments={
            'slam_params_file': slam_params_file,
            'use_sim_time': 'false'
        }.items()
    )

    return LaunchDescription([
        robot_state_publisher_node,
        static_tf_node,  # <--- Include it here
        sllidar_launch,
        slam_toolbox_launch
    ])