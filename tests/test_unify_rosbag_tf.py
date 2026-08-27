"""Integration and safety gates for :mod:`scripts.unify_rosbag_tf`."""

from __future__ import annotations

import hashlib
import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from rosbags.highlevel import AnyReader  # noqa: E402
from rosbags.rosbag1 import Writer  # noqa: E402
from rosbags.typesys import Stores, get_types_from_msg, get_typestore  # noqa: E402
from unify_rosbag_tf import (  # noqa: E402
    BRIDGE_EDGES,
    LEGACY_HEAD_EDGES,
    REQUIRED_URDF_EDGES,
    TransformSpec,
    main,
    read_required_urdf_edges,
)


REAL_BAG = ROOT / "rosbag/A03-A22-H-C-01-004-5_140-dex_hand-20260820190611-53-3ea2cb-v003.bag"
REAL_URDF = ROOT / "urdf_kuavo5/urdf/biped_s300053_foxglove.urdf"


def _tf_message(typestore, transforms):
    Header = typestore.types["std_msgs/msg/Header"]
    Time = typestore.types["builtin_interfaces/msg/Time"]
    TransformStamped = typestore.types["geometry_msgs/msg/TransformStamped"]
    Transform = typestore.types["geometry_msgs/msg/Transform"]
    Vector3 = typestore.types["geometry_msgs/msg/Vector3"]
    Quaternion = typestore.types["geometry_msgs/msg/Quaternion"]
    TFMessage = typestore.types["tf2_msgs/msg/TFMessage"]
    result = []
    for parent, child, xyz, quat in transforms:
        result.append(
            TransformStamped(
                header=Header(seq=0, stamp=Time(sec=0, nanosec=0), frame_id=parent),
                child_frame_id=child,
                transform=Transform(
                    translation=Vector3(x=xyz[0], y=xyz[1], z=xyz[2]),
                    rotation=Quaternion(x=quat[0], y=quat[1], z=quat[2], w=quat[3]),
                ),
            )
        )
    return TFMessage(transforms=result)


def _create_fixture(path: Path, *, legacy_reference: bool = False, conflict: bool = False) -> None:
    typestore = get_typestore(Stores.ROS1_NOETIC)
    typestore.register(
        get_types_from_msg(
            "geometry_msgs/TransformStamped[] transforms", "tf2_msgs/msg/TFMessage"
        )
    )
    pose_type = "geometry_msgs/msg/PoseStamped"
    Header = typestore.types["std_msgs/msg/Header"]
    Time = typestore.types["builtin_interfaces/msg/Time"]
    PoseStamped = typestore.types[pose_type]
    Pose = typestore.types["geometry_msgs/msg/Pose"]
    Point = typestore.types["geometry_msgs/msg/Point"]
    Quaternion = typestore.types["geometry_msgs/msg/Quaternion"]
    static = [
        ("base_link", "dummy_link", (0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0)),
        ("zhead_2_link", "head_camera_base", (0.1, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0)),
        ("head_camera_base", "head_camera_depth", (0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0)),
    ]
    if conflict:
        static.append(("other_parent", "dummy_link", (1.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0)))
    dynamic = [
        ("odom", "base_link", (0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0)),
        ("odom", "zhead_2_link", (0.0, 0.0, 1.0), (0.0, 0.0, 0.0, 1.0)),
        ("odom", "zarm_l7_link", (0.0, 0.0, 1.0), (0.0, 0.0, 0.0, 1.0)),
        ("odom", "zarm_r7_link", (0.0, 0.0, 1.0), (0.0, 0.0, 0.0, 1.0)),
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with Writer(path) as writer:
        tf_static = writer.add_connection("/tf_static", "tf2_msgs/msg/TFMessage", typestore=typestore)
        tf_dynamic = writer.add_connection("/tf", "tf2_msgs/msg/TFMessage", typestore=typestore)
        pose = writer.add_connection("/cam_h/color/camera_info", pose_type, typestore=typestore)
        static_raw = typestore.serialize_ros1(_tf_message(typestore, static), "tf2_msgs/msg/TFMessage")
        writer.write(tf_static, 1_000_000_000, static_raw)
        writer.write(tf_static, 1_000_000_001, static_raw)  # duplicate message to deduplicate
        dynamic_raw = typestore.serialize_ros1(_tf_message(typestore, dynamic), "tf2_msgs/msg/TFMessage")
        writer.write(tf_dynamic, 2_000_000_000, dynamic_raw)
        pose_message = PoseStamped(
            header=Header(seq=0, stamp=Time(sec=0, nanosec=0), frame_id="head_camera_base" if legacy_reference else "base_link"),
            pose=Pose(
                position=Point(x=0.0, y=0.0, z=0.0),
                orientation=Quaternion(x=0.0, y=0.0, z=0.0, w=1.0),
            ),
        )
        writer.write(pose, 3_000_000_000, typestore.serialize_ros1(pose_message, pose_type))


def _records(path: Path, topic: str):
    result = []
    with AnyReader([path]) as reader:
        for connection, timestamp, rawdata in reader.messages():
            if connection.topic == topic:
                result.append((timestamp, rawdata))
    return result


def _edge_map(path: Path, topic: str = "/tf_static"):
    edges = []
    with AnyReader([path]) as reader:
        for connection, _timestamp, rawdata in reader.messages():
            if connection.topic != topic:
                continue
            message = reader.deserialize(rawdata, connection.msgtype)
            edges.extend((t.header.frame_id, t.child_frame_id) for t in message.transforms)
    return edges


class UnifyRosbagTfTests(unittest.TestCase):
    def test_real_urdf_contains_all_required_camera_fixed_joints(self) -> None:
        edges = read_required_urdf_edges(REAL_URDF)
        self.assertEqual(set(edges), set(REQUIRED_URDF_EDGES))

    def test_fixture_deduplicates_static_adds_urdf_and_preserves_dynamic(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, output = root / "input.bag", root / "output.bag"
            _create_fixture(source)
            before = hashlib.sha256(source.read_bytes()).digest()
            self.assertEqual(main(["--input", str(source), "--output", str(output), "--urdf", str(REAL_URDF)]), 0)
            self.assertEqual(hashlib.sha256(source.read_bytes()).digest(), before)
            edges = _edge_map(output)
            self.assertEqual(len(edges), len(set(edges)))
            self.assertEqual(len(edges), 3 + len(REQUIRED_URDF_EDGES) + len(BRIDGE_EDGES) - len(LEGACY_HEAD_EDGES))
            self.assertNotIn(("zhead_2_link", "head_camera_base"), edges)
            self.assertIn(("camera_base", "cam_h_link"), edges)
            with AnyReader([output]) as reader:
                static_connections = [c for c in reader.connections if c.topic == "/tf_static"]
                self.assertEqual(len(static_connections), 1)
                self.assertEqual(static_connections[0].ext.latching, 1)
            self.assertEqual(_records(source, "/tf"), _records(output, "/tf"))
            self.assertEqual(_records(source, "/cam_h/color/camera_info"), _records(output, "/cam_h/color/camera_info"))

    def test_legacy_reference_fails_without_flag_and_succeeds_with_flag(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "legacy.bag"
            _create_fixture(source, legacy_reference=True)
            rejected = root / "rejected.bag"
            self.assertEqual(main(["--input", str(source), "--output", str(rejected), "--urdf", str(REAL_URDF)]), 1)
            self.assertFalse(rejected.exists())
            kept = root / "kept.bag"
            self.assertEqual(main(["--input", str(source), "--output", str(kept), "--urdf", str(REAL_URDF), "--keep-legacy-head-chain"]), 0)
            self.assertIn(("zhead_2_link", "head_camera_base"), _edge_map(kept))

    def test_conflict_fails_without_leaving_partial_output(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, output = root / "conflict.bag", root / "out.bag"
            _create_fixture(source, conflict=True)
            self.assertEqual(main(["--input", str(source), "--output", str(output), "--urdf", str(REAL_URDF)]), 1)
            self.assertFalse(output.exists())
            self.assertFalse(Path(str(output) + ".tmp").exists())

    def test_dry_run_real_bag_is_read_only_and_validates_single_root(self) -> None:
        before = hashlib.sha256(REAL_BAG.read_bytes()).digest()
        with TemporaryDirectory() as tmp:
            output = Path(tmp) / "not-created.bag"
            captured = io.StringIO()
            with redirect_stdout(captured):
                self.assertEqual(main(["--input", str(REAL_BAG), "--output", str(output), "--urdf", str(REAL_URDF), "--dry-run"]), 0)
            self.assertFalse(output.exists())
            self.assertEqual(hashlib.sha256(REAL_BAG.read_bytes()).digest(), before)
            self.assertIn("normalized transforms: 40", captured.getvalue())

    def test_default_output_collision_is_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, output = root / "input.bag", root / "output.bag"
            _create_fixture(source)
            output.write_bytes(b"keep")
            self.assertEqual(main(["--input", str(source), "--output", str(output), "--urdf", str(REAL_URDF)]), 1)
            self.assertEqual(output.read_bytes(), b"keep")


if __name__ == "__main__":
    unittest.main()
