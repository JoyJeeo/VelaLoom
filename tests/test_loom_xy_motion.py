"""Tests for the configurable horizontal synthetic-motion converter."""

from __future__ import annotations

import math
import io
import sys
import unittest
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import loom_xy_motion  # noqa: E402
from loom_xy_motion import (  # noqa: E402
    DIRECTION_VECTORS,
    InteractionCancelled,
    MotionPlan,
    analyze_bag,
    confirm_writing,
    create_motion_plan,
    direction_in_odom,
    minimum_jerk_progress,
    main,
    parse_args,
    parse_time_seconds,
    resolve_output_path,
    rewrite_bag,
    trajectory_position,
    validate_output,
)
from rosbags.highlevel import AnyReader  # noqa: E402
from rosbags.rosbag1 import Writer  # noqa: E402
from rosbags.typesys import Stores, get_types_from_msg, get_typestore  # noqa: E402


TEST_OUTPUT_ROOT = ROOT / "test_output/issue-021/unit"


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _connection_metadata(connection):
    return (
        connection.topic,
        connection.msgtype,
        connection.msgdef.data,
        connection.digest,
        getattr(connection.ext, "callerid", None),
        getattr(connection.ext, "latching", None),
    )


def _transform_snapshot(transform):
    return (
        int(transform.header.seq),
        int(transform.header.stamp.sec),
        int(transform.header.stamp.nanosec),
        str(transform.header.frame_id),
        str(transform.child_frame_id),
        float(transform.transform.translation.x),
        float(transform.transform.translation.y),
        float(transform.transform.translation.z),
        float(transform.transform.rotation.x),
        float(transform.transform.rotation.y),
        float(transform.transform.rotation.z),
        float(transform.transform.rotation.w),
    )


def _transform(
    typestore,
    parent: str,
    child: str,
    stamp_ns: int,
    *,
    xyz=(0.0, 0.0, 0.0),
    quaternion=(0.0, 0.0, 0.0, 1.0),
):
    Header = typestore.types["std_msgs/msg/Header"]
    Time = typestore.types["builtin_interfaces/msg/Time"]
    TransformStamped = typestore.types["geometry_msgs/msg/TransformStamped"]
    Transform = typestore.types["geometry_msgs/msg/Transform"]
    Vector3 = typestore.types["geometry_msgs/msg/Vector3"]
    Quaternion = typestore.types["geometry_msgs/msg/Quaternion"]
    seconds, nanoseconds = divmod(stamp_ns, 1_000_000_000)
    return TransformStamped(
        header=Header(
            seq=7,
            stamp=Time(sec=seconds, nanosec=nanoseconds),
            frame_id=parent,
        ),
        child_frame_id=child,
        transform=Transform(
            translation=Vector3(x=xyz[0], y=xyz[1], z=xyz[2]),
            rotation=Quaternion(
                x=quaternion[0],
                y=quaternion[1],
                z=quaternion[2],
                w=quaternion[3],
            ),
        ),
    )


def _write_fixture_bag(
    path: Path,
    *,
    target_stamps=(10, 11, 12, 13, 14),
    include_target=True,
    duplicate_in_message=False,
    second_target_connection=False,
    static_target=False,
    target_x_values=None,
    quaternion=(0.0, 0.0, 0.0, 1.0),
) -> None:
    typestore = get_typestore(Stores.ROS1_NOETIC)
    typestore.register(
        get_types_from_msg(
            "geometry_msgs/TransformStamped[] transforms",
            "tf2_msgs/msg/TFMessage",
        )
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    TFMessage = typestore.types["tf2_msgs/msg/TFMessage"]
    String = typestore.types["std_msgs/msg/String"]
    values = target_x_values or [float(index) / 10.0 for index in range(len(target_stamps))]
    with Writer(path) as writer:
        tf_connection = writer.add_connection(
            "/tf",
            "tf2_msgs/msg/TFMessage",
            typestore=typestore,
            callerid="/robot_state",
            latching=0,
        )
        payload_connection = writer.add_connection(
            "/payload", "std_msgs/msg/String", typestore=typestore
        )
        second_connection = None
        if second_target_connection:
            second_connection = writer.add_connection(
                "/tf",
                "tf2_msgs/msg/TFMessage",
                typestore=typestore,
                callerid="/duplicate_root",
                latching=0,
            )
        static_connection = None
        if static_target:
            static_connection = writer.add_connection(
                "/tf_static",
                "tf2_msgs/msg/TFMessage",
                typestore=typestore,
                callerid="/bad_static_root",
                latching=1,
            )
        for index, stamp_s in enumerate(target_stamps):
            stamp_ns = int(stamp_s * 1_000_000_000)
            transforms = [
                _transform(
                    typestore,
                    "base_link",
                    "body_link",
                    stamp_ns,
                    xyz=(1.0, 2.0, 3.0),
                )
            ]
            if include_target:
                target = _transform(
                    typestore,
                    "odom",
                    "base_link",
                    stamp_ns,
                    xyz=(values[index], -values[index], 0.75),
                    quaternion=quaternion,
                )
                transforms.insert(0, target)
                if duplicate_in_message and index == 0:
                    transforms.insert(1, target)
            raw = typestore.serialize_ros1(
                TFMessage(transforms=transforms), "tf2_msgs/msg/TFMessage"
            )
            writer.write(tf_connection, 20_000_000_000 + index, raw)
        if second_connection is not None:
            stamp_ns = 15_000_000_000
            raw = typestore.serialize_ros1(
                TFMessage(
                    transforms=[
                        _transform(
                            typestore,
                            "odom",
                            "base_link",
                            stamp_ns,
                            xyz=(0.5, -0.5, 0.75),
                        )
                    ]
                ),
                "tf2_msgs/msg/TFMessage",
            )
            writer.write(second_connection, 21_000_000_000, raw)
        if static_connection is not None:
            raw = typestore.serialize_ros1(
                TFMessage(
                    transforms=[
                        _transform(
                            typestore,
                            "odom",
                            "base_link",
                            10_000_000_000,
                            xyz=(0.0, 0.0, 0.75),
                        )
                    ]
                ),
                "tf2_msgs/msg/TFMessage",
            )
            writer.write(static_connection, 22_000_000_000, raw)
        writer.write(
            payload_connection,
            30_000_000_000,
            typestore.serialize_ros1(String(data="preserve"), "std_msgs/msg/String"),
        )


class InterfaceAndTrajectoryTests(unittest.TestCase):
    def test_six_business_arguments_and_only_dry_run_are_exposed(self) -> None:
        args = parse_args(
            [
                "--input",
                "input.bag",
                "--output",
                "output.bag",
                "--direction",
                "robot-left",
                "--distance-m",
                "1.25",
                "--start-s",
                "00:02.5",
                "--end-s",
                "00:01:20.5",
                "--dry-run",
            ]
        )
        self.assertEqual(args.input, Path("input.bag"))
        self.assertEqual(args.output, Path("output.bag"))
        self.assertEqual(args.direction, "robot-left")
        self.assertEqual(args.distance_m, 1.25)
        self.assertEqual(args.start_s, 2.5)
        self.assertEqual(args.end_s, 80.5)
        self.assertTrue(args.dry_run)

    def test_time_parser_accepts_all_three_formats(self) -> None:
        cases = {
            "2.5": 2.5,
            "00:02.5": 2.5,
            "12:34.25": 754.25,
            "00:01:20.5": 80.5,
            "12:34:56.75": 45296.75,
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(parse_time_seconds(raw), expected)

    def test_time_parser_rejects_invalid_or_non_finite_values(self) -> None:
        for raw in ("", "nan", "inf", "-1", "1:60", "1:2:60", "1::2", "a"):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    parse_time_seconds(raw)

    def test_four_local_directions_are_frozen(self) -> None:
        self.assertEqual(
            DIRECTION_VECTORS,
            {
                "robot-up": (1.0, 0.0),
                "robot-down": (-1.0, 0.0),
                "robot-left": (0.0, 1.0),
                "robot-right": (0.0, -1.0),
            },
        )
        yaw_90 = (0.0, 0.0, math.sqrt(0.5), math.sqrt(0.5))
        expected = {
            "robot-up": (0.0, 1.0),
            "robot-down": (0.0, -1.0),
            "robot-left": (-1.0, 0.0),
            "robot-right": (1.0, 0.0),
        }
        for direction, vector in expected.items():
            with self.subTest(direction=direction):
                actual = direction_in_odom(direction, yaw_90)
                self.assertAlmostEqual(actual[0], vector[0], places=12)
                self.assertAlmostEqual(actual[1], vector[1], places=12)

    def test_minimum_jerk_is_clamped_and_has_zero_endpoint_derivatives(self) -> None:
        self.assertEqual(minimum_jerk_progress(-1.0), 0.0)
        self.assertEqual(minimum_jerk_progress(0.0), 0.0)
        self.assertEqual(minimum_jerk_progress(1.0), 1.0)
        self.assertEqual(minimum_jerk_progress(2.0), 1.0)
        values = [minimum_jerk_progress(index / 100.0) for index in range(101)]
        self.assertTrue(all(left <= right for left, right in zip(values, values[1:])))
        epsilon = 1e-5
        start_velocity = minimum_jerk_progress(epsilon) / epsilon
        end_velocity = (1.0 - minimum_jerk_progress(1.0 - epsilon)) / epsilon
        self.assertLess(start_velocity, 2e-9)
        self.assertLess(end_velocity, 2e-9)

    def test_trajectory_stops_before_and_after_exact_straight_motion(self) -> None:
        plan = MotionPlan(
            direction="robot-left",
            local_direction=(0.0, 1.0),
            odom_direction=(0.6, 0.8),
            distance_m=2.5,
            start_s=2.0,
            end_s=6.0,
            initial_xy=(4.0, -3.0),
        )
        self.assertEqual(trajectory_position(plan, -1.0), (4.0, -3.0))
        self.assertEqual(trajectory_position(plan, 2.0), (4.0, -3.0))
        self.assertEqual(trajectory_position(plan, 6.0), (5.5, -1.0))
        self.assertEqual(trajectory_position(plan, 100.0), (5.5, -1.0))
        projections = []
        for index in range(101):
            x, y = trajectory_position(plan, 2.0 + 4.0 * index / 100.0)
            dx, dy = x - 4.0, y + 3.0
            projections.append(dx * 0.6 + dy * 0.8)
            lateral = dx * -0.8 + dy * 0.6
            self.assertLessEqual(abs(lateral), 1e-12)
        self.assertTrue(
            all(left <= right for left, right in zip(projections, projections[1:]))
        )
        self.assertAlmostEqual(plan.theoretical_max_speed, 1.171875, places=12)


class BagAnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        TEST_OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    def test_scans_target_connection_timing_pose_and_topology_read_only(self) -> None:
        with TemporaryDirectory(dir=TEST_OUTPUT_ROOT) as temporary:
            bag = Path(temporary) / "input.bag"
            _write_fixture_bag(bag)
            before = _sha256(bag)
            analysis = analyze_bag(bag)
            after = _sha256(bag)
        self.assertEqual(before, after)
        self.assertEqual(analysis.input_sha256, before)
        self.assertEqual(analysis.total_messages, 6)
        self.assertEqual(analysis.target_count, 5)
        self.assertEqual(analysis.duration_seconds, 4.0)
        self.assertEqual(analysis.initial_xy, (0.0, -0.0))
        self.assertEqual(analysis.initial_quaternion, (0.0, 0.0, 0.0, 1.0))
        self.assertEqual(analysis.roots, ("odom",))
        self.assertEqual(analysis.target_callerid, "/robot_state")

        plan = create_motion_plan(
            analysis,
            direction="robot-right",
            distance_m=1.5,
            start_s=1.0,
            end_s=3.0,
        )
        self.assertEqual(plan.odom_direction, (0.0, -1.0))
        self.assertEqual(plan.final_xy, (0.0, -1.5))

    def test_rejects_missing_duplicate_publishers_and_repeated_target(self) -> None:
        cases = (
            {"include_target": False},
            {"second_target_connection": True},
            {"duplicate_in_message": True},
            {"static_target": True},
        )
        with TemporaryDirectory(dir=TEST_OUTPUT_ROOT) as temporary:
            root = Path(temporary)
            for index, kwargs in enumerate(cases):
                with self.subTest(index=index):
                    bag = root / f"invalid-{index}.bag"
                    _write_fixture_bag(bag, **kwargs)
                    with self.assertRaises(ValueError):
                        analyze_bag(bag)

    def test_rejects_non_finite_pose_time_regression_and_bad_quaternion(self) -> None:
        cases = (
            {"target_x_values": [0.0, 0.1, float("nan"), 0.3, 0.4]},
            {"target_stamps": (10, 11, 9, 13, 14)},
            {"quaternion": (0.0, 0.0, 0.0, 0.0)},
        )
        with TemporaryDirectory(dir=TEST_OUTPUT_ROOT) as temporary:
            root = Path(temporary)
            for index, kwargs in enumerate(cases):
                with self.subTest(index=index):
                    bag = root / f"invalid-pose-{index}.bag"
                    _write_fixture_bag(bag, **kwargs)
                    with self.assertRaises(ValueError):
                        analyze_bag(bag)

    def test_rejects_invalid_motion_time_ranges(self) -> None:
        with TemporaryDirectory(dir=TEST_OUTPUT_ROOT) as temporary:
            bag = Path(temporary) / "input.bag"
            _write_fixture_bag(bag)
            analysis = analyze_bag(bag)
        for start, end in ((-1.0, 1.0), (1.0, 1.0), (2.0, 1.0), (0.0, 4.1)):
            with self.subTest(start=start, end=end):
                with self.assertRaises(ValueError):
                    create_motion_plan(
                        analysis,
                        direction="robot-up",
                        distance_m=1.0,
                        start_s=start,
                        end_s=end,
                    )


class InteractionAndDryRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        TEST_OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    def test_conflict_can_change_only_directory_without_creating_it(self) -> None:
        with TemporaryDirectory(dir=TEST_OUTPUT_ROOT) as temporary:
            root = Path(temporary)
            input_bag = root / "input.bag"
            output_bag = root / "output.bag"
            input_bag.write_bytes(b"input")
            output_bag.write_bytes(b"existing")
            new_directory = root / "not-created"
            resolved = resolve_output_path(
                input_bag,
                output_bag,
                input_stream=io.StringIO(f"1\n{new_directory}\n"),
                output_stream=io.StringIO(),
                is_tty=True,
            )
            self.assertEqual(resolved, (new_directory / "output.bag").resolve())
            self.assertFalse(new_directory.exists())
            self.assertEqual(output_bag.read_bytes(), b"existing")

    def test_rename_repeats_after_second_conflict_and_appends_bag_suffix(self) -> None:
        with TemporaryDirectory(dir=TEST_OUTPUT_ROOT) as temporary:
            root = Path(temporary)
            input_bag = root / "input.bag"
            output_bag = root / "output.bag"
            second = root / "second.bag"
            input_bag.write_bytes(b"input")
            output_bag.write_bytes(b"existing")
            second.write_bytes(b"second")
            console = io.StringIO()
            resolved = resolve_output_path(
                input_bag,
                output_bag,
                input_stream=io.StringIO("2\nsubdir/bad\n2\nsecond\n2\nfinal\n"),
                output_stream=console,
                is_tty=True,
            )
            self.assertEqual(resolved, (root / "final.bag").resolve())
            self.assertGreaterEqual(console.getvalue().count("Output conflict"), 2)
            self.assertIn("must be a file name", console.getvalue())

    def test_conflict_non_tty_abort_and_eof_are_safe(self) -> None:
        with TemporaryDirectory(dir=TEST_OUTPUT_ROOT) as temporary:
            root = Path(temporary)
            input_bag = root / "input.bag"
            output_bag = root / "output.bag"
            input_bag.write_bytes(b"input")
            output_bag.write_bytes(b"existing")
            with self.assertRaises(ValueError):
                resolve_output_path(
                    input_bag,
                    output_bag,
                    input_stream=io.StringIO(),
                    output_stream=io.StringIO(),
                    is_tty=False,
                )
            for response in ("3\n", ""):
                with self.subTest(response=response):
                    with self.assertRaises(InteractionCancelled):
                        resolve_output_path(
                            input_bag,
                            output_bag,
                            input_stream=io.StringIO(response),
                            output_stream=io.StringIO(),
                            is_tty=True,
                        )

    def test_final_confirmation_defaults_yes_and_handles_no_eof_invalid(self) -> None:
        self.assertTrue(
            confirm_writing(io.StringIO("\n"), io.StringIO(), is_tty=True)
        )
        self.assertTrue(
            confirm_writing(io.StringIO("yes\n"), io.StringIO(), is_tty=True)
        )
        with self.assertRaises(InteractionCancelled):
            confirm_writing(io.StringIO("no\n"), io.StringIO(), is_tty=True)
        with self.assertRaises(InteractionCancelled):
            confirm_writing(io.StringIO(""), io.StringIO(), is_tty=True)
        console = io.StringIO()
        self.assertTrue(
            confirm_writing(
                io.StringIO("maybe\ny\n"), console, is_tty=True
            )
        )
        self.assertIn("enter y or n", console.getvalue())

    def test_dry_run_scans_and_prints_plan_without_creating_any_path(self) -> None:
        with TemporaryDirectory(dir=TEST_OUTPUT_ROOT) as temporary:
            root = Path(temporary)
            bag = root / "input.bag"
            output = root / "missing-parent/output.bag"
            _write_fixture_bag(bag)
            before = _sha256(bag)
            stdout = io.StringIO()
            code = main(
                [
                    "--input",
                    str(bag),
                    "--output",
                    str(output),
                    "--direction",
                    "robot-up",
                    "--distance-m",
                    "1.0",
                    "--start-s",
                    "1",
                    "--end-s",
                    "3",
                    "--dry-run",
                ],
                input_stream=io.StringIO(),
                output_stream=stdout,
                error_stream=io.StringIO(),
                is_tty=False,
            )
            self.assertEqual(code, 0)
            self.assertEqual(_sha256(bag), before)
            self.assertFalse(output.parent.exists())
            self.assertFalse(output.exists())
            report = stdout.getvalue()
            self.assertIn("DRY RUN", report)
            self.assertIn("odom->base_link targets=5", report)
            self.assertIn("direction=robot-up", report)
            self.assertIn("theoretical max speed=", report)
            self.assertIn("no output created", report)


class BagRewriteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        TEST_OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    def test_writes_exact_trajectory_and_preserves_every_other_field(self) -> None:
        with TemporaryDirectory(dir=TEST_OUTPUT_ROOT) as temporary:
            root = Path(temporary)
            bag = root / "input.bag"
            output = root / "nested/output.bag"
            _write_fixture_bag(bag)
            input_hash = _sha256(bag)
            stdout = io.StringIO()
            code = main(
                [
                    "--input",
                    str(bag),
                    "--output",
                    str(output),
                    "--direction",
                    "robot-up",
                    "--distance-m",
                    "1",
                    "--start-s",
                    "1",
                    "--end-s",
                    "3",
                ],
                input_stream=io.StringIO("\n"),
                output_stream=stdout,
                error_stream=io.StringIO(),
                is_tty=True,
            )
            self.assertEqual(code, 0)
            self.assertTrue(output.is_file())
            self.assertEqual(_sha256(bag), input_hash)
            self.assertIn("target transforms=5", stdout.getvalue())

            with AnyReader([bag]) as source, AnyReader([output]) as derived:
                self.assertEqual(
                    tuple(_connection_metadata(item) for item in source.connections),
                    tuple(_connection_metadata(item) for item in derived.connections),
                )
                source_records = list(source.messages())
                derived_records = list(derived.messages())
                self.assertEqual(len(source_records), len(derived_records))
                expected_x = [0.0, 0.0, 0.5, 1.0, 1.0]
                observed_x = []
                for source_record, derived_record in zip(
                    source_records, derived_records
                ):
                    source_connection, source_time, source_raw = source_record
                    derived_connection, derived_time, derived_raw = derived_record
                    self.assertEqual(source_time, derived_time)
                    self.assertEqual(
                        _connection_metadata(source_connection),
                        _connection_metadata(derived_connection),
                    )
                    if source_connection.topic != "/tf":
                        self.assertEqual(source_raw, derived_raw)
                        continue
                    source_message = source.deserialize(
                        source_raw, source_connection.msgtype
                    )
                    derived_message = derived.deserialize(
                        derived_raw, derived_connection.msgtype
                    )
                    self.assertEqual(
                        len(source_message.transforms), len(derived_message.transforms)
                    )
                    for source_tf, derived_tf in zip(
                        source_message.transforms, derived_message.transforms
                    ):
                        source_snapshot = _transform_snapshot(source_tf)
                        derived_snapshot = _transform_snapshot(derived_tf)
                        if source_snapshot[3:5] == ("odom", "base_link"):
                            observed_x.append(derived_snapshot[5])
                            self.assertEqual(source_snapshot[:5], derived_snapshot[:5])
                            self.assertEqual(source_snapshot[7:], derived_snapshot[7:])
                            self.assertEqual(derived_snapshot[6], 0.0)
                        else:
                            self.assertEqual(source_snapshot, derived_snapshot)
                self.assertEqual(observed_x, expected_x)

            analysis = analyze_bag(bag)
            plan = create_motion_plan(
                analysis,
                direction="robot-up",
                distance_m=1.0,
                start_s=1.0,
                end_s=3.0,
            )
            verified = validate_output(bag, output, analysis, plan)
            self.assertEqual(verified["messages"], 6)
            self.assertEqual(verified["target_transforms"], 5)
            self.assertEqual(verified["roots"], ("odom",))

    def test_validation_failure_cleans_only_exact_temporary_file(self) -> None:
        with TemporaryDirectory(dir=TEST_OUTPUT_ROOT) as temporary:
            root = Path(temporary)
            bag = root / "input.bag"
            output = root / "output.bag"
            unrelated = root / ".output.bag.keep.tmp.bag"
            _write_fixture_bag(bag)
            unrelated.write_bytes(b"keep")
            analysis = analyze_bag(bag)
            plan = create_motion_plan(
                analysis,
                direction="robot-left",
                distance_m=1.0,
                start_s=1.0,
                end_s=3.0,
            )
            with patch.object(
                loom_xy_motion,
                "validate_output",
                side_effect=ValueError("injected validation failure"),
            ):
                with self.assertRaisesRegex(ValueError, "injected validation failure"):
                    rewrite_bag(bag, output, analysis, plan)
            self.assertFalse(output.exists())
            self.assertEqual(unrelated.read_bytes(), b"keep")
            self.assertEqual(
                [path for path in root.glob(".output.bag.*.tmp.bag") if path != unrelated],
                [],
            )

    def test_publish_race_never_overwrites_competing_output(self) -> None:
        with TemporaryDirectory(dir=TEST_OUTPUT_ROOT) as temporary:
            root = Path(temporary)
            bag = root / "input.bag"
            output = root / "output.bag"
            _write_fixture_bag(bag)
            analysis = analyze_bag(bag)
            plan = create_motion_plan(
                analysis,
                direction="robot-down",
                distance_m=1.0,
                start_s=1.0,
                end_s=3.0,
            )
            original_validate = validate_output

            def validate_then_race(*args, **kwargs):
                result = original_validate(*args, **kwargs)
                output.write_bytes(b"raced")
                return result

            with patch.object(
                loom_xy_motion, "validate_output", side_effect=validate_then_race
            ):
                with self.assertRaisesRegex(ValueError, "appeared before publish"):
                    rewrite_bag(bag, output, analysis, plan)
            self.assertEqual(output.read_bytes(), b"raced")
            self.assertEqual(list(root.glob(".output.bag.*.tmp.bag")), [])


if __name__ == "__main__":
    unittest.main()
