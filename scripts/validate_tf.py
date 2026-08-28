#!/usr/bin/env python3
"""Validate ROS1 TF data against a URDF and sensor joint-state source.

Purpose:
    Read a ROS1 bag and URDF without modifying either input, then validate TF
    topology, publication semantics, model geometry, source-state matching,
    timing, limits, and continuity.
Input:
    A read-only ROS1 ``.bag``, a read-only URDF, and optional versioned YAML
    configuration or CLI overrides.
Output:
    A terminal validation report and, only when ``--json-out`` is supplied, a
    JSON report.  This tool never creates or rewrites TF or rosbag data.
Example:
    ``python scripts/validate_tf.py --bag input.bag --urdf robot.urdf --joint-map 0=joint_a 1=joint_b``
"""

from __future__ import annotations

import argparse
import bisect
import copy
import hashlib
import json
import math
import os
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence, TextIO


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = ROOT / "configs/validate_tf.yaml"
SCHEMA_VERSION = 1
POLICY_VALUES = {"interactive", "fail", "warn", "ignore"}
NONINTERACTIVE_POLICY_VALUES = {"fail", "warn", "ignore"}

PROGRAM_DEFAULTS: dict[str, Any] = {
    "topics": {
        "sensor": "/sensors_data_raw",
        "tf": "/tf",
        "tf_static": "/tf_static",
    },
    "source": {
        "position_field": "joint_data.joint_q",
        "velocity_field": "joint_data.joint_v",
        "timestamp_field": "header.stamp",
    },
    "expected_root": None,
    "matching": {"before_ms": 30.0, "after_ms": 5.0},
    "policies": {
        "missing_joint": "interactive",
        "extra_edge": "warn",
        "fixed_dynamic": "warn",
        "unsupported_source": "warn",
    },
    "thresholds": {
        "quaternion_norm": 1.0e-6,
        "translation_m": 1.0e-6,
        "rotation_rad": 1.0e-5,
        "angular_rms_rad": 1.0e-2,
        "angular_max_rad": 2.0e-2,
        "linear_rms_m": 1.0e-4,
        "linear_max_m": 1.0e-3,
        "angular_limit_warn_rad": 1.0e-6,
        "angular_limit_fail_rad": 1.0e-2,
        "linear_limit_warn_m": 1.0e-6,
        "linear_limit_fail_m": 1.0e-3,
        "continuity_gap_ms": None,
        "angular_jump_rad": None,
        "linear_jump_m": None,
    },
    "joints": {},
}

SECTION_KEYS: dict[str, set[str]] = {
    "inputs": {"bag", "urdf"},
    "topics": set(PROGRAM_DEFAULTS["topics"]),
    "source": set(PROGRAM_DEFAULTS["source"]),
    "matching": set(PROGRAM_DEFAULTS["matching"]),
    "policies": set(PROGRAM_DEFAULTS["policies"]),
    "thresholds": set(PROGRAM_DEFAULTS["thresholds"]),
}
TOP_LEVEL_KEYS = {
    "version",
    "inputs",
    "topics",
    "source",
    "expected_root",
    "matching",
    "policies",
    "thresholds",
    "joints",
}


class ConfigError(ValueError):
    """Raised for CLI, configuration, or preflight errors (exit code 2)."""


class DataError(ValueError):
    """Raised when input data cannot satisfy validation requirements."""


class UserAbort(RuntimeError):
    """Raised when the caller aborts an interactive validation decision."""


@dataclass(frozen=True)
class EffectiveConfig:
    """Fully merged and path-resolved validator configuration."""

    bag: Path
    urdf: Path
    sensor_topic: str
    tf_topic: str
    tf_static_topic: str
    position_field: str
    velocity_field: str | None
    timestamp_field: str
    expected_root: str | None
    joint_map: dict[int, str]
    missing_joint_policy: str
    extra_edge_policy: str
    fixed_dynamic_policy: str
    unsupported_source_policy: str
    before_ns: int
    after_ns: int
    thresholds: dict[str, float | None]
    strict: bool
    json_out: Path | None
    loaded_config: Path | None
    sources: dict[str, str]

    def reportable(self) -> dict[str, Any]:
        document = asdict(self)
        for key in ("bag", "urdf", "json_out", "loaded_config"):
            value = document[key]
            document[key] = None if value is None else str(value)
        document["joint_map"] = {
            str(index): name for index, name in sorted(self.joint_map.items())
        }
        return document


@dataclass(frozen=True)
class UrdfJoint:
    """Validated model definition for one direct URDF joint."""

    name: str
    joint_type: str
    parent: str
    child: str
    origin_xyz: tuple[float, float, float]
    origin_rpy: tuple[float, float, float]
    origin_quaternion: tuple[float, float, float, float]
    axis: tuple[float, float, float] | None
    lower: float | None
    upper: float | None
    velocity_limit: float | None


@dataclass(frozen=True)
class UrdfModel:
    """Validated URDF links, joints, roots, and input digest."""

    links: frozenset[str]
    joints: tuple[UrdfJoint, ...]
    roots: tuple[str, ...]
    sha256: str

    @property
    def by_name(self) -> dict[str, UrdfJoint]:
        return {joint.name: joint for joint in self.joints}

    @property
    def by_edge(self) -> dict[tuple[str, str], UrdfJoint]:
        return {(joint.parent, joint.child): joint for joint in self.joints}

    @property
    def type_counts(self) -> dict[str, int]:
        return dict(sorted(Counter(joint.joint_type for joint in self.joints).items()))


@dataclass(frozen=True)
class SensorSample:
    """One validated source-state message."""

    stamp_ns: int
    record_ns: int
    positions: tuple[float, ...]
    velocities: tuple[float, ...] | None


@dataclass
class EdgeScan:
    """Bounded-memory publication summary for one TF parent-child edge."""

    parent: str
    child: str
    dynamic_count: int = 0
    static_count: int = 0
    connections: set[int] = field(default_factory=set)
    callers: set[str] = field(default_factory=set)
    first_static_pose: tuple[
        tuple[float, float, float], tuple[float, float, float, float]
    ] | None = None
    static_pose_conflicts: int = 0
    nonmonotonic_dynamic_stamps: int = 0
    duplicate_dynamic_stamps: int = 0
    last_dynamic_stamp: int | None = None

    @property
    def sources(self) -> tuple[str, ...]:
        result = []
        if self.dynamic_count:
            result.append("dynamic")
        if self.static_count:
            result.append("static")
        return tuple(result)

    def reportable(self) -> dict[str, Any]:
        return {
            "parent": self.parent,
            "child": self.child,
            "dynamic_count": self.dynamic_count,
            "static_count": self.static_count,
            "connections": sorted(self.connections),
            "callers": sorted(self.callers),
            "sources": list(self.sources),
            "static_pose_conflicts": self.static_pose_conflicts,
            "nonmonotonic_dynamic_stamps": self.nonmonotonic_dynamic_stamps,
            "duplicate_dynamic_stamps": self.duplicate_dynamic_stamps,
        }


@dataclass
class BagScan:
    """Read-only sensor and TF publication/topology summary."""

    sha256_before: str
    message_count: int
    topic_counts: dict[str, int]
    connections: tuple[dict[str, Any], ...]
    sensor_samples: tuple[SensorSample, ...]
    edges: dict[tuple[str, str], EdgeScan]
    roots: tuple[str, ...]
    errors: list[str]
    warnings: list[str]

    @property
    def dynamic_transform_count(self) -> int:
        return sum(edge.dynamic_count for edge in self.edges.values())

    @property
    def static_transform_count(self) -> int:
        return sum(edge.static_count for edge in self.edges.values())

    def reportable(self) -> dict[str, Any]:
        return {
            "sha256_before": self.sha256_before,
            "message_count": self.message_count,
            "topic_counts": dict(sorted(self.topic_counts.items())),
            "connections": list(self.connections),
            "sensor_samples": len(self.sensor_samples),
            "dynamic_transforms": self.dynamic_transform_count,
            "static_transforms": self.static_transform_count,
            "roots": list(self.roots),
            "edges": [
                self.edges[key].reportable() for key in sorted(self.edges)
            ],
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


@dataclass
class GeometryAnalysis:
    """URDF-edge geometry, extracted coordinates, and continuity findings."""

    joint_counts: dict[str, int]
    missing_joints: tuple[UrdfJoint, ...]
    extra_edges: tuple[tuple[str, str], ...]
    fixed_dynamic: tuple[str, ...]
    unsupported_source: tuple[str, ...]
    tf_states: dict[int, dict[str, float]]
    continuity: dict[str, dict[str, float | int | None]]
    limit_stats: dict[str, dict[str, float | int | str]]
    errors: list[str]
    warnings: list[str]

    def reportable(self) -> dict[str, Any]:
        return {
            "joint_counts": dict(sorted(self.joint_counts.items())),
            "missing_joints": [
                {
                    "name": joint.name,
                    "type": joint.joint_type,
                    "parent": joint.parent,
                    "child": joint.child,
                }
                for joint in self.missing_joints
            ],
            "extra_edges": [list(edge) for edge in self.extra_edges],
            "fixed_dynamic": list(self.fixed_dynamic),
            "unsupported_source": list(self.unsupported_source),
            "tf_state_count": len(self.tf_states),
            "continuity": self.continuity,
            "limit_stats": self.limit_stats,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


@dataclass
class MatchAnalysis:
    """Whole-state source matching and unit-safe error metrics."""

    complete_tf_states: int
    partial_tf_states: int
    matched_tf_states: int
    unmatched_tf_states: int
    ambiguous_candidates: int
    skipped_sensor_samples: int
    angular_rms_rad: float | None
    angular_max_rad: float | None
    angular_max_joint: str | None
    linear_rms_m: float | None
    linear_max_m: float | None
    linear_max_joint: str | None
    normalized_rms: float | None
    time_delta_ms: dict[str, float | None]
    sensor_continuity: dict[str, dict[str, float | int | None]]
    errors: list[str]
    warnings: list[str]

    def reportable(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ValidationOutcome:
    """Final policy-resolved status and auditable report document."""

    status: str
    exit_code: int
    errors: list[str]
    warnings: list[str]
    decisions: list[dict[str, str]]
    document: dict[str, Any]


ALLOWED_JOINT_TYPES = {
    "fixed",
    "revolute",
    "continuous",
    "prismatic",
    "planar",
    "floating",
}
AXIS_JOINT_TYPES = {"revolute", "continuous", "prismatic", "planar"}
SCALAR_JOINT_TYPES = {"revolute", "continuous", "prismatic"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    return digest.hexdigest()


def _parse_vector(
    raw: str, *, field: str, context: str
) -> tuple[float, float, float]:
    try:
        values = tuple(float(value) for value in raw.split())
    except ValueError as exc:
        raise ConfigError(f"{context} has invalid {field}") from exc
    if len(values) != 3 or not all(math.isfinite(value) for value in values):
        raise ConfigError(f"{context} {field} must contain three finite numbers")
    return values


def _normalize_vector(
    vector: tuple[float, float, float], *, context: str
) -> tuple[float, float, float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if not math.isfinite(norm) or norm <= 1.0e-15:
        raise ConfigError(f"{context} axis must be finite and non-zero")
    return tuple(value / norm for value in vector)


def quaternion_from_rpy(
    roll: float, pitch: float, yaw: float
) -> tuple[float, float, float, float]:
    cr, sr = math.cos(roll / 2.0), math.sin(roll / 2.0)
    cp, sp = math.cos(pitch / 2.0), math.sin(pitch / 2.0)
    cy, sy = math.cos(yaw / 2.0), math.sin(yaw / 2.0)
    quaternion = (
        sr * cp * cy - cr * sp * sy,
        cr * sp * cy + sr * cp * sy,
        cr * cp * sy - sr * sp * cy,
        cr * cp * cy + sr * sp * sy,
    )
    norm = math.sqrt(sum(value * value for value in quaternion))
    if not math.isfinite(norm) or norm <= 1.0e-15:
        raise ConfigError("URDF origin produced an invalid quaternion")
    return tuple(value / norm for value in quaternion)


def _required_link(node: ET.Element, tag: str, *, joint: str) -> str:
    child = node.find(tag)
    value = "" if child is None else child.attrib.get("link", "").strip()
    if not value:
        raise ConfigError(f"URDF joint {joint!r} requires {tag}.link")
    return value


def _optional_finite_attribute(
    node: ET.Element | None, attribute: str, *, context: str
) -> float | None:
    if node is None or attribute not in node.attrib:
        return None
    try:
        value = float(node.attrib[attribute])
    except ValueError as exc:
        raise ConfigError(f"{context} has invalid {attribute}") from exc
    if not math.isfinite(value):
        raise ConfigError(f"{context} {attribute} must be finite")
    return value


def _validate_acyclic_links(
    links: set[str], children: Mapping[str, Sequence[str]]
) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(link: str) -> None:
        if link in visiting:
            raise ConfigError(f"URDF joint graph contains a cycle at link {link!r}")
        if link in visited:
            return
        visiting.add(link)
        for child in children.get(link, ()):
            visit(child)
        visiting.remove(link)
        visited.add(link)

    for link in sorted(links):
        visit(link)


def read_urdf(path: Path) -> UrdfModel:
    """Read and validate every supported direct joint in a URDF."""

    if not path.is_file():
        raise ConfigError(f"URDF does not exist or is not a file: {path}")
    if path.suffix.lower() not in {".urdf", ".xml"}:
        raise ConfigError(f"URDF input must use .urdf or .xml: {path}")
    digest = sha256_file(path)
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        raise ConfigError(f"cannot parse URDF {path}: {exc}") from exc
    if root.tag != "robot":
        raise ConfigError("URDF root element must be <robot>")

    links: set[str] = set()
    for link_node in root.findall("link"):
        name = link_node.attrib.get("name", "").strip()
        if not name:
            raise ConfigError("URDF link requires a non-empty name")
        if name in links:
            raise ConfigError(f"URDF repeats link name {name!r}")
        links.add(name)
    if not links:
        raise ConfigError("URDF contains no links")

    names: set[str] = set()
    child_parent: dict[str, str] = {}
    children: dict[str, list[str]] = defaultdict(list)
    joints: list[UrdfJoint] = []
    for joint_node in root.findall("joint"):
        name = joint_node.attrib.get("name", "").strip()
        if not name:
            raise ConfigError("URDF joint requires a non-empty name")
        if name in names:
            raise ConfigError(f"URDF repeats joint name {name!r}")
        names.add(name)
        joint_type = joint_node.attrib.get("type", "").strip()
        if joint_type not in ALLOWED_JOINT_TYPES:
            raise ConfigError(f"URDF joint {name!r} has unsupported type {joint_type!r}")
        parent = _required_link(joint_node, "parent", joint=name)
        child = _required_link(joint_node, "child", joint=name)
        if parent not in links or child not in links:
            raise ConfigError(f"URDF joint {name!r} references an unknown link")
        if parent == child:
            raise ConfigError(f"URDF joint {name!r} has the same parent and child")
        if child in child_parent:
            raise ConfigError(
                f"URDF child {child!r} has multiple parents: "
                f"{child_parent[child]!r} and {parent!r}"
            )
        child_parent[child] = parent
        children[parent].append(child)

        origin = joint_node.find("origin")
        xyz = _parse_vector(
            "0 0 0" if origin is None else origin.attrib.get("xyz", "0 0 0"),
            field="origin.xyz",
            context=f"URDF joint {name!r}",
        )
        rpy = _parse_vector(
            "0 0 0" if origin is None else origin.attrib.get("rpy", "0 0 0"),
            field="origin.rpy",
            context=f"URDF joint {name!r}",
        )
        axis = None
        if joint_type in AXIS_JOINT_TYPES:
            axis_node = joint_node.find("axis")
            axis = _normalize_vector(
                _parse_vector(
                    "1 0 0" if axis_node is None else axis_node.attrib.get("xyz", "1 0 0"),
                    field="axis.xyz",
                    context=f"URDF joint {name!r}",
                ),
                context=f"URDF joint {name!r}",
            )

        limit = joint_node.find("limit")
        lower = _optional_finite_attribute(limit, "lower", context=f"URDF joint {name!r} limit")
        upper = _optional_finite_attribute(limit, "upper", context=f"URDF joint {name!r} limit")
        velocity = _optional_finite_attribute(
            limit, "velocity", context=f"URDF joint {name!r} limit"
        )
        if joint_type in {"revolute", "prismatic"} and (lower is None or upper is None):
            raise ConfigError(f"URDF joint {name!r} requires finite lower and upper limits")
        if lower is not None and upper is not None and lower > upper:
            raise ConfigError(f"URDF joint {name!r} has lower limit above upper limit")
        if velocity is not None and velocity < 0.0:
            raise ConfigError(f"URDF joint {name!r} velocity limit must be non-negative")

        joints.append(
            UrdfJoint(
                name=name,
                joint_type=joint_type,
                parent=parent,
                child=child,
                origin_xyz=xyz,
                origin_rpy=rpy,
                origin_quaternion=quaternion_from_rpy(*rpy),
                axis=axis,
                lower=lower,
                upper=upper,
                velocity_limit=velocity,
            )
        )

    _validate_acyclic_links(links, children)
    roots = tuple(sorted(links - set(child_parent)))
    return UrdfModel(
        links=frozenset(links),
        joints=tuple(joints),
        roots=roots,
        sha256=digest,
    )


def _load_rosbags():
    try:
        from rosbags.highlevel import AnyReader
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise ConfigError("rosbags is required in the VelaLoom environment") from exc
    return AnyReader


def _field_value(message: Any, path: str) -> Any:
    value = message
    for component in path.split("."):
        if not component or not hasattr(value, component):
            raise ConfigError(f"message does not provide configured field {path!r}")
        value = getattr(value, component)
    return value


def _timestamp_ns(value: Any, *, field_name: str) -> int:
    if hasattr(value, "sec") and hasattr(value, "nanosec"):
        sec, nanosec = int(value.sec), int(value.nanosec)
        if nanosec < 0 or nanosec >= 1_000_000_000:
            raise ConfigError(f"{field_name} contains an invalid nanosecond value")
        return sec * 1_000_000_000 + nanosec
    if isinstance(value, bool):
        raise ConfigError(f"{field_name} must be a ROS time or numeric seconds")
    try:
        seconds = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{field_name} must be a ROS time or numeric seconds") from exc
    if not math.isfinite(seconds):
        raise ConfigError(f"{field_name} must be finite")
    return round(seconds * 1_000_000_000)


def _finite_array(value: Any, *, field_name: str) -> tuple[float, ...]:
    if isinstance(value, (str, bytes)):
        raise ConfigError(f"{field_name} must be a numeric array")
    try:
        result = tuple(float(item) for item in value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{field_name} must be a numeric array") from exc
    if not all(math.isfinite(item) for item in result):
        raise DataError(f"{field_name} contains a non-finite value")
    return result


def _quaternion_tuple(rotation: Any) -> tuple[float, float, float, float]:
    return (
        float(rotation.x),
        float(rotation.y),
        float(rotation.z),
        float(rotation.w),
    )


def _translation_tuple(translation: Any) -> tuple[float, float, float]:
    return (float(translation.x), float(translation.y), float(translation.z))


def _same_rotation(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
    tolerance: float,
) -> bool:
    return all(abs(a - b) <= tolerance for a, b in zip(first, second)) or all(
        abs(a + b) <= tolerance for a, b in zip(first, second)
    )


def _same_pose(
    first: tuple[tuple[float, float, float], tuple[float, float, float, float]],
    second: tuple[tuple[float, float, float], tuple[float, float, float, float]],
    config: EffectiveConfig,
) -> bool:
    return all(
        abs(a - b) <= config.thresholds["translation_m"]
        for a, b in zip(first[0], second[0])
    ) and _same_rotation(first[1], second[1], config.thresholds["rotation_rad"])


def _tf_graph_findings(
    edges: Mapping[tuple[str, str], EdgeScan]
) -> tuple[tuple[str, ...], list[str]]:
    errors: list[str] = []
    parents_by_child: dict[str, set[str]] = defaultdict(set)
    children: dict[str, set[str]] = defaultdict(set)
    nodes: set[str] = set()
    for parent, child in edges:
        parents_by_child[child].add(parent)
        children[parent].add(child)
        nodes.update((parent, child))
    for child, parents in sorted(parents_by_child.items()):
        if len(parents) > 1:
            errors.append(
                f"TF child {child!r} has multiple parents: {', '.join(sorted(parents))}"
            )

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            errors.append(f"TF graph contains a cycle at frame {node!r}")
            return
        if node in visited:
            return
        visiting.add(node)
        for child in sorted(children.get(node, ())):
            visit(child)
        visiting.remove(node)
        visited.add(node)

    for node in sorted(nodes):
        visit(node)
    roots = tuple(sorted(nodes - set(parents_by_child)))
    return roots, errors


def validate_joint_map(config: EffectiveConfig, model: UrdfModel) -> None:
    if not config.joint_map:
        raise ConfigError("at least one valid joint mapping is required")
    by_name = model.by_name
    for index, name in sorted(config.joint_map.items()):
        joint = by_name.get(name)
        if joint is None:
            raise ConfigError(f"joint mapping index {index} references unknown joint {name!r}")
        if joint.joint_type not in SCALAR_JOINT_TYPES:
            raise ConfigError(
                f"joint mapping {name!r} uses {joint.joint_type!r}; only revolute, "
                "continuous, and prismatic joints accept scalar source indices"
            )


def preflight(config: EffectiveConfig) -> None:
    if not config.bag.is_file():
        raise ConfigError(f"bag does not exist or is not a file: {config.bag}")
    if config.bag.suffix.lower() != ".bag":
        raise ConfigError(f"bag input must use .bag: {config.bag}")
    if config.json_out is not None:
        if config.json_out in {config.bag, config.urdf}:
            raise ConfigError("JSON report path must differ from bag and URDF inputs")
        if config.json_out.exists():
            raise ConfigError(f"JSON report already exists: {config.json_out}")
        if not config.json_out.parent.is_dir():
            raise ConfigError(
                f"JSON report parent directory does not exist: {config.json_out.parent}"
            )


def scan_bag(config: EffectiveConfig, model: UrdfModel) -> BagScan:
    """Scan source and TF topics without writing or retaining all transforms."""

    preflight(config)
    validate_joint_map(config, model)
    AnyReader = _load_rosbags()
    input_hash = sha256_file(config.bag)
    edges: dict[tuple[str, str], EdgeScan] = {}
    sensor_samples: list[SensorSample] = []
    errors: list[str] = []
    warnings: list[str] = []
    topic_counts: Counter[str] = Counter()
    message_count = 0
    previous_sensor_stamp: int | None = None

    try:
        reader_context = AnyReader([config.bag])
        with reader_context as reader:
            selected = [
                connection
                for connection in reader.connections
                if connection.topic
                in {config.sensor_topic, config.tf_topic, config.tf_static_topic}
            ]
            by_topic: dict[str, list[Any]] = defaultdict(list)
            for connection in selected:
                by_topic[connection.topic].append(connection)
            if not by_topic[config.sensor_topic]:
                raise ConfigError(f"sensor topic not found in bag: {config.sensor_topic}")
            if not by_topic[config.tf_topic] and not by_topic[config.tf_static_topic]:
                raise ConfigError("neither configured TF topic exists in the bag")

            connection_report = tuple(
                {
                    "id": int(connection.id),
                    "topic": connection.topic,
                    "msgtype": connection.msgtype,
                    "msgcount": int(connection.msgcount),
                    "callerid": getattr(connection.ext, "callerid", None),
                    "latching": getattr(connection.ext, "latching", None),
                }
                for connection in sorted(selected, key=lambda item: item.id)
            )
            max_index = max(config.joint_map)
            for connection, record_ns, raw in reader.messages(connections=selected):
                message_count += 1
                topic_counts[connection.topic] += 1
                message = reader.deserialize(raw, connection.msgtype)
                if connection.topic == config.sensor_topic:
                    try:
                        positions = _finite_array(
                            _field_value(message, config.position_field),
                            field_name=config.position_field,
                        )
                    except DataError as exc:
                        errors.append(str(exc))
                        continue
                    if max_index >= len(positions):
                        raise ConfigError(
                            f"sensor position array length {len(positions)} does not cover "
                            f"mapped index {max_index}"
                        )
                    velocities = None
                    if config.velocity_field is not None:
                        try:
                            velocities = _finite_array(
                                _field_value(message, config.velocity_field),
                                field_name=config.velocity_field,
                            )
                        except DataError as exc:
                            errors.append(str(exc))
                            continue
                        if max_index >= len(velocities):
                            raise ConfigError(
                                f"sensor velocity array length {len(velocities)} does not cover "
                                f"mapped index {max_index}"
                            )
                    stamp_ns = _timestamp_ns(
                        _field_value(message, config.timestamp_field),
                        field_name=config.timestamp_field,
                    )
                    if previous_sensor_stamp is not None and stamp_ns < previous_sensor_stamp:
                        errors.append(
                            f"sensor timestamp regressed from {previous_sensor_stamp} to {stamp_ns}"
                        )
                    previous_sensor_stamp = stamp_ns
                    sensor_samples.append(
                        SensorSample(stamp_ns, int(record_ns), positions, velocities)
                    )
                    continue

                if not hasattr(message, "transforms"):
                    raise ConfigError(
                        f"configured TF topic {connection.topic} has incompatible message type"
                    )
                is_static = connection.topic == config.tf_static_topic
                caller = getattr(connection.ext, "callerid", None)
                for transform in message.transforms:
                    parent = str(transform.header.frame_id).strip()
                    child = str(transform.child_frame_id).strip()
                    if not parent or not child:
                        errors.append("TF transform has an empty parent or child frame")
                        continue
                    if parent == child:
                        errors.append(f"TF transform repeats frame {parent!r} as parent and child")
                        continue
                    try:
                        translation = _translation_tuple(transform.transform.translation)
                        rotation = _quaternion_tuple(transform.transform.rotation)
                    except (AttributeError, TypeError, ValueError) as exc:
                        raise ConfigError(
                            f"configured TF topic {connection.topic} has incompatible transform fields"
                        ) from exc
                    if not all(math.isfinite(value) for value in (*translation, *rotation)):
                        errors.append(f"TF edge {parent!r}->{child!r} contains non-finite values")
                        continue
                    norm = math.sqrt(sum(value * value for value in rotation))
                    if abs(norm - 1.0) > config.thresholds["quaternion_norm"]:
                        errors.append(
                            f"TF edge {parent!r}->{child!r} has non-normalized quaternion "
                            f"(norm={norm:.9g})"
                        )
                        continue
                    normalized = tuple(value / norm for value in rotation)
                    key = (parent, child)
                    edge = edges.setdefault(key, EdgeScan(parent, child))
                    edge.connections.add(int(connection.id))
                    if caller:
                        edge.callers.add(str(caller))
                    if is_static:
                        edge.static_count += 1
                        pose = (translation, normalized)
                        if edge.first_static_pose is None:
                            edge.first_static_pose = pose
                        elif not _same_pose(edge.first_static_pose, pose, config):
                            edge.static_pose_conflicts += 1
                    else:
                        edge.dynamic_count += 1
                        stamp = _timestamp_ns(
                            transform.header.stamp, field_name="TF header.stamp"
                        )
                        if edge.last_dynamic_stamp is not None:
                            if stamp < edge.last_dynamic_stamp:
                                edge.nonmonotonic_dynamic_stamps += 1
                            elif stamp == edge.last_dynamic_stamp:
                                edge.duplicate_dynamic_stamps += 1
                        edge.last_dynamic_stamp = stamp
    except ConfigError:
        raise
    except (OSError, RuntimeError) as exc:
        raise ConfigError(f"cannot scan bag {config.bag}: {exc}") from exc

    if topic_counts[config.sensor_topic] == 0:
        raise ConfigError(f"sensor topic {config.sensor_topic} contains no messages")
    if not sensor_samples:
        errors.append(f"sensor topic {config.sensor_topic} contains no valid messages")
    for edge in edges.values():
        if edge.static_pose_conflicts:
            errors.append(
                f"static TF edge {edge.parent!r}->{edge.child!r} has conflicting poses"
            )
        elif edge.static_count > 1:
            warnings.append(
                f"static TF edge {edge.parent!r}->{edge.child!r} is duplicated "
                f"{edge.static_count} times"
            )
        if len(edge.callers) > 1:
            errors.append(
                f"TF edge {edge.parent!r}->{edge.child!r} has multiple callers: "
                + ", ".join(sorted(edge.callers))
            )
        if edge.nonmonotonic_dynamic_stamps:
            errors.append(
                f"dynamic TF edge {edge.parent!r}->{edge.child!r} has "
                f"{edge.nonmonotonic_dynamic_stamps} timestamp regressions"
            )
    roots, graph_errors = _tf_graph_findings(edges)
    errors.extend(graph_errors)
    if config.expected_root is not None:
        if roots != (config.expected_root,):
            errors.append(
                f"expected unique TF root {config.expected_root!r}, observed {list(roots)!r}"
            )
    if sha256_file(config.bag) != input_hash:
        errors.append("bag SHA-256 changed during read-only scan")
    return BagScan(
        sha256_before=input_hash,
        message_count=message_count,
        topic_counts=dict(topic_counts),
        connections=connection_report,
        sensor_samples=tuple(sensor_samples),
        edges=edges,
        roots=roots,
        errors=errors,
        warnings=warnings,
    )


def _quat_conjugate(
    quaternion: tuple[float, float, float, float]
) -> tuple[float, float, float, float]:
    return (-quaternion[0], -quaternion[1], -quaternion[2], quaternion[3])


def _quat_multiply(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    ax, ay, az, aw = first
    bx, by, bz, bw = second
    result = (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )
    norm = math.sqrt(sum(value * value for value in result))
    return tuple(value / norm for value in result)


def _quat_axis_angle(
    axis: tuple[float, float, float], angle: float
) -> tuple[float, float, float, float]:
    sine = math.sin(angle / 2.0)
    return (
        axis[0] * sine,
        axis[1] * sine,
        axis[2] * sine,
        math.cos(angle / 2.0),
    )


def _rotation_distance(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> float:
    dot = abs(sum(a * b for a, b in zip(first, second)))
    return 2.0 * math.acos(min(1.0, max(-1.0, dot)))


def _rotate_vector(
    quaternion: tuple[float, float, float, float],
    vector: tuple[float, float, float],
) -> tuple[float, float, float]:
    pure = (vector[0], vector[1], vector[2], 0.0)
    # This unrolled product avoids normalizing the pure-vector intermediate.
    x, y, z, w = quaternion
    vx, vy, vz, _ = pure
    first = (
        w * vx + y * vz - z * vy,
        w * vy - x * vz + z * vx,
        w * vz + x * vy - y * vx,
        -x * vx - y * vy - z * vz,
    )
    cx, cy, cz, cw = _quat_conjugate(quaternion)
    return (
        first[3] * cx + first[0] * cw + first[1] * cz - first[2] * cy,
        first[3] * cy - first[0] * cz + first[1] * cw + first[2] * cx,
        first[3] * cz + first[0] * cy - first[1] * cx + first[2] * cw,
    )


def _vector_norm(vector: Sequence[float]) -> float:
    return math.sqrt(sum(value * value for value in vector))


def _angular_difference(first: float, second: float) -> float:
    return abs((first - second + math.pi) % (2.0 * math.pi) - math.pi)


def _extract_joint_coordinate(
    joint: UrdfJoint,
    translation: tuple[float, float, float],
    rotation: tuple[float, float, float, float],
    config: EffectiveConfig,
) -> tuple[float | None, list[str]]:
    """Validate one pose and return its scalar coordinate when applicable."""

    problems: list[str] = []
    translation_tolerance = config.thresholds["translation_m"]
    rotation_tolerance = config.thresholds["rotation_rad"]
    assert translation_tolerance is not None and rotation_tolerance is not None
    origin_q = joint.origin_quaternion
    relative_q = _quat_multiply(_quat_conjugate(origin_q), rotation)
    delta_parent = tuple(
        value - origin for value, origin in zip(translation, joint.origin_xyz)
    )
    delta_local = _rotate_vector(_quat_conjugate(origin_q), delta_parent)

    if joint.joint_type == "fixed":
        if _vector_norm(delta_parent) > translation_tolerance:
            problems.append("translation differs from URDF fixed origin")
        if _rotation_distance(origin_q, rotation) > rotation_tolerance:
            problems.append("rotation differs from URDF fixed origin")
        return None, problems

    if joint.joint_type in {"revolute", "continuous"}:
        assert joint.axis is not None
        if _vector_norm(delta_parent) > translation_tolerance:
            problems.append("translation moves for a rotational joint")
        vector = relative_q[:3]
        projection = sum(value * axis for value, axis in zip(vector, joint.axis))
        angle = 2.0 * math.atan2(projection, relative_q[3])
        expected = _quat_axis_angle(joint.axis, angle)
        if _rotation_distance(relative_q, expected) > rotation_tolerance:
            problems.append("rotation is not confined to the URDF axis")
        return angle, problems

    if joint.joint_type == "prismatic":
        assert joint.axis is not None
        if _rotation_distance(origin_q, rotation) > rotation_tolerance:
            problems.append("rotation moves for a prismatic joint")
        coordinate = sum(value * axis for value, axis in zip(delta_local, joint.axis))
        residual = tuple(
            value - coordinate * axis for value, axis in zip(delta_local, joint.axis)
        )
        if _vector_norm(residual) > translation_tolerance:
            problems.append("translation is not confined to the URDF axis")
        return coordinate, problems

    if joint.joint_type == "planar":
        assert joint.axis is not None
        normal_component = sum(
            value * axis for value, axis in zip(delta_local, joint.axis)
        )
        if abs(normal_component) > translation_tolerance:
            problems.append("planar translation leaves the URDF plane")
        vector = relative_q[:3]
        projection = sum(value * axis for value, axis in zip(vector, joint.axis))
        angle = 2.0 * math.atan2(projection, relative_q[3])
        if _rotation_distance(relative_q, _quat_axis_angle(joint.axis, angle)) > rotation_tolerance:
            problems.append("planar rotation is not about the plane normal")
        return None, problems

    # A floating joint permits every finite, normalized rigid transform.
    return None, problems


def _limit_thresholds(
    joint: UrdfJoint, config: EffectiveConfig
) -> tuple[float, float, str]:
    if joint.joint_type == "prismatic":
        warn = config.thresholds["linear_limit_warn_m"]
        fail = config.thresholds["linear_limit_fail_m"]
        unit = "m"
    else:
        warn = config.thresholds["angular_limit_warn_rad"]
        fail = config.thresholds["angular_limit_fail_rad"]
        unit = "rad"
    assert warn is not None and fail is not None
    return warn, fail, unit


def _quantile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _continuity_metrics(
    series: Mapping[str, list[tuple[int, float]]],
    model: UrdfModel,
    config: EffectiveConfig,
    warnings: list[str],
    *,
    source_label: str = "TF",
) -> dict[str, dict[str, float | int | None]]:
    result: dict[str, dict[str, float | int | None]] = {}
    by_name = model.by_name
    for name, raw_values in sorted(series.items()):
        values = sorted(set(raw_values))
        intervals_ms: list[float] = []
        jumps: list[float] = []
        speeds: list[float] = []
        joint = by_name[name]
        for (first_stamp, first), (second_stamp, second) in zip(values, values[1:]):
            delta_ns = second_stamp - first_stamp
            if delta_ns <= 0:
                continue
            interval_ms = delta_ns / 1_000_000.0
            jump = (
                _angular_difference(second, first)
                if joint.joint_type in {"revolute", "continuous"}
                else abs(second - first)
            )
            intervals_ms.append(interval_ms)
            jumps.append(jump)
            speeds.append(jump / (delta_ns / 1_000_000_000.0))
        duration = (values[-1][0] - values[0][0]) / 1_000_000_000.0 if len(values) > 1 else 0.0
        metrics = {
            "samples": len(values),
            "frequency_hz": (len(values) - 1) / duration if duration > 0.0 else None,
            "interval_p50_ms": _quantile(intervals_ms, 0.50),
            "interval_p99_ms": _quantile(intervals_ms, 0.99),
            "interval_max_ms": max(intervals_ms) if intervals_ms else None,
            "jump_p50": _quantile(jumps, 0.50),
            "jump_p99": _quantile(jumps, 0.99),
            "jump_max": max(jumps) if jumps else None,
            "speed_max": max(speeds) if speeds else None,
            "velocity_limit": joint.velocity_limit,
        }
        result[name] = metrics
        gap_limit = config.thresholds["continuity_gap_ms"]
        jump_limit = config.thresholds[
            "linear_jump_m" if joint.joint_type == "prismatic" else "angular_jump_rad"
        ]
        if gap_limit is not None and metrics["interval_max_ms"] is not None and metrics["interval_max_ms"] > gap_limit:
            warnings.append(
                f"joint {name!r} maximum {source_label} gap "
                f"{metrics['interval_max_ms']:.6g} ms "
                f"exceeds {gap_limit:.6g} ms"
            )
        if jump_limit is not None and metrics["jump_max"] is not None and metrics["jump_max"] > jump_limit:
            warnings.append(
                f"joint {name!r} maximum {source_label} jump "
                f"{metrics['jump_max']:.6g} exceeds "
                f"{jump_limit:.6g}"
            )
        if (
            joint.velocity_limit is not None
            and metrics["speed_max"] is not None
            and metrics["speed_max"] > joint.velocity_limit
        ):
            warnings.append(
                f"joint {name!r} {source_label}-derived peak speed "
                f"{metrics['speed_max']:.6g} exceeds "
                f"URDF velocity limit {joint.velocity_limit:.6g}; this is an auxiliary "
                "indicator because sampled data may omit intermediate motion"
            )
    return result


def analyze_geometry(
    config: EffectiveConfig, model: UrdfModel, scan: BagScan
) -> GeometryAnalysis:
    """Second-pass, bounded-memory URDF geometry and coordinate analysis."""

    AnyReader = _load_rosbags()
    by_edge = model.by_edge
    mapped_names = set(config.joint_map.values())
    joint_counts: Counter[str] = Counter()
    states: dict[int, dict[str, float]] = defaultdict(dict)
    continuity_series: dict[str, list[tuple[int, float]]] = defaultdict(list)
    limit_stats: dict[str, dict[str, float | int | str]] = {}
    errors: list[str] = []
    warnings: list[str] = []
    seen_problem_keys: set[tuple[str, str]] = set()
    with AnyReader([config.bag]) as reader:
        selected = [
            connection
            for connection in reader.connections
            if connection.topic in {config.tf_topic, config.tf_static_topic}
        ]
        for connection, _, raw in reader.messages(connections=selected):
            message = reader.deserialize(raw, connection.msgtype)
            is_static = connection.topic == config.tf_static_topic
            for transform in message.transforms:
                parent = str(transform.header.frame_id).strip()
                child = str(transform.child_frame_id).strip()
                joint = by_edge.get((parent, child))
                if joint is None:
                    continue
                translation = _translation_tuple(transform.transform.translation)
                rotation = _quaternion_tuple(transform.transform.rotation)
                if not all(math.isfinite(value) for value in (*translation, *rotation)):
                    continue
                norm = math.sqrt(sum(value * value for value in rotation))
                if norm <= 0.0 or abs(norm - 1.0) > config.thresholds["quaternion_norm"]:
                    continue
                rotation = tuple(value / norm for value in rotation)
                joint_counts[joint.name] += 1
                coordinate, problems = _extract_joint_coordinate(
                    joint, translation, rotation, config
                )
                for problem in problems:
                    key = (joint.name, problem)
                    if key not in seen_problem_keys:
                        errors.append(f"joint {joint.name!r}: {problem}")
                        seen_problem_keys.add(key)
                if coordinate is None:
                    continue
                if joint.lower is not None and joint.upper is not None:
                    violation = max(
                        joint.lower - coordinate, coordinate - joint.upper, 0.0
                    )
                    warn_limit, fail_limit, unit = _limit_thresholds(joint, config)
                    stats = limit_stats.setdefault(
                        joint.name,
                        {
                            "unit": unit,
                            "samples": 0,
                            "minimum": coordinate,
                            "maximum": coordinate,
                            "violations": 0,
                            "failures": 0,
                            "max_violation": 0.0,
                        },
                    )
                    stats["samples"] += 1
                    stats["minimum"] = min(float(stats["minimum"]), coordinate)
                    stats["maximum"] = max(float(stats["maximum"]), coordinate)
                    stats["max_violation"] = max(
                        float(stats["max_violation"]), violation
                    )
                    if violation > warn_limit:
                        stats["violations"] += 1
                    if violation > fail_limit:
                        stats["failures"] += 1
                if is_static:
                    continue
                stamp = _timestamp_ns(transform.header.stamp, field_name="TF header.stamp")
                continuity_series[joint.name].append((stamp, coordinate))
                if joint.name in mapped_names:
                    previous = states[stamp].get(joint.name)
                    if previous is not None and (
                        _angular_difference(previous, coordinate)
                        if joint.joint_type in {"revolute", "continuous"}
                        else abs(previous - coordinate)
                    ) > config.thresholds[
                        "rotation_rad"
                        if joint.joint_type in {"revolute", "continuous"}
                        else "translation_m"
                    ]:
                        errors.append(
                            f"joint {joint.name!r} has conflicting TF coordinates at stamp {stamp}"
                        )
                    else:
                        states[stamp][joint.name] = coordinate

    observed_edges = set(scan.edges)
    missing = tuple(
        joint for joint in model.joints if (joint.parent, joint.child) not in observed_edges
    )
    for joint in missing:
        if (joint.child, joint.parent) in observed_edges:
            errors.append(
                f"URDF joint {joint.name!r} appears reversed in TF: "
                f"{joint.child!r}->{joint.parent!r}"
            )
    extra = tuple(sorted(observed_edges - set(by_edge)))
    fixed_dynamic = tuple(
        sorted(
            joint.name
            for joint in model.joints
            if joint.joint_type == "fixed"
            and scan.edges.get((joint.parent, joint.child)) is not None
            and scan.edges[(joint.parent, joint.child)].dynamic_count
        )
    )
    unsupported = tuple(
        sorted(
            joint.name
            for joint in model.joints
            if joint.joint_type in {"planar", "floating"}
        )
    )
    for joint in model.joints:
        edge = scan.edges.get((joint.parent, joint.child))
        if edge is None:
            continue
        if edge.dynamic_count and edge.static_count:
            errors.append(
                f"URDF joint {joint.name!r} is published on both dynamic and static TF topics"
            )
        if joint.joint_type != "fixed" and edge.static_count:
            errors.append(
                f"movable URDF joint {joint.name!r} is published on the static TF topic"
            )
    for name, stats in sorted(limit_stats.items()):
        if not stats["violations"]:
            continue
        joint = model.by_name[name]
        detail = (
            f"joint {name!r} observed range [{float(stats['minimum']):.9g}, "
            f"{float(stats['maximum']):.9g}] {stats['unit']} against limits "
            f"[{joint.lower:.9g}, {joint.upper:.9g}]; maximum violation "
            f"{float(stats['max_violation']):.9g} {stats['unit']} across "
            f"{stats['violations']} samples"
        )
        if stats["failures"]:
            errors.append(detail)
        else:
            warnings.append(detail)
    continuity = _continuity_metrics(
        continuity_series, model, config, warnings
    )
    return GeometryAnalysis(
        joint_counts=dict(joint_counts),
        missing_joints=missing,
        extra_edges=extra,
        fixed_dynamic=fixed_dynamic,
        unsupported_source=unsupported,
        tf_states=dict(states),
        continuity=continuity,
        limit_stats=limit_stats,
        errors=errors,
        warnings=warnings,
    )


def _rms(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return math.sqrt(sum(value * value for value in values) / len(values))


def match_source_states(
    config: EffectiveConfig,
    model: UrdfModel,
    scan: BagScan,
    geometry: GeometryAnalysis,
) -> MatchAnalysis:
    """Match complete TF states to one whole sensor message within the window."""

    by_name = model.by_name
    required = set(config.joint_map.values())
    samples = sorted(scan.sensor_samples, key=lambda sample: sample.stamp_ns)
    sample_stamps = [sample.stamp_ns for sample in samples]
    complete: list[tuple[int, dict[str, float]]] = []
    partial = 0
    for stamp, state in sorted(geometry.tf_states.items()):
        if required.issubset(state):
            complete.append((stamp, state))
        else:
            partial += 1

    angular_errors: list[tuple[float, str]] = []
    linear_errors: list[tuple[float, str]] = []
    normalized_errors: list[float] = []
    time_deltas: list[float] = []
    used_samples: set[int] = set()
    unmatched = 0
    ambiguous = 0
    angular_rms_limit = config.thresholds["angular_rms_rad"]
    angular_max_limit = config.thresholds["angular_max_rad"]
    linear_rms_limit = config.thresholds["linear_rms_m"]
    linear_max_limit = config.thresholds["linear_max_m"]
    assert all(
        value is not None
        for value in (
            angular_rms_limit,
            angular_max_limit,
            linear_rms_limit,
            linear_max_limit,
        )
    )

    for tf_stamp, state in complete:
        start = bisect.bisect_left(sample_stamps, tf_stamp - config.before_ns)
        stop = bisect.bisect_right(sample_stamps, tf_stamp + config.after_ns)
        candidates: list[
            tuple[float, int, int, list[tuple[float, str]], list[tuple[float, str]]]
        ] = []
        for sample_index in range(start, stop):
            sample = samples[sample_index]
            angular: list[tuple[float, str]] = []
            linear: list[tuple[float, str]] = []
            normalized: list[float] = []
            for index, name in config.joint_map.items():
                joint = by_name[name]
                observed = state[name]
                expected = sample.positions[index]
                if joint.joint_type in {"revolute", "continuous"}:
                    error = _angular_difference(observed, expected)
                    angular.append((error, name))
                    normalized.append(error / angular_max_limit)
                else:
                    error = abs(observed - expected)
                    linear.append((error, name))
                    normalized.append(error / linear_max_limit)
            angular_values = [item[0] for item in angular]
            linear_values = [item[0] for item in linear]
            acceptable = (
                (not angular_values or (_rms(angular_values) <= angular_rms_limit and max(angular_values) <= angular_max_limit))
                and (not linear_values or (_rms(linear_values) <= linear_rms_limit and max(linear_values) <= linear_max_limit))
            )
            if acceptable:
                score = _rms(normalized) or 0.0
                candidates.append(
                    (
                        score,
                        abs(sample.stamp_ns - tf_stamp),
                        sample_index,
                        angular,
                        linear,
                    )
                )
        if not candidates:
            unmatched += 1
            continue
        candidates.sort(key=lambda item: (item[0], item[1], item[2]))
        selected = candidates[0]
        if len(candidates) > 1 and math.isclose(
            candidates[1][0], selected[0], rel_tol=1.0e-6, abs_tol=1.0e-12
        ):
            ambiguous += 1
        _, _, sample_index, angular, linear = selected
        used_samples.add(sample_index)
        angular_errors.extend(angular)
        linear_errors.extend(linear)
        normalized_errors.extend(
            [value / angular_max_limit for value, _ in angular]
            + [value / linear_max_limit for value, _ in linear]
        )
        time_deltas.append((samples[sample_index].stamp_ns - tf_stamp) / 1_000_000.0)

    errors: list[str] = []
    warnings: list[str] = []
    sensor_series: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for sample in samples:
        for index, name in config.joint_map.items():
            sensor_series[name].append((sample.stamp_ns, sample.positions[index]))
    sensor_continuity = _continuity_metrics(
        sensor_series,
        model,
        config,
        warnings,
        source_label="sensor",
    )
    for index, name in config.joint_map.items():
        positions = [sample.positions[index] for sample in samples]
        metrics = sensor_continuity[name]
        metrics["position_min"] = min(positions) if positions else None
        metrics["position_max"] = max(positions) if positions else None
        reported = [
            abs(sample.velocities[index])
            for sample in samples
            if sample.velocities is not None
        ]
        metrics["reported_velocity_max"] = max(reported) if reported else None
        velocity_limit = by_name[name].velocity_limit
        if (
            velocity_limit is not None
            and metrics["reported_velocity_max"] is not None
            and metrics["reported_velocity_max"] > velocity_limit
        ):
            warnings.append(
                f"joint {name!r} reported sensor velocity "
                f"{metrics['reported_velocity_max']:.6g} exceeds URDF velocity limit "
                f"{velocity_limit:.6g}"
            )
    if not complete:
        errors.append("no TF timestamp contains every mapped joint")
    if unmatched:
        errors.append(
            f"{unmatched} complete TF states have no source candidate within time/error thresholds"
        )
    if ambiguous:
        warnings.append(
            f"{ambiguous} TF states have multiple equally plausible source candidates"
        )
    angular_max_pair = max(angular_errors, default=(None, None))
    linear_max_pair = max(linear_errors, default=(None, None))
    return MatchAnalysis(
        complete_tf_states=len(complete),
        partial_tf_states=partial,
        matched_tf_states=len(complete) - unmatched,
        unmatched_tf_states=unmatched,
        ambiguous_candidates=ambiguous,
        skipped_sensor_samples=len(samples) - len(used_samples),
        angular_rms_rad=_rms([value for value, _ in angular_errors]),
        angular_max_rad=angular_max_pair[0],
        angular_max_joint=angular_max_pair[1],
        linear_rms_m=_rms([value for value, _ in linear_errors]),
        linear_max_m=linear_max_pair[0],
        linear_max_joint=linear_max_pair[1],
        normalized_rms=_rms(normalized_errors),
        time_delta_ms={
            "p50": _quantile(time_deltas, 0.50),
            "p99": _quantile(time_deltas, 0.99),
            "min": min(time_deltas) if time_deltas else None,
            "max": max(time_deltas) if time_deltas else None,
        },
        sensor_continuity=sensor_continuity,
        errors=errors,
        warnings=warnings,
    )


def _stream_is_tty(stream: TextIO) -> bool:
    return bool(getattr(stream, "isatty", lambda: False)())


def _prompt_missing_joint_decisions(
    joints: Sequence[UrdfJoint],
    *,
    input_stream: TextIO,
    output_stream: TextIO,
) -> list[dict[str, str]]:
    if not _stream_is_tty(input_stream):
        raise DataError(
            "missing URDF joints require --missing-joint-policy fail|warn|ignore "
            "when stdin is not a TTY"
        )
    decisions: list[dict[str, str]] = []
    apply_remaining: str | None = None
    labels = {"f": "fail", "w": "warn", "i": "ignore"}
    for joint in joints:
        if apply_remaining is not None:
            decisions.append(
                {"category": "missing_joint", "item": joint.name, "decision": apply_remaining}
            )
            continue
        output_stream.write(
            f"Missing URDF joint {joint.name} ({joint.joint_type}, "
            f"{joint.parent}->{joint.child})\n"
            "Choose [F]ailure/[W]arning/[I]gnore/[A]bort or "
            "[FA]/[WA]/[IA] for all remaining: "
        )
        output_stream.flush()
        raw = input_stream.readline()
        if raw == "":
            raise UserAbort("input closed during missing-joint decision")
        choice = raw.strip().lower()
        if choice == "a":
            raise UserAbort("caller aborted missing-joint validation")
        if choice not in {"f", "w", "i", "fa", "wa", "ia"}:
            raise UserAbort(f"invalid missing-joint choice {choice!r}")
        decision = labels[choice[0]]
        decisions.append(
            {"category": "missing_joint", "item": joint.name, "decision": decision}
        )
        if choice.endswith("a"):
            apply_remaining = decision
    return decisions


def _policy_decisions(
    category: str, items: Sequence[str], policy: str
) -> list[dict[str, str]]:
    return [
        {"category": category, "item": item, "decision": policy} for item in items
    ]


def _apply_decisions(
    decisions: Sequence[Mapping[str, str]], errors: list[str], warnings: list[str]
) -> None:
    for decision in decisions:
        action = decision["decision"]
        detail = f"{decision['category']}: {decision['item']}"
        if action == "fail":
            errors.append(detail)
        elif action == "warn":
            warnings.append(detail)


def resolve_outcome(
    config: EffectiveConfig,
    model: UrdfModel,
    scan: BagScan,
    geometry: GeometryAnalysis,
    matching: MatchAnalysis,
    *,
    input_stream: TextIO,
    output_stream: TextIO,
) -> ValidationOutcome:
    """Apply interactive/non-interactive policies and produce final status."""

    errors = [*scan.errors, *geometry.errors, *matching.errors]
    warnings = [*scan.warnings, *geometry.warnings, *matching.warnings]
    decisions: list[dict[str, str]] = []
    if geometry.missing_joints:
        if config.missing_joint_policy == "interactive":
            try:
                decisions.extend(
                    _prompt_missing_joint_decisions(
                        geometry.missing_joints,
                        input_stream=input_stream,
                        output_stream=output_stream,
                    )
                )
            except DataError as exc:
                errors.append(str(exc))
        else:
            decisions.extend(
                _policy_decisions(
                    "missing_joint",
                    [joint.name for joint in geometry.missing_joints],
                    config.missing_joint_policy,
                )
            )
    decisions.extend(
        _policy_decisions(
            "extra_edge",
            [f"{parent}->{child}" for parent, child in geometry.extra_edges],
            config.extra_edge_policy,
        )
    )
    decisions.extend(
        _policy_decisions(
            "fixed_dynamic",
            list(geometry.fixed_dynamic),
            config.fixed_dynamic_policy,
        )
    )
    decisions.extend(
        _policy_decisions(
            "unsupported_source",
            list(geometry.unsupported_source),
            config.unsupported_source_policy,
        )
    )
    _apply_decisions(decisions, errors, warnings)

    bag_after = sha256_file(config.bag)
    urdf_after = sha256_file(config.urdf)
    if bag_after != scan.sha256_before:
        errors.append("bag SHA-256 changed during validation")
    if urdf_after != model.sha256:
        errors.append("URDF SHA-256 changed during validation")
    if errors or (config.strict and warnings):
        status, exit_code = "FAIL", 1
    elif warnings:
        status, exit_code = "PASS_WITH_WARNINGS", 0
    else:
        status, exit_code = "PASS", 0
    document = {
        "schema_version": 1,
        "status": status,
        "strict": config.strict,
        "inputs": {
            "bag": str(config.bag),
            "bag_sha256_before": scan.sha256_before,
            "bag_sha256_after": bag_after,
            "urdf": str(config.urdf),
            "urdf_sha256_before": model.sha256,
            "urdf_sha256_after": urdf_after,
            "unchanged": bag_after == scan.sha256_before and urdf_after == model.sha256,
        },
        "effective_config": config.reportable(),
        "urdf": {
            "links": len(model.links),
            "joints": len(model.joints),
            "joint_types": model.type_counts,
            "roots": list(model.roots),
        },
        "bag": scan.reportable(),
        "geometry": geometry.reportable(),
        "matching": matching.reportable(),
        "decisions": decisions,
        "errors": errors,
        "warnings": warnings,
    }
    return ValidationOutcome(status, exit_code, errors, warnings, decisions, document)


def print_report(outcome: ValidationOutcome, stream: TextIO) -> None:
    document = outcome.document
    inputs = document["inputs"]
    urdf = document["urdf"]
    bag = document["bag"]
    geometry = document["geometry"]
    matching = document["matching"]
    stream.write("\nTF validation report\n")
    stream.write(
        f"Inputs: bag={inputs['bag']} ({inputs['bag_sha256_before']}), "
        f"urdf={inputs['urdf']} ({inputs['urdf_sha256_before']})\n"
    )
    stream.write(
        f"URDF: {urdf['links']} links, {urdf['joints']} joints, "
        f"types={json.dumps(urdf['joint_types'], sort_keys=True)}\n"
    )
    stream.write(
        f"TF: roots={bag['roots']}, dynamic={bag['dynamic_transforms']}, "
        f"static={bag['static_transforms']}, connections={len(bag['connections'])}\n"
    )
    for connection in bag["connections"]:
        stream.write(
            f"  connection {connection['id']}: {connection['topic']} "
            f"caller={connection['callerid']!r} messages={connection['msgcount']}\n"
        )
    stream.write(
        f"Model coverage: observed={len(geometry['joint_counts'])}, "
        f"missing={len(geometry['missing_joints'])}, "
        f"extra_edges={len(geometry['extra_edges'])}\n"
    )
    stream.write(
        f"Source matching: complete={matching['complete_tf_states']}, "
        f"matched={matching['matched_tf_states']}, "
        f"unmatched={matching['unmatched_tf_states']}, "
        f"skipped_sensor={matching['skipped_sensor_samples']}\n"
    )
    stream.write(
        f"Errors: angular_rms={matching['angular_rms_rad']!r} rad, "
        f"angular_max={matching['angular_max_rad']!r} rad "
        f"({matching['angular_max_joint']!r}), linear_rms={matching['linear_rms_m']!r} m, "
        f"linear_max={matching['linear_max_m']!r} m "
        f"({matching['linear_max_joint']!r}), normalized_rms={matching['normalized_rms']!r}\n"
    )
    stream.write(
        f"Continuity: TF={len(geometry['continuity'])} scalar joints, "
        f"sensor={len(matching['sensor_continuity'])} mapped joints; "
        f"time_delta_ms={json.dumps(matching['time_delta_ms'], sort_keys=True)}\n"
    )
    if outcome.decisions:
        stream.write("Policy decisions:\n")
        for decision in outcome.decisions:
            stream.write(
                f"  {decision['category']} {decision['item']}: {decision['decision']}\n"
            )
    if outcome.errors:
        stream.write("Failures:\n")
        for error in outcome.errors:
            stream.write(f"  - {error}\n")
    if outcome.warnings:
        stream.write("Warnings:\n")
        for warning in outcome.warnings:
            stream.write(f"  - {warning}\n")
    stream.write(f"Final status: {outcome.status}\n")


def write_json_report(path: Path, document: Mapping[str, Any]) -> None:
    if path.exists():
        raise ConfigError(f"JSON report already exists: {path}")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(document, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, path)
    except OSError as exc:
        if temporary.exists():
            temporary.unlink()
        raise ConfigError(f"cannot write JSON report {path}: {exc}") from exc


def _load_yaml(path: Path) -> Mapping[str, Any]:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise ConfigError("PyYAML is required in the VelaLoom environment") from exc
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot load config {path}: {exc}") from exc
    if not isinstance(document, Mapping):
        raise ConfigError("config root must be a mapping")
    return document


def _validate_config_document(document: Mapping[str, Any]) -> None:
    unknown = set(document) - TOP_LEVEL_KEYS
    if unknown:
        raise ConfigError("unknown config keys: " + ", ".join(sorted(unknown)))
    if document.get("version") != SCHEMA_VERSION:
        raise ConfigError(
            f"config version must be {SCHEMA_VERSION}, got {document.get('version')!r}"
        )
    for section, allowed in SECTION_KEYS.items():
        value = document.get(section, {})
        if not isinstance(value, Mapping):
            raise ConfigError(f"config section {section!r} must be a mapping")
        section_unknown = set(value) - allowed
        if section_unknown:
            raise ConfigError(
                f"unknown {section} keys: " + ", ".join(sorted(section_unknown))
            )
    joints = document.get("joints", {})
    if not isinstance(joints, (Mapping, list)):
        raise ConfigError("config 'joints' must be a mapping or list")


def _nonempty_string(value: Any, *, field: str, allow_none: bool = False) -> str | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{field} must be a non-empty string")
    return value.strip()


def _finite_nonnegative(value: Any, *, field: str, allow_none: bool = False) -> float | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool):
        raise ConfigError(f"{field} must be a finite non-negative number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{field} must be a finite non-negative number") from exc
    if not math.isfinite(number) or number < 0.0:
        raise ConfigError(f"{field} must be a finite non-negative number")
    return number


def parse_joint_map(values: Mapping[Any, Any] | Sequence[Any]) -> dict[int, str]:
    """Parse config or CLI index-to-URDF-joint mappings."""

    if isinstance(values, Mapping):
        items = list(values.items())
    else:
        items = []
        for raw in values:
            if not isinstance(raw, str) or "=" not in raw:
                raise ConfigError(f"invalid joint mapping {raw!r}; expected INDEX=JOINT_NAME")
            index, name = raw.split("=", 1)
            items.append((index, name))
    result: dict[int, str] = {}
    names: set[str] = set()
    for raw_index, raw_name in items:
        if isinstance(raw_index, bool):
            raise ConfigError(f"invalid joint mapping index {raw_index!r}")
        try:
            index = int(raw_index)
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"invalid joint mapping index {raw_index!r}") from exc
        name = _nonempty_string(raw_name, field=f"joint mapping {index}")
        assert name is not None
        if index < 0:
            raise ConfigError("joint mapping indices must be non-negative")
        if index in result:
            raise ConfigError(f"joint mapping repeats index {index}")
        if name in names:
            raise ConfigError(f"joint mapping repeats joint name {name!r}")
        result[index] = name
        names.add(name)
    return result


def _reject_repeated_multivalue(argv: Sequence[str]) -> None:
    occurrences = sum(
        token == "--joint-map" or token.startswith("--joint-map=") for token in argv
    )
    if occurrences > 1:
        raise ConfigError("--joint-map may appear only once; list all mappings after it")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Default matching and threshold semantics (configured in YAML):
  before_ms=30, after_ms=5          sensor candidate window around TF time
  quaternion_norm=1e-6              allowed quaternion norm error
  translation_m=1e-6                URDF geometry translation tolerance (m)
  rotation_rad=1e-5                 URDF geometry rotation tolerance (rad)
  angular_rms_rad=0.01              whole-state angular RMS gate (rad)
  angular_max_rad=0.02              maximum single angular error (rad)
  linear_rms_m=0.0001               whole-state linear RMS gate (m)
  linear_max_m=0.001                maximum single linear error (m)
  angular_limit_warn/fail=1e-6/0.01 position-limit tiers (rad)
  linear_limit_warn/fail=1e-6/0.001 position-limit tiers (m)
  continuity_gap/angular_jump/linear_jump=null
                                       report continuity without universal hard gates

CLI values override YAML values; YAML overrides program defaults.  CLI paths are
relative to the current directory, while YAML paths are relative to that file.
""",
    )
    parser.add_argument("--config", type=Path, help="versioned YAML configuration")
    parser.add_argument("--bag", type=Path, help="read-only ROS1 bag")
    parser.add_argument("--urdf", type=Path, help="read-only URDF model")
    parser.add_argument("--sensor-topic", help="sensor joint-state source topic")
    parser.add_argument("--tf-topic", help="dynamic TF topic")
    parser.add_argument("--tf-static-topic", help="static TF topic")
    parser.add_argument("--position-field", help="dotted sensor position-array field")
    parser.add_argument("--velocity-field", help="dotted optional velocity-array field")
    parser.add_argument("--timestamp-field", help="dotted sensor timestamp field")
    parser.add_argument("--expected-root", help="optional expected unique TF root")
    parser.add_argument(
        "--joint-map",
        nargs="+",
        metavar="INDEX=JOINT_NAME",
        help="one option followed by all sensor-index mappings",
    )
    parser.add_argument(
        "--missing-joint-policy", choices=sorted(POLICY_VALUES)
    )
    parser.add_argument(
        "--extra-edge-policy", choices=sorted(NONINTERACTIVE_POLICY_VALUES)
    )
    parser.add_argument(
        "--fixed-dynamic-policy", choices=sorted(NONINTERACTIVE_POLICY_VALUES)
    )
    parser.add_argument("--json-out", type=Path, help="optional new JSON report path")
    parser.add_argument(
        "--strict", action="store_true", help="promote all warnings to failure"
    )
    return parser


def _set_nested(
    merged: dict[str, Any],
    sources: dict[str, str],
    section: str,
    key: str,
    value: Any,
    source: str,
) -> None:
    merged.setdefault(section, {})[key] = value
    sources[f"{section}.{key}"] = source


def resolve_config(
    argv: Sequence[str] | None = None,
    *,
    cwd: Path | None = None,
    default_config: Path = DEFAULT_CONFIG_PATH,
) -> EffectiveConfig:
    """Merge program defaults, YAML, and CLI into one audited configuration."""

    raw_argv = list(sys.argv[1:] if argv is None else argv)
    _reject_repeated_multivalue(raw_argv)
    args = build_parser().parse_args(raw_argv)
    invocation_dir = (Path.cwd() if cwd is None else cwd).resolve()

    merged = copy.deepcopy(PROGRAM_DEFAULTS)
    merged["inputs"] = {"bag": None, "urdf": None}
    sources: dict[str, str] = {}
    for section in ("topics", "source", "matching", "policies", "thresholds"):
        for key in merged[section]:
            sources[f"{section}.{key}"] = "program default"
    sources["expected_root"] = "program default"
    sources["joints"] = "program default"
    sources["inputs.bag"] = "unset"
    sources["inputs.urdf"] = "unset"

    loaded_config: Path | None = None
    requested = args.config
    if requested is not None:
        loaded_config = (
            requested if requested.is_absolute() else invocation_dir / requested
        ).resolve()
        if not loaded_config.is_file():
            raise ConfigError(f"explicit config does not exist: {loaded_config}")
    elif default_config.is_file():
        loaded_config = default_config.resolve()

    if loaded_config is not None:
        document = _load_yaml(loaded_config)
        _validate_config_document(document)
        config_source = f"config:{loaded_config}"
        config_dir = loaded_config.parent
        for key in ("bag", "urdf"):
            raw = document.get("inputs", {}).get(key)
            if raw is not None:
                text = _nonempty_string(raw, field=f"inputs.{key}")
                assert text is not None
                path = Path(text)
                merged["inputs"][key] = (
                    path if path.is_absolute() else config_dir / path
                ).resolve()
                sources[f"inputs.{key}"] = config_source
        for section in ("topics", "source", "matching", "policies", "thresholds"):
            for key, value in document.get(section, {}).items():
                _set_nested(merged, sources, section, key, value, config_source)
        if "expected_root" in document:
            merged["expected_root"] = document["expected_root"]
            sources["expected_root"] = config_source
        if "joints" in document:
            merged["joints"] = parse_joint_map(document["joints"])
            sources["joints"] = config_source

    cli_paths = {"bag": args.bag, "urdf": args.urdf}
    for key, raw_path in cli_paths.items():
        if raw_path is not None:
            merged["inputs"][key] = (
                raw_path if raw_path.is_absolute() else invocation_dir / raw_path
            ).resolve()
            sources[f"inputs.{key}"] = "CLI"

    cli_nested = {
        ("topics", "sensor"): args.sensor_topic,
        ("topics", "tf"): args.tf_topic,
        ("topics", "tf_static"): args.tf_static_topic,
        ("source", "position_field"): args.position_field,
        ("source", "velocity_field"): args.velocity_field,
        ("source", "timestamp_field"): args.timestamp_field,
        ("policies", "missing_joint"): args.missing_joint_policy,
        ("policies", "extra_edge"): args.extra_edge_policy,
        ("policies", "fixed_dynamic"): args.fixed_dynamic_policy,
    }
    for (section, key), value in cli_nested.items():
        if value is not None:
            _set_nested(merged, sources, section, key, value, "CLI")
    if args.expected_root is not None:
        merged["expected_root"] = args.expected_root
        sources["expected_root"] = "CLI"
    if args.joint_map is not None:
        merged["joints"] = parse_joint_map(args.joint_map)
        sources["joints"] = "CLI"

    bag = merged["inputs"]["bag"]
    urdf = merged["inputs"]["urdf"]
    if bag is None or urdf is None:
        missing = [name for name, value in (("bag", bag), ("urdf", urdf)) if value is None]
        raise ConfigError("missing required input after config merge: " + ", ".join(missing))

    topics = {
        key: _nonempty_string(value, field=f"topics.{key}")
        for key, value in merged["topics"].items()
    }
    source_values = {
        "position_field": _nonempty_string(
            merged["source"]["position_field"], field="source.position_field"
        ),
        "velocity_field": _nonempty_string(
            merged["source"]["velocity_field"],
            field="source.velocity_field",
            allow_none=True,
        ),
        "timestamp_field": _nonempty_string(
            merged["source"]["timestamp_field"], field="source.timestamp_field"
        ),
    }
    expected_root = _nonempty_string(
        merged["expected_root"], field="expected_root", allow_none=True
    )
    policies: dict[str, str] = {}
    for key, value in merged["policies"].items():
        parsed = _nonempty_string(value, field=f"policies.{key}")
        assert parsed is not None
        allowed = POLICY_VALUES if key == "missing_joint" else NONINTERACTIVE_POLICY_VALUES
        if parsed not in allowed:
            raise ConfigError(
                f"policies.{key} must be one of: {', '.join(sorted(allowed))}"
            )
        policies[key] = parsed

    before_ms = _finite_nonnegative(
        merged["matching"]["before_ms"], field="matching.before_ms"
    )
    after_ms = _finite_nonnegative(
        merged["matching"]["after_ms"], field="matching.after_ms"
    )
    assert before_ms is not None and after_ms is not None
    thresholds: dict[str, float | None] = {}
    for key, value in merged["thresholds"].items():
        thresholds[key] = _finite_nonnegative(
            value,
            field=f"thresholds.{key}",
            allow_none=key in {"continuity_gap_ms", "angular_jump_rad", "linear_jump_m"},
        )
    for kind, unit in (("angular", "rad"), ("linear", "m")):
        if thresholds[f"{kind}_limit_fail_{unit}"] < thresholds[f"{kind}_limit_warn_{unit}"]:
            raise ConfigError(
                f"{kind}_limit_fail_{unit} must be >= {kind}_limit_warn_{unit}"
            )

    json_out = None
    if args.json_out is not None:
        json_out = (
            args.json_out if args.json_out.is_absolute() else invocation_dir / args.json_out
        ).resolve()

    return EffectiveConfig(
        bag=Path(bag),
        urdf=Path(urdf),
        sensor_topic=topics["sensor"],
        tf_topic=topics["tf"],
        tf_static_topic=topics["tf_static"],
        position_field=source_values["position_field"],
        velocity_field=source_values["velocity_field"],
        timestamp_field=source_values["timestamp_field"],
        expected_root=expected_root,
        joint_map=dict(merged["joints"]),
        missing_joint_policy=policies["missing_joint"],
        extra_edge_policy=policies["extra_edge"],
        fixed_dynamic_policy=policies["fixed_dynamic"],
        unsupported_source_policy=policies["unsupported_source"],
        before_ns=round(before_ms * 1_000_000),
        after_ns=round(after_ms * 1_000_000),
        thresholds=thresholds,
        strict=bool(args.strict),
        json_out=json_out,
        loaded_config=loaded_config,
        sources=sources,
    )


def print_effective_config(config: EffectiveConfig, stream: TextIO) -> None:
    stream.write("Effective configuration:\n")
    values = config.reportable()
    for key in (
        "bag",
        "urdf",
        "sensor_topic",
        "tf_topic",
        "tf_static_topic",
        "position_field",
        "velocity_field",
        "timestamp_field",
        "expected_root",
        "missing_joint_policy",
        "extra_edge_policy",
        "fixed_dynamic_policy",
        "unsupported_source_policy",
        "before_ns",
        "after_ns",
        "joint_map",
        "thresholds",
        "strict",
        "json_out",
    ):
        stream.write(f"  {key}: {json.dumps(values[key], ensure_ascii=False, sort_keys=True)}\n")
    stream.write("Value sources:\n")
    for key, value in sorted(config.sources.items()):
        stream.write(f"  {key}: {value}\n")


def main(
    argv: Iterable[str] | None = None,
    *,
    input_stream: TextIO = sys.stdin,
    output_stream: TextIO = sys.stdout,
    error_stream: TextIO = sys.stderr,
) -> int:
    try:
        config = resolve_config(None if argv is None else list(argv))
        print_effective_config(config, output_stream)
        model = read_urdf(config.urdf)
        scan = scan_bag(config, model)
        geometry = analyze_geometry(config, model, scan)
        matching = match_source_states(config, model, scan, geometry)
        outcome = resolve_outcome(
            config,
            model,
            scan,
            geometry,
            matching,
            input_stream=input_stream,
            output_stream=output_stream,
        )
        print_report(outcome, output_stream)
        if config.json_out is not None:
            write_json_report(config.json_out, outcome.document)
        return outcome.exit_code
    except UserAbort as exc:
        print(f"ABORTED: {exc}", file=error_stream)
        return 3
    except ConfigError as exc:
        print(f"ERROR: {exc}", file=error_stream)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
