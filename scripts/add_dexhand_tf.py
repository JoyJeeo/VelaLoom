#!/usr/bin/env python3
"""Add feedback-driven dexterous-hand transforms to a new ROS1 bag.

Purpose:
    Convert ``/dexhand/state.position`` feedback into the 20 dynamic finger
    transforms defined by a supplied URDF.
Input:
    A read-only ROS1 ``.bag`` and a read-only URDF containing the expected
    left/right dexterous-hand revolute joints.
Output:
    A distinct ROS1 ``.bag`` whose original records are preserved and whose
    additional ``/tf`` records describe the observed finger posture.
Example:
    ``python scripts/add_dexhand_tf.py --input input.bag --output output.bag --urdf robot.urdf --dry-run``
"""

from __future__ import annotations

import argparse
import hashlib
import math
import os
import sys
import uuid
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence, TextIO


DEFAULT_STATE_TOPIC = "/dexhand/state"
FEEDBACK_TO_JOINT_SUFFIXES = {
    "thumb_aux": ("thumbCMC",),
    "thumb": ("thumbMCP",),
    "index": ("indexMCP", "indexPIP"),
    "middle": ("middleMCP", "middlePIP"),
    "ring": ("ringMCP", "ringPIP"),
    "pinky": ("littleMCP", "littlePIP"),
}
NUMERIC_TOLERANCE = 1e-12


@dataclass(frozen=True)
class UrdfJoint:
    """Validated URDF kinematics for one target revolute joint."""

    name: str
    parent: str
    child: str
    origin_xyz: tuple[float, float, float]
    origin_rpy: tuple[float, float, float]
    axis: tuple[float, float, float]
    lower: float
    upper: float


@dataclass(frozen=True)
class MappedState:
    """One validated feedback sample mapped to all 20 target angles."""

    angles: dict[str, float]
    clipped_low: tuple[str, ...]
    clipped_high: tuple[str, ...]


@dataclass(frozen=True)
class TransformSpec:
    """One parent-to-child transform computed from a URDF joint."""

    parent: str
    child: str
    translation: tuple[float, float, float]
    rotation: tuple[float, float, float, float]


@dataclass(frozen=True)
class ChannelStats:
    """Observed range and clipping counts for one feedback channel."""

    minimum: float
    maximum: float
    clipped_low: int
    clipped_high: int


@dataclass(frozen=True)
class AnalysisResult:
    """Complete read-only validation result for one bag and URDF."""

    input_sha256: str
    urdf_sha256: str
    joints: dict[str, UrdfJoint]
    total_messages: int
    state_messages: int
    bag_start: int
    bag_end: int
    state_names: tuple[str, ...]
    name_layout_counts: dict[tuple[str, ...], int]
    channel_stats: dict[str, ChannelStats]
    timestamp_fallbacks: int
    input_edges: frozenset[tuple[str, str]]
    final_edges: frozenset[tuple[str, str]]
    dynamic_tf_messages: int
    static_tf_messages: int
    dynamic_transforms: int
    static_transforms: int
    original_stream_sha256: str
    original_connection_metadata: tuple[tuple[object, ...], ...]

    @property
    def duration_seconds(self) -> float:
        return max(0, self.bag_end - self.bag_start) / 1_000_000_000

    @property
    def state_frequency(self) -> float:
        if self.duration_seconds == 0.0:
            return 0.0
        return self.state_messages / self.duration_seconds

    @property
    def expected_tf_messages(self) -> int:
        return self.state_messages

    @property
    def expected_transforms(self) -> int:
        return self.state_messages * len(self.joints)

    @property
    def expected_output_messages(self) -> int:
        return self.total_messages + self.state_messages


def required_feedback_names() -> tuple[str, ...]:
    """Return the 12 required JointState feedback names in stable order."""

    return tuple(
        f"{side}_{channel}"
        for side in ("l", "r")
        for channel in FEEDBACK_TO_JOINT_SUFFIXES
    )


def target_joint_names() -> tuple[str, ...]:
    """Return the 20 expected URDF joint names in stable order."""

    return tuple(
        f"{side}_{joint}"
        for side in ("l", "r")
        for joints in FEEDBACK_TO_JOINT_SUFFIXES.values()
        for joint in joints
    )


def _parse_vector(
    raw: str, *, field: str, joint: str
) -> tuple[float, float, float]:
    try:
        values = tuple(float(value) for value in raw.split())
    except ValueError as exc:
        raise ValueError(f"joint {joint!r} has invalid {field}") from exc
    if len(values) != 3 or not all(math.isfinite(value) for value in values):
        raise ValueError(
            f"joint {joint!r} {field} must contain three finite numbers"
        )
    return values


def _required_node_text(node, tag: str, attribute: str, *, joint: str) -> str:
    child = node.find(tag)
    if child is None or not child.attrib.get(attribute):
        raise ValueError(f"joint {joint!r} is missing {tag}.{attribute}")
    return child.attrib[attribute]


def read_hand_joints(path: Path) -> dict[str, UrdfJoint]:
    """Read exactly the 20 expected revolute hand joints from a URDF."""

    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        raise ValueError(f"cannot read URDF {path}: {exc}") from exc

    links = {node.attrib.get("name", "") for node in root.findall("link")}
    expected = set(target_joint_names())
    found: dict[str, UrdfJoint] = {}
    children: set[str] = set()
    for node in root.findall("joint"):
        name = node.attrib.get("name", "")
        if name not in expected:
            continue
        if name in found:
            raise ValueError(f"URDF repeats target joint {name!r}")
        if node.attrib.get("type") != "revolute":
            raise ValueError(f"target joint {name!r} must have type='revolute'")

        parent = _required_node_text(node, "parent", "link", joint=name)
        child = _required_node_text(node, "child", "link", joint=name)
        if parent not in links or child not in links:
            raise ValueError(f"target joint {name!r} references an unknown link")
        if child in children:
            raise ValueError(f"target hand child {child!r} has multiple URDF parents")

        origin = node.find("origin")
        xyz = _parse_vector(
            origin.attrib.get("xyz", "0 0 0") if origin is not None else "0 0 0",
            field="origin xyz",
            joint=name,
        )
        rpy = _parse_vector(
            origin.attrib.get("rpy", "0 0 0") if origin is not None else "0 0 0",
            field="origin rpy",
            joint=name,
        )
        axis = _parse_vector(
            _required_node_text(node, "axis", "xyz", joint=name),
            field="axis",
            joint=name,
        )
        axis_norm = math.sqrt(sum(value * value for value in axis))
        if axis_norm <= NUMERIC_TOLERANCE:
            raise ValueError(f"target joint {name!r} axis must be non-zero")
        axis = tuple(value / axis_norm for value in axis)

        limit = node.find("limit")
        if limit is None or "lower" not in limit.attrib or "upper" not in limit.attrib:
            raise ValueError(f"target joint {name!r} requires lower and upper limits")
        try:
            lower = float(limit.attrib["lower"])
            upper = float(limit.attrib["upper"])
        except ValueError as exc:
            raise ValueError(f"target joint {name!r} has invalid limits") from exc
        if not math.isfinite(lower) or not math.isfinite(upper) or lower > upper:
            raise ValueError(f"target joint {name!r} has invalid limits")

        found[name] = UrdfJoint(
            name=name,
            parent=parent,
            child=child,
            origin_xyz=xyz,
            origin_rpy=rpy,
            axis=axis,
            lower=lower,
            upper=upper,
        )
        children.add(child)

    missing = expected - set(found)
    if missing:
        raise ValueError("URDF is missing target joints: " + ", ".join(sorted(missing)))
    return {name: found[name] for name in target_joint_names()}


def map_feedback(names: Sequence[str], positions: Sequence[float]) -> MappedState:
    """Validate one JointState and map named feedback to 20 URDF angles."""

    if len(names) != len(positions):
        raise ValueError(
            f"JointState name/position length mismatch: {len(names)} != {len(positions)}"
        )
    normalized_names = tuple(str(name) for name in names)
    if len(normalized_names) != len(set(normalized_names)):
        raise ValueError("JointState contains duplicate names")

    values: dict[str, float] = {}
    for name, raw_value in zip(normalized_names, positions):
        value = float(raw_value)
        if not math.isfinite(value):
            raise ValueError(f"JointState position for {name!r} is not finite")
        values[name] = value
    missing = set(required_feedback_names()) - set(values)
    if missing:
        raise ValueError("JointState is missing feedback names: " + ", ".join(sorted(missing)))

    clipped_low = tuple(name for name in required_feedback_names() if values[name] < 0.0)
    clipped_high = tuple(name for name in required_feedback_names() if values[name] > 100.0)
    angles: dict[str, float] = {}
    for side in ("l", "r"):
        for channel, joint_suffixes in FEEDBACK_TO_JOINT_SUFFIXES.items():
            feedback_name = f"{side}_{channel}"
            unit = min(1.0, max(0.0, values[feedback_name] / 100.0))
            for suffix in joint_suffixes:
                angles[f"{side}_{suffix}"] = unit
    return MappedState(
        angles=angles,
        clipped_low=clipped_low,
        clipped_high=clipped_high,
    )


def scale_mapped_state(
    mapped: MappedState, joints: Mapping[str, UrdfJoint]
) -> MappedState:
    """Scale normalized mapped values to each URDF joint's limits."""

    angles = {
        name: joints[name].lower
        + mapped.angles[name] * (joints[name].upper - joints[name].lower)
        for name in target_joint_names()
    }
    return MappedState(
        angles=angles,
        clipped_low=mapped.clipped_low,
        clipped_high=mapped.clipped_high,
    )


def _quaternion_from_rpy(
    roll: float, pitch: float, yaw: float
) -> tuple[float, float, float, float]:
    cr, sr = math.cos(roll / 2.0), math.sin(roll / 2.0)
    cp, sp = math.cos(pitch / 2.0), math.sin(pitch / 2.0)
    cy, sy = math.cos(yaw / 2.0), math.sin(yaw / 2.0)
    return _normalize_quaternion(
        (
            sr * cp * cy - cr * sp * sy,
            cr * sp * cy + sr * cp * sy,
            cr * cp * sy - sr * sp * cy,
            cr * cp * cy + sr * sp * sy,
        )
    )


def _normalize_quaternion(
    value: tuple[float, float, float, float]
) -> tuple[float, float, float, float]:
    norm = math.sqrt(sum(component * component for component in value))
    if not math.isfinite(norm) or norm <= NUMERIC_TOLERANCE:
        raise ValueError("cannot normalize quaternion")
    return tuple(component / norm for component in value)


def _multiply_quaternions(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    ax, ay, az, aw = first
    bx, by, bz, bw = second
    return _normalize_quaternion(
        (
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz,
        )
    )


def transform_for_joint(joint: UrdfJoint, angle: float) -> TransformSpec:
    """Compute ``T_origin * R_axis(angle)`` for one URDF joint."""

    if not math.isfinite(angle):
        raise ValueError(f"joint {joint.name!r} angle is not finite")
    half = angle / 2.0
    sine = math.sin(half)
    axis_rotation = (
        joint.axis[0] * sine,
        joint.axis[1] * sine,
        joint.axis[2] * sine,
        math.cos(half),
    )
    rotation = _multiply_quaternions(
        _quaternion_from_rpy(*joint.origin_rpy), axis_rotation
    )
    return TransformSpec(
        parent=joint.parent,
        child=joint.child,
        translation=joint.origin_xyz,
        rotation=rotation,
    )


def transforms_for_state(
    mapped: MappedState, joints: Mapping[str, UrdfJoint]
) -> tuple[TransformSpec, ...]:
    """Build all 20 transforms in stable target-joint order."""

    return tuple(
        transform_for_joint(joints[name], mapped.angles[name])
        for name in target_joint_names()
    )


def load_rosbags():
    """Import rosbags lazily so path and XML errors remain readable."""

    try:
        from rosbags.highlevel import AnyReader
        from rosbags.rosbag1 import Writer
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "rosbags is required; install it in the VelaLoom environment"
        ) from exc
    return AnyReader, Writer


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _connection_metadata(connection) -> tuple[object, ...]:
    extension = connection.ext
    message_definition = getattr(connection.msgdef, "data", connection.msgdef)
    return (
        connection.topic,
        connection.msgtype,
        message_definition,
        connection.digest,
        getattr(extension, "callerid", None),
        getattr(extension, "latching", None),
    )


def _update_record_digest(
    digest, metadata: tuple[object, ...], timestamp: int, rawdata: bytes
) -> None:
    metadata_bytes = repr(metadata).encode("utf-8")
    digest.update(len(metadata_bytes).to_bytes(8, "little"))
    digest.update(metadata_bytes)
    digest.update(int(timestamp).to_bytes(8, "little", signed=True))
    digest.update(len(rawdata).to_bytes(8, "little"))
    digest.update(rawdata)


def _validate_graph(edges: set[tuple[str, str]], *, label: str) -> None:
    parents: dict[str, set[str]] = defaultdict(set)
    children: dict[str, set[str]] = defaultdict(set)
    nodes: set[str] = set()
    for parent, child in edges:
        if not parent or not child:
            raise ValueError(f"{label} contains an empty TF frame")
        if parent == child:
            raise ValueError(f"{label} contains self-cycle at {child!r}")
        parents[child].add(parent)
        children[parent].add(child)
        nodes.update((parent, child))
    multiple = {child: value for child, value in parents.items() if len(value) > 1}
    if multiple:
        detail = ", ".join(
            f"{child}<-{sorted(value)}" for child, value in sorted(multiple.items())
        )
        raise ValueError(f"{label} contains children with multiple parents: {detail}")

    colors: dict[str, int] = {}

    def visit(node: str) -> None:
        color = colors.get(node, 0)
        if color == 1:
            raise ValueError(f"{label} contains a TF cycle involving {node!r}")
        if color == 2:
            return
        colors[node] = 1
        for child in children.get(node, ()):
            visit(child)
        colors[node] = 2

    for node in nodes:
        visit(node)


def _reachable(edges: set[tuple[str, str]], root: str) -> set[str]:
    children: dict[str, set[str]] = defaultdict(set)
    for parent, child in edges:
        children[parent].add(child)
    result = {root}
    pending = [root]
    while pending:
        parent = pending.pop()
        for child in children.get(parent, ()):
            if child not in result:
                result.add(child)
                pending.append(child)
    return result


def _stamp_is_zero(message) -> bool:
    return int(message.header.stamp.sec) == 0 and int(message.header.stamp.nanosec) == 0


def analyze_inputs(
    input_bag: Path, urdf_path: Path, state_topic: str = DEFAULT_STATE_TOPIC
) -> AnalysisResult:
    """Scan and validate all source state and TF records without writing."""

    joints = read_hand_joints(urdf_path)
    input_sha256 = _sha256(input_bag)
    urdf_sha256 = _sha256(urdf_path)
    AnyReader, _ = load_rosbags()

    total_messages = 0
    state_messages = 0
    bag_start: int | None = None
    bag_end: int | None = None
    name_layout_counts: Counter[tuple[str, ...]] = Counter()
    state_names: set[str] = set()
    minimums = {name: math.inf for name in required_feedback_names()}
    maximums = {name: -math.inf for name in required_feedback_names()}
    clipped_low: Counter[str] = Counter()
    clipped_high: Counter[str] = Counter()
    timestamp_fallbacks = 0
    edges: set[tuple[str, str]] = set()
    dynamic_tf_messages = 0
    static_tf_messages = 0
    dynamic_transforms = 0
    static_transforms = 0
    original_stream_digest = hashlib.sha256()
    original_connection_metadata: tuple[tuple[object, ...], ...]

    with AnyReader([input_bag]) as reader:
        original_connection_metadata = tuple(
            _connection_metadata(connection) for connection in reader.connections
        )
        state_connections = [
            connection for connection in reader.connections if connection.topic == state_topic
        ]
        if not state_connections:
            raise ValueError(f"state topic not found: {state_topic}")
        wrong_types = sorted(
            {connection.msgtype for connection in state_connections}
            - {"sensor_msgs/msg/JointState"}
        )
        if wrong_types:
            raise ValueError(
                f"state topic {state_topic!r} must use sensor_msgs/JointState, got {wrong_types}"
            )

        for connection, timestamp, rawdata in reader.messages():
            _update_record_digest(
                original_stream_digest,
                _connection_metadata(connection),
                timestamp,
                rawdata,
            )
            total_messages += 1
            bag_start = timestamp if bag_start is None else min(bag_start, timestamp)
            bag_end = timestamp if bag_end is None else max(bag_end, timestamp)
            if connection.topic == state_topic:
                state_messages += 1
                message = reader.deserialize(rawdata, connection.msgtype)
                names = tuple(str(name) for name in message.name)
                name_layout_counts[names] += 1
                state_names.update(names)
                normalized = map_feedback(names, message.position)
                scale_mapped_state(normalized, joints)
                _stamp_from_state(message, timestamp)
                if _stamp_is_zero(message):
                    timestamp_fallbacks += 1
                values = dict(zip(names, (float(value) for value in message.position)))
                for name in required_feedback_names():
                    minimums[name] = min(minimums[name], values[name])
                    maximums[name] = max(maximums[name], values[name])
                clipped_low.update(normalized.clipped_low)
                clipped_high.update(normalized.clipped_high)
            elif connection.topic in {"/tf", "/tf_static"}:
                message = reader.deserialize(rawdata, connection.msgtype)
                if connection.topic == "/tf":
                    dynamic_tf_messages += 1
                    dynamic_transforms += len(message.transforms)
                else:
                    static_tf_messages += 1
                    static_transforms += len(message.transforms)
                for transform in message.transforms:
                    edges.add(
                        (str(transform.header.frame_id), str(transform.child_frame_id))
                    )

    if not state_messages:
        raise ValueError(f"state topic contains no messages: {state_topic}")
    if bag_start is None or bag_end is None:
        raise ValueError("input bag contains no messages")

    _validate_graph(edges, label="input TF graph")
    base_reachable = _reachable(edges, "base_link")
    missing_palms = {"l_palm", "r_palm"} - base_reachable
    if missing_palms:
        raise ValueError(
            "hand palms are not reachable from base_link: "
            + ", ".join(sorted(missing_palms))
        )

    parents_by_child: dict[str, set[str]] = defaultdict(set)
    for parent, child in edges:
        parents_by_child[child].add(parent)
    conflicts = {
        joint.child: parents_by_child[joint.child]
        for joint in joints.values()
        if joint.child in parents_by_child
    }
    if conflicts:
        detail = ", ".join(
            f"{child}<-{sorted(parents)}"
            for child, parents in sorted(conflicts.items())
        )
        raise ValueError(f"target hand children already have TF publishers: {detail}")

    added_edges = {(joint.parent, joint.child) for joint in joints.values()}
    final_edges = edges | added_edges
    _validate_graph(final_edges, label="combined TF graph")
    for side in ("l", "r"):
        reachable = _reachable(final_edges, f"{side}_palm")
        missing_children = {
            joint.child
            for joint in joints.values()
            if joint.name.startswith(f"{side}_") and joint.child not in reachable
        }
        if missing_children:
            raise ValueError(
                f"{side}_palm cannot reach target children: "
                + ", ".join(sorted(missing_children))
            )

    channel_stats = {
        name: ChannelStats(
            minimum=minimums[name],
            maximum=maximums[name],
            clipped_low=clipped_low[name],
            clipped_high=clipped_high[name],
        )
        for name in required_feedback_names()
    }
    return AnalysisResult(
        input_sha256=input_sha256,
        urdf_sha256=urdf_sha256,
        joints=joints,
        total_messages=total_messages,
        state_messages=state_messages,
        bag_start=bag_start,
        bag_end=bag_end,
        state_names=tuple(sorted(state_names)),
        name_layout_counts=dict(name_layout_counts),
        channel_stats=channel_stats,
        timestamp_fallbacks=timestamp_fallbacks,
        input_edges=frozenset(edges),
        final_edges=frozenset(final_edges),
        dynamic_tf_messages=dynamic_tf_messages,
        static_tf_messages=static_tf_messages,
        dynamic_transforms=dynamic_transforms,
        static_transforms=static_transforms,
        original_stream_sha256=original_stream_digest.hexdigest(),
        original_connection_metadata=original_connection_metadata,
    )


NEW_TF_CALLER_ID = "/add_dexhand_tf"


def _copy_connection(writer, reader, source):
    extension = source.ext
    return writer.add_connection(
        source.topic,
        source.msgtype,
        typestore=reader.typestore,
        msgdef=source.msgdef.data,
        md5sum=source.digest,
        callerid=getattr(extension, "callerid", None),
        latching=getattr(extension, "latching", None),
    )


def _stamp_from_state(message, record_timestamp: int) -> tuple[int, int]:
    seconds = int(message.header.stamp.sec)
    nanoseconds = int(message.header.stamp.nanosec)
    if seconds == 0 and nanoseconds == 0:
        return divmod(record_timestamp, 1_000_000_000)
    if seconds < 0 or not 0 <= nanoseconds < 1_000_000_000:
        raise ValueError(
            f"JointState header stamp is invalid: sec={seconds} nanosec={nanoseconds}"
        )
    return seconds, nanoseconds


def _make_tf_message(typestore, specs: Sequence[TransformSpec], stamp: tuple[int, int]):
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
                    stamp=Time(sec=stamp[0], nanosec=stamp[1]),
                    frame_id=spec.parent,
                ),
                child_frame_id=spec.child,
                transform=Transform(
                    translation=Vector3(
                        x=spec.translation[0],
                        y=spec.translation[1],
                        z=spec.translation[2],
                    ),
                    rotation=Quaternion(
                        x=spec.rotation[0],
                        y=spec.rotation[1],
                        z=spec.rotation[2],
                        w=spec.rotation[3],
                    ),
                ),
            )
            for spec in specs
        ]
    )


def _serialized_tf_for_state(reader, message, timestamp: int, analysis: AnalysisResult):
    normalized = map_feedback(message.name, message.position)
    scaled = scale_mapped_state(normalized, analysis.joints)
    specs = transforms_for_state(scaled, analysis.joints)
    tf_message = _make_tf_message(
        reader.typestore, specs, _stamp_from_state(message, timestamp)
    )
    return reader.typestore.serialize_ros1(tf_message, "tf2_msgs/msg/TFMessage")


def _is_new_tf_connection(connection) -> bool:
    return (
        connection.topic == "/tf"
        and connection.msgtype == "tf2_msgs/msg/TFMessage"
        and getattr(connection.ext, "callerid", None) == NEW_TF_CALLER_ID
    )


def validate_output(path: Path, analysis: AnalysisResult, state_topic: str) -> dict[str, int]:
    """Reopen an output and verify original-record and new-TF invariants."""

    AnyReader, _ = load_rosbags()
    original_digest = hashlib.sha256()
    expected_new_digest = hashlib.sha256()
    actual_new_digest = hashlib.sha256()
    original_messages = 0
    new_messages = 0
    new_transforms = 0
    expected_parents = {joint.child: joint.parent for joint in analysis.joints.values()}
    original_metadata: list[tuple[object, ...]] = []
    with AnyReader([path]) as reader:
        new_connections = [
            connection for connection in reader.connections if _is_new_tf_connection(connection)
        ]
        if len(new_connections) != 1:
            raise ValueError(
                f"output must contain exactly one generated /tf connection, found {len(new_connections)}"
            )
        if getattr(new_connections[0].ext, "latching", None) not in (None, 0):
            raise ValueError("generated /tf connection must not be latched")
        original_metadata = [
            _connection_metadata(connection)
            for connection in reader.connections
            if not _is_new_tf_connection(connection)
        ]
        if Counter(original_metadata) != Counter(analysis.original_connection_metadata):
            raise ValueError("output changed original connection metadata")

        for connection, timestamp, rawdata in reader.messages():
            if _is_new_tf_connection(connection):
                new_messages += 1
                _update_record_digest(actual_new_digest, (), timestamp, rawdata)
                message = reader.deserialize(rawdata, connection.msgtype)
                if len(message.transforms) != len(expected_parents):
                    raise ValueError(
                        f"generated /tf message has {len(message.transforms)} transforms, "
                        f"expected {len(expected_parents)}"
                    )
                actual_parents: dict[str, str] = {}
                for transform in message.transforms:
                    child = str(transform.child_frame_id)
                    parent = str(transform.header.frame_id)
                    if child in actual_parents:
                        raise ValueError(f"generated /tf repeats child {child!r}")
                    actual_parents[child] = parent
                if actual_parents != expected_parents:
                    raise ValueError("generated /tf parent-child edges do not match the URDF")
                new_transforms += len(message.transforms)
                continue

            original_messages += 1
            _update_record_digest(
                original_digest,
                _connection_metadata(connection),
                timestamp,
                rawdata,
            )
            if connection.topic == state_topic:
                state = reader.deserialize(rawdata, connection.msgtype)
                expected_raw = _serialized_tf_for_state(reader, state, timestamp, analysis)
                _update_record_digest(expected_new_digest, (), timestamp, expected_raw)

    if original_messages != analysis.total_messages:
        raise ValueError(
            f"output original message count mismatch: expected {analysis.total_messages}, "
            f"got {original_messages}"
        )
    if original_digest.hexdigest() != analysis.original_stream_sha256:
        raise ValueError(
            "output changed original bytes, timestamps, order, or connection metadata"
        )
    if new_messages != analysis.expected_tf_messages:
        raise ValueError(
            f"generated /tf message count mismatch: expected {analysis.expected_tf_messages}, "
            f"got {new_messages}"
        )
    if new_transforms != analysis.expected_transforms:
        raise ValueError(
            f"generated transform count mismatch: expected {analysis.expected_transforms}, "
            f"got {new_transforms}"
        )
    if actual_new_digest.digest() != expected_new_digest.digest():
        raise ValueError("generated /tf values or timestamps do not match source feedback")
    return {
        "output_messages": original_messages + new_messages,
        "generated_tf_messages": new_messages,
        "generated_transforms": new_transforms,
    }


def rewrite_bag(
    input_bag: Path,
    output_bag: Path,
    analysis: AnalysisResult,
    state_topic: str = DEFAULT_STATE_TOPIC,
    urdf_path: Path | None = None,
) -> dict[str, int | str]:
    """Write, reopen, validate, and atomically publish the output bag."""

    AnyReader, Writer = load_rosbags()
    if _sha256(input_bag) != analysis.input_sha256:
        raise ValueError("input bag changed after analysis")
    if urdf_path is not None and _sha256(urdf_path) != analysis.urdf_sha256:
        raise ValueError("URDF changed after analysis")
    output_bag.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_bag.with_name(
        f".{output_bag.name}.{uuid.uuid4().hex}.tmp.bag"
    )
    generated_messages = 0
    try:
        with AnyReader([input_bag]) as reader, Writer(temporary) as writer:
            connection_map = {
                source.id: _copy_connection(writer, reader, source)
                for source in reader.connections
            }
            generated_connection = writer.add_connection(
                "/tf",
                "tf2_msgs/msg/TFMessage",
                typestore=reader.typestore,
                callerid=NEW_TF_CALLER_ID,
                latching=0,
            )
            for source, timestamp, rawdata in reader.messages():
                writer.write(connection_map[source.id], timestamp, rawdata)
                if source.topic != state_topic:
                    continue
                message = reader.deserialize(rawdata, source.msgtype)
                generated_raw = _serialized_tf_for_state(
                    reader, message, timestamp, analysis
                )
                writer.write(generated_connection, timestamp, generated_raw)
                generated_messages += 1
        if generated_messages != analysis.expected_tf_messages:
            raise ValueError(
                "state message count changed between scan and write: "
                f"expected {analysis.expected_tf_messages}, got {generated_messages}"
            )
        verified = validate_output(temporary, analysis, state_topic)
        if _sha256(input_bag) != analysis.input_sha256:
            raise ValueError("input bag changed during write")
        if urdf_path is not None and _sha256(urdf_path) != analysis.urdf_sha256:
            raise ValueError("URDF changed during write")
        os.replace(temporary, output_bag)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise
    return {**verified, "input_sha256": analysis.input_sha256}


def print_analysis(
    analysis: AnalysisResult,
    input_bag: Path,
    output_bag: Path,
    state_topic: str,
    output_stream: TextIO,
    *,
    dry_run: bool,
) -> None:
    """Print the complete auditable scan summary."""

    prefix = "DRY RUN: " if dry_run else ""
    print(f"{prefix}input={input_bag}", file=output_stream)
    print(f"output={output_bag}", file=output_stream)
    print(
        f"messages={analysis.total_messages} duration={analysis.duration_seconds:.6f}s",
        file=output_stream,
    )
    print(
        f"state topic={state_topic} messages={analysis.state_messages} "
        f"frequency={analysis.state_frequency:.3f}Hz "
        f"name_layouts={len(analysis.name_layout_counts)}",
        file=output_stream,
    )
    print(f"state names={list(analysis.state_names)}", file=output_stream)
    for name, stats in analysis.channel_stats.items():
        print(
            f"  {name}: min={stats.minimum:g} max={stats.maximum:g} "
            f"clip_low={stats.clipped_low} clip_high={stats.clipped_high}",
            file=output_stream,
        )
    print(f"timestamp fallbacks={analysis.timestamp_fallbacks}", file=output_stream)
    print(f"target joints={len(analysis.joints)}", file=output_stream)
    for name, joint in analysis.joints.items():
        print(f"  {name}: {joint.parent} -> {joint.child}", file=output_stream)
    print(
        f"input TF edges={len(analysis.input_edges)} conflicts=0; "
        f"combined edges={len(analysis.final_edges)} topology=PASS",
        file=output_stream,
    )
    print(
        f"expected additions: TFMessage={analysis.expected_tf_messages} "
        f"TransformStamped={analysis.expected_transforms} "
        f"output messages={analysis.expected_output_messages}",
        file=output_stream,
    )
    clipped = sum(
        stats.clipped_low + stats.clipped_high
        for stats in analysis.channel_stats.values()
    )
    if clipped:
        print(f"WARNING: {clipped} feedback values will be clipped", file=output_stream)


def _validate_cli_paths(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    input_bag = args.input.resolve()
    output_bag = args.output.resolve()
    urdf_path = args.urdf.resolve()
    if not input_bag.is_file() or input_bag.suffix != ".bag":
        raise ValueError(f"input is not an existing .bag file: {input_bag}")
    if not urdf_path.is_file():
        raise ValueError(f"URDF is not an existing file: {urdf_path}")
    if output_bag.suffix != ".bag":
        raise ValueError(f"output must use the .bag extension: {output_bag}")
    if input_bag == output_bag:
        raise ValueError("output must differ from input; the input bag is read-only")
    if output_bag.exists() and not args.overwrite:
        raise ValueError(f"output exists (use --overwrite): {output_bag}")
    if not args.state_topic.startswith("/") or args.state_topic == "/":
        raise ValueError("state topic must be a non-root absolute topic")
    return input_bag, output_bag, urdf_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Add 20 URDF-defined dynamic finger transforms derived from "
            "/dexhand/state feedback to a distinct ROS1 bag."
        )
    )
    parser.add_argument("--input", type=Path, required=True, metavar="BAG")
    parser.add_argument("--output", type=Path, required=True, metavar="BAG")
    parser.add_argument("--urdf", type=Path, required=True, metavar="URDF")
    parser.add_argument(
        "--state-topic",
        default=DEFAULT_STATE_TOPIC,
        metavar="TOPIC",
        help=f"feedback JointState topic (default: {DEFAULT_STATE_TOPIC})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="scan and validate completely without creating an output file",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace an existing output only after successful validation",
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    output_stream: TextIO | None = None,
    error_stream: TextIO | None = None,
) -> int:
    args = parse_args(argv)
    output_stream = sys.stdout if output_stream is None else output_stream
    error_stream = sys.stderr if error_stream is None else error_stream
    try:
        input_bag, output_bag, urdf_path = _validate_cli_paths(args)
        analysis = analyze_inputs(input_bag, urdf_path, args.state_topic)
        print_analysis(
            analysis,
            input_bag,
            output_bag,
            args.state_topic,
            output_stream,
            dry_run=args.dry_run,
        )
        if _sha256(input_bag) != analysis.input_sha256:
            raise ValueError("input bag changed during analysis")
        if _sha256(urdf_path) != analysis.urdf_sha256:
            raise ValueError("URDF changed during analysis")
        if args.dry_run:
            print("Result: dry-run complete; no output created", file=output_stream)
            return 0
        verified = rewrite_bag(
            input_bag,
            output_bag,
            analysis,
            args.state_topic,
            urdf_path,
        )
        print(
            f"Result: wrote {output_bag} with "
            f"{verified['generated_tf_messages']} generated /tf messages and "
            f"{verified['generated_transforms']} transforms",
            file=output_stream,
        )
        print(f"Input SHA-256 unchanged: {analysis.input_sha256}", file=output_stream)
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=error_stream)
        print("Result: failed; no output created", file=error_stream)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
