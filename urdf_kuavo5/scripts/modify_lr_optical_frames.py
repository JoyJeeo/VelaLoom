#!/usr/bin/env python3
"""Point the left/right color streams at the rotated URDF optical frames."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from rosbags.highlevel import AnyReader
from rosbags.rosbag1 import Writer


FRAME_REMAP = {
    "/cam_l/color/image_raw/compressed": "l_d405_camera_optical_frame",
    "/cam_l/color/camera_info": "l_d405_camera_optical_frame",
    "/cam_r/color/image_raw/compressed": "r_d405_camera_optical_frame",
    "/cam_r/color/camera_info": "r_d405_camera_optical_frame",
}


def qz(angle: float) -> tuple[float, float, float, float]:
    return (0.0, 0.0, math.sin(angle / 2.0), math.cos(angle / 2.0))


def append_optical_transforms(message, timestamp: int, typestore) -> int:
    existing = {(t.header.frame_id, t.child_frame_id) for t in message.transforms}
    if message.transforms:
        s = message.transforms[0].header.stamp
        stamp = typestore.types["builtin_interfaces/msg/Time"](s.sec, s.nanosec)
    else:
        sec, nanosec = divmod(timestamp, 1_000_000_000)
        stamp = typestore.types["builtin_interfaces/msg/Time"](sec, nanosec)

    Header = typestore.types["std_msgs/msg/Header"]
    TransformStamped = typestore.types["geometry_msgs/msg/TransformStamped"]
    Transform = typestore.types["geometry_msgs/msg/Transform"]
    Vector3 = typestore.types["geometry_msgs/msg/Vector3"]
    Quaternion = typestore.types["geometry_msgs/msg/Quaternion"]
    specs = [
        ("l_d405_camera_base", "l_d405_camera", (0.01085, 0.009, 0.021), qz(0.0)),
        ("l_d405_camera", "l_d405_camera_optical_frame", qz(-math.pi / 2.0)),
        ("r_d405_camera_base", "r_d405_camera", (0.01085, -0.009, 0.021), qz(0.0)),
        ("r_d405_camera", "r_d405_camera_optical_frame", qz(math.pi / 2.0)),
    ]
    added = 0
    for spec in specs:
        parent, child, *rest = spec
        if len(rest) == 2:
            translation, rotation = rest
        else:
            translation = (0.0, 0.0, 0.0)
            rotation = rest[0]
        tx, ty, tz = translation
        qx, qy, qzv, qw = rotation
        if (parent, child) in existing:
            continue
        message.transforms.append(
            TransformStamped(
                header=Header(seq=0, stamp=stamp, frame_id=parent),
                child_frame_id=child,
                transform=Transform(
                    translation=Vector3(x=tx, y=ty, z=tz),
                    rotation=Quaternion(x=qx, y=qy, z=qzv, w=qw),
                ),
            )
        )
        existing.add((parent, child))
        added += 1
    return added


def rewrite(input_bag: Path, output_bag: Path):
    if output_bag.exists():
        raise FileExistsError(f"Refusing to overwrite existing output: {output_bag}")
    counts = {topic: 0 for topic in FRAME_REMAP}
    added_tf = 0
    with AnyReader([input_bag]) as reader, Writer(output_bag) as writer:
        typestore = reader.typestore
        connections = {}
        for source in reader.connections:
            ext = source.ext
            connections[source.id] = writer.add_connection(
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
                counts[source.topic] += 1
            elif source.topic == "/tf_static":
                message = reader.deserialize(rawdata, source.msgtype)
                added_tf += append_optical_transforms(message, timestamp, typestore)
                outdata = typestore.serialize_ros1(message, source.msgtype)
            writer.write(connections[source.id], timestamp, outdata)
    return counts, added_tf


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_bag", type=Path)
    parser.add_argument("output_bag", type=Path)
    args = parser.parse_args()
    counts, added_tf = rewrite(args.input_bag, args.output_bag)
    print(f"Wrote: {args.output_bag}")
    print(f"Added optical static transforms: {added_tf}")
    for topic, count in counts.items():
        print(f"Rewritten frame_ids: {topic}: {count}")


if __name__ == "__main__":
    main()
