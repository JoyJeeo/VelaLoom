"""Regression gates for the interactive TF forest normalizer."""

from __future__ import annotations

import io
import hashlib
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TEST_OUTPUT = ROOT / "test_output/issue-018"
sys.path.insert(0, str(ROOT / "scripts"))

from rosbags.highlevel import AnyReader  # noqa: E402
from rosbags.rosbag1 import Writer  # noqa: E402
from rosbags.typesys import Stores, get_types_from_msg, get_typestore  # noqa: E402
from unify_rosbag_tf import (  # noqa: E402
    InteractionCancelled,
    analyze_bag,
    build_parser,
    format_forest,
    main,
    plan_connections,
)


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


def _create_fixture(
    path: Path,
    *,
    static_messages=(),
    dynamic_messages=(),
    include_data: bool = True,
) -> None:
    typestore = get_typestore(Stores.ROS1_NOETIC)
    typestore.register(
        get_types_from_msg(
            "geometry_msgs/TransformStamped[] transforms", "tf2_msgs/msg/TFMessage"
        )
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with Writer(path) as writer:
        tf_static = writer.add_connection(
            "/tf_static", "tf2_msgs/msg/TFMessage", typestore=typestore, latching=1
        )
        tf_dynamic = writer.add_connection(
            "/tf", "tf2_msgs/msg/TFMessage", typestore=typestore
        )
        data = writer.add_connection(
            "/data", "std_msgs/msg/String", typestore=typestore, callerid="fixture"
        )
        timestamp = 1_000_000_000
        for transforms in static_messages:
            message = _tf_message(typestore, transforms)
            raw = typestore.serialize_ros1(message, "tf2_msgs/msg/TFMessage")
            writer.write(tf_static, timestamp, raw)
            timestamp += 1
        for transforms in dynamic_messages:
            message = _tf_message(typestore, transforms)
            raw = typestore.serialize_ros1(message, "tf2_msgs/msg/TFMessage")
            writer.write(tf_dynamic, timestamp, raw)
            timestamp += 1
        if include_data:
            message = typestore.types["std_msgs/msg/String"](data="untouched")
            writer.write(data, timestamp, typestore.serialize_ros1(message, "std_msgs/msg/String"))


def _edge(
    parent: str,
    child: str,
    xyz=(0.0, 0.0, 0.0),
    quat=(0.0, 0.0, 0.0, 1.0),
):
    return parent, child, xyz, quat


class FakeTTY(io.StringIO):
    def isatty(self) -> bool:
        return True


def _passthrough_records(path: Path):
    records = []
    with AnyReader([path]) as reader:
        for connection, timestamp, rawdata in reader.messages():
            if connection.topic == "/tf_static":
                continue
            ext = connection.ext
            records.append(
                (
                    connection.topic,
                    connection.msgtype,
                    connection.digest,
                    getattr(ext, "callerid", None),
                    getattr(ext, "latching", None),
                    timestamp,
                    rawdata,
                )
            )
    return records


def _static_output(path: Path):
    edges = []
    messages = 0
    latching = []
    with AnyReader([path]) as reader:
        latching = [
            getattr(connection.ext, "latching", None)
            for connection in reader.connections
            if connection.topic == "/tf_static"
        ]
        for connection, _timestamp, rawdata in reader.messages():
            if connection.topic != "/tf_static":
                continue
            messages += 1
            message = reader.deserialize(rawdata, connection.msgtype)
            edges.extend(
                (transform.header.frame_id, transform.child_frame_id)
                for transform in message.transforms
            )
    return messages, edges, latching


class UnifyRosbagTfScanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        TEST_OUTPUT.mkdir(parents=True, exist_ok=True)

    def test_cli_has_no_urdf_or_legacy_head_options(self) -> None:
        parser = build_parser()
        parsed = parser.parse_args(["--input", "input.bag", "--output", "output.bag"])
        self.assertFalse(hasattr(parsed, "urdf"))
        self.assertFalse(hasattr(parsed, "keep_legacy_head_chain"))
        with self.assertRaises(SystemExit):
            parser.parse_args(
                ["--input", "input.bag", "--output", "output.bag", "--urdf", "robot.urdf"]
            )
        with self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "--input",
                    "input.bag",
                    "--output",
                    "output.bag",
                    "--keep-legacy-head-chain",
                ]
            )

    def test_scan_deduplicates_static_and_combines_edge_sources(self) -> None:
        with TemporaryDirectory(dir=TEST_OUTPUT) as tmp:
            source = Path(tmp) / "input.bag"
            static = [_edge("odom", "base_link"), _edge("base_link", "sensor")]
            _create_fixture(
                source,
                static_messages=[static, static],
                dynamic_messages=[[
                    _edge("odom", "base_link"),
                    _edge("base_link", "arm"),
                ]],
            )
            analysis = analyze_bag(source)
            self.assertEqual(analysis.tf_messages, 1)
            self.assertEqual(analysis.static_messages, 2)
            self.assertEqual(analysis.static_input_transforms, 4)
            self.assertEqual(analysis.static_duplicates, 2)
            self.assertEqual(len(analysis.static_edges), 2)
            self.assertEqual(len(analysis.dynamic_edges), 2)
            self.assertEqual(analysis.graph.roots, ("odom",))
            self.assertEqual(analysis.graph.edge_sources[("odom", "base_link")], "B")
            self.assertEqual(analysis.graph.edge_sources[("base_link", "sensor")], "S")
            self.assertEqual(analysis.graph.edge_sources[("base_link", "arm")], "D")
            rendered = format_forest(analysis.graph, "Combined sources")
            self.assertIn("base_link [B]", rendered)
            self.assertIn("sensor [S]", rendered)
            self.assertIn("arm [D]", rendered)

    def test_forest_rendering_is_complete_stable_and_recommends_odom_tree(self) -> None:
        with TemporaryDirectory(dir=TEST_OUTPUT) as tmp:
            source = Path(tmp) / "forest.bag"
            _create_fixture(
                source,
                static_messages=[[
                    _edge("camera_root", "z_sensor"),
                    _edge("camera_root", "a_sensor"),
                ]],
                dynamic_messages=[[
                    _edge("odom", "base_link"),
                    _edge("base_link", "arm"),
                ]],
            )
            rendered = format_forest(analyze_bag(source).graph, "Before repair")
            self.assertIn("Before repair", rendered)
            self.assertIn("Tree 1: root=camera_root frames=3", rendered)
            self.assertIn(
                "Tree 2: root=odom frames=3 markers=odom,base_link [RECOMMENDED]",
                rendered,
            )
            self.assertIn("a_sensor [S]", rendered)
            self.assertIn("z_sensor [S]", rendered)
            self.assertLess(rendered.index("a_sensor [S]"), rendered.index("z_sensor [S]"))
            self.assertIn("base_link [D]", rendered)
            self.assertIn("arm [D]", rendered)

    def test_static_pose_conflict_fails(self) -> None:
        with TemporaryDirectory(dir=TEST_OUTPUT) as tmp:
            source = Path(tmp) / "conflict.bag"
            _create_fixture(
                source,
                static_messages=[
                    [_edge("root", "child")],
                    [_edge("root", "child", xyz=(1.0, 0.0, 0.0))],
                ],
            )
            with self.assertRaisesRegex(ValueError, "conflicting static transform"):
                analyze_bag(source)

    def test_combined_multi_parent_and_cycle_fail(self) -> None:
        cases = (
            (
                "multi-parent",
                [[_edge("root_a", "child")]],
                [[_edge("root_b", "child")]],
                "multiple parents",
            ),
            (
                "cycle",
                [[_edge("a", "b")]],
                [[_edge("b", "a")]],
                "cycle",
            ),
        )
        for name, static, dynamic, error in cases:
            with self.subTest(name=name), TemporaryDirectory(dir=TEST_OUTPUT) as tmp:
                source = Path(tmp) / f"{name}.bag"
                _create_fixture(source, static_messages=static, dynamic_messages=dynamic)
                with self.assertRaisesRegex(ValueError, error):
                    analyze_bag(source)


class UnifyRosbagTfInteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        TEST_OUTPUT.mkdir(parents=True, exist_ok=True)

    def _three_tree_graph(self):
        temporary = TemporaryDirectory(dir=TEST_OUTPUT)
        self.addCleanup(temporary.cleanup)
        source = Path(temporary.name) / "three-trees.bag"
        _create_fixture(
            source,
            static_messages=[[
                _edge("camera_root", "camera_child"),
                _edge("lidar_root", "lidar_child"),
            ]],
            dynamic_messages=[[_edge("odom", "base_link")]],
        )
        return analyze_bag(source).graph

    def test_selects_target_and_each_mount_can_use_the_growing_tree(self) -> None:
        graph = self._three_tree_graph()
        output = io.StringIO()

        plan = plan_connections(
            graph,
            FakeTTY("odom\nbase_link\ncamera_child\n"),
            output,
        )

        self.assertEqual(plan.target_root, "odom")
        self.assertEqual(
            [(item.parent, item.child) for item in plan.bridges],
            [("base_link", "camera_root"), ("camera_child", "lidar_root")],
        )
        self.assertEqual(plan.final_graph.roots, ("odom",))
        self.assertIn("Selected target root: odom", output.getvalue())
        self.assertIn("Selected mount: base_link -> camera_root (identity)", output.getvalue())
        self.assertIn("Selected mount: camera_child -> lidar_root (identity)", output.getvalue())

    def test_root_number_list_tree_and_invalid_mount_retry(self) -> None:
        graph = self._three_tree_graph()
        output = io.StringIO()

        plan = plan_connections(
            graph,
            FakeTTY("3\nlist\ntree\ncamera_root\nmissing\nbase_link\nbase_link\n"),
            output,
        )

        self.assertEqual(plan.target_root, "odom")
        rendered = output.getvalue()
        self.assertIn("Available parent links: base_link, odom", rendered)
        self.assertIn("=== Current TF forest ===", rendered)
        self.assertIn("cannot use camera_root: it is inside the detached tree", rendered)
        self.assertIn("cannot use missing: it is not in the current merged target tree", rendered)

    def test_abort_and_eof_cancel_without_a_plan(self) -> None:
        graph = self._three_tree_graph()
        for user_input in (FakeTTY("abort\n"), FakeTTY("")):
            with self.subTest(value=user_input.getvalue()):
                with self.assertRaises(InteractionCancelled):
                    plan_connections(graph, user_input, io.StringIO())

    def test_multiple_roots_require_a_tty(self) -> None:
        graph = self._three_tree_graph()
        with self.assertRaisesRegex(ValueError, "stdin is not a TTY.*camera_root.*lidar_root.*odom"):
            plan_connections(graph, io.StringIO("odom\n"), io.StringIO())

    def test_single_tree_needs_no_root_or_mount_input(self) -> None:
        with TemporaryDirectory(dir=TEST_OUTPUT) as tmp:
            source = Path(tmp) / "single.bag"
            _create_fixture(
                source,
                static_messages=[[_edge("odom", "base_link")]],
            )
            output = io.StringIO()
            plan = plan_connections(analyze_bag(source).graph, io.StringIO(), output)
            self.assertEqual(plan.target_root, "odom")
            self.assertEqual(plan.bridges, ())
            self.assertEqual(plan.final_graph.roots, ("odom",))
            self.assertIn("Single TF tree detected", output.getvalue())

    def test_empty_tf_data_and_empty_frame_fail(self) -> None:
        cases = (
            ("empty", [[]], [], "no TF transforms"),
            ("empty-frame", [[_edge("", "child")]], [], "empty frame"),
        )
        for name, static, dynamic, error in cases:
            with self.subTest(name=name), TemporaryDirectory(dir=TEST_OUTPUT) as tmp:
                source = Path(tmp) / f"{name}.bag"
                _create_fixture(source, static_messages=static, dynamic_messages=dynamic)
                with self.assertRaisesRegex(ValueError, error):
                    analyze_bag(source)


class UnifyRosbagTfWriteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        TEST_OUTPUT.mkdir(parents=True, exist_ok=True)

    def _single_tree_fixture(self, root: Path) -> Path:
        source = root / "input.bag"
        static = [_edge("odom", "base_link"), _edge("base_link", "sensor")]
        _create_fixture(
            source,
            static_messages=[static, static],
            dynamic_messages=[[
                _edge("odom", "base_link"),
                _edge("base_link", "arm"),
            ]],
        )
        return source

    def test_default_enter_writes_one_latched_static_and_preserves_passthrough(self) -> None:
        with TemporaryDirectory(dir=TEST_OUTPUT) as tmp:
            root = Path(tmp)
            source = self._single_tree_fixture(root)
            output = root / "nested/output.bag"
            before_hash = hashlib.sha256(source.read_bytes()).digest()
            before_records = _passthrough_records(source)
            stdout, stderr = io.StringIO(), io.StringIO()

            result = main(
                ["--input", str(source), "--output", str(output)],
                input_stream=FakeTTY("maybe\n\n"),
                output_stream=stdout,
                error_stream=stderr,
            )

            self.assertEqual(result, 0, stderr.getvalue())
            self.assertTrue(output.is_file())
            self.assertEqual(hashlib.sha256(source.read_bytes()).digest(), before_hash)
            self.assertEqual(_passthrough_records(output), before_records)
            messages, edges, latching = _static_output(output)
            self.assertEqual(messages, 1)
            self.assertEqual(edges, [("base_link", "sensor"), ("odom", "base_link")])
            self.assertEqual(latching, [1])
            self.assertIn("=== After repair ===", stdout.getvalue())
            self.assertIn("Topology validation: PASS root=odom frames=4", stdout.getvalue())
            self.assertIn("Proceed [Y/n]:", stdout.getvalue())
            self.assertIn("Please answer y/yes or n/no.", stdout.getvalue())
            self.assertIn("Result: wrote", stdout.getvalue())

    def test_multi_tree_dry_run_completes_interaction_without_creating_paths(self) -> None:
        with TemporaryDirectory(dir=TEST_OUTPUT) as tmp:
            root = Path(tmp)
            source = root / "input.bag"
            _create_fixture(
                source,
                static_messages=[[_edge("camera_root", "camera_child")]],
                dynamic_messages=[[_edge("odom", "base_link")]],
            )
            output = root / "absent/output.bag"
            stdout, stderr = io.StringIO(), io.StringIO()

            result = main(
                ["--input", str(source), "--output", str(output), "--dry-run"],
                input_stream=FakeTTY("odom\nbase_link\n"),
                output_stream=stdout,
                error_stream=stderr,
            )

            self.assertEqual(result, 0, stderr.getvalue())
            self.assertFalse(output.exists())
            self.assertFalse(output.parent.exists())
            self.assertNotIn("Proceed [Y/n]:", stdout.getvalue())
            self.assertIn("Added identity edges: 1", stdout.getvalue())
            self.assertIn("base_link -> camera_root [S]", stdout.getvalue())
            self.assertIn("Result: dry-run complete; no output created", stdout.getvalue())

    def test_negative_confirmation_and_eof_cancel_without_output(self) -> None:
        for label, user_input in (("no", "n\n"), ("eof", "")):
            with self.subTest(label=label), TemporaryDirectory(dir=TEST_OUTPUT) as tmp:
                root = Path(tmp)
                source = self._single_tree_fixture(root)
                output = root / "cancelled/output.bag"
                stdout, stderr = io.StringIO(), io.StringIO()
                result = main(
                    ["--input", str(source), "--output", str(output)],
                    input_stream=FakeTTY(user_input),
                    output_stream=stdout,
                    error_stream=stderr,
                )
                self.assertEqual(result, 0, stderr.getvalue())
                self.assertFalse(output.exists())
                self.assertFalse(output.parent.exists())
                self.assertIn("Result: cancelled; no output created", stdout.getvalue())

    def test_non_tty_multi_root_fails_without_output(self) -> None:
        with TemporaryDirectory(dir=TEST_OUTPUT) as tmp:
            root = Path(tmp)
            source = root / "input.bag"
            output = root / "output.bag"
            _create_fixture(
                source,
                static_messages=[[_edge("camera_root", "camera_child")]],
                dynamic_messages=[[_edge("odom", "base_link")]],
            )
            stdout, stderr = io.StringIO(), io.StringIO()
            result = main(
                ["--input", str(source), "--output", str(output)],
                input_stream=io.StringIO("odom\nbase_link\n\n"),
                output_stream=stdout,
                error_stream=stderr,
            )
            self.assertEqual(result, 1)
            self.assertFalse(output.exists())
            self.assertIn("stdin is not a TTY", stderr.getvalue())
            self.assertIn("Tree 1: root=camera_root", stdout.getvalue())

    def test_default_collision_and_failed_verification_preserve_existing_output(self) -> None:
        with TemporaryDirectory(dir=TEST_OUTPUT) as tmp:
            root = Path(tmp)
            source = self._single_tree_fixture(root)
            output = root / "output.bag"
            output.write_bytes(b"keep")
            stderr = io.StringIO()
            self.assertEqual(
                main(
                    ["--input", str(source), "--output", str(output)],
                    input_stream=FakeTTY("\n"),
                    output_stream=io.StringIO(),
                    error_stream=stderr,
                ),
                1,
            )
            self.assertEqual(output.read_bytes(), b"keep")
            self.assertIn("output exists", stderr.getvalue())

            with patch("unify_rosbag_tf.verify_output", side_effect=ValueError("forced verify failure")):
                self.assertEqual(
                    main(
                        [
                            "--input",
                            str(source),
                            "--output",
                            str(output),
                            "--overwrite",
                        ],
                        input_stream=FakeTTY("\n"),
                        output_stream=io.StringIO(),
                        error_stream=io.StringIO(),
                    ),
                    1,
                )
            self.assertEqual(output.read_bytes(), b"keep")
            self.assertEqual(list(root.glob(".output.*.tmp.bag")), [])

    def test_overwrite_success_and_input_output_same_path_guard(self) -> None:
        with TemporaryDirectory(dir=TEST_OUTPUT) as tmp:
            root = Path(tmp)
            source = self._single_tree_fixture(root)
            output = root / "output.bag"
            output.write_bytes(b"replace me")
            stderr = io.StringIO()

            result = main(
                [
                    "--input",
                    str(source),
                    "--output",
                    str(output),
                    "--overwrite",
                ],
                input_stream=FakeTTY("yes\n"),
                output_stream=io.StringIO(),
                error_stream=stderr,
            )

            self.assertEqual(result, 0, stderr.getvalue())
            self.assertNotEqual(output.read_bytes(), b"replace me")
            self.assertEqual(_static_output(output)[0], 1)

            same_path_error = io.StringIO()
            self.assertEqual(
                main(
                    [
                        "--input",
                        str(source),
                        "--output",
                        str(source),
                        "--overwrite",
                    ],
                    input_stream=FakeTTY("yes\n"),
                    output_stream=io.StringIO(),
                    error_stream=same_path_error,
                ),
                1,
            )
            self.assertIn("output must differ from input", same_path_error.getvalue())

            bad_suffix_error = io.StringIO()
            self.assertEqual(
                main(
                    ["--input", str(source), "--output", str(root / "output.txt")],
                    input_stream=FakeTTY("yes\n"),
                    output_stream=io.StringIO(),
                    error_stream=bad_suffix_error,
                ),
                1,
            )
            self.assertIn("output must use the .bag extension", bad_suffix_error.getvalue())

    def test_abort_during_mount_cancels_before_creating_output(self) -> None:
        with TemporaryDirectory(dir=TEST_OUTPUT) as tmp:
            root = Path(tmp)
            source = root / "input.bag"
            output = root / "absent/output.bag"
            _create_fixture(
                source,
                static_messages=[[_edge("camera_root", "camera_child")]],
                dynamic_messages=[[_edge("odom", "base_link")]],
            )
            stdout = io.StringIO()
            self.assertEqual(
                main(
                    ["--input", str(source), "--output", str(output)],
                    input_stream=FakeTTY("odom\nabort\n"),
                    output_stream=stdout,
                    error_stream=io.StringIO(),
                ),
                0,
            )
            self.assertFalse(output.exists())
            self.assertFalse(output.parent.exists())
            self.assertIn("Result: cancelled", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
