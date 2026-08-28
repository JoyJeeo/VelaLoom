"""Tests for the generic read-only TF validator."""

from __future__ import annotations

import io
import shutil
import sys
import unittest
from pathlib import Path

import numpy as np
from rosbags.rosbag1 import Writer
from rosbags.typesys import Stores, get_typestore, get_types_from_msg


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from validate_tf import (  # noqa: E402
    ConfigError,
    analyze_geometry,
    main,
    match_source_states,
    parse_joint_map,
    print_effective_config,
    read_urdf,
    resolve_config,
    resolve_outcome,
    scan_bag,
)


TEST_OUTPUT_ROOT = ROOT / "test_output/issue-006/unit"


class FakeTTY(io.StringIO):
    def isatty(self) -> bool:
        return True


def _typestore():
    typestore = get_typestore(Stores.ROS1_NOETIC)
    types = get_types_from_msg(
            "std_msgs/Header header\nfloat64[] positions\nfloat64[] velocities",
            "test_msgs/msg/Sensor",
        )
    types.update(
        get_types_from_msg(
            "geometry_msgs/TransformStamped[] transforms",
            "tf2_msgs/msg/TFMessage",
        )
    )
    typestore.register(types)
    return typestore


def _transform(typestore, parent, child, stamp_ns, translation=(0.0, 0.0, 0.0), rotation=(0.0, 0.0, 0.0, 1.0)):
    Header = typestore.types["std_msgs/msg/Header"]
    Time = typestore.types["builtin_interfaces/msg/Time"]
    Vector3 = typestore.types["geometry_msgs/msg/Vector3"]
    Quaternion = typestore.types["geometry_msgs/msg/Quaternion"]
    Transform = typestore.types["geometry_msgs/msg/Transform"]
    TransformStamped = typestore.types["geometry_msgs/msg/TransformStamped"]
    return TransformStamped(
        header=Header(
            seq=np.uint32(0),
            stamp=Time(sec=stamp_ns // 1_000_000_000, nanosec=stamp_ns % 1_000_000_000),
            frame_id=parent,
        ),
        child_frame_id=child,
        transform=Transform(
            translation=Vector3(x=translation[0], y=translation[1], z=translation[2]),
            rotation=Quaternion(x=rotation[0], y=rotation[1], z=rotation[2], w=rotation[3]),
        ),
    )


def _write_validation_bag(
    path: Path,
    *,
    dynamic_groups=(),
    static_groups=(),
    sensor_positions=(0.0,),
    sensor_velocities=(0.0,),
    sensor_stamp_ns=1_000_000_000,
    include_sensor=True,
) -> None:
    typestore = _typestore()
    Header = typestore.types["std_msgs/msg/Header"]
    Time = typestore.types["builtin_interfaces/msg/Time"]
    Sensor = typestore.types["test_msgs/msg/Sensor"]
    TFMessage = typestore.types["tf2_msgs/msg/TFMessage"]
    with Writer(path) as writer:
        sensor_connection = writer.add_connection(
            "/sensor", "test_msgs/msg/Sensor", typestore=typestore, callerid="/sensor_pub"
        )
        dynamic_connections = [
            writer.add_connection(
                "/tf", "tf2_msgs/msg/TFMessage", typestore=typestore, callerid=caller
            )
            for caller, _ in dynamic_groups
        ]
        static_connections = [
            writer.add_connection(
                "/tf_static",
                "tf2_msgs/msg/TFMessage",
                typestore=typestore,
                callerid=caller,
                latching=1,
            )
            for caller, _ in static_groups
        ]
        if include_sensor:
            sensor = Sensor(
                header=Header(
                    seq=np.uint32(0),
                    stamp=Time(
                        sec=sensor_stamp_ns // 1_000_000_000,
                        nanosec=sensor_stamp_ns % 1_000_000_000,
                    ),
                    frame_id="",
                ),
                positions=np.asarray(sensor_positions, dtype=np.float64),
                velocities=np.asarray(sensor_velocities, dtype=np.float64),
            )
            writer.write(
                sensor_connection,
                sensor_stamp_ns,
                typestore.serialize_ros1(sensor, "test_msgs/msg/Sensor"),
            )
        for connection, (_, transforms) in zip(dynamic_connections, dynamic_groups):
            message = TFMessage(transforms=list(transforms))
            writer.write(
                connection,
                sensor_stamp_ns + 1,
                typestore.serialize_ros1(message, "tf2_msgs/msg/TFMessage"),
            )
        for connection, (_, transforms) in zip(static_connections, static_groups):
            message = TFMessage(transforms=list(transforms))
            writer.write(
                connection,
                sensor_stamp_ns + 2,
                typestore.serialize_ros1(message, "tf2_msgs/msg/TFMessage"),
            )


class ValidateTfConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        TEST_OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
        self.root = TEST_OUTPUT_ROOT / self._testMethodName
        if self.root.exists():
            shutil.rmtree(self.root)
        self.root.mkdir()

    def tearDown(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)

    def _config(self, text: str) -> Path:
        path = self.root / "config.yaml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_default_config_loads_authorized_baseline_and_mapping(self) -> None:
        config = resolve_config([], cwd=ROOT)
        self.assertEqual(
            config.bag, ROOT / "test_output/issue-020/01_dexhand_tf.bag"
        )
        self.assertEqual(config.expected_root, "odom")
        self.assertEqual(config.sensor_topic, "/sensors_data_raw")
        self.assertEqual(len(config.joint_map), 29)
        self.assertEqual(config.joint_map[28], "zhead_2_joint")

    def test_explicit_config_resolves_paths_relative_to_config(self) -> None:
        config_path = self._config(
            """version: 1
inputs: {bag: data/input.bag, urdf: model/robot.urdf}
joints: {0: joint_a}
"""
        )
        config = resolve_config(["--config", str(config_path)], cwd=ROOT)
        self.assertEqual(config.bag, self.root / "data/input.bag")
        self.assertEqual(config.urdf, self.root / "model/robot.urdf")
        self.assertEqual(config.joint_map, {0: "joint_a"})

    def test_cli_overrides_config_and_replaces_joint_list(self) -> None:
        config_path = self._config(
            """version: 1
inputs: {bag: old.bag, urdf: old.urdf}
topics: {sensor: /old}
joints: {0: old_a, 1: old_b}
"""
        )
        config = resolve_config(
            [
                "--config",
                str(config_path),
                "--bag",
                "new.bag",
                "--urdf",
                "new.urdf",
                "--sensor-topic",
                "/new",
                "--joint-map",
                "4=joint_d",
                "5=joint_e",
                "--strict",
            ],
            cwd=self.root,
        )
        self.assertEqual(config.bag, self.root / "new.bag")
        self.assertEqual(config.sensor_topic, "/new")
        self.assertEqual(config.joint_map, {4: "joint_d", 5: "joint_e"})
        self.assertTrue(config.strict)
        self.assertEqual(config.sources["joints"], "CLI")

    def test_pure_cli_uses_program_defaults_when_default_config_is_missing(self) -> None:
        config = resolve_config(
            ["--bag", "a.bag", "--urdf", "b.urdf", "--joint-map", "0=j"],
            cwd=self.root,
            default_config=self.root / "missing.yaml",
        )
        self.assertEqual(config.sensor_topic, "/sensors_data_raw")
        self.assertEqual(config.before_ns, 30_000_000)
        self.assertEqual(config.after_ns, 5_000_000)

    def test_missing_inputs_and_invalid_schema_fail(self) -> None:
        with self.assertRaisesRegex(ConfigError, "missing required input"):
            resolve_config([], cwd=self.root, default_config=self.root / "missing.yaml")
        invalid = self._config("version: 2\ninputs: {}\n")
        with self.assertRaisesRegex(ConfigError, "version must be 1"):
            resolve_config(["--config", str(invalid)], cwd=self.root)

    def test_explicit_missing_config_and_unknown_key_fail(self) -> None:
        with self.assertRaisesRegex(ConfigError, "explicit config does not exist"):
            resolve_config(["--config", str(self.root / "missing.yaml")], cwd=ROOT)
        invalid = self._config(
            "version: 1\ninputs: {bag: a.bag, urdf: b.urdf}\nsurprise: true\n"
        )
        with self.assertRaisesRegex(ConfigError, "unknown config keys"):
            resolve_config(["--config", str(invalid)], cwd=ROOT)

    def test_joint_map_validation_and_repeated_option(self) -> None:
        self.assertEqual(parse_joint_map(["0=a", "2=b"]), {0: "a", 2: "b"})
        for values in (["bad"], ["-1=a"], ["0=a", "0=b"], ["0=a", "1=a"]):
            with self.subTest(values=values), self.assertRaises(ConfigError):
                parse_joint_map(values)
        with self.assertRaisesRegex(ConfigError, "may appear only once"):
            resolve_config(
                [
                    "--bag",
                    "a.bag",
                    "--urdf",
                    "b.urdf",
                    "--joint-map",
                    "0=a",
                    "--joint-map",
                    "1=b",
                ],
                cwd=self.root,
                default_config=self.root / "missing.yaml",
            )

    def test_main_prints_effective_values_and_sources_without_writing(self) -> None:
        stream = io.StringIO()
        print_effective_config(resolve_config([], cwd=ROOT), stream)
        self.assertIn("Effective configuration", stream.getvalue())
        self.assertIn("Value sources", stream.getvalue())

    def test_help_documents_threshold_names_and_units(self) -> None:
        from validate_tf import build_parser

        help_text = build_parser().format_help()
        self.assertIn("angular_rms_rad", help_text)
        self.assertIn("linear_max_m", help_text)
        self.assertIn("sensor candidate window", help_text)


class ValidateTfUrdfTests(unittest.TestCase):
    def setUp(self) -> None:
        TEST_OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
        self.root = TEST_OUTPUT_ROOT / self._testMethodName
        if self.root.exists():
            shutil.rmtree(self.root)
        self.root.mkdir()

    def tearDown(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)

    def _urdf(self, links: list[str], joints: str, name: str = "fixture.urdf") -> Path:
        path = self.root / name
        link_xml = "".join(f'<link name="{link}"/>' for link in links)
        path.write_text(
            f'<robot name="fixture">{link_xml}{joints}</robot>', encoding="utf-8"
        )
        return path

    def test_reads_all_supported_joint_types_and_normalizes_axes(self) -> None:
        path = self._urdf(
            ["root", "fixed", "rev", "cont", "pris", "plane", "float"],
            """
<joint name="fixed_j" type="fixed"><parent link="root"/><child link="fixed"/></joint>
<joint name="rev_j" type="revolute"><parent link="fixed"/><child link="rev"/><origin xyz="1 2 3" rpy="0.1 0.2 0.3"/><axis xyz="0 0 2"/><limit lower="-1" upper="1" velocity="2"/></joint>
<joint name="cont_j" type="continuous"><parent link="rev"/><child link="cont"/><axis xyz="0 1 0"/><limit velocity="3"/></joint>
<joint name="pris_j" type="prismatic"><parent link="cont"/><child link="pris"/><axis xyz="2 0 0"/><limit lower="0" upper="0.5" velocity="1"/></joint>
<joint name="planar_j" type="planar"><parent link="pris"/><child link="plane"/><axis xyz="0 0 1"/></joint>
<joint name="floating_j" type="floating"><parent link="plane"/><child link="float"/></joint>
""",
        )
        model = read_urdf(path)
        self.assertEqual(model.roots, ("root",))
        self.assertEqual(
            model.type_counts,
            {
                "continuous": 1,
                "fixed": 1,
                "floating": 1,
                "planar": 1,
                "prismatic": 1,
                "revolute": 1,
            },
        )
        self.assertEqual(model.by_name["rev_j"].axis, (0.0, 0.0, 1.0))
        self.assertAlmostEqual(
            sum(value * value for value in model.by_name["rev_j"].origin_quaternion),
            1.0,
        )

    def test_rejects_unknown_links_duplicate_names_and_multiple_parents(self) -> None:
        cases = [
            (
                ["a", "b"],
                '<joint name="j" type="fixed"><parent link="missing"/><child link="b"/></joint>',
                "unknown link",
            ),
            (
                ["a", "b", "c"],
                '<joint name="j" type="fixed"><parent link="a"/><child link="b"/></joint><joint name="j" type="fixed"><parent link="b"/><child link="c"/></joint>',
                "repeats joint name",
            ),
            (
                ["a", "b", "c"],
                '<joint name="j1" type="fixed"><parent link="a"/><child link="c"/></joint><joint name="j2" type="fixed"><parent link="b"/><child link="c"/></joint>',
                "multiple parents",
            ),
        ]
        for index, (links, joints, message) in enumerate(cases):
            with self.subTest(index=index):
                with self.assertRaisesRegex(ConfigError, message):
                    read_urdf(self._urdf(links, joints, f"case-{index}.urdf"))

    def test_rejects_cycle_invalid_axis_origin_and_limits(self) -> None:
        cases = [
            (
                ["a", "b"],
                '<joint name="j1" type="fixed"><parent link="a"/><child link="b"/></joint><joint name="j2" type="fixed"><parent link="b"/><child link="a"/></joint>',
                "cycle",
            ),
            (
                ["a", "b"],
                '<joint name="j" type="revolute"><parent link="a"/><child link="b"/><axis xyz="0 0 0"/><limit lower="-1" upper="1"/></joint>',
                "axis must be finite and non-zero",
            ),
            (
                ["a", "b"],
                '<joint name="j" type="fixed"><parent link="a"/><child link="b"/><origin xyz="1 2"/></joint>',
                "three finite numbers",
            ),
            (
                ["a", "b"],
                '<joint name="j" type="revolute"><parent link="a"/><child link="b"/><limit lower="2" upper="1"/></joint>',
                "lower limit above upper",
            ),
            (
                ["a", "b"],
                '<joint name="j" type="continuous"><parent link="a"/><child link="b"/><limit velocity="-1"/></joint>',
                "velocity limit must be non-negative",
            ),
        ]
        for index, (links, joints, message) in enumerate(cases):
            with self.subTest(index=index):
                with self.assertRaisesRegex(ConfigError, message):
                    read_urdf(self._urdf(links, joints, f"bad-{index}.urdf"))


class ValidateTfBagScanTests(unittest.TestCase):
    def setUp(self) -> None:
        TEST_OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
        self.root = TEST_OUTPUT_ROOT / self._testMethodName
        if self.root.exists():
            shutil.rmtree(self.root)
        self.root.mkdir()
        self.urdf = self.root / "robot.urdf"
        self.urdf.write_text(
            """<robot name="fixture">
<link name="root"/><link name="base"/><link name="joint_link"/>
<joint name="mount" type="fixed"><parent link="root"/><child link="base"/></joint>
<joint name="joint" type="revolute"><parent link="base"/><child link="joint_link"/><limit lower="-2" upper="2" velocity="5"/></joint>
</robot>""",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)

    def _config(self, bag: Path, *, expected_root="root"):
        argv = [
            "--bag",
            str(bag),
            "--urdf",
            str(self.urdf),
            "--sensor-topic",
            "/sensor",
            "--position-field",
            "positions",
            "--velocity-field",
            "velocities",
            "--timestamp-field",
            "header.stamp",
            "--joint-map",
            "0=joint",
        ]
        if expected_root is not None:
            argv.extend(("--expected-root", expected_root))
        return resolve_config(
            argv, cwd=self.root, default_config=self.root / "missing.yaml"
        )

    def test_scans_sensor_tf_connections_and_allows_distinct_callers(self) -> None:
        typestore = _typestore()
        bag = self.root / "valid.bag"
        _write_validation_bag(
            bag,
            dynamic_groups=(("/dynamic_pub", [_transform(typestore, "base", "joint_link", 1_000_000_000)]),),
            static_groups=(("/static_pub", [_transform(typestore, "root", "base", 0)]),),
        )
        scan = scan_bag(self._config(bag), read_urdf(self.urdf))
        self.assertEqual(scan.errors, [])
        self.assertEqual(scan.roots, ("root",))
        self.assertEqual(len(scan.sensor_samples), 1)
        self.assertEqual(scan.dynamic_transform_count, 1)
        self.assertEqual(scan.static_transform_count, 1)
        self.assertEqual(
            {item["callerid"] for item in scan.connections},
            {"/sensor_pub", "/dynamic_pub", "/static_pub"},
        )

    def test_reports_multiple_parent_cycle_and_same_edge_callers(self) -> None:
        typestore = _typestore()
        cases = [
            (
                (("/a", [_transform(typestore, "other", "base", 1_000_000_000)]),),
                (("/s", [_transform(typestore, "root", "base", 0)]),),
                "multiple parents",
            ),
            (
                (("/a", [_transform(typestore, "a", "b", 1_000_000_000), _transform(typestore, "b", "a", 1_000_000_001)]),),
                (),
                "cycle",
            ),
            (
                (
                    ("/a", [_transform(typestore, "root", "base", 1_000_000_000)]),
                    ("/b", [_transform(typestore, "root", "base", 1_000_000_001)]),
                ),
                (),
                "multiple callers",
            ),
        ]
        for index, (dynamic, static, expected) in enumerate(cases):
            with self.subTest(index=index):
                bag = self.root / f"topology-{index}.bag"
                _write_validation_bag(
                    bag, dynamic_groups=dynamic, static_groups=static
                )
                scan = scan_bag(
                    self._config(bag, expected_root=None), read_urdf(self.urdf)
                )
                self.assertTrue(any(expected in error for error in scan.errors), scan.errors)

    def test_static_duplicates_warn_and_conflicting_pose_fails(self) -> None:
        typestore = _typestore()
        identical = _transform(typestore, "root", "base", 0)
        bag = self.root / "duplicate.bag"
        _write_validation_bag(
            bag,
            dynamic_groups=(("/d", [_transform(typestore, "base", "joint_link", 1_000_000_000)]),),
            static_groups=(("/s", [identical, identical]),),
        )
        scan = scan_bag(self._config(bag), read_urdf(self.urdf))
        self.assertTrue(any("duplicated" in warning for warning in scan.warnings))
        conflict_bag = self.root / "conflict.bag"
        _write_validation_bag(
            conflict_bag,
            dynamic_groups=(("/d", [_transform(typestore, "base", "joint_link", 1_000_000_000)]),),
            static_groups=(("/s", [identical, _transform(typestore, "root", "base", 0, translation=(0.1, 0.0, 0.0))]),),
        )
        conflict = scan_bag(self._config(conflict_bag), read_urdf(self.urdf))
        self.assertTrue(any("conflicting poses" in error for error in conflict.errors))

    def test_nonfinite_sensor_and_bad_quaternion_are_data_failures(self) -> None:
        typestore = _typestore()
        bag = self.root / "bad-data.bag"
        _write_validation_bag(
            bag,
            sensor_positions=(float("nan"),),
            dynamic_groups=(("/d", [_transform(typestore, "base", "joint_link", 1_000_000_000, rotation=(0.0, 0.0, 0.0, 2.0))]),),
            static_groups=(("/s", [_transform(typestore, "root", "base", 0)]),),
        )
        scan = scan_bag(self._config(bag), read_urdf(self.urdf))
        self.assertTrue(any("non-finite" in error for error in scan.errors))
        self.assertTrue(any("non-normalized" in error for error in scan.errors))

    def test_missing_sensor_topic_fails_before_analysis(self) -> None:
        typestore = _typestore()
        bag = self.root / "missing-sensor.bag"
        _write_validation_bag(
            bag,
            include_sensor=False,
            dynamic_groups=(("/d", [_transform(typestore, "base", "joint_link", 1_000_000_000)]),),
        )
        with self.assertRaisesRegex(ConfigError, "contains no messages"):
            scan_bag(self._config(bag), read_urdf(self.urdf))


class ValidateTfGeometryAndMatchingTests(unittest.TestCase):
    def setUp(self) -> None:
        TEST_OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
        self.root = TEST_OUTPUT_ROOT / self._testMethodName
        if self.root.exists():
            shutil.rmtree(self.root)
        self.root.mkdir()

    def tearDown(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)

    def _urdf(self, text: str) -> Path:
        path = self.root / "robot.urdf"
        path.write_text(text, encoding="utf-8")
        return path

    def _config(self, bag: Path, urdf: Path, mappings):
        return resolve_config(
            [
                "--bag",
                str(bag),
                "--urdf",
                str(urdf),
                "--sensor-topic",
                "/sensor",
                "--position-field",
                "positions",
                "--velocity-field",
                "velocities",
                "--timestamp-field",
                "header.stamp",
                "--expected-root",
                "root",
                "--joint-map",
                *mappings,
            ],
            cwd=self.root,
            default_config=self.root / "missing.yaml",
        )

    def test_validates_all_joint_geometries_and_matches_one_whole_state(self) -> None:
        urdf = self._urdf(
            """<robot name="fixture">
<link name="root"/><link name="fixed"/><link name="rev"/><link name="cont"/><link name="pris"/><link name="plane"/><link name="float"/>
<joint name="fixed_j" type="fixed"><parent link="root"/><child link="fixed"/></joint>
<joint name="rev_j" type="revolute"><parent link="fixed"/><child link="rev"/><axis xyz="0 0 1"/><limit lower="-1" upper="1" velocity="5"/></joint>
<joint name="cont_j" type="continuous"><parent link="rev"/><child link="cont"/><axis xyz="0 1 0"/><limit velocity="5"/></joint>
<joint name="pris_j" type="prismatic"><parent link="cont"/><child link="pris"/><axis xyz="1 0 0"/><limit lower="0" upper="1" velocity="2"/></joint>
<joint name="planar_j" type="planar"><parent link="pris"/><child link="plane"/><axis xyz="0 0 1"/></joint>
<joint name="floating_j" type="floating"><parent link="plane"/><child link="float"/></joint>
</robot>"""
        )
        typestore = _typestore()
        stamp = 1_020_000_000
        rev_q, cont_q, planar_q = 0.4, -0.2, 0.3
        dynamic = [
            _transform(typestore, "fixed", "rev", stamp, rotation=(0.0, 0.0, np.sin(rev_q / 2), np.cos(rev_q / 2))),
            _transform(typestore, "rev", "cont", stamp, rotation=(0.0, np.sin(cont_q / 2), 0.0, np.cos(cont_q / 2))),
            _transform(typestore, "cont", "pris", stamp, translation=(0.1, 0.0, 0.0)),
            _transform(typestore, "pris", "plane", stamp, translation=(0.1, 0.2, 0.0), rotation=(0.0, 0.0, np.sin(planar_q / 2), np.cos(planar_q / 2))),
            _transform(typestore, "plane", "float", stamp, translation=(1.0, 2.0, 3.0), rotation=(0.0, 0.0, np.sin(0.1), np.cos(0.1))),
        ]
        bag = self.root / "valid.bag"
        _write_validation_bag(
            bag,
            sensor_positions=(rev_q, cont_q, 0.1),
            sensor_velocities=(0.0, 0.0, 0.0),
            sensor_stamp_ns=1_000_000_000,
            dynamic_groups=(("/dynamic", dynamic),),
            static_groups=(("/static", [_transform(typestore, "root", "fixed", 0)]),),
        )
        config = self._config(bag, urdf, ("0=rev_j", "1=cont_j", "2=pris_j"))
        model = read_urdf(urdf)
        scan = scan_bag(config, model)
        geometry = analyze_geometry(config, model, scan)
        matching = match_source_states(config, model, scan, geometry)
        self.assertEqual(geometry.errors, [])
        self.assertEqual(geometry.missing_joints, ())
        self.assertEqual(set(geometry.unsupported_source), {"planar_j", "floating_j"})
        self.assertEqual(matching.errors, [])
        self.assertEqual(matching.matched_tf_states, 1)
        self.assertAlmostEqual(matching.angular_max_rad, 0.0, places=12)
        self.assertAlmostEqual(matching.linear_max_m, 0.0, places=12)
        self.assertAlmostEqual(matching.time_delta_ms["min"], -20.0)
        self.assertEqual(matching.sensor_continuity["rev_j"]["samples"], 1)
        self.assertEqual(
            matching.sensor_continuity["pris_j"]["reported_velocity_max"], 0.0
        )
        swapped_config = self._config(
            bag, urdf, ("0=cont_j", "1=rev_j", "2=pris_j")
        )
        swapped_geometry = analyze_geometry(swapped_config, model, scan)
        swapped = match_source_states(
            swapped_config, model, scan, swapped_geometry
        )
        self.assertEqual(swapped.matched_tf_states, 0)

    def test_quaternion_sign_equivalence_and_wrong_geometry(self) -> None:
        urdf = self._urdf(
            """<robot name="fixture"><link name="root"/><link name="base"/><link name="tip"/>
<joint name="mount" type="fixed"><parent link="root"/><child link="base"/></joint>
<joint name="joint" type="revolute"><parent link="base"/><child link="tip"/><axis xyz="0 0 1"/><limit lower="-1" upper="1"/></joint></robot>"""
        )
        typestore = _typestore()
        bag = self.root / "bad-geometry.bag"
        _write_validation_bag(
            bag,
            dynamic_groups=(("/d", [_transform(typestore, "base", "tip", 1_000_000_000, translation=(0.01, 0.0, 0.0), rotation=(np.sin(0.1), 0.0, 0.0, np.cos(0.1)))]),),
            static_groups=(("/s", [_transform(typestore, "root", "base", 0, rotation=(0.0, 0.0, 0.0, -1.0))]),),
        )
        config = self._config(bag, urdf, ("0=joint",))
        model = read_urdf(urdf)
        scan = scan_bag(config, model)
        geometry = analyze_geometry(config, model, scan)
        self.assertFalse(any("mount" in error for error in geometry.errors))
        self.assertTrue(any("translation moves" in error for error in geometry.errors))
        self.assertTrue(any("URDF axis" in error for error in geometry.errors))

    def test_detects_reversed_parent_child_and_position_limit(self) -> None:
        urdf = self._urdf(
            """<robot name="fixture"><link name="root"/><link name="tip"/>
<joint name="joint" type="revolute"><parent link="root"/><child link="tip"/><axis xyz="0 0 1"/><limit lower="-0.2" upper="0.2"/></joint></robot>"""
        )
        typestore = _typestore()
        reversed_bag = self.root / "reversed.bag"
        _write_validation_bag(
            reversed_bag,
            dynamic_groups=(("/d", [_transform(typestore, "tip", "root", 1_000_000_000)]),),
        )
        config = self._config(reversed_bag, urdf, ("0=joint",))
        model = read_urdf(urdf)
        scan = scan_bag(config, model)
        geometry = analyze_geometry(config, model, scan)
        self.assertTrue(any("appears reversed" in error for error in geometry.errors))

        limit_bag = self.root / "limit.bag"
        angle = 0.5
        _write_validation_bag(
            limit_bag,
            sensor_positions=(angle,),
            dynamic_groups=(("/d", [_transform(typestore, "root", "tip", 1_000_000_000, rotation=(0.0, 0.0, np.sin(angle / 2), np.cos(angle / 2)))]),),
        )
        limit_config = self._config(limit_bag, urdf, ("0=joint",))
        limit_scan = scan_bag(limit_config, model)
        limit_geometry = analyze_geometry(limit_config, model, limit_scan)
        self.assertTrue(any("against limits" in error for error in limit_geometry.errors))
        self.assertEqual(limit_geometry.limit_stats["joint"]["failures"], 1)

    def test_wrong_source_value_and_delay_outside_window_do_not_match(self) -> None:
        urdf = self._urdf(
            """<robot name="fixture"><link name="root"/><link name="tip"/>
<joint name="joint" type="continuous"><parent link="root"/><child link="tip"/><axis xyz="0 0 1"/></joint></robot>"""
        )
        typestore = _typestore()
        for name, sensor_value, tf_stamp in (
            ("wrong", -0.5, 1_000_000_000),
            ("degrees", np.degrees(0.5), 1_000_000_000),
            ("late", 0.5, 1_040_000_000),
        ):
            with self.subTest(name=name):
                bag = self.root / f"{name}.bag"
                angle = 0.5
                _write_validation_bag(
                    bag,
                    sensor_positions=(sensor_value,),
                    sensor_stamp_ns=1_000_000_000,
                    dynamic_groups=(("/d", [_transform(typestore, "root", "tip", tf_stamp, rotation=(0.0, 0.0, np.sin(angle / 2), np.cos(angle / 2)))]),),
                )
                config = self._config(bag, urdf, ("0=joint",))
                model = read_urdf(urdf)
                scan = scan_bag(config, model)
                geometry = analyze_geometry(config, model, scan)
                matching = match_source_states(config, model, scan, geometry)
                self.assertEqual(matching.matched_tf_states, 0)
                self.assertTrue(matching.errors)


class ValidateTfPolicyAndReportTests(unittest.TestCase):
    def setUp(self) -> None:
        TEST_OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
        self.root = TEST_OUTPUT_ROOT / self._testMethodName
        if self.root.exists():
            shutil.rmtree(self.root)
        self.root.mkdir()
        self.urdf = self.root / "robot.urdf"
        self.urdf.write_text(
            """<robot name="fixture"><link name="root"/><link name="tip"/>
<joint name="joint" type="continuous"><parent link="root"/><child link="tip"/><axis xyz="0 0 1"/></joint></robot>""",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)

    def _bag(self, *, extra=False) -> Path:
        typestore = _typestore()
        bag = self.root / "input.bag"
        transforms = [_transform(typestore, "root", "tip", 1_000_000_000)]
        if extra:
            transforms.append(_transform(typestore, "root", "sensor", 1_000_000_000))
        _write_validation_bag(
            bag,
            dynamic_groups=(("/dynamic", transforms),),
        )
        return bag

    def _argv(self, bag: Path):
        return [
            "--bag",
            str(bag),
            "--urdf",
            str(self.urdf),
            "--sensor-topic",
            "/sensor",
            "--position-field",
            "positions",
            "--velocity-field",
            "velocities",
            "--timestamp-field",
            "header.stamp",
            "--expected-root",
            "root",
            "--joint-map",
            "0=joint",
        ]

    def _analysis(self, bag: Path, extra_policy="warn"):
        config = resolve_config(
            [*self._argv(bag), "--extra-edge-policy", extra_policy], cwd=ROOT
        )
        model = read_urdf(self.urdf)
        scan = scan_bag(config, model)
        geometry = analyze_geometry(config, model, scan)
        matching = match_source_states(config, model, scan, geometry)
        return config, model, scan, geometry, matching

    def test_main_writes_optional_json_and_refuses_overwrite(self) -> None:
        bag = self._bag()
        report = self.root / "report.json"
        output = io.StringIO()
        error = io.StringIO()
        result = main(
            [*self._argv(bag), "--missing-joint-policy", "fail", "--json-out", str(report)],
            input_stream=io.StringIO(),
            output_stream=output,
            error_stream=error,
        )
        self.assertEqual(result, 0, error.getvalue())
        self.assertTrue(report.is_file())
        document = __import__("json").loads(report.read_text(encoding="utf-8"))
        self.assertEqual(document["status"], "PASS")
        self.assertTrue(document["inputs"]["unchanged"])
        self.assertIn("Source matching", output.getvalue())
        second = main(
            [*self._argv(bag), "--json-out", str(report)],
            input_stream=io.StringIO(),
            output_stream=io.StringIO(),
            error_stream=error,
        )
        self.assertEqual(second, 2)
        self.assertIn("already exists", error.getvalue())

    def test_no_json_path_creates_no_report(self) -> None:
        bag = self._bag()
        before = set(self.root.iterdir())
        result = main(
            [*self._argv(bag), "--missing-joint-policy", "fail"],
            input_stream=io.StringIO(),
            output_stream=io.StringIO(),
            error_stream=io.StringIO(),
        )
        self.assertEqual(result, 0)
        self.assertEqual(set(self.root.iterdir()), before)

    def test_extra_edge_policy_and_strict_status(self) -> None:
        bag = self._bag(extra=True)
        config, model, scan, geometry, matching = self._analysis(bag)
        outcome = resolve_outcome(
            config,
            model,
            scan,
            geometry,
            matching,
            input_stream=io.StringIO(),
            output_stream=io.StringIO(),
        )
        self.assertEqual(outcome.status, "PASS_WITH_WARNINGS")
        strict = resolve_config([*self._argv(bag), "--strict"], cwd=ROOT)
        strict_outcome = resolve_outcome(
            strict,
            model,
            scan,
            geometry,
            matching,
            input_stream=io.StringIO(),
            output_stream=io.StringIO(),
        )
        self.assertEqual(strict_outcome.status, "FAIL")
        self.assertEqual(strict_outcome.exit_code, 1)

    def test_missing_joint_interactive_batch_decision_non_tty_and_abort(self) -> None:
        original = self.urdf.read_text(encoding="utf-8")
        self.urdf.write_text(
            original.replace(
                "</robot>",
                '<link name="missing"/><joint name="missing_joint" type="fixed"><parent link="tip"/><child link="missing"/></joint></robot>',
            ),
            encoding="utf-8",
        )
        bag = self._bag()
        config, model, scan, geometry, matching = self._analysis(bag)
        interactive = resolve_outcome(
            config,
            model,
            scan,
            geometry,
            matching,
            input_stream=FakeTTY("wa\n"),
            output_stream=io.StringIO(),
        )
        self.assertEqual(interactive.status, "PASS_WITH_WARNINGS")
        self.assertTrue(
            any(item["item"] == "missing_joint" for item in interactive.decisions)
        )
        non_tty = resolve_outcome(
            config,
            model,
            scan,
            geometry,
            matching,
            input_stream=io.StringIO(),
            output_stream=io.StringIO(),
        )
        self.assertEqual(non_tty.status, "FAIL")
        aborted = main(
            self._argv(bag),
            input_stream=FakeTTY("a\n"),
            output_stream=io.StringIO(),
            error_stream=(error := io.StringIO()),
        )
        self.assertEqual(aborted, 3)
        self.assertIn("ABORTED", error.getvalue())


if __name__ == "__main__":
    unittest.main()
