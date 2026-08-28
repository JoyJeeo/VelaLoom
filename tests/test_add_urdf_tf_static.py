"""Tests for the independent URDF fixed-joint rosbag converter."""

from __future__ import annotations

import io
import hashlib
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from rosbags.highlevel import AnyReader  # noqa: E402
from rosbags.rosbag1 import Writer  # noqa: E402
from rosbags.typesys import Stores, get_types_from_msg, get_typestore  # noqa: E402

from add_urdf_tf_static import (  # noqa: E402
    UserAbort,
    analyze_inputs,
    build_output_plan,
    confirm_write,
    load_decisions,
    main,
    read_fixed_joints,
    resolve_conflicts,
    rewrite_bag,
    save_decisions,
)


TEST_OUTPUT_ROOT = ROOT / "test_output/test_add_urdf_tf_static"


def _tf_message(typestore, transforms):
    Header = typestore.types["std_msgs/msg/Header"]
    Time = typestore.types["builtin_interfaces/msg/Time"]
    TransformStamped = typestore.types["geometry_msgs/msg/TransformStamped"]
    Transform = typestore.types["geometry_msgs/msg/Transform"]
    Vector3 = typestore.types["geometry_msgs/msg/Vector3"]
    Quaternion = typestore.types["geometry_msgs/msg/Quaternion"]
    TFMessage = typestore.types["tf2_msgs/msg/TFMessage"]
    return TFMessage(
        transforms=[
            TransformStamped(
                header=Header(
                    seq=0,
                    stamp=Time(sec=0, nanosec=0),
                    frame_id=parent,
                ),
                child_frame_id=child,
                transform=Transform(
                    translation=Vector3(x=xyz[0], y=xyz[1], z=xyz[2]),
                    rotation=Quaternion(x=quat[0], y=quat[1], z=quat[2], w=quat[3]),
                ),
            )
            for parent, child, xyz, quat in transforms
        ]
    )


def _write_bag(path: Path, *, static=(), dynamic=(), payloads=("keep-a", "keep-b")) -> None:
    typestore = get_typestore(Stores.ROS1_NOETIC)
    typestore.register(
        get_types_from_msg(
            "geometry_msgs/TransformStamped[] transforms",
            "tf2_msgs/msg/TFMessage",
        )
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with Writer(path) as writer:
        static_connection = writer.add_connection(
            "/tf_static", "tf2_msgs/msg/TFMessage", typestore=typestore, latching=1
        )
        dynamic_connection = writer.add_connection(
            "/tf", "tf2_msgs/msg/TFMessage", typestore=typestore
        )
        string_type = "std_msgs/msg/String"
        string_connection = writer.add_connection(
            "/payload", string_type, typestore=typestore
        )
        if static:
            raw = typestore.serialize_ros1(
                _tf_message(typestore, static), "tf2_msgs/msg/TFMessage"
            )
            writer.write(static_connection, 1_000_000_000, raw)
            writer.write(static_connection, 1_000_000_001, raw)
        if dynamic:
            raw = typestore.serialize_ros1(
                _tf_message(typestore, dynamic), "tf2_msgs/msg/TFMessage"
            )
            writer.write(dynamic_connection, 2_000_000_000, raw)
            writer.write(dynamic_connection, 2_000_000_001, raw)
        String = typestore.types[string_type]
        for index, payload in enumerate(payloads):
            writer.write(
                string_connection,
                3_000_000_000 + index,
                typestore.serialize_ros1(String(data=payload), string_type),
            )


def _write_urdf(path: Path, joints: str) -> None:
    path.write_text(f'<robot name="fixture">{joints}</robot>', encoding="utf-8")


def _topic_records(path: Path, topic: str):
    result = []
    with AnyReader([path]) as reader:
        for connection, timestamp, rawdata in reader.messages():
            if connection.topic == topic:
                result.append((timestamp, rawdata))
    return result


def _tf_edges(path: Path, topic: str):
    edges = []
    with AnyReader([path]) as reader:
        for connection, _timestamp, rawdata in reader.messages():
            if connection.topic != topic:
                continue
            message = reader.deserialize(rawdata, connection.msgtype)
            edges.extend(
                (transform.header.frame_id, transform.child_frame_id)
                for transform in message.transforms
            )
    return edges


class UrdfFixedAnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        TEST_OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    def test_reads_every_direct_fixed_joint_and_ignores_movable_joint(self) -> None:
        with TemporaryDirectory(dir=TEST_OUTPUT_ROOT) as tmp:
            urdf = Path(tmp) / "fixture.urdf"
            _write_urdf(
                urdf,
                """
                <joint name="fixed_with_origin" type="fixed">
                  <parent link="base"/><child link="sensor"/>
                  <origin xyz="1 2 3" rpy="0 0 1.5707963267948966"/>
                </joint>
                <joint name="fixed_identity" type="fixed">
                  <parent link="sensor"/><child link="optical"/>
                </joint>
                <joint name="moving" type="revolute">
                  <parent link="base"/><child link="arm"/>
                </joint>
                """,
            )
            result = read_fixed_joints(urdf)
            self.assertEqual(set(result), {"sensor", "optical"})
            self.assertEqual(result["sensor"].translation, (1.0, 2.0, 3.0))
            self.assertAlmostEqual(result["sensor"].rotation[2], 2**-0.5)
            self.assertAlmostEqual(result["sensor"].rotation[3], 2**-0.5)
            self.assertEqual(result["optical"].rotation, (0.0, 0.0, 0.0, 1.0))

    def test_rejects_invalid_origin_and_multiple_urdf_parents(self) -> None:
        invalid_cases = (
            '<joint name="bad" type="fixed"><parent link="a"/><child link="b"/><origin xyz="1 2"/></joint>',
            '<joint name="bad" type="fixed"><parent link="a"/><child link="b"/><origin rpy="0 nan 0"/></joint>',
            '<joint name="one" type="fixed"><parent link="a"/><child link="b"/></joint>'
            '<joint name="two" type="fixed"><parent link="c"/><child link="b"/></joint>',
        )
        with TemporaryDirectory(dir=TEST_OUTPUT_ROOT) as tmp:
            for index, joints in enumerate(invalid_cases):
                with self.subTest(index=index):
                    urdf = Path(tmp) / f"invalid-{index}.urdf"
                    _write_urdf(urdf, joints)
                    with self.assertRaises(ValueError):
                        read_fixed_joints(urdf)

    def test_classifies_missing_identical_pose_parent_and_dynamic_conflicts(self) -> None:
        identity = (0.0, 0.0, 0.0, 1.0)
        with TemporaryDirectory(dir=TEST_OUTPUT_ROOT) as tmp:
            root = Path(tmp)
            urdf, bag = root / "fixture.urdf", root / "fixture.bag"
            _write_urdf(
                urdf,
                """
                <joint name="same" type="fixed"><parent link="base"/><child link="same"/></joint>
                <joint name="missing" type="fixed"><parent link="base"/><child link="missing"/></joint>
                <joint name="pose" type="fixed"><parent link="base"/><child link="pose"/></joint>
                <joint name="parent" type="fixed"><parent link="base"/><child link="parent"/></joint>
                <joint name="dynamic" type="fixed"><parent link="base"/><child link="dynamic"/></joint>
                """,
            )
            _write_bag(
                bag,
                static=(
                    ("base", "same", (0.0, 0.0, 0.0), identity),
                    ("base", "pose", (1.0, 0.0, 0.0), identity),
                    ("other", "parent", (0.0, 0.0, 0.0), identity),
                ),
                dynamic=(("base", "dynamic", (0.0, 0.0, 0.0), identity),),
            )
            result = analyze_inputs(bag, read_fixed_joints(urdf))
            self.assertEqual(result.classifications["same"], "already_identical")
            self.assertEqual(result.classifications["missing"], "missing")
            self.assertEqual(result.classifications["pose"], "conflict")
            self.assertEqual(result.classifications["parent"], "conflict")
            self.assertEqual(result.classifications["dynamic"], "conflict")
            conflicts = {conflict.child: conflict for conflict in result.conflicts}
            self.assertIn("pose_mismatch", conflicts["pose"].reasons)
            self.assertIn("different_static_parent", conflicts["parent"].reasons)
            self.assertIn("dynamic_child", conflicts["dynamic"].reasons)
            self.assertEqual(conflicts["dynamic"].dynamic_candidates[0].transform_count, 2)

    def test_bag_internal_multiple_parent_is_reported_as_conflict(self) -> None:
        identity = (0.0, 0.0, 0.0, 1.0)
        with TemporaryDirectory(dir=TEST_OUTPUT_ROOT) as tmp:
            root = Path(tmp)
            urdf, bag = root / "fixture.urdf", root / "fixture.bag"
            _write_urdf(
                urdf,
                '<joint name="fixed" type="fixed"><parent link="urdf_parent"/><child link="child"/></joint>',
            )
            _write_bag(
                bag,
                static=(
                    ("bag_parent_a", "child", (0.0, 0.0, 0.0), identity),
                    ("bag_parent_b", "child", (0.0, 0.0, 0.0), identity),
                ),
            )
            result = analyze_inputs(bag, read_fixed_joints(urdf))
            self.assertEqual(result.classifications["child"], "conflict")
            self.assertIn("bag_static_multiple_parents", result.conflicts[0].reasons)
            self.assertEqual(len(result.conflicts[0].static_candidates), 2)

    def test_conflict_prompts_have_no_default_and_dynamic_delete_requires_yes(self) -> None:
        identity = (0.0, 0.0, 0.0, 1.0)
        with TemporaryDirectory(dir=TEST_OUTPUT_ROOT) as tmp:
            root = Path(tmp)
            urdf, bag = root / "fixture.urdf", root / "fixture.bag"
            _write_urdf(
                urdf,
                """
                <joint name="static" type="fixed"><parent link="base"/><child link="static"/></joint>
                <joint name="dynamic" type="fixed"><parent link="base"/><child link="dynamic"/></joint>
                """,
            )
            _write_bag(
                bag,
                static=(("other", "static", (0.0, 0.0, 0.0), identity),),
                dynamic=(("base", "dynamic", (0.0, 0.0, 0.0), identity),),
            )
            analysis = analyze_inputs(bag, read_fixed_joints(urdf))
            output = io.StringIO()
            choices = resolve_conflicts(
                analysis,
                input_stream=io.StringIO("\ninvalid\nu\nu\nno\nYES\nu\n"),
                output_stream=output,
            )
            self.assertEqual(choices, {"dynamic": "use_urdf", "static": "use_urdf"})
            rendered = output.getvalue()
            self.assertIn("no default", rendered)
            self.assertIn("Type YES", rendered)
            self.assertIn("Invalid choice", rendered)

    def test_keep_choices_and_final_confirmation_default_y(self) -> None:
        identity = (0.0, 0.0, 0.0, 1.0)
        with TemporaryDirectory(dir=TEST_OUTPUT_ROOT) as tmp:
            root = Path(tmp)
            urdf, bag = root / "fixture.urdf", root / "fixture.bag"
            _write_urdf(
                urdf,
                """
                <joint name="static" type="fixed"><parent link="base"/><child link="static"/></joint>
                <joint name="dynamic" type="fixed"><parent link="base"/><child link="dynamic"/></joint>
                """,
            )
            _write_bag(
                bag,
                static=(("other", "static", (0.0, 0.0, 0.0), identity),),
                dynamic=(("base", "dynamic", (0.0, 0.0, 0.0), identity),),
            )
            analysis = analyze_inputs(bag, read_fixed_joints(urdf))
            choices = resolve_conflicts(
                analysis,
                input_stream=io.StringIO("k\nb\n"),
                output_stream=io.StringIO(),
            )
            self.assertEqual(choices, {"dynamic": "keep_dynamic", "static": "keep_bag"})
        self.assertTrue(
            confirm_write(
                Path("output.bag"),
                input_stream=io.StringIO("\n"),
                output_stream=io.StringIO(),
            )
        )
        self.assertFalse(
            confirm_write(
                Path("output.bag"),
                input_stream=io.StringIO("no\n"),
                output_stream=io.StringIO(),
            )
        )

    def test_eof_aborts_without_a_default_conflict_choice(self) -> None:
        identity = (0.0, 0.0, 0.0, 1.0)
        with TemporaryDirectory(dir=TEST_OUTPUT_ROOT) as tmp:
            root = Path(tmp)
            urdf, bag = root / "fixture.urdf", root / "fixture.bag"
            _write_urdf(
                urdf,
                '<joint name="fixed" type="fixed"><parent link="base"/><child link="child"/></joint>',
            )
            _write_bag(
                bag,
                static=(("other", "child", (0.0, 0.0, 0.0), identity),),
            )
            analysis = analyze_inputs(bag, read_fixed_joints(urdf))
            with self.assertRaises(UserAbort):
                resolve_conflicts(
                    analysis,
                    input_stream=io.StringIO(""),
                    output_stream=io.StringIO(),
                )

    def test_decisions_round_trip_and_reject_hash_or_candidate_mismatch(self) -> None:
        identity = (0.0, 0.0, 0.0, 1.0)
        with TemporaryDirectory(dir=TEST_OUTPUT_ROOT) as tmp:
            root = Path(tmp)
            urdf, bag = root / "fixture.urdf", root / "fixture.bag"
            decisions = root / "decisions.json"
            _write_urdf(
                urdf,
                '<joint name="fixed" type="fixed"><parent link="base"/><child link="child"/></joint>',
            )
            _write_bag(
                bag,
                static=(("other", "child", (0.0, 0.0, 0.0), identity),),
            )
            analysis = analyze_inputs(bag, read_fixed_joints(urdf))
            save_decisions(
                decisions,
                analysis,
                {"child": "use_urdf"},
                bag_path=bag,
                urdf_path=urdf,
            )
            self.assertEqual(
                load_decisions(
                    decisions,
                    analysis,
                    bag_path=bag,
                    urdf_path=urdf,
                ),
                {"child": "use_urdf"},
            )

            document = json.loads(decisions.read_text(encoding="utf-8"))
            document["inputs"]["bag_sha256"] = "0" * 64
            decisions.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                load_decisions(
                    decisions,
                    analysis,
                    bag_path=bag,
                    urdf_path=urdf,
                )

            save_decisions(
                decisions,
                analysis,
                {"child": "use_urdf"},
                bag_path=bag,
                urdf_path=urdf,
            )
            document = json.loads(decisions.read_text(encoding="utf-8"))
            document["conflicts"][0]["candidate"]["urdf"]["parent"] = "changed"
            decisions.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "candidate set"):
                load_decisions(
                    decisions,
                    analysis,
                    bag_path=bag,
                    urdf_path=urdf,
                )

    def test_rewrite_normalizes_static_filters_selected_dynamic_and_preserves_data(self) -> None:
        identity = (0.0, 0.0, 0.0, 1.0)
        with TemporaryDirectory(dir=TEST_OUTPUT_ROOT) as tmp:
            root = Path(tmp)
            urdf, bag, output = (
                root / "fixture.urdf",
                root / "fixture.bag",
                root / "output.bag",
            )
            _write_urdf(
                urdf,
                """
                <joint name="same" type="fixed"><parent link="base"/><child link="same"/></joint>
                <joint name="missing" type="fixed"><parent link="base"/><child link="missing"/></joint>
                <joint name="static" type="fixed"><parent link="base"/><child link="static"/></joint>
                <joint name="dynamic" type="fixed"><parent link="base"/><child link="dynamic"/></joint>
                """,
            )
            _write_bag(
                bag,
                static=(
                    ("base", "same", (0.0, 0.0, 0.0), identity),
                    ("other", "static", (0.0, 0.0, 0.0), identity),
                ),
                dynamic=(
                    ("base", "dynamic", (0.0, 0.0, 0.0), identity),
                    ("odom", "moving", (0.0, 0.0, 0.0), identity),
                ),
            )
            before_hash = hashlib.sha256(bag.read_bytes()).hexdigest()
            analysis = analyze_inputs(bag, read_fixed_joints(urdf))
            choices = {"dynamic": "use_urdf", "static": "use_urdf"}
            plan = build_output_plan(analysis, choices)
            result = rewrite_bag(bag, output, plan)

            self.assertEqual(hashlib.sha256(bag.read_bytes()).hexdigest(), before_hash)
            self.assertEqual(
                set(_tf_edges(output, "/tf_static")),
                {
                    ("base", "same"),
                    ("base", "missing"),
                    ("base", "static"),
                    ("base", "dynamic"),
                },
            )
            self.assertEqual(_tf_edges(output, "/tf"), [("odom", "moving")] * 2)
            self.assertEqual(_topic_records(output, "/payload"), _topic_records(bag, "/payload"))
            self.assertEqual(result["deleted_dynamic_transforms"], 2)
            self.assertEqual(result["modified_dynamic_messages"], 2)
            self.assertEqual(result["fixed_coverage"], "4/4")
            with AnyReader([output]) as reader:
                static_connections = [
                    connection
                    for connection in reader.connections
                    if connection.topic == "/tf_static"
                ]
                self.assertEqual(len(static_connections), 1)
                self.assertEqual(static_connections[0].ext.latching, 1)
            self.assertFalse(output.with_name(output.stem + ".tmp.bag").exists())

    def test_keep_dynamic_skips_urdf_fixed_and_leaves_dynamic_raw_bytes(self) -> None:
        identity = (0.0, 0.0, 0.0, 1.0)
        with TemporaryDirectory(dir=TEST_OUTPUT_ROOT) as tmp:
            root = Path(tmp)
            urdf, bag, output = root / "fixture.urdf", root / "fixture.bag", root / "out.bag"
            _write_urdf(
                urdf,
                '<joint name="dynamic" type="fixed"><parent link="base"/><child link="dynamic"/></joint>',
            )
            _write_bag(
                bag,
                dynamic=(("base", "dynamic", (0.0, 0.0, 0.0), identity),),
            )
            analysis = analyze_inputs(bag, read_fixed_joints(urdf))
            plan = build_output_plan(analysis, {"dynamic": "keep_dynamic"})
            rewrite_bag(bag, output, plan)
            self.assertEqual(_topic_records(output, "/tf"), _topic_records(bag, "/tf"))
            self.assertNotIn(("base", "dynamic"), _tf_edges(output, "/tf_static"))

    def test_cli_dry_run_never_writes_and_decisions_yes_replays_noninteractively(self) -> None:
        identity = (0.0, 0.0, 0.0, 1.0)
        with TemporaryDirectory(dir=TEST_OUTPUT_ROOT) as tmp:
            root = Path(tmp)
            urdf, bag, output, decisions = (
                root / "fixture.urdf",
                root / "fixture.bag",
                root / "output.bag",
                root / "decisions.json",
            )
            _write_urdf(
                urdf,
                '<joint name="fixed" type="fixed"><parent link="base"/><child link="child"/></joint>',
            )
            _write_bag(
                bag,
                static=(("other", "child", (0.0, 0.0, 0.0), identity),),
            )
            stdout = io.StringIO()
            self.assertEqual(
                main(
                    [
                        "--input", str(bag), "--output", str(output),
                        "--urdf", str(urdf), "--dry-run",
                    ],
                    input_stream=io.StringIO(""),
                    output_stream=stdout,
                    error_stream=io.StringIO(),
                ),
                0,
            )
            self.assertFalse(output.exists())
            self.assertIn("DRY RUN", stdout.getvalue())

            analysis = analyze_inputs(bag, read_fixed_joints(urdf))
            save_decisions(
                decisions,
                analysis,
                {"child": "use_urdf"},
                bag_path=bag,
                urdf_path=urdf,
            )
            self.assertEqual(
                main(
                    [
                        "--input", str(bag), "--output", str(output),
                        "--urdf", str(urdf), "--decisions-in", str(decisions),
                        "--yes",
                    ],
                    input_stream=io.StringIO(""),
                    output_stream=io.StringIO(),
                    error_stream=io.StringIO(),
                ),
                0,
            )
            self.assertTrue(output.exists())
            self.assertIn(("base", "child"), _tf_edges(output, "/tf_static"))

    def test_cli_rejects_existing_output_and_incomplete_noninteractive_decisions(self) -> None:
        identity = (0.0, 0.0, 0.0, 1.0)
        with TemporaryDirectory(dir=TEST_OUTPUT_ROOT) as tmp:
            root = Path(tmp)
            urdf, bag, output = root / "fixture.urdf", root / "fixture.bag", root / "out.bag"
            _write_urdf(
                urdf,
                '<joint name="fixed" type="fixed"><parent link="base"/><child link="child"/></joint>',
            )
            _write_bag(
                bag,
                static=(("other", "child", (0.0, 0.0, 0.0), identity),),
            )
            output.write_bytes(b"keep")
            stderr = io.StringIO()
            self.assertEqual(
                main(
                    ["--input", str(bag), "--output", str(output), "--urdf", str(urdf), "--yes"],
                    input_stream=io.StringIO(""),
                    output_stream=io.StringIO(),
                    error_stream=stderr,
                ),
                1,
            )
            self.assertEqual(output.read_bytes(), b"keep")
            output.unlink()
            stderr = io.StringIO()
            self.assertEqual(
                main(
                    ["--input", str(bag), "--output", str(output), "--urdf", str(urdf), "--yes"],
                    input_stream=io.StringIO(""),
                    output_stream=io.StringIO(),
                    error_stream=stderr,
                ),
                1,
            )
            self.assertFalse(output.exists())
            self.assertIn("non-interactive", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
