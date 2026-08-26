"""Batch-behaviour gates for :mod:`scripts.sync_frameid`."""

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

from sync_frameid import build_parser, main  # noqa: E402

from rosbags.highlevel import AnyReader  # noqa: E402
from rosbags.rosbag1 import Writer  # noqa: E402
from rosbags.typesys import Stores, get_types_from_msg, get_typestore  # noqa: E402


MAPPINGS = ["/cam/a=frame_a", "/cam/b=frame_b"]


def _create_bag(path: Path, topics: tuple[str, ...] = ("/cam/a", "/cam/b", "/other")) -> None:
    """Create a tiny ROS1 bag with two messages on each topic."""
    path.parent.mkdir(parents=True, exist_ok=True)
    typestore = get_typestore(Stores.ROS1_NOETIC)
    message_type = "geometry_msgs/msg/PoseStamped"
    Header = typestore.types["std_msgs/msg/Header"]
    Pose = typestore.types["geometry_msgs/msg/Pose"]
    Point = typestore.types["geometry_msgs/msg/Point"]
    Quaternion = typestore.types["geometry_msgs/msg/Quaternion"]
    Time = typestore.types["builtin_interfaces/msg/Time"]
    with Writer(path) as writer:
        connections = {
            topic: writer.add_connection(topic, message_type, typestore=typestore)
            for topic in topics
        }
        for index in range(2):
            for topic in topics:
                message = typestore.types[message_type](
                    header=Header(
                        seq=index,
                        stamp=Time(sec=index, nanosec=0),
                        frame_id=f"original_{topic.rsplit('/', 1)[-1]}",
                    ),
                    pose=Pose(
                        position=Point(x=0.0, y=0.0, z=0.0),
                        orientation=Quaternion(x=0.0, y=0.0, z=0.0, w=1.0),
                    ),
                )
                raw = typestore.serialize_ros1(message, message_type)
                writer.write(connections[topic], index * 1_000_000_000, raw)


def _create_tf_bag(path: Path) -> None:
    """Create a TF bag to ensure generic frame rewriting rejects TF safely."""
    path.parent.mkdir(parents=True, exist_ok=True)
    typestore = get_typestore(Stores.ROS1_NOETIC)
    message_type = "test_msgs/msg/TFMessage"
    typestore.register(
        get_types_from_msg("geometry_msgs/TransformStamped[] transforms", message_type)
    )
    Header = typestore.types["std_msgs/msg/Header"]
    Time = typestore.types["builtin_interfaces/msg/Time"]
    TransformStamped = typestore.types["geometry_msgs/msg/TransformStamped"]
    Transform = typestore.types["geometry_msgs/msg/Transform"]
    Vector3 = typestore.types["geometry_msgs/msg/Vector3"]
    Quaternion = typestore.types["geometry_msgs/msg/Quaternion"]
    TFMessage = typestore.types[message_type]
    with Writer(path) as writer:
        connection = writer.add_connection("/tf", message_type, typestore=typestore)
        message = TFMessage(
            transforms=[
                TransformStamped(
                    header=Header(seq=0, stamp=Time(sec=0, nanosec=0), frame_id="base_link"),
                    child_frame_id="camera_link",
                    transform=Transform(
                        translation=Vector3(x=0.0, y=0.0, z=0.0),
                        rotation=Quaternion(x=0.0, y=0.0, z=0.0, w=1.0),
                    ),
                )
            ]
        )
        writer.write(connection, 0, typestore.serialize_ros1(message, message_type))


def _run(input_paths: list[Path], output: Path, *extra: str) -> int:
    args = ["--input", *(str(path) for path in input_paths), "--output", str(output)]
    for mapping in MAPPINGS:
        args.extend(("--map", mapping))
    args.extend(extra)
    return main(args)


def _frames(path: Path) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    with AnyReader([path]) as reader:
        for connection, _timestamp, rawdata in reader.messages():
            message = reader.deserialize(rawdata, connection.msgtype)
            result.setdefault(connection.topic, []).append(message.header.frame_id)
    return result


class SyncFrameidBatchTests(unittest.TestCase):
    def test_map_accepts_multiple_values_until_next_option(self) -> None:
        args = build_parser().parse_args(
            [
                "--input",
                "input.bag",
                "--output",
                "output",
                "--map",
                "/cam/a=frame_a",
                "/cam/b=frame_b",
                "--dry-run",
            ]
        )
        self.assertEqual(
            args.mappings,
            [[("/cam/a", "frame_a"), ("/cam/b", "frame_b")]],
        )
        self.assertTrue(args.dry_run)

    def test_map_keeps_repeated_invocation_compatibility(self) -> None:
        args = build_parser().parse_args(
            [
                "--input",
                "input.bag",
                "--output",
                "output",
                "--map",
                "/cam/a=frame_a",
                "--map",
                "/cam/b=frame_b",
            ]
        )
        self.assertEqual(
            args.mappings,
            [[("/cam/a", "frame_a")], [("/cam/b", "frame_b")]],
        )

    def test_single_map_group_rewrites_multiple_topics(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "input.bag"
            output = tmp_path / "output"
            _create_bag(source)

            self.assertEqual(
                main(
                    [
                        "--input",
                        str(source),
                        "--output",
                        str(output),
                        "--map",
                        "/cam/a=frame_a",
                        "/cam/b=frame_b",
                    ]
                ),
                0,
            )
            self.assertEqual(_frames(output / "input.bag")["/cam/a"], ["frame_a"] * 2)
            self.assertEqual(_frames(output / "input.bag")["/cam/b"], ["frame_b"] * 2)

    def test_invalid_mapping_is_rejected(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            main(
                [
                    "--input",
                    "/does/not/exist.bag",
                    "--output",
                    "/tmp/vela-test-output",
                    "--map",
                    "not-a-mapping",
                ]
            )
        self.assertEqual(raised.exception.code, 2)

    def test_missing_input_is_rejected(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            main(
                [
                    "--input",
                    "/does/not/exist.bag",
                    "--output",
                    "/tmp/vela-test-output",
                    "--map",
                    "/cam/a=frame_a",
                ]
            )
        self.assertEqual(raised.exception.code, 2)

    def test_tf_topic_is_rejected_without_a_dedicated_mode(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "tf.bag"
            _create_tf_bag(source)
            output = tmp_path / "output"
            before = hashlib.sha256(source.read_bytes()).digest()

            self.assertEqual(
                main(
                    [
                        "--input",
                        str(source),
                        "--output",
                        str(output),
                        "--map",
                        "/tf=renamed_frame",
                    ]
                ),
                1,
            )
            self.assertFalse((output / "tf.bag").exists())
            self.assertEqual(hashlib.sha256(source.read_bytes()).digest(), before)

    def test_multiple_inputs_and_recursive_directory_are_processed_once(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "source"
            first = source / "first.bag"
            second = source / "nested" / "second.bag"
            _create_bag(first)
            _create_bag(second)
            output = tmp_path / "output"

            # Passing the file and its parent directory together must not duplicate it.
            self.assertEqual(_run([first, source], output, "--recursive"), 0)
            outputs = sorted(output.glob("*.bag"))
            self.assertEqual([path.name for path in outputs], ["first.bag", "second.bag"])
            self.assertEqual(_frames(output / "first.bag")["/cam/a"], ["frame_a", "frame_a"])
            self.assertEqual(_frames(output / "second.bag")["/cam/b"], ["frame_b", "frame_b"])
            self.assertEqual(_frames(output / "first.bag")["/other"], ["original_other"] * 2)

    def test_dry_run_scans_without_creating_output(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "input.bag"
            _create_bag(source)
            output = tmp_path / "not-written"
            captured = io.StringIO()

            with redirect_stdout(captured):
                self.assertEqual(_run([source], output, "--dry-run"), 0)
            self.assertFalse(output.exists())
            self.assertIn("DRY RUN:", captured.getvalue())

    def test_default_collision_protection_and_overwrite(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "input.bag"
            _create_bag(source)
            output = tmp_path / "output"
            output.mkdir()
            existing = output / source.name
            existing.write_bytes(b"keep this file")
            before = hashlib.sha256(source.read_bytes()).digest()

            self.assertEqual(_run([source], output), 0)
            collision_outputs = [path for path in output.glob("input_loom_*.bag")]
            self.assertEqual(len(collision_outputs), 1)
            self.assertEqual(existing.read_bytes(), b"keep this file")

            self.assertEqual(_run([source], output, "--overwrite"), 0)
            self.assertEqual(_frames(existing)["/cam/a"], ["frame_a", "frame_a"])
            self.assertEqual(hashlib.sha256(source.read_bytes()).digest(), before)


if __name__ == "__main__":
    unittest.main()
