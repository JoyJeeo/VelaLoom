#!/usr/bin/env python3
"""Create a configurable horizontal synthetic trajectory in a ROS1 bag.

Purpose:
    Replace only dynamic ``odom -> base_link`` horizontal translation with a
    straight minimum-jerk trajectory for visualization.
Input:
    One read-only ROS1 ``.bag`` plus direction, distance, start, and end time.
Output:
    A distinct ROS1 ``.bag`` preserving all data except the target ``x/y``.
Example:
    ``python scripts/loom_xy_motion.py --input input.bag --output output.bag``
    ``--direction robot-up --distance-m 1.0 --start-s 2 --end-s 12 --dry-run``
"""

from __future__ import annotations

import argparse
import hashlib
import math
import os
import sys
import uuid
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence, TextIO


DIRECTION_VECTORS = {
    "robot-up": (1.0, 0.0),
    "robot-down": (-1.0, 0.0),
    "robot-left": (0.0, 1.0),
    "robot-right": (0.0, -1.0),
}


class InteractionCancelled(Exception):
    """Raised when a caller explicitly aborts or closes a required prompt."""


@dataclass(frozen=True)
class MotionPlan:
    """Fully resolved horizontal motion in the odom frame."""

    direction: str
    local_direction: tuple[float, float]
    odom_direction: tuple[float, float]
    distance_m: float
    start_s: float
    end_s: float
    initial_xy: tuple[float, float]

    @property
    def final_xy(self) -> tuple[float, float]:
        return (
            self.initial_xy[0] + self.distance_m * self.odom_direction[0],
            self.initial_xy[1] + self.distance_m * self.odom_direction[1],
        )

    @property
    def theoretical_max_speed(self) -> float:
        return 1.875 * self.distance_m / (self.end_s - self.start_s)


@dataclass(frozen=True)
class BagAnalysis:
    """Read-only facts required to plan and verify one conversion."""

    input_sha256: str
    total_messages: int
    connection_metadata: tuple[tuple[object, ...], ...]
    target_count: int
    target_connection_index: int
    target_callerid: str | None
    first_header_ns: int
    last_header_ns: int
    initial_xy: tuple[float, float]
    initial_quaternion: tuple[float, float, float, float]
    edges: frozenset[tuple[str, str]]
    roots: tuple[str, ...]

    @property
    def duration_seconds(self) -> float:
        return (self.last_header_ns - self.first_header_ns) / 1_000_000_000.0


def parse_time_seconds(raw: str) -> float:
    """Parse decimal seconds, MM:SS, or HH:MM:SS into finite seconds."""

    text = raw.strip()
    if not text:
        raise ValueError("time must not be empty")
    parts = text.split(":")
    if len(parts) == 1:
        try:
            value = float(parts[0])
        except ValueError as exc:
            raise ValueError(f"invalid time: {raw!r}") from exc
    elif len(parts) in (2, 3):
        if any(not part for part in parts):
            raise ValueError(f"invalid time: {raw!r}")
        whole = parts[:-1]
        if any(not part.isdigit() for part in whole):
            raise ValueError(f"invalid time: {raw!r}")
        try:
            seconds = float(parts[-1])
        except ValueError as exc:
            raise ValueError(f"invalid time: {raw!r}") from exc
        if not math.isfinite(seconds) or seconds < 0.0 or seconds >= 60.0:
            raise ValueError(f"seconds component must be in [0, 60): {raw!r}")
        if len(parts) == 2:
            value = int(parts[0]) * 60.0 + seconds
        else:
            minutes = int(parts[1])
            if minutes >= 60:
                raise ValueError(f"minutes component must be in [0, 60): {raw!r}")
            value = int(parts[0]) * 3600.0 + minutes * 60.0 + seconds
    else:
        raise ValueError(f"invalid time: {raw!r}")
    if not math.isfinite(value) or value < 0.0:
        raise ValueError(f"time must be finite and non-negative: {raw!r}")
    return value


def _positive_distance(raw: str) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("distance must be a number") from exc
    if not math.isfinite(value) or value <= 0.0:
        raise argparse.ArgumentTypeError("distance must be finite and positive")
    return value


def _time_argument(raw: str) -> float:
    try:
        return parse_time_seconds(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def direction_in_odom(
    direction: str, quaternion: tuple[float, float, float, float]
) -> tuple[float, float]:
    """Rotate a frozen robot-local direction by the first pose's yaw."""

    if direction not in DIRECTION_VECTORS:
        raise ValueError(f"unsupported direction: {direction!r}")
    if len(quaternion) != 4 or not all(math.isfinite(value) for value in quaternion):
        raise ValueError("initial quaternion must contain four finite values")
    x, y, z, w = quaternion
    norm = math.sqrt(x * x + y * y + z * z + w * w)
    if norm <= 1e-12:
        raise ValueError("initial quaternion must be non-zero")
    x, y, z, w = (value / norm for value in quaternion)
    yaw = math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))
    local_x, local_y = DIRECTION_VECTORS[direction]
    cosine = math.cos(yaw)
    sine = math.sin(yaw)
    odom_x = cosine * local_x - sine * local_y
    odom_y = sine * local_x + cosine * local_y
    magnitude = math.hypot(odom_x, odom_y)
    return odom_x / magnitude, odom_y / magnitude


def minimum_jerk_progress(value: float) -> float:
    """Return clamped quintic minimum-jerk progress in [0, 1]."""

    u = min(1.0, max(0.0, float(value)))
    return 10.0 * u**3 - 15.0 * u**4 + 6.0 * u**5


def trajectory_position(plan: MotionPlan, relative_time_s: float) -> tuple[float, float]:
    """Evaluate the planned horizontal position at a relative target time."""

    u = (relative_time_s - plan.start_s) / (plan.end_s - plan.start_s)
    distance = plan.distance_m * minimum_jerk_progress(u)
    return (
        plan.initial_xy[0] + distance * plan.odom_direction[0],
        plan.initial_xy[1] + distance * plan.odom_direction[1],
    )


def load_rosbags():
    """Load ROS1 bag support lazily for readable dependency failures."""

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


def _is_target(transform) -> bool:
    return (
        str(transform.header.frame_id) == "odom"
        and str(transform.child_frame_id) == "base_link"
    )


def _transform_snapshot(transform) -> tuple[object, ...]:
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


def _header_stamp_ns(transform) -> int:
    seconds = int(transform.header.stamp.sec)
    nanoseconds = int(transform.header.stamp.nanosec)
    if seconds < 0 or not 0 <= nanoseconds < 1_000_000_000:
        raise ValueError(
            "target TF has an invalid header stamp: "
            f"sec={seconds} nanosec={nanoseconds}"
        )
    return seconds * 1_000_000_000 + nanoseconds


def _validated_pose(transform) -> tuple[tuple[float, float, float], tuple[float, ...]]:
    translation = transform.transform.translation
    rotation = transform.transform.rotation
    xyz = (float(translation.x), float(translation.y), float(translation.z))
    quaternion = (
        float(rotation.x),
        float(rotation.y),
        float(rotation.z),
        float(rotation.w),
    )
    parent = str(transform.header.frame_id)
    child = str(transform.child_frame_id)
    if not parent or not child:
        raise ValueError("TF contains an empty parent or child frame")
    if not all(math.isfinite(value) for value in xyz + quaternion):
        raise ValueError(f"TF contains a non-finite pose: {parent}->{child}")
    if math.sqrt(sum(value * value for value in quaternion)) <= 1e-12:
        raise ValueError(f"TF contains a zero quaternion: {parent}->{child}")
    return xyz, quaternion


def _validate_topology(edges: set[tuple[str, str]]) -> tuple[str, ...]:
    if not edges:
        raise ValueError("input bag contains no TF edges")
    parents: dict[str, set[str]] = defaultdict(set)
    children: dict[str, set[str]] = defaultdict(set)
    nodes: set[str] = set()
    for parent, child in edges:
        if parent == child:
            raise ValueError(f"TF graph contains a self-cycle at {child!r}")
        parents[child].add(parent)
        children[parent].add(child)
        nodes.update((parent, child))
    multiple = {child: values for child, values in parents.items() if len(values) > 1}
    if multiple:
        detail = ", ".join(
            f"{child}<-{sorted(values)}" for child, values in sorted(multiple.items())
        )
        raise ValueError(f"TF graph contains children with multiple parents: {detail}")

    colors: dict[str, int] = {}

    def visit(node: str) -> None:
        if colors.get(node) == 1:
            raise ValueError(f"TF graph contains a cycle involving {node!r}")
        if colors.get(node) == 2:
            return
        colors[node] = 1
        for child in children.get(node, ()):
            visit(child)
        colors[node] = 2

    for node in nodes:
        visit(node)
    roots = tuple(sorted(nodes - set(parents)))
    if roots != ("odom",):
        raise ValueError(f"TF graph must have the unique root 'odom', found {roots}")
    return roots


def analyze_bag(input_bag: Path) -> BagAnalysis:
    """Scan target TF ownership, timing, pose, connections, and topology read-only."""

    input_bag = input_bag.resolve()
    if not input_bag.is_file() or input_bag.suffix != ".bag":
        raise ValueError(f"input is not an existing .bag file: {input_bag}")
    input_sha256 = _sha256(input_bag)
    AnyReader, _ = load_rosbags()
    total_messages = 0
    target_count = 0
    target_connection_ids: set[int] = set()
    target_connection_index: int | None = None
    target_callerid: str | None = None
    first_header_ns: int | None = None
    last_header_ns: int | None = None
    initial_xy: tuple[float, float] | None = None
    initial_quaternion: tuple[float, float, float, float] | None = None
    edges: set[tuple[str, str]] = set()

    with AnyReader([input_bag]) as reader:
        connection_metadata = tuple(
            _connection_metadata(connection) for connection in reader.connections
        )
        connection_indexes = {
            connection.id: index for index, connection in enumerate(reader.connections)
        }
        tf_connections = [
            connection
            for connection in reader.connections
            if connection.topic in {"/tf", "/tf_static"}
        ]
        wrong_types = sorted(
            {
                connection.msgtype
                for connection in tf_connections
                if connection.msgtype != "tf2_msgs/msg/TFMessage"
            }
        )
        if wrong_types:
            raise ValueError(f"TF topics must use tf2_msgs/TFMessage, got {wrong_types}")

        for connection, _timestamp, rawdata in reader.messages():
            total_messages += 1
            if connection.topic not in {"/tf", "/tf_static"}:
                continue
            message = reader.deserialize(rawdata, connection.msgtype)
            targets_in_message = 0
            for transform in message.transforms:
                xyz, quaternion = _validated_pose(transform)
                parent = str(transform.header.frame_id)
                child = str(transform.child_frame_id)
                edges.add((parent, child))
                if connection.topic == "/tf_static" and (parent, child) == (
                    "odom",
                    "base_link",
                ):
                    raise ValueError(
                        "odom->base_link must not also be published on /tf_static"
                    )
                if connection.topic != "/tf" or (parent, child) != (
                    "odom",
                    "base_link",
                ):
                    continue
                targets_in_message += 1
                if targets_in_message > 1:
                    raise ValueError(
                        "a /tf message repeats the odom->base_link target transform"
                    )
                header_ns = _header_stamp_ns(transform)
                if last_header_ns is not None and header_ns <= last_header_ns:
                    raise ValueError(
                        "odom->base_link header stamps must be strictly increasing"
                    )
                if first_header_ns is None:
                    first_header_ns = header_ns
                    initial_xy = (xyz[0], xyz[1])
                    initial_quaternion = tuple(quaternion)
                last_header_ns = header_ns
                target_count += 1
                target_connection_ids.add(connection.id)
                target_connection_index = connection_indexes[connection.id]
                target_callerid = getattr(connection.ext, "callerid", None)

    if total_messages == 0:
        raise ValueError("input bag contains no messages")
    if target_count == 0:
        raise ValueError("input bag contains no dynamic odom->base_link transform")
    if len(target_connection_ids) != 1:
        raise ValueError(
            "odom->base_link must be published by exactly one /tf connection, "
            f"found {len(target_connection_ids)}"
        )
    roots = _validate_topology(edges)
    assert first_header_ns is not None
    assert last_header_ns is not None
    assert initial_xy is not None
    assert initial_quaternion is not None
    assert target_connection_index is not None
    return BagAnalysis(
        input_sha256=input_sha256,
        total_messages=total_messages,
        connection_metadata=connection_metadata,
        target_count=target_count,
        target_connection_index=target_connection_index,
        target_callerid=target_callerid,
        first_header_ns=first_header_ns,
        last_header_ns=last_header_ns,
        initial_xy=initial_xy,
        initial_quaternion=initial_quaternion,
        edges=frozenset(edges),
        roots=roots,
    )


def create_motion_plan(
    analysis: BagAnalysis,
    *,
    direction: str,
    distance_m: float,
    start_s: float,
    end_s: float,
) -> MotionPlan:
    """Validate user motion parameters against the scanned target duration."""

    if not math.isfinite(distance_m) or distance_m <= 0.0:
        raise ValueError("distance must be finite and positive")
    if not all(math.isfinite(value) for value in (start_s, end_s)):
        raise ValueError("start and end times must be finite")
    if start_s < 0.0 or start_s >= end_s:
        raise ValueError("motion time range must satisfy 0 <= start < end")
    if end_s > analysis.duration_seconds + 1e-12:
        raise ValueError(
            f"end time {end_s:g}s exceeds target duration "
            f"{analysis.duration_seconds:.9f}s"
        )
    return MotionPlan(
        direction=direction,
        local_direction=DIRECTION_VECTORS[direction],
        odom_direction=direction_in_odom(direction, analysis.initial_quaternion),
        distance_m=distance_m,
        start_s=start_s,
        end_s=end_s,
        initial_xy=analysis.initial_xy,
    )


def _read_prompt(input_stream: TextIO, output_stream: TextIO, prompt: str) -> str:
    print(prompt, end="", file=output_stream, flush=True)
    response = input_stream.readline()
    if response == "":
        raise InteractionCancelled("input closed")
    return response.strip()


def resolve_output_path(
    input_bag: Path,
    requested_output: Path,
    *,
    input_stream: TextIO,
    output_stream: TextIO,
    is_tty: bool,
) -> Path:
    """Resolve an occupied output interactively without creating any path."""

    input_bag = input_bag.resolve()
    candidate = requested_output.expanduser().resolve()
    if candidate.suffix != ".bag":
        raise ValueError(f"output must use the .bag extension: {candidate}")
    while candidate.exists() or candidate == input_bag:
        if not is_tty:
            raise ValueError(
                f"output conflicts in non-TTY mode; specify another --output: {candidate}"
            )
        reason = "input path" if candidate == input_bag else "existing path"
        print(f"Output conflict ({reason}): {candidate}", file=output_stream)
        print("  1) Change output directory (keep file name)", file=output_stream)
        print("  2) Rename output file (keep directory)", file=output_stream)
        print("  3) Abort", file=output_stream)
        choice = _read_prompt(
            input_stream, output_stream, "Choice [1/2/3]: "
        ).lower()
        if choice == "3":
            raise InteractionCancelled("output selection aborted")
        if choice == "1":
            directory = _read_prompt(
                input_stream, output_stream, "New output directory: "
            )
            if not directory:
                print("Directory must not be empty.", file=output_stream)
                continue
            candidate = (Path(directory).expanduser() / candidate.name).resolve()
            continue
        if choice == "2":
            name = _read_prompt(input_stream, output_stream, "New file name: ")
            if (
                not name
                or Path(name).name != name
                or "/" in name
                or "\\" in name
            ):
                print("New name must be a file name without a directory.", file=output_stream)
                continue
            renamed = Path(name)
            if renamed.suffix == "":
                renamed = renamed.with_suffix(".bag")
            if renamed.suffix != ".bag":
                print("New name must use the .bag extension.", file=output_stream)
                continue
            candidate = (candidate.parent / renamed).resolve()
            continue
        print("Please enter 1, 2, or 3.", file=output_stream)
    return candidate


def confirm_writing(
    input_stream: TextIO, output_stream: TextIO, *, is_tty: bool
) -> bool:
    """Require final confirmation; Enter defaults to yes and EOF aborts."""

    del is_tty  # Piped explicit confirmation is allowed when no conflict exists.
    while True:
        answer = _read_prompt(
            input_stream, output_stream, "Proceed [Y/n]: "
        ).lower()
        if answer in {"", "y", "yes"}:
            return True
        if answer in {"n", "no"}:
            raise InteractionCancelled("write cancelled")
        print("Please enter y or n.", file=output_stream)


def print_plan(
    analysis: BagAnalysis,
    plan: MotionPlan,
    input_bag: Path,
    output_bag: Path,
    output_stream: TextIO,
    *,
    dry_run: bool,
) -> None:
    """Print every resolved input, trajectory, and output fact before writing."""

    if dry_run:
        print("DRY RUN", file=output_stream)
    print(f"input={input_bag}", file=output_stream)
    print(f"input SHA-256={analysis.input_sha256}", file=output_stream)
    print(f"output={output_bag}", file=output_stream)
    print(
        f"odom->base_link targets={analysis.target_count} "
        f"duration={analysis.duration_seconds:.9f}s "
        f"callerid={analysis.target_callerid!r}",
        file=output_stream,
    )
    print(f"direction={plan.direction}", file=output_stream)
    print(
        f"local vector={plan.local_direction} odom vector={plan.odom_direction}",
        file=output_stream,
    )
    print(
        f"distance={plan.distance_m:.9g}m start={plan.start_s:.9g}s "
        f"end={plan.end_s:.9g}s",
        file=output_stream,
    )
    print(
        f"initial xy={plan.initial_xy} final xy={plan.final_xy}",
        file=output_stream,
    )
    print(
        f"theoretical max speed={plan.theoretical_max_speed:.9g}m/s",
        file=output_stream,
    )
    pending = []
    current = output_bag.parent
    while not current.exists():
        pending.append(current)
        if current.parent == current:
            break
        current = current.parent
    print(
        "directories to create="
        + (", ".join(str(path) for path in reversed(pending)) if pending else "none"),
        file=output_stream,
    )


def _validate_cli_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    input_bag = args.input.expanduser().resolve()
    output_bag = args.output.expanduser().resolve()
    if not input_bag.is_file() or input_bag.suffix != ".bag":
        raise ValueError(f"input is not an existing .bag file: {input_bag}")
    if output_bag.suffix != ".bag":
        raise ValueError(f"output must use the .bag extension: {output_bag}")
    return input_bag, output_bag


def validate_output(
    input_bag: Path,
    output_bag: Path,
    analysis: BagAnalysis,
    plan: MotionPlan,
) -> dict[str, object]:
    """Reopen source and output together and prove all conversion invariants."""

    AnyReader, _ = load_rosbags()
    message_count = 0
    target_count = 0
    unchanged_raw_records = 0
    output_edges: set[tuple[str, str]] = set()
    projections: list[float] = []
    with AnyReader([input_bag]) as source_reader, AnyReader(
        [output_bag]
    ) as output_reader:
        source_metadata = tuple(
            _connection_metadata(connection) for connection in source_reader.connections
        )
        output_metadata = tuple(
            _connection_metadata(connection) for connection in output_reader.connections
        )
        if source_metadata != analysis.connection_metadata:
            raise ValueError("input connection metadata changed after analysis")
        if output_metadata != source_metadata:
            raise ValueError("output changed connection metadata or connection order")

        source_records = source_reader.messages()
        output_records = output_reader.messages()
        while True:
            try:
                source_record = next(source_records)
            except StopIteration:
                source_record = None
            try:
                output_record = next(output_records)
            except StopIteration:
                output_record = None
            if source_record is None or output_record is None:
                if source_record is not None or output_record is not None:
                    raise ValueError("output message count differs from input")
                break

            source_connection, source_timestamp, source_raw = source_record
            output_connection, output_timestamp, output_raw = output_record
            message_count += 1
            if source_timestamp != output_timestamp:
                raise ValueError(f"output changed record timestamp at message {message_count}")
            if _connection_metadata(source_connection) != _connection_metadata(
                output_connection
            ):
                raise ValueError(f"output changed record connection at message {message_count}")
            if source_connection.topic not in {"/tf", "/tf_static"}:
                if source_raw != output_raw:
                    raise ValueError(
                        f"output changed non-TF bytes at message {message_count}"
                    )
                unchanged_raw_records += 1
                continue

            source_message = source_reader.deserialize(
                source_raw, source_connection.msgtype
            )
            output_message = output_reader.deserialize(
                output_raw, output_connection.msgtype
            )
            if len(source_message.transforms) != len(output_message.transforms):
                raise ValueError(
                    f"output changed transform count at message {message_count}"
                )
            message_has_target = False
            for source_tf, output_tf in zip(
                source_message.transforms, output_message.transforms
            ):
                source_snapshot = _transform_snapshot(source_tf)
                output_snapshot = _transform_snapshot(output_tf)
                source_target = source_connection.topic == "/tf" and _is_target(
                    source_tf
                )
                output_target = output_connection.topic == "/tf" and _is_target(
                    output_tf
                )
                if source_target != output_target:
                    raise ValueError("output changed TF parent/child identity")
                output_edges.add((str(output_tf.header.frame_id), str(output_tf.child_frame_id)))
                if not source_target:
                    if source_snapshot != output_snapshot:
                        raise ValueError(
                            f"output changed a non-target transform at message {message_count}"
                        )
                    continue

                message_has_target = True
                target_count += 1
                if (
                    source_snapshot[:5] != output_snapshot[:5]
                    or source_snapshot[7:] != output_snapshot[7:]
                ):
                    raise ValueError(
                        f"output changed target fields other than x/y at message {message_count}"
                    )
                relative_s = (
                    _header_stamp_ns(source_tf) - analysis.first_header_ns
                ) / 1_000_000_000.0
                expected_x, expected_y = trajectory_position(plan, relative_s)
                actual_x, actual_y = output_snapshot[5], output_snapshot[6]
                x_matches = math.isclose(
                    actual_x, expected_x, rel_tol=0.0, abs_tol=1e-12
                )
                y_matches = math.isclose(
                    actual_y, expected_y, rel_tol=0.0, abs_tol=1e-12
                )
                if not x_matches or not y_matches:
                    raise ValueError(
                        f"output target trajectory mismatch at message {message_count}"
                    )
                dx = actual_x - plan.initial_xy[0]
                dy = actual_y - plan.initial_xy[1]
                projections.append(
                    dx * plan.odom_direction[0] + dy * plan.odom_direction[1]
                )
                lateral = dx * -plan.odom_direction[1] + dy * plan.odom_direction[0]
                if abs(lateral) > 1e-9:
                    raise ValueError("output trajectory lateral error exceeds 1e-9 m")
            if not message_has_target:
                if source_raw != output_raw:
                    raise ValueError(
                        f"output reserialized an unchanged TF record at message {message_count}"
                    )
                unchanged_raw_records += 1

    if message_count != analysis.total_messages:
        raise ValueError(
            f"output message count mismatch: expected {analysis.total_messages}, "
            f"got {message_count}"
        )
    if target_count != analysis.target_count:
        raise ValueError(
            f"output target count mismatch: expected {analysis.target_count}, "
            f"got {target_count}"
        )
    if any(
        right + 1e-12 < left for left, right in zip(projections, projections[1:])
    ):
        raise ValueError("output trajectory is not monotonic in the chosen direction")
    roots = _validate_topology(output_edges)
    return {
        "messages": message_count,
        "target_transforms": target_count,
        "unchanged_raw_records": unchanged_raw_records,
        "roots": roots,
    }


def rewrite_bag(
    input_bag: Path,
    output_bag: Path,
    analysis: BagAnalysis,
    plan: MotionPlan,
) -> dict[str, object]:
    """Stream, verify, and atomically publish a no-overwrite derived bag."""

    input_bag = input_bag.resolve()
    output_bag = output_bag.resolve()
    if input_bag == output_bag:
        raise ValueError("output must differ from the read-only input")
    if output_bag.exists():
        raise ValueError(f"output already exists: {output_bag}")
    if _sha256(input_bag) != analysis.input_sha256:
        raise ValueError("input bag changed after analysis")
    AnyReader, Writer = load_rosbags()
    output_bag.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_bag.with_name(
        f".{output_bag.name}.{uuid.uuid4().hex}.tmp.bag"
    )
    rewritten_targets = 0
    try:
        with AnyReader([input_bag]) as reader, Writer(temporary) as writer:
            connection_map = {
                source.id: _copy_connection(writer, reader, source)
                for source in reader.connections
            }
            for source, timestamp, rawdata in reader.messages():
                output_raw = rawdata
                if source.topic == "/tf":
                    message = reader.deserialize(rawdata, source.msgtype)
                    changed = False
                    for transform in message.transforms:
                        if not _is_target(transform):
                            continue
                        relative_s = (
                            _header_stamp_ns(transform) - analysis.first_header_ns
                        ) / 1_000_000_000.0
                        x, y = trajectory_position(plan, relative_s)
                        transform.transform.translation.x = x
                        transform.transform.translation.y = y
                        rewritten_targets += 1
                        changed = True
                    if changed:
                        output_raw = reader.typestore.serialize_ros1(
                            message, source.msgtype
                        )
                writer.write(connection_map[source.id], timestamp, output_raw)
        if rewritten_targets != analysis.target_count:
            raise ValueError(
                "target count changed between scan and write: "
                f"expected {analysis.target_count}, got {rewritten_targets}"
            )
        verified = validate_output(input_bag, temporary, analysis, plan)
        if _sha256(input_bag) != analysis.input_sha256:
            raise ValueError("input bag changed during conversion")
        try:
            os.link(temporary, output_bag)
        except FileExistsError as exc:
            raise ValueError(
                f"output appeared before publish; refusing to overwrite: {output_bag}"
            ) from exc
        temporary.unlink()
        return {**verified, "input_sha256": analysis.input_sha256}
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Replace only dynamic odom->base_link x/y with a configurable "
            "straight minimum-jerk visualization trajectory."
        )
    )
    parser.add_argument("--input", type=Path, required=True, metavar="BAG")
    parser.add_argument("--output", type=Path, required=True, metavar="BAG")
    parser.add_argument(
        "--direction",
        choices=tuple(DIRECTION_VECTORS),
        required=True,
        help="robot-relative direction frozen using the first target yaw",
    )
    parser.add_argument(
        "--distance-m",
        type=_positive_distance,
        required=True,
        metavar="METERS",
        help="finite positive travel distance in meters",
    )
    parser.add_argument(
        "--start-s",
        type=_time_argument,
        required=True,
        metavar="TIME",
        help="start relative to the first target stamp (seconds, MM:SS, or HH:MM:SS)",
    )
    parser.add_argument(
        "--end-s",
        type=_time_argument,
        required=True,
        metavar="TIME",
        help="end relative to the first target stamp (seconds, MM:SS, or HH:MM:SS)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="scan and print the complete plan without creating files",
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
    error_stream: TextIO | None = None,
    is_tty: bool | None = None,
) -> int:
    """Run complete planning and, after confirmation, the bag conversion."""

    input_stream = sys.stdin if input_stream is None else input_stream
    output_stream = sys.stdout if output_stream is None else output_stream
    error_stream = sys.stderr if error_stream is None else error_stream
    interactive = input_stream.isatty() if is_tty is None else is_tty
    args = parse_args(argv)
    try:
        input_bag, requested_output = _validate_cli_paths(args)
        analysis = analyze_bag(input_bag)
        plan = create_motion_plan(
            analysis,
            direction=args.direction,
            distance_m=args.distance_m,
            start_s=args.start_s,
            end_s=args.end_s,
        )
        output_bag = resolve_output_path(
            input_bag,
            requested_output,
            input_stream=input_stream,
            output_stream=output_stream,
            is_tty=interactive,
        )
        print_plan(
            analysis,
            plan,
            input_bag,
            output_bag,
            output_stream,
            dry_run=args.dry_run,
        )
        if _sha256(input_bag) != analysis.input_sha256:
            raise ValueError("input bag changed during planning")
        if args.dry_run:
            print("Result: dry-run complete; no output created", file=output_stream)
            return 0
        confirm_writing(input_stream, output_stream, is_tty=interactive)
        verified = rewrite_bag(input_bag, output_bag, analysis, plan)
        print(
            f"Result: wrote {output_bag}; messages={verified['messages']} "
            f"target transforms={verified['target_transforms']} "
            f"roots={verified['roots']}",
            file=output_stream,
        )
        print(f"Input SHA-256 unchanged: {analysis.input_sha256}", file=output_stream)
        return 0
    except InteractionCancelled as exc:
        print(f"CANCELLED: {exc}", file=error_stream)
        print("Result: cancelled; no output created", file=error_stream)
        return 3
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=error_stream)
        print("Result: failed; no output created", file=error_stream)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
