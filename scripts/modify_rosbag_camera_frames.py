#!/usr/bin/env python3
"""Rewrite camera image/camera-info frame_ids and connect camera TF trees in a ROS1 bag.

This script uses the pure-Python ``rosbags`` package, so a ROS installation is
not required. The input bag is never modified; a new ROS1 bag is written.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from rosbags.highlevel import AnyReader
from rosbags.rosbag1 import Writer


FRAME_REMAP = {
    "/cam_r/color/image_raw/compressed": "r_d405_camera_base",
    "/cam_r/color/camera_info": "r_d405_camera_base",
    "/cam_h/color/image_raw/compressed": "camera_base",
    "/cam_h/color/camera_info": "camera_base",
    "/cam_l/color/image_raw/compressed": "l_d405_camera_base",
    "/cam_l/color/camera_info": "l_d405_camera_base",
}


def quaternion_from_rpy(roll: float, pitch: float, yaw: float) -> tuple[float, float, float, float]:
    """Quaternion for the URDF convention R = Rz(yaw) Ry(pitch) Rx(roll)."""

    cr = math.cos(roll / 2.0)
    sr = math.sin(roll / 2.0)
    cp = math.cos(pitch / 2.0)
    sp = math.sin(pitch / 2.0)
    cy = math.cos(yaw / 2.0)
    sy = math.sin(yaw / 2.0)
    return (
        sr * cp * cy - cr * sp * sy,
        cr * sp * cy + sr * cp * sy,
        cr * cp * sy - sr * sp * cy,
        cr * cp * cy + sr * sp * sy,
    )


def build_alias_specs():
    """Return fixed transforms that join sensor trees to the robot tree."""

    q_identity = (0.0, 0.0, 0.0, 1.0)
    return [
        ("zarm_l7_link", "l_camera_link", (0.0, -0.0005, -0.0586), q_identity),
        (
            "l_camera_link",
            "l_d405_camera_base",
            (0.12576, -0.032535, -0.034283),
            quaternion_from_rpy(1.933, 0.75188, -2.8883),
        ),
        ("l_d405_camera_base", "cam_l_link", (0.0, 0.0, 0.0), q_identity),
        ("zarm_r7_link", "r_camera_link_connect", (0.0, 0.0005, -0.0586), q_identity),
        (
            "r_camera_link_connect",
            "r_d405_camera_base",
            (0.12576, 0.032535, -0.034283),
            quaternion_from_rpy(-1.933, 0.75188, 2.8883),
        ),
        ("r_d405_camera_base", "cam_r_link", (0.0, 0.0, 0.0), q_identity),
        (
            "zhead_2_link",
            "camera_base",
            (0.093839, 0.0475, 0.050077),
            quaternion_from_rpy(0.0, 0.3406, 0.0),
        ),
        ("camera_base", "cam_h_link", (0.0, 0.0, 0.0), q_identity),
    ]


def stamp_for_message(message, bag_timestamp: int, time_type):
    if message.transforms:
        source = message.transforms[0].header.stamp
        return time_type(source.sec, source.nanosec)
    sec, nanosec = divmod(bag_timestamp, 1_000_000_000)
    return time_type(sec, nanosec)


def append_alias_transforms(message, bag_timestamp: int, typestore, time_type) -> int:
    """Append missing fixed transforms to one TFMessage."""

    existing = {(t.header.frame_id, t.child_frame_id) for t in message.transforms}
    stamp = stamp_for_message(message, bag_timestamp, time_type)
    Header = typestore.types["std_msgs/msg/Header"]
    TransformStamped = typestore.types["geometry_msgs/msg/TransformStamped"]
    Transform = typestore.types["geometry_msgs/msg/Transform"]
    Vector3 = typestore.types["geometry_msgs/msg/Vector3"]
    Quaternion = typestore.types["geometry_msgs/msg/Quaternion"]
    added = 0
    for parent, child, translation, rotation in build_alias_specs():
        if (parent, child) in existing:
            continue
        tx, ty, tz = translation
        qx, qy, qz, qw = rotation
        message.transforms.append(
            TransformStamped(
                header=Header(seq=0, stamp=stamp, frame_id=parent),
                child_frame_id=child,
                transform=Transform(
                    translation=Vector3(x=tx, y=ty, z=tz),
                    rotation=Quaternion(x=qx, y=qy, z=qz, w=qw),
                ),
            )
        )
        existing.add((parent, child))
        added += 1
    return added


def copy_and_modify(input_bag: Path, output_bag: Path):
    if output_bag.exists():
        raise FileExistsError(f"Refusing to overwrite existing output: {output_bag}")
    output_bag.parent.mkdir(parents=True, exist_ok=True)
    frame_counts = {topic: 0 for topic in FRAME_REMAP}
    static_transforms_added = 0

    with AnyReader([input_bag]) as reader, Writer(output_bag) as writer:
        typestore = reader.typestore
        Time = typestore.types["builtin_interfaces/msg/Time"]
        connection_map = {}
        for source in reader.connections:
            ext = source.ext
            connection_map[source.id] = writer.add_connection(
                source.topic,
                source.msgtype,
                typestore=typestore,
                msgdef=source.msgdef.data,
                md5sum=source.digest,
                callerid=getattr(ext, "callerid", None),
                latching=getattr(ext, "latching", None),
            )

        for source, timestamp, rawdata in reader.messages():
            outdata = rawdata
            if source.topic in FRAME_REMAP:
                message = reader.deserialize(rawdata, source.msgtype)
                message.header.frame_id = FRAME_REMAP[source.topic]
                outdata = typestore.serialize_ros1(message, source.msgtype)
                frame_counts[source.topic] += 1
            elif source.topic == "/tf_static":
                message = reader.deserialize(rawdata, source.msgtype)
                added = append_alias_transforms(message, timestamp, typestore, Time)
                if added:
                    static_transforms_added += added
                    outdata = typestore.serialize_ros1(message, source.msgtype)
            writer.write(connection_map[source.id], timestamp, outdata)
    return frame_counts, static_transforms_added


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_bag", type=Path)
    parser.add_argument("output_bag", type=Path)
    args = parser.parse_args()
    counts, added = copy_and_modify(args.input_bag, args.output_bag)
    print(f"Wrote: {args.output_bag}")
    print(f"Added static transforms: {added}")
    for topic, count in counts.items():
        print(f"Rewritten frame_ids: {topic}: {count}")


if __name__ == "__main__":
    main()
