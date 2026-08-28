"""Tests for the feedback-driven dexterous-hand TF converter."""

from __future__ import annotations

import hashlib
import io
import math
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import add_dexhand_tf  # noqa: E402
from add_dexhand_tf import (  # noqa: E402
    DEFAULT_STATE_TOPIC,
    FEEDBACK_TO_JOINT_SUFFIXES,
    MappedState,
    UrdfJoint,
    analyze_inputs,
    main,
    map_feedback,
    parse_args,
    read_hand_joints,
    required_feedback_names,
    scale_mapped_state,
    target_joint_names,
    transform_for_joint,
)
from rosbags.highlevel import AnyReader  # noqa: E402
from rosbags.rosbag1 import Writer  # noqa: E402
from rosbags.typesys import Stores, get_types_from_msg, get_typestore  # noqa: E402

TEST_OUTPUT_ROOT = ROOT / "test_output/issue-020/unit"


def _joint(name: str, *, axis=(1.0, 0.0, 0.0), lower=0.0, upper=2.0):
    return UrdfJoint(
        name=name,
        parent=f"{name}_parent",
        child=f"{name}_child",
        origin_xyz=(1.0, 2.0, 3.0),
        origin_rpy=(0.0, 0.0, 0.0),
        axis=axis,
        lower=lower,
        upper=upper,
    )


def _minimal_hand_urdf() -> str:
    links = {"l_palm", "r_palm"}
    joints = []
    for name in target_joint_names():
        side, suffix = name.split("_", 1)
        if suffix == "thumbCMC":
            parent, child = f"{side}_palm", f"{side}_thumb_prox"
        elif suffix == "thumbMCP":
            parent, child = f"{side}_thumb_prox", f"{side}_thumb_dist"
        elif suffix.endswith("MCP"):
            finger = suffix[:-3].lower()
            parent, child = f"{side}_palm", f"{side}_{finger}_prox"
        else:
            finger = suffix[:-3].lower()
            parent, child = f"{side}_{finger}_prox", f"{side}_{finger}_dist"
        links.update((parent, child))
        joints.append(
            f'<joint name="{name}" type="revolute">'
            f'<parent link="{parent}"/><child link="{child}"/>'
            '<origin xyz="1 2 3" rpy="0 0 0"/>'
            '<axis xyz="2 0 0"/><limit lower="-1" upper="3"/>'
            '</joint>'
        )
    return '<robot name="fixture">' + "".join(
        f'<link name="{name}"/>' for name in sorted(links)
    ) + "".join(joints) + '</robot>'


def _tf_message(typestore, edges):
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
                    stamp=Time(sec=1, nanosec=0),
                    frame_id=parent,
                ),
                child_frame_id=child,
                transform=Transform(
                    translation=Vector3(x=0.0, y=0.0, z=0.0),
                    rotation=Quaternion(x=0.0, y=0.0, z=0.0, w=1.0),
                ),
            )
            for parent, child in edges
        ]
    )


def _joint_state(typestore, names, positions, *, stamp=(2, 0)):
    Header = typestore.types["std_msgs/msg/Header"]
    Time = typestore.types["builtin_interfaces/msg/Time"]
    JointState = typestore.types["sensor_msgs/msg/JointState"]
    return JointState(
        header=Header(
            seq=0,
            stamp=Time(sec=stamp[0], nanosec=stamp[1]),
            frame_id="",
        ),
        name=list(names),
        position=np.asarray(positions, dtype=np.float64),
        velocity=np.asarray([], dtype=np.float64),
        effort=np.asarray([], dtype=np.float64),
    )


def _write_fixture_bag(
    path: Path,
    *,
    tf_edges=(("base_link", "l_palm"), ("base_link", "r_palm")),
    states=None,
) -> None:
    typestore = get_typestore(Stores.ROS1_NOETIC)
    typestore.register(
        get_types_from_msg(
            "geometry_msgs/TransformStamped[] transforms",
            "tf2_msgs/msg/TFMessage",
        )
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    states = states or [
        (required_feedback_names(), [0.0] * 12, (0, 0)),
        (tuple(reversed(required_feedback_names())), [50.0] * 12, (3, 0)),
    ]
    with Writer(path) as writer:
        static_connection = writer.add_connection(
            "/tf_static", "tf2_msgs/msg/TFMessage", typestore=typestore, latching=1
        )
        state_connection = writer.add_connection(
            "/dexhand/state", "sensor_msgs/msg/JointState", typestore=typestore
        )
        payload_connection = writer.add_connection(
            "/payload", "std_msgs/msg/String", typestore=typestore
        )
        tf_raw = typestore.serialize_ros1(
            _tf_message(typestore, tf_edges), "tf2_msgs/msg/TFMessage"
        )
        writer.write(static_connection, 1_000_000_000, tf_raw)
        for index, (names, positions, stamp) in enumerate(states):
            raw = typestore.serialize_ros1(
                _joint_state(typestore, names, positions, stamp=stamp),
                "sensor_msgs/msg/JointState",
            )
            writer.write(state_connection, 2_000_000_000 + index * 1_000_000_000, raw)
        String = typestore.types["std_msgs/msg/String"]
        writer.write(
            payload_connection,
            5_000_000_000,
            typestore.serialize_ros1(String(data="preserve"), "std_msgs/msg/String"),
        )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _connection_metadata(connection):
    return (
        connection.topic,
        connection.msgtype,
        connection.msgdef.data,
        connection.digest,
        getattr(connection.ext, "callerid", None),
        getattr(connection.ext, "latching", None),
    )


def _original_records(path: Path):
    records = []
    with AnyReader([path]) as reader:
        for connection, timestamp, rawdata in reader.messages():
            if getattr(connection.ext, "callerid", None) == add_dexhand_tf.NEW_TF_CALLER_ID:
                continue
            records.append((_connection_metadata(connection), timestamp, rawdata))
    return records


class InterfaceContractTests(unittest.TestCase):
    def test_cli_defaults_and_flags_are_frozen(self) -> None:
        args = parse_args(
            [
                "--input",
                "input.bag",
                "--output",
                "output.bag",
                "--urdf",
                "robot.urdf",
                "--dry-run",
                "--overwrite",
            ]
        )
        self.assertEqual(args.input, Path("input.bag"))
        self.assertEqual(args.output, Path("output.bag"))
        self.assertEqual(args.urdf, Path("robot.urdf"))
        self.assertEqual(args.state_topic, DEFAULT_STATE_TOPIC)
        self.assertTrue(args.dry_run)
        self.assertTrue(args.overwrite)

    def test_mapping_contract_has_12_feedback_names_and_20_joints(self) -> None:
        self.assertEqual(
            FEEDBACK_TO_JOINT_SUFFIXES,
            {
                "thumb_aux": ("thumbCMC",),
                "thumb": ("thumbMCP",),
                "index": ("indexMCP", "indexPIP"),
                "middle": ("middleMCP", "middlePIP"),
                "ring": ("ringMCP", "ringPIP"),
                "pinky": ("littleMCP", "littlePIP"),
            },
        )
        self.assertEqual(len(required_feedback_names()), 12)
        self.assertEqual(len(set(required_feedback_names())), 12)
        self.assertEqual(len(target_joint_names()), 20)
        self.assertEqual(len(set(target_joint_names())), 20)
        self.assertIn("l_thumbCMC", target_joint_names())
        self.assertIn("r_littlePIP", target_joint_names())


class KinematicsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        TEST_OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    def test_reads_exact_target_joints_and_normalizes_axis(self) -> None:
        with TemporaryDirectory(dir=TEST_OUTPUT_ROOT) as tmp:
            path = Path(tmp) / "hand.urdf"
            path.write_text(_minimal_hand_urdf(), encoding="utf-8")
            joints = read_hand_joints(path)
        self.assertEqual(tuple(joints), target_joint_names())
        self.assertEqual(joints["l_thumbCMC"].axis, (1.0, 0.0, 0.0))
        self.assertEqual(joints["r_littlePIP"].lower, -1.0)
        self.assertEqual(joints["r_littlePIP"].upper, 3.0)

    def test_rejects_missing_wrong_type_axis_limit_and_duplicate_child(self) -> None:
        replacements = (
            ('name="l_thumbCMC"', 'name="not_thumbCMC"'),
            ('name="l_thumbCMC" type="revolute"', 'name="l_thumbCMC" type="fixed"'),
            ('<axis xyz="2 0 0"/>', '<axis xyz="0 0 0"/>'),
            ('<limit lower="-1" upper="3"/>', '<limit lower="4" upper="3"/>'),
            ('child link="l_thumb_dist"', 'child link="l_thumb_prox"'),
        )
        with TemporaryDirectory(dir=TEST_OUTPUT_ROOT) as tmp:
            for index, (old, new) in enumerate(replacements):
                with self.subTest(index=index):
                    path = Path(tmp) / f"invalid-{index}.urdf"
                    path.write_text(
                        _minimal_hand_urdf().replace(old, new, 1), encoding="utf-8"
                    )
                    with self.assertRaises(ValueError):
                        read_hand_joints(path)

    def test_name_driven_mapping_scales_zero_midpoint_and_full_close(self) -> None:
        names = list(reversed(required_feedback_names()))
        positions = [50.0] * len(names)
        positions[names.index("l_thumb_aux")] = 0.0
        positions[names.index("r_pinky")] = 100.0
        mapped = map_feedback(names, positions)
        joints = {
            name: _joint(name, lower=-1.0, upper=3.0)
            for name in target_joint_names()
        }
        scaled = scale_mapped_state(mapped, joints)
        self.assertEqual(scaled.angles["l_thumbCMC"], -1.0)
        self.assertEqual(scaled.angles["l_thumbMCP"], 1.0)
        self.assertEqual(scaled.angles["l_indexMCP"], 1.0)
        self.assertEqual(scaled.angles["l_indexPIP"], 1.0)
        self.assertEqual(scaled.angles["r_littleMCP"], 3.0)
        self.assertEqual(scaled.angles["r_littlePIP"], 3.0)

    def test_feedback_clips_only_out_of_range_finite_values(self) -> None:
        names = list(required_feedback_names())
        positions = [50.0] * len(names)
        positions[names.index("l_index")] = -1.0
        positions[names.index("r_ring")] = 101.0
        mapped = map_feedback(names, positions)
        self.assertEqual(mapped.clipped_low, ("l_index",))
        self.assertEqual(mapped.clipped_high, ("r_ring",))
        self.assertEqual(mapped.angles["l_indexMCP"], 0.0)
        self.assertEqual(mapped.angles["r_ringPIP"], 1.0)

        invalid_samples = (
            (names[:-1], positions),
            (names, positions[:-1]),
            (names[:-1] + [names[0]], positions),
            (names[:-1], positions[:-1]),
            (names, positions[:-1] + [float("nan")]),
            (names, positions[:-1] + [float("inf")]),
        )
        for bad_names, bad_positions in invalid_samples:
            with self.subTest(names=len(bad_names), positions=len(bad_positions)):
                with self.assertRaises(ValueError):
                    map_feedback(bad_names, bad_positions)

    def test_transform_composes_origin_then_axis_rotation(self) -> None:
        joint = UrdfJoint(
            name="l_indexMCP",
            parent="l_palm",
            child="l_index_prox",
            origin_xyz=(1.0, 2.0, 3.0),
            origin_rpy=(0.0, 0.0, math.pi / 2.0),
            axis=(1.0, 0.0, 0.0),
            lower=0.0,
            upper=math.pi,
        )
        transform = transform_for_joint(joint, math.pi / 2.0)
        self.assertEqual(transform.translation, (1.0, 2.0, 3.0))
        for component in transform.rotation:
            self.assertAlmostEqual(component, 0.5, places=12)

        mirrored = _joint("r_thumbCMC", axis=(0.0, -1.0, 0.0))
        mirrored_transform = transform_for_joint(mirrored, math.pi)
        self.assertAlmostEqual(mirrored_transform.rotation[1], -1.0, places=12)


class BagAnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        TEST_OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    def test_scans_complete_state_and_tf_inputs_without_writing(self) -> None:
        with TemporaryDirectory(dir=TEST_OUTPUT_ROOT) as tmp:
            root = Path(tmp)
            bag = root / "input.bag"
            urdf = root / "hand.urdf"
            urdf.write_text(_minimal_hand_urdf(), encoding="utf-8")
            _write_fixture_bag(bag)
            before = (_sha256(bag), _sha256(urdf))
            result = analyze_inputs(bag, urdf)
            after = (_sha256(bag), _sha256(urdf))
        self.assertEqual(before, after)
        self.assertEqual(result.total_messages, 4)
        self.assertEqual(result.state_messages, 2)
        self.assertEqual(result.timestamp_fallbacks, 1)
        self.assertEqual(len(result.joints), 20)
        self.assertEqual(result.expected_tf_messages, 2)
        self.assertEqual(result.expected_transforms, 40)
        self.assertEqual(result.expected_output_messages, 6)
        self.assertEqual(result.channel_stats["l_index"].minimum, 0.0)
        self.assertEqual(result.channel_stats["l_index"].maximum, 50.0)

    def test_rejects_tf_conflicts_unreachable_palms_multi_parent_and_cycles(self) -> None:
        cases = (
            (("base_link", "l_palm"), ("base_link", "r_palm"), ("l_palm", "l_thumb_prox")),
            (("base_link", "l_palm"),),
            (("base_link", "l_palm"), ("other", "l_palm"), ("base_link", "r_palm")),
            (("base_link", "l_palm"), ("l_palm", "base_link"), ("base_link", "r_palm")),
        )
        with TemporaryDirectory(dir=TEST_OUTPUT_ROOT) as tmp:
            root = Path(tmp)
            urdf = root / "hand.urdf"
            urdf.write_text(_minimal_hand_urdf(), encoding="utf-8")
            for index, edges in enumerate(cases):
                with self.subTest(index=index):
                    bag = root / f"invalid-{index}.bag"
                    _write_fixture_bag(bag, tf_edges=edges)
                    with self.assertRaises(ValueError):
                        analyze_inputs(bag, urdf)

    def test_rejects_invalid_state_before_any_output_is_created(self) -> None:
        names = required_feedback_names()
        invalid_states = (
            (names[:-1], [0.0] * 11, (1, 0)),
            (names, [0.0] * 11 + [float("nan")], (1, 0)),
            (names[:-1] + (names[0],), [0.0] * 12, (1, 0)),
            (names, [0.0] * 12, (1, 1_000_000_000)),
        )
        with TemporaryDirectory(dir=TEST_OUTPUT_ROOT) as tmp:
            root = Path(tmp)
            urdf = root / "hand.urdf"
            urdf.write_text(_minimal_hand_urdf(), encoding="utf-8")
            for index, state in enumerate(invalid_states):
                with self.subTest(index=index):
                    bag = root / f"invalid-state-{index}.bag"
                    output = root / f"output-{index}.bag"
                    _write_fixture_bag(bag, states=[state])
                    error = io.StringIO()
                    code = main(
                        [
                            "--input", str(bag),
                            "--output", str(output),
                            "--urdf", str(urdf),
                            "--dry-run",
                        ],
                        output_stream=io.StringIO(),
                        error_stream=error,
                    )
                    self.assertEqual(code, 1)
                    self.assertFalse(output.exists())
                    self.assertIn("no output created", error.getvalue())

    def test_dry_run_reports_prediction_and_never_creates_output(self) -> None:
        with TemporaryDirectory(dir=TEST_OUTPUT_ROOT) as tmp:
            root = Path(tmp)
            bag = root / "input.bag"
            output = root / "output.bag"
            urdf = root / "hand.urdf"
            urdf.write_text(_minimal_hand_urdf(), encoding="utf-8")
            _write_fixture_bag(bag)
            stdout = io.StringIO()
            code = main(
                [
                    "--input", str(bag),
                    "--output", str(output),
                    "--urdf", str(urdf),
                    "--dry-run",
                ],
                output_stream=stdout,
                error_stream=io.StringIO(),
            )
            self.assertEqual(code, 0)
            self.assertFalse(output.exists())
            self.assertIn("TFMessage=2 TransformStamped=40", stdout.getvalue())
            self.assertIn("topology=PASS", stdout.getvalue())

            clipped_bag = root / "clipped.bag"
            clipped_positions = [0.0] * len(required_feedback_names())
            clipped_positions[required_feedback_names().index("l_index")] = 101.0
            _write_fixture_bag(
                clipped_bag,
                states=[(required_feedback_names(), clipped_positions, (1, 0))],
            )
            clipped_stdout = io.StringIO()
            code = main(
                [
                    "--input", str(clipped_bag),
                    "--output", str(root / "clipped-output.bag"),
                    "--urdf", str(urdf),
                    "--dry-run",
                ],
                output_stream=clipped_stdout,
                error_stream=io.StringIO(),
            )
            self.assertEqual(code, 0)
            self.assertIn(
                "WARNING: 1 feedback values will be clipped",
                clipped_stdout.getvalue(),
            )


class BagRewriteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        TEST_OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    def test_writes_one_20_transform_message_per_state_and_preserves_originals(self) -> None:
        with TemporaryDirectory(dir=TEST_OUTPUT_ROOT) as tmp:
            root = Path(tmp)
            bag = root / "input.bag"
            output = root / "output.bag"
            urdf = root / "hand.urdf"
            urdf.write_text(_minimal_hand_urdf(), encoding="utf-8")
            _write_fixture_bag(bag)
            input_hash = _sha256(bag)
            stdout = io.StringIO()
            code = main(
                [
                    "--input", str(bag),
                    "--output", str(output),
                    "--urdf", str(urdf),
                ],
                output_stream=stdout,
                error_stream=io.StringIO(),
            )
            self.assertEqual(code, 0)
            self.assertTrue(output.is_file())
            self.assertEqual(_sha256(bag), input_hash)
            self.assertEqual(_original_records(output), _original_records(bag))

            generated = []
            with AnyReader([output]) as reader:
                new_connections = [
                    connection
                    for connection in reader.connections
                    if getattr(connection.ext, "callerid", None)
                    == add_dexhand_tf.NEW_TF_CALLER_ID
                ]
                self.assertEqual(len(new_connections), 1)
                self.assertEqual(new_connections[0].ext.latching, 0)
                for connection, timestamp, rawdata in reader.messages(
                    connections=new_connections
                ):
                    generated.append(
                        (timestamp, reader.deserialize(rawdata, connection.msgtype))
                    )
            self.assertEqual([timestamp for timestamp, _ in generated], [2_000_000_000, 3_000_000_000])
            self.assertEqual([len(message.transforms) for _, message in generated], [20, 20])
            self.assertEqual(generated[0][1].transforms[0].header.stamp.sec, 2)
            self.assertEqual(generated[1][1].transforms[0].header.stamp.sec, 3)
            self.assertEqual(
                {transform.child_frame_id for transform in generated[0][1].transforms},
                {joint.child for joint in read_hand_joints(urdf).values()},
            )
            self.assertIn("generated /tf messages and 40 transforms", stdout.getvalue())

    def test_default_overwrite_and_same_path_protections(self) -> None:
        with TemporaryDirectory(dir=TEST_OUTPUT_ROOT) as tmp:
            root = Path(tmp)
            bag = root / "input.bag"
            output = root / "output.bag"
            urdf = root / "hand.urdf"
            urdf.write_text(_minimal_hand_urdf(), encoding="utf-8")
            _write_fixture_bag(bag)
            output.write_bytes(b"existing")
            error = io.StringIO()
            code = main(
                ["--input", str(bag), "--output", str(output), "--urdf", str(urdf)],
                output_stream=io.StringIO(),
                error_stream=error,
            )
            self.assertEqual(code, 1)
            self.assertEqual(output.read_bytes(), b"existing")
            self.assertIn("use --overwrite", error.getvalue())

            code = main(
                [
                    "--input", str(bag),
                    "--output", str(output),
                    "--urdf", str(urdf),
                    "--overwrite",
                ],
                output_stream=io.StringIO(),
                error_stream=io.StringIO(),
            )
            self.assertEqual(code, 0)
            self.assertNotEqual(output.read_bytes(), b"existing")

            code = main(
                [
                    "--input", str(bag),
                    "--output", str(bag),
                    "--urdf", str(urdf),
                    "--dry-run",
                ],
                output_stream=io.StringIO(),
                error_stream=io.StringIO(),
            )
            self.assertEqual(code, 1)

    def test_validation_failure_preserves_existing_output_and_cleans_exact_temp(self) -> None:
        with TemporaryDirectory(dir=TEST_OUTPUT_ROOT) as tmp:
            root = Path(tmp)
            bag = root / "input.bag"
            output = root / "output.bag"
            urdf = root / "hand.urdf"
            urdf.write_text(_minimal_hand_urdf(), encoding="utf-8")
            _write_fixture_bag(bag)
            output.write_bytes(b"keep-existing")
            analysis = analyze_inputs(bag, urdf)
            with patch.object(
                add_dexhand_tf,
                "validate_output",
                side_effect=ValueError("injected validation failure"),
            ):
                with self.assertRaisesRegex(ValueError, "injected validation failure"):
                    add_dexhand_tf.rewrite_bag(bag, output, analysis)
            self.assertEqual(output.read_bytes(), b"keep-existing")
            self.assertEqual(list(root.glob(".output.bag.*.tmp.bag")), [])


if __name__ == "__main__":
    unittest.main()
