#!/usr/bin/env python3
"""Interactively add all URDF fixed joints to a new ROS1 bag.

Purpose:
    Compare every direct ``type="fixed"`` URDF joint with ``/tf_static``
    and ``/tf`` in an input ROS1 bag, require an explicit decision for every
    conflict, and write one normalized latched ``/tf_static`` message.
Input:
    A read-only ROS1 ``.bag``, a URDF, and optional audited decisions JSON.
Output:
    A distinct ROS1 ``.bag`` plus an optional decisions JSON.  The input bag
    and URDF are never modified.
Example:
    ``python scripts/add_urdf_tf_static.py --input input.bag --output output.bag --urdf robot.urdf``
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, TextIO


POSE_TOLERANCE = 1e-9
DECISIONS_SCHEMA_VERSION = 1


class UserAbort(RuntimeError):
    """Raised when the caller aborts or input closes before a safe choice."""


@dataclass(frozen=True)
class TransformSpec:
    """A validated parent-to-child rigid transform."""

    parent: str
    child: str
    translation: tuple[float, float, float]
    rotation: tuple[float, float, float, float]
    stamp: tuple[int, int] = (0, 0)


@dataclass(frozen=True)
class StaticCandidate:
    """One unique static pose and the number of input occurrences."""

    spec: TransformSpec
    transform_count: int


@dataclass(frozen=True)
class DynamicCandidate:
    """One dynamic edge and its input occurrence counts."""

    parent: str
    child: str
    transform_count: int
    message_count: int


@dataclass(frozen=True)
class Conflict:
    """All competing bag candidates for one URDF fixed child."""

    child: str
    urdf: TransformSpec
    static_candidates: tuple[StaticCandidate, ...]
    dynamic_candidates: tuple[DynamicCandidate, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class BagScan:
    """Read-only summary of TF content and bag timing."""

    static_by_child: dict[str, tuple[StaticCandidate, ...]]
    dynamic_by_child: dict[str, tuple[DynamicCandidate, ...]]
    dynamic_message_ids_by_child: dict[str, frozenset[int]]
    total_messages: int
    non_tf_messages: int
    static_messages: int
    start_timestamp: int
    first_static_timestamp: int | None
    frames: frozenset[str]


@dataclass(frozen=True)
class AnalysisResult:
    """URDF-to-bag classifications and conflicts."""

    urdf_by_child: dict[str, TransformSpec]
    bag: BagScan
    classifications: dict[str, str]
    conflicts: tuple[Conflict, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class OutputPlan:
    """Fully resolved static output and any explicitly authorized TF deletion."""

    analysis: AnalysisResult
    choices: dict[str, str]
    static_specs: tuple[TransformSpec, ...]
    delete_dynamic_children: frozenset[str]
    covered_fixed: int
    expected_deleted_dynamic_transforms: int
    expected_modified_dynamic_messages: int


def load_rosbags():
    """Import rosbags lazily so parse-only failures remain clear."""

    try:
        from rosbags.highlevel import AnyReader
        from rosbags.rosbag1 import Writer
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "rosbags is required; install it in the VelaLoom environment"
        ) from exc
    return AnyReader, Writer


def _parse_vector(raw: str, *, field: str, joint: str) -> tuple[float, float, float]:
    try:
        values = tuple(float(value) for value in raw.split())
    except ValueError as exc:
        raise ValueError(f"invalid {field} for fixed joint {joint!r}") from exc
    if len(values) != 3 or not all(math.isfinite(value) for value in values):
        raise ValueError(
            f"fixed joint {joint!r} {field} must contain three finite numbers"
        )
    return values


def _quaternion_from_rpy(
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
    if not math.isfinite(norm) or norm <= POSE_TOLERANCE:
        raise ValueError("URDF RPY produced an invalid quaternion")
    return tuple(value / norm for value in quaternion)


def _same_rotation(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> bool:
    """Treat the equivalent q and -q quaternion forms as the same rotation."""

    direct = all(abs(a - b) <= POSE_TOLERANCE for a, b in zip(first, second))
    negated = all(abs(a + b) <= POSE_TOLERANCE for a, b in zip(first, second))
    return direct or negated


def same_pose(first: TransformSpec, second: TransformSpec) -> bool:
    return all(
        abs(a - b) <= POSE_TOLERANCE
        for a, b in zip(first.translation, second.translation)
    ) and _same_rotation(first.rotation, second.rotation)


def read_fixed_joints(path: Path) -> dict[str, TransformSpec]:
    """Read and validate every direct fixed joint, keyed by child frame."""

    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        raise ValueError(f"cannot read URDF {path}: {exc}") from exc

    by_child: dict[str, TransformSpec] = {}
    for joint_node in root.findall("joint"):
        if joint_node.attrib.get("type") != "fixed":
            continue
        joint_name = joint_node.attrib.get("name", "<unnamed>")
        parent_node = joint_node.find("parent")
        child_node = joint_node.find("child")
        parent = "" if parent_node is None else parent_node.attrib.get("link", "").strip()
        child = "" if child_node is None else child_node.attrib.get("link", "").strip()
        if not parent or not child:
            raise ValueError(
                f"fixed joint {joint_name!r} must have non-empty parent and child links"
            )
        origin = joint_node.find("origin")
        if origin is None:
            translation = (0.0, 0.0, 0.0)
            rpy = (0.0, 0.0, 0.0)
        else:
            translation = _parse_vector(
                origin.attrib.get("xyz", "0 0 0"),
                field="xyz",
                joint=joint_name,
            )
            rpy = _parse_vector(
                origin.attrib.get("rpy", "0 0 0"),
                field="rpy",
                joint=joint_name,
            )
        spec = TransformSpec(
            parent=parent,
            child=child,
            translation=translation,
            rotation=_quaternion_from_rpy(*rpy),
        )
        previous = by_child.get(child)
        if previous is not None:
            if previous.parent != parent:
                raise ValueError(
                    f"URDF fixed child {child!r} has multiple parents: "
                    f"{previous.parent!r}, {parent!r}"
                )
            if not same_pose(previous, spec):
                raise ValueError(
                    f"URDF repeats fixed edge {parent}->{child} with different poses"
                )
            continue
        by_child[child] = spec
    return by_child


def _normalized_quaternion(
    values: tuple[float, float, float, float], *, edge: str
) -> tuple[float, float, float, float]:
    if not all(math.isfinite(value) for value in values):
        raise ValueError(f"TF edge {edge} contains a non-finite quaternion")
    norm = math.sqrt(sum(value * value for value in values))
    if norm <= POSE_TOLERANCE:
        raise ValueError(f"TF edge {edge} contains an invalid zero quaternion")
    return tuple(value / norm for value in values)


def _spec_from_message(transform) -> TransformSpec:
    parent = transform.header.frame_id.strip()
    child = transform.child_frame_id.strip()
    if not parent or not child:
        raise ValueError("TF transform has an empty parent or child frame")
    vector = transform.transform.translation
    translation = (float(vector.x), float(vector.y), float(vector.z))
    if not all(math.isfinite(value) for value in translation):
        raise ValueError(f"TF edge {parent}->{child} contains non-finite translation")
    quaternion = transform.transform.rotation
    rotation = _normalized_quaternion(
        (
            float(quaternion.x),
            float(quaternion.y),
            float(quaternion.z),
            float(quaternion.w),
        ),
        edge=f"{parent}->{child}",
    )
    stamp = transform.header.stamp
    return TransformSpec(
        parent,
        child,
        translation,
        rotation,
        (int(stamp.sec), int(stamp.nanosec)),
    )


def scan_bag(path: Path) -> BagScan:
    """Scan bag TF content without creating or modifying any file."""

    AnyReader, _ = load_rosbags()
    static_specs: dict[tuple[str, str], list[list[object]]] = defaultdict(list)
    dynamic_counts: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    dynamic_message_ids_by_child: dict[str, set[int]] = defaultdict(set)
    dynamic_message_index = 0
    total_messages = 0
    non_tf_messages = 0
    static_messages = 0
    start_timestamp: int | None = None
    first_static_timestamp: int | None = None
    frames: set[str] = set()

    with AnyReader([path]) as reader:
        for connection, timestamp, rawdata in reader.messages():
            total_messages += 1
            start_timestamp = timestamp if start_timestamp is None else min(start_timestamp, timestamp)
            if connection.topic not in {"/tf", "/tf_static"}:
                non_tf_messages += 1
                continue
            message = reader.deserialize(rawdata, connection.msgtype)
            if connection.topic == "/tf_static":
                static_messages += 1
                first_static_timestamp = (
                    timestamp
                    if first_static_timestamp is None
                    else min(first_static_timestamp, timestamp)
                )
                for transform in message.transforms:
                    spec = _spec_from_message(transform)
                    frames.update((spec.parent, spec.child))
                    candidates = static_specs[(spec.parent, spec.child)]
                    matching = next(
                        (candidate for candidate in candidates if same_pose(candidate[0], spec)),
                        None,
                    )
                    if matching is None:
                        candidates.append([spec, 1])
                    else:
                        matching[1] += 1
                continue

            message_edges: set[tuple[str, str]] = set()
            message_children: set[str] = set()
            for transform in message.transforms:
                spec = _spec_from_message(transform)
                edge = (spec.parent, spec.child)
                frames.update(edge)
                dynamic_counts[edge][0] += 1
                message_edges.add(edge)
                message_children.add(spec.child)
            for edge in message_edges:
                dynamic_counts[edge][1] += 1
            for child in message_children:
                dynamic_message_ids_by_child[child].add(dynamic_message_index)
            dynamic_message_index += 1

    static_by_child: dict[str, list[StaticCandidate]] = defaultdict(list)
    for (_parent, child), candidates in static_specs.items():
        for spec, count in candidates:
            static_by_child[child].append(StaticCandidate(spec, int(count)))
    dynamic_by_child: dict[str, list[DynamicCandidate]] = defaultdict(list)
    for (parent, child), (transform_count, message_count) in dynamic_counts.items():
        dynamic_by_child[child].append(
            DynamicCandidate(parent, child, transform_count, message_count)
        )

    return BagScan(
        static_by_child={
            child: tuple(
                sorted(candidates, key=lambda candidate: (candidate.spec.parent, candidate.spec.translation, candidate.spec.rotation))
            )
            for child, candidates in static_by_child.items()
        },
        dynamic_by_child={
            child: tuple(sorted(candidates, key=lambda candidate: candidate.parent))
            for child, candidates in dynamic_by_child.items()
        },
        dynamic_message_ids_by_child={
            child: frozenset(message_ids)
            for child, message_ids in dynamic_message_ids_by_child.items()
        },
        total_messages=total_messages,
        non_tf_messages=non_tf_messages,
        static_messages=static_messages,
        start_timestamp=0 if start_timestamp is None else start_timestamp,
        first_static_timestamp=first_static_timestamp,
        frames=frozenset(frames),
    )


def _conflict_reasons(
    urdf: TransformSpec,
    static: tuple[StaticCandidate, ...],
    dynamic: tuple[DynamicCandidate, ...],
) -> tuple[str, ...]:
    reasons: list[str] = []
    parents = {candidate.spec.parent for candidate in static}
    if len(parents) > 1:
        reasons.append("bag_static_multiple_parents")
    same_edge = [candidate for candidate in static if candidate.spec.parent == urdf.parent]
    if same_edge and any(not same_pose(candidate.spec, urdf) for candidate in same_edge):
        reasons.append("pose_mismatch")
    if any(candidate.spec.parent != urdf.parent for candidate in static):
        reasons.append("different_static_parent")
    if dynamic:
        reasons.append("dynamic_child")
    return tuple(reasons)


def _similar_name_warnings(
    urdf_by_child: dict[str, TransformSpec], bag_frames: frozenset[str]
) -> tuple[str, ...]:
    """Report only the known one-edit radar spelling risk without merging it."""

    urdf_frames = {
        frame
        for spec in urdf_by_child.values()
        for frame in (spec.parent, spec.child)
    }
    if "head_rader" in urdf_frames and "head_radar" in bag_frames:
        return (
            "similar frame names remain distinct: URDF 'head_rader' and bag 'head_radar'",
        )
    return ()


def analyze_inputs(
    bag_path: Path, urdf_by_child: dict[str, TransformSpec]
) -> AnalysisResult:
    """Classify each URDF fixed child against read-only bag TF data."""

    bag = scan_bag(bag_path)
    classifications: dict[str, str] = {}
    conflicts: list[Conflict] = []
    for child, urdf in sorted(urdf_by_child.items()):
        static = bag.static_by_child.get(child, ())
        dynamic = bag.dynamic_by_child.get(child, ())
        identical = (
            len(static) == 1
            and static[0].spec.parent == urdf.parent
            and same_pose(static[0].spec, urdf)
            and not dynamic
        )
        if identical:
            classifications[child] = "already_identical"
            continue
        if not static and not dynamic:
            classifications[child] = "missing"
            continue
        classifications[child] = "conflict"
        conflicts.append(
            Conflict(
                child=child,
                urdf=urdf,
                static_candidates=static,
                dynamic_candidates=dynamic,
                reasons=_conflict_reasons(urdf, static, dynamic),
            )
        )
    return AnalysisResult(
        urdf_by_child=dict(urdf_by_child),
        bag=bag,
        classifications=classifications,
        conflicts=tuple(conflicts),
        warnings=_similar_name_warnings(urdf_by_child, bag.frames),
    )


def _spec_payload(spec: TransformSpec) -> dict[str, object]:
    return {
        "parent": spec.parent,
        "child": spec.child,
        "translation": list(spec.translation),
        "rotation": list(spec.rotation),
    }


def _conflict_payload(conflict: Conflict) -> dict[str, object]:
    """Return the complete stable candidate set used for decision validation."""

    return {
        "child": conflict.child,
        "reasons": list(conflict.reasons),
        "urdf": _spec_payload(conflict.urdf),
        "bag_static": [
            {
                "transform": _spec_payload(candidate.spec),
                "transform_count": candidate.transform_count,
            }
            for candidate in conflict.static_candidates
        ],
        "bag_dynamic": [
            {
                "parent": candidate.parent,
                "child": candidate.child,
                "transform_count": candidate.transform_count,
                "message_count": candidate.message_count,
            }
            for candidate in conflict.dynamic_candidates
        ],
    }


def _allowed_choices(conflict: Conflict) -> set[str]:
    if conflict.dynamic_candidates:
        return {"keep_dynamic", "use_urdf"}
    return {"keep_bag", "use_urdf"}


def _bag_static_is_unique(conflict: Conflict) -> bool:
    return len(conflict.static_candidates) == 1


def _read_choice(input_stream: TextIO, prompt: str, output_stream: TextIO) -> str:
    output_stream.write(prompt)
    output_stream.flush()
    raw = input_stream.readline()
    if raw == "":
        raise UserAbort("stdin closed before all conflicts were resolved")
    return raw.strip().lower()


def _render_spec(spec: TransformSpec) -> str:
    return (
        f"{spec.parent} -> {spec.child}; xyz={spec.translation}; "
        f"quaternion={spec.rotation}"
    )


def _print_conflict(conflict: Conflict, output_stream: TextIO) -> None:
    output_stream.write(
        f"\nConflict for child {conflict.child!r}\n"
        f"  reasons: {', '.join(conflict.reasons)}\n"
        f"  URDF: {_render_spec(conflict.urdf)}\n"
    )
    if conflict.static_candidates:
        output_stream.write("  bag /tf_static candidates:\n")
        for candidate in conflict.static_candidates:
            output_stream.write(
                f"    - {_render_spec(candidate.spec)}; "
                f"occurrences={candidate.transform_count}\n"
            )
    if conflict.dynamic_candidates:
        output_stream.write("  bag /tf dynamic candidates:\n")
        for candidate in conflict.dynamic_candidates:
            output_stream.write(
                f"    - {candidate.parent} -> {candidate.child}; "
                f"transforms={candidate.transform_count}; "
                f"messages={candidate.message_count}\n"
            )


def resolve_conflicts(
    analysis: AnalysisResult,
    *,
    input_stream: TextIO,
    output_stream: TextIO,
) -> dict[str, str]:
    """Prompt once per child conflict; conflict choices never have defaults."""

    choices: dict[str, str] = {}
    for conflict in analysis.conflicts:
        _print_conflict(conflict, output_stream)
        while True:
            if conflict.dynamic_candidates:
                raw = _read_choice(
                    input_stream,
                    "Choose [k] keep dynamic and skip URDF / "
                    "[u] delete dynamic and use URDF / [a] abort (no default): ",
                    output_stream,
                )
                aliases = {
                    "k": "keep_dynamic",
                    "keep": "keep_dynamic",
                    "keep_dynamic": "keep_dynamic",
                    "u": "use_urdf",
                    "urdf": "use_urdf",
                    "use_urdf": "use_urdf",
                    "a": "abort",
                    "abort": "abort",
                }
            else:
                raw = _read_choice(
                    input_stream,
                    "Choose [u] use URDF / [b] keep bag / "
                    "[a] abort (no default): ",
                    output_stream,
                )
                aliases = {
                    "u": "use_urdf",
                    "urdf": "use_urdf",
                    "use_urdf": "use_urdf",
                    "b": "keep_bag",
                    "bag": "keep_bag",
                    "keep_bag": "keep_bag",
                    "a": "abort",
                    "abort": "abort",
                }
            choice = aliases.get(raw)
            if choice is None:
                output_stream.write("Invalid choice; this conflict has no default.\n")
                continue
            if choice == "abort":
                raise UserAbort(f"caller aborted at conflict for child {conflict.child!r}")
            if choice == "keep_bag" and not _bag_static_is_unique(conflict):
                output_stream.write(
                    "Cannot keep bag: its static candidates are not unique; "
                    "choose URDF or abort.\n"
                )
                continue
            if choice == "use_urdf" and conflict.dynamic_candidates:
                transform_count = sum(
                    candidate.transform_count
                    for candidate in conflict.dynamic_candidates
                )
                message_count = sum(
                    candidate.message_count for candidate in conflict.dynamic_candidates
                )
                while True:
                    confirmation = _read_choice(
                        input_stream,
                        f"This will delete {transform_count} transforms across up to "
                        f"{message_count} /tf messages. Type YES to confirm: ",
                        output_stream,
                    )
                    if confirmation == "yes":
                        break
                    output_stream.write("Deletion not confirmed; type the full word YES.\n")
            choices[conflict.child] = choice
            break
    return choices


def confirm_write(
    output_path: Path, *, input_stream: TextIO, output_stream: TextIO
) -> bool:
    """Ask the sole defaulted question: Enter means yes for final writing."""

    while True:
        output_stream.write(f"Proceed with writing {output_path}? [Y/n] ")
        output_stream.flush()
        raw = input_stream.readline()
        if raw == "":
            raise UserAbort("stdin closed before final write confirmation")
        answer = raw.strip().lower()
        if answer in {"", "y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        output_stream.write("Please answer y/yes or n/no.\n")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_choice(conflict: Conflict, choice: object) -> str:
    if not isinstance(choice, str) or choice not in _allowed_choices(conflict):
        rendered = ", ".join(sorted(_allowed_choices(conflict)))
        raise ValueError(
            f"invalid decision for child {conflict.child!r}; expected one of: {rendered}"
        )
    if choice == "keep_bag" and not _bag_static_is_unique(conflict):
        raise ValueError(
            f"cannot keep non-unique bag static candidates for child {conflict.child!r}"
        )
    return choice


def _decisions_document(
    analysis: AnalysisResult,
    choices: dict[str, str],
    *,
    bag_path: Path,
    urdf_path: Path,
) -> dict[str, object]:
    expected_children = {conflict.child for conflict in analysis.conflicts}
    if set(choices) != expected_children:
        missing = sorted(expected_children - set(choices))
        extra = sorted(set(choices) - expected_children)
        raise ValueError(
            f"decisions are incomplete or unexpected; missing={missing}, extra={extra}"
        )
    records = []
    for conflict in analysis.conflicts:
        choice = _validate_choice(conflict, choices[conflict.child])
        deleted = (
            sum(candidate.transform_count for candidate in conflict.dynamic_candidates)
            if choice == "use_urdf"
            else 0
        )
        modified_messages = (
            len(analysis.bag.dynamic_message_ids_by_child.get(conflict.child, ()))
            if choice == "use_urdf"
            else 0
        )
        records.append(
            {
                "candidate": _conflict_payload(conflict),
                "choice": choice,
                "dynamic_transforms_to_delete": deleted,
                "dynamic_messages_to_modify": modified_messages,
            }
        )
    classification_counts = {
        kind: sum(value == kind for value in analysis.classifications.values())
        for kind in ("already_identical", "missing", "conflict")
    }
    return {
        "schema_version": DECISIONS_SCHEMA_VERSION,
        "inputs": {
            "bag_sha256": sha256_file(bag_path),
            "urdf_sha256": sha256_file(urdf_path),
        },
        "input_summary": {
            "messages": analysis.bag.total_messages,
            "urdf_fixed_joints": len(analysis.urdf_by_child),
            **classification_counts,
        },
        "conflicts": records,
    }


def save_decisions(
    path: Path,
    analysis: AnalysisResult,
    choices: dict[str, str],
    *,
    bag_path: Path,
    urdf_path: Path,
) -> None:
    """Atomically save an auditable and input-bound decisions document."""

    document = _decisions_document(
        analysis,
        choices,
        bag_path=bag_path,
        urdf_path=urdf_path,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise


def load_decisions(
    path: Path,
    analysis: AnalysisResult,
    *,
    bag_path: Path,
    urdf_path: Path,
) -> dict[str, str]:
    """Load decisions only when hashes and the full conflict set still match."""

    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read decisions JSON {path}: {exc}") from exc
    if document.get("schema_version") != DECISIONS_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported decisions schema version: {document.get('schema_version')!r}"
        )
    inputs = document.get("inputs")
    if not isinstance(inputs, dict) or inputs != {
        "bag_sha256": sha256_file(bag_path),
        "urdf_sha256": sha256_file(urdf_path),
    }:
        raise ValueError("decisions input SHA-256 does not match the current bag and URDF")
    records = document.get("conflicts")
    if not isinstance(records, list) or len(records) != len(analysis.conflicts):
        raise ValueError("decisions conflict candidate set is incomplete or unexpected")
    choices: dict[str, str] = {}
    for conflict, record in zip(analysis.conflicts, records):
        if not isinstance(record, dict) or record.get("candidate") != _conflict_payload(conflict):
            raise ValueError(
                f"decisions conflict candidate set changed for child {conflict.child!r}"
            )
        choice = _validate_choice(conflict, record.get("choice"))
        expected_deleted = (
            sum(candidate.transform_count for candidate in conflict.dynamic_candidates)
            if choice == "use_urdf"
            else 0
        )
        expected_modified_messages = (
            len(analysis.bag.dynamic_message_ids_by_child.get(conflict.child, ()))
            if choice == "use_urdf"
            else 0
        )
        if record.get("dynamic_transforms_to_delete") != expected_deleted:
            raise ValueError(
                f"decisions dynamic deletion count changed for child {conflict.child!r}"
            )
        if record.get("dynamic_messages_to_modify") != expected_modified_messages:
            raise ValueError(
                f"decisions dynamic message count changed for child {conflict.child!r}"
            )
        choices[conflict.child] = choice
    return choices


def build_output_plan(
    analysis: AnalysisResult, choices: dict[str, str]
) -> OutputPlan:
    """Apply explicit decisions and prove the final static/dynamic child set."""

    expected_children = {conflict.child for conflict in analysis.conflicts}
    if set(choices) != expected_children:
        missing = sorted(expected_children - set(choices))
        extra = sorted(set(choices) - expected_children)
        raise ValueError(
            f"decisions are incomplete or unexpected; missing={missing}, extra={extra}"
        )
    conflicts = {conflict.child: conflict for conflict in analysis.conflicts}
    for child, conflict in conflicts.items():
        _validate_choice(conflict, choices[child])

    final_static: dict[str, TransformSpec] = {}
    for child, candidates in analysis.bag.static_by_child.items():
        conflict = conflicts.get(child)
        if conflict is None:
            if len(candidates) != 1:
                raise ValueError(
                    f"bag-only static child {child!r} has unresolved pose/parent conflicts"
                )
            final_static[child] = candidates[0].spec
            continue
        choice = choices[child]
        if choice == "keep_bag":
            if len(candidates) != 1:
                raise ValueError(
                    f"cannot keep non-unique bag static candidates for child {child!r}"
                )
            final_static[child] = candidates[0].spec
        elif choice == "keep_dynamic":
            # Keeping the dynamic authority also removes a competing static child.
            continue

    delete_dynamic_children = frozenset(
        child
        for child, conflict in conflicts.items()
        if conflict.dynamic_candidates and choices[child] == "use_urdf"
    )
    for child, urdf in analysis.urdf_by_child.items():
        classification = analysis.classifications[child]
        if classification == "missing":
            final_static[child] = urdf
        elif classification == "already_identical":
            final_static.setdefault(child, urdf)
        elif choices[child] == "use_urdf":
            final_static[child] = urdf

    for child in analysis.bag.dynamic_by_child:
        if child in final_static and child not in delete_dynamic_children:
            raise ValueError(
                f"final child {child!r} would remain both static and dynamic"
            )
    covered_fixed = sum(
        1
        for child, urdf in analysis.urdf_by_child.items()
        if child in final_static
        and final_static[child].parent == urdf.parent
        and same_pose(final_static[child], urdf)
    )
    expected_deleted = sum(
        candidate.transform_count
        for child in delete_dynamic_children
        for candidate in analysis.bag.dynamic_by_child.get(child, ())
    )
    modified_message_ids: set[int] = set()
    for child in delete_dynamic_children:
        modified_message_ids.update(
            analysis.bag.dynamic_message_ids_by_child.get(child, ())
        )
    return OutputPlan(
        analysis=analysis,
        choices=dict(choices),
        static_specs=tuple(
            sorted(final_static.values(), key=lambda spec: (spec.parent, spec.child))
        ),
        delete_dynamic_children=delete_dynamic_children,
        covered_fixed=covered_fixed,
        expected_deleted_dynamic_transforms=expected_deleted,
        expected_modified_dynamic_messages=len(modified_message_ids),
    )


def _make_tf_message(typestore, specs: tuple[TransformSpec, ...]):
    Header = typestore.types["std_msgs/msg/Header"]
    Time = typestore.types["builtin_interfaces/msg/Time"]
    TransformStamped = typestore.types["geometry_msgs/msg/TransformStamped"]
    Transform = typestore.types["geometry_msgs/msg/Transform"]
    Vector3 = typestore.types["geometry_msgs/msg/Vector3"]
    Quaternion = typestore.types["geometry_msgs/msg/Quaternion"]
    transforms = [
        TransformStamped(
            header=Header(
                seq=0,
                stamp=Time(sec=spec.stamp[0], nanosec=spec.stamp[1]),
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
    return typestore.types["tf2_msgs/msg/TFMessage"](transforms=transforms)


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


def validate_output(path: Path, plan: OutputPlan) -> dict[str, int]:
    """Reopen the temporary output and verify normalized TF invariants."""

    AnyReader, _ = load_rosbags()
    expected = {spec.child: spec for spec in plan.static_specs}
    actual: dict[str, TransformSpec] = {}
    static_message_count = 0
    total_messages = 0
    deleted_children_seen: set[str] = set()
    with AnyReader([path]) as reader:
        static_connections = [
            connection
            for connection in reader.connections
            if connection.topic == "/tf_static"
        ]
        if len(static_connections) != 1 or static_connections[0].ext.latching != 1:
            raise ValueError("output must contain exactly one latched /tf_static connection")
        for connection, _timestamp, rawdata in reader.messages():
            total_messages += 1
            if connection.topic == "/tf_static":
                static_message_count += 1
                message = reader.deserialize(rawdata, connection.msgtype)
                for transform in message.transforms:
                    spec = _spec_from_message(transform)
                    if spec.child in actual:
                        raise ValueError(
                            f"output /tf_static repeats child {spec.child!r}"
                        )
                    actual[spec.child] = spec
            elif connection.topic == "/tf":
                message = reader.deserialize(rawdata, connection.msgtype)
                deleted_children_seen.update(
                    transform.child_frame_id
                    for transform in message.transforms
                    if transform.child_frame_id in plan.delete_dynamic_children
                )
    if static_message_count != 1:
        raise ValueError("output must contain exactly one /tf_static message")
    if set(actual) != set(expected) or any(
        actual[child].parent != expected[child].parent
        or not same_pose(actual[child], expected[child])
        for child in expected
    ):
        raise ValueError("output /tf_static does not match the resolved plan")
    if deleted_children_seen:
        raise ValueError(
            "output still contains dynamic transforms selected for deletion: "
            + ", ".join(sorted(deleted_children_seen))
        )
    expected_messages = (
        plan.analysis.bag.total_messages - plan.analysis.bag.static_messages + 1
    )
    if total_messages != expected_messages:
        raise ValueError(
            f"output message count mismatch: expected {expected_messages}, got {total_messages}"
        )
    return {"output_messages": total_messages, "output_static_transforms": len(actual)}


def rewrite_bag(
    input_bag: Path, output_bag: Path, plan: OutputPlan
) -> dict[str, int | str]:
    """Write, validate, and atomically publish the resolved output bag."""

    AnyReader, Writer = load_rosbags()
    temporary = output_bag.with_name(output_bag.stem + ".tmp" + output_bag.suffix)
    if temporary.exists():
        temporary.unlink()
    static_timestamp = (
        plan.analysis.bag.first_static_timestamp
        if plan.analysis.bag.first_static_timestamp is not None
        else plan.analysis.bag.start_timestamp
    )
    deleted_dynamic_transforms = 0
    modified_dynamic_messages = 0
    try:
        with AnyReader([input_bag]) as reader, Writer(temporary) as writer:
            connection_map = {
                source.id: _copy_connection(writer, reader, source)
                for source in reader.connections
                if source.topic != "/tf_static"
            }
            static_connection = writer.add_connection(
                "/tf_static",
                "tf2_msgs/msg/TFMessage",
                typestore=reader.typestore,
                latching=1,
            )
            normalized = _make_tf_message(reader.typestore, plan.static_specs)
            normalized_raw = reader.typestore.serialize_ros1(
                normalized, "tf2_msgs/msg/TFMessage"
            )
            static_written = False
            for source, timestamp, rawdata in reader.messages():
                if not static_written and timestamp >= static_timestamp:
                    writer.write(static_connection, static_timestamp, normalized_raw)
                    static_written = True
                if source.topic == "/tf_static":
                    continue
                if source.topic == "/tf" and plan.delete_dynamic_children:
                    message = reader.deserialize(rawdata, source.msgtype)
                    kept = [
                        transform
                        for transform in message.transforms
                        if transform.child_frame_id not in plan.delete_dynamic_children
                    ]
                    removed = len(message.transforms) - len(kept)
                    if removed:
                        deleted_dynamic_transforms += removed
                        modified_dynamic_messages += 1
                        message = type(message)(transforms=kept)
                        rawdata = reader.typestore.serialize_ros1(message, source.msgtype)
                writer.write(connection_map[source.id], timestamp, rawdata)
            if not static_written:
                writer.write(static_connection, static_timestamp, normalized_raw)
        if deleted_dynamic_transforms != plan.expected_deleted_dynamic_transforms:
            raise ValueError(
                "dynamic deletion count changed between scan and write: "
                f"expected {plan.expected_deleted_dynamic_transforms}, "
                f"got {deleted_dynamic_transforms}"
            )
        if modified_dynamic_messages != plan.expected_modified_dynamic_messages:
            raise ValueError(
                "dynamic modified-message count changed between scan and write: "
                f"expected {plan.expected_modified_dynamic_messages}, "
                f"got {modified_dynamic_messages}"
            )
        verified = validate_output(temporary, plan)
        os.replace(temporary, output_bag)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise
    return {
        **verified,
        "deleted_dynamic_transforms": deleted_dynamic_transforms,
        "modified_dynamic_messages": modified_dynamic_messages,
        "fixed_coverage": f"{plan.covered_fixed}/{len(plan.analysis.urdf_by_child)}",
    }


def _print_analysis(
    analysis: AnalysisResult, *, output_stream: TextIO, dry_run: bool
) -> None:
    counts = {
        kind: sum(value == kind for value in analysis.classifications.values())
        for kind in ("already_identical", "missing", "conflict")
    }
    prefix = "DRY RUN: " if dry_run else ""
    output_stream.write(
        f"{prefix}input messages={analysis.bag.total_messages}; "
        f"URDF fixed={len(analysis.urdf_by_child)}; "
        f"already_identical={counts['already_identical']}; "
        f"missing={counts['missing']}; conflicts={counts['conflict']}\n"
    )
    for warning in analysis.warnings:
        output_stream.write(f"WARNING: {warning}\n")
    for conflict in analysis.conflicts:
        _print_conflict(conflict, output_stream)


def _print_plan_summary(
    plan: OutputPlan, output_path: Path, *, output_stream: TextIO
) -> None:
    choice_counts = {
        choice: sum(value == choice for value in plan.choices.values())
        for choice in ("use_urdf", "keep_bag", "keep_dynamic")
    }
    output_stream.write(
        "\nResolved write summary\n"
        f"  input messages: {plan.analysis.bag.total_messages}\n"
        f"  URDF fixed joints: {len(plan.analysis.urdf_by_child)}\n"
        f"  normalized static transforms: {len(plan.static_specs)}\n"
        f"  already identical: "
        f"{sum(value == 'already_identical' for value in plan.analysis.classifications.values())}\n"
        f"  missing fixed joints added: "
        f"{sum(value == 'missing' for value in plan.analysis.classifications.values())}\n"
        f"  choices: use_urdf={choice_counts['use_urdf']}, "
        f"keep_bag={choice_counts['keep_bag']}, "
        f"keep_dynamic={choice_counts['keep_dynamic']}\n"
        f"  dynamic transforms to delete: "
        f"{plan.expected_deleted_dynamic_transforms}\n"
        f"  dynamic messages to modify: "
        f"{plan.expected_modified_dynamic_messages}\n"
        f"  final URDF fixed coverage: {plan.covered_fixed}/"
        f"{len(plan.analysis.urdf_by_child)}\n"
        f"  output: {output_path}\n"
    )
    for child, choice in sorted(plan.choices.items()):
        output_stream.write(f"  decision: {child}={choice}\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, metavar="BAG")
    parser.add_argument("--output", required=True, type=Path, metavar="BAG")
    parser.add_argument("--urdf", required=True, type=Path, metavar="URDF")
    parser.add_argument(
        "--dry-run", action="store_true", help="scan and report without prompting or writing"
    )
    parser.add_argument(
        "--overwrite", action="store_true", help="replace existing output artifacts"
    )
    parser.add_argument("--decisions-in", type=Path, metavar="JSON")
    parser.add_argument("--decisions-out", type=Path, metavar="JSON")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="skip only the final write prompt; conflicts still need complete decisions",
    )
    return parser


def _stream_is_tty(stream: TextIO) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, OSError):
        return False


def main(
    argv: Iterable[str] | None = None,
    *,
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
    error_stream: TextIO | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    input_stream = sys.stdin if input_stream is None else input_stream
    output_stream = sys.stdout if output_stream is None else output_stream
    error_stream = sys.stderr if error_stream is None else error_stream
    input_bag = args.input.resolve()
    output_bag = args.output.resolve()
    urdf_path = args.urdf.resolve()
    decisions_in = args.decisions_in.resolve() if args.decisions_in else None
    decisions_out = args.decisions_out.resolve() if args.decisions_out else None
    try:
        if not input_bag.is_file() or input_bag.suffix != ".bag":
            raise ValueError(f"input is not an existing .bag file: {input_bag}")
        if not urdf_path.is_file():
            raise ValueError(f"URDF does not exist: {urdf_path}")
        if output_bag == input_bag:
            raise ValueError("output must differ from input; the input bag is read-only")
        if output_bag.suffix != ".bag":
            raise ValueError(f"output must use the .bag suffix: {output_bag}")
        if output_bag.exists() and not args.overwrite and not args.dry_run:
            raise ValueError(f"output exists (use --overwrite): {output_bag}")
        if decisions_in is not None and not decisions_in.is_file():
            raise ValueError(f"decisions input does not exist: {decisions_in}")
        if decisions_out is not None:
            protected = {input_bag, output_bag, urdf_path}
            if decisions_out in protected:
                raise ValueError("decisions output must differ from bag and URDF paths")
            if decisions_out.exists() and not args.overwrite and not args.dry_run:
                raise ValueError(
                    f"decisions output exists (use --overwrite): {decisions_out}"
                )

        urdf_by_child = read_fixed_joints(urdf_path)
        analysis = analyze_inputs(input_bag, urdf_by_child)
        _print_analysis(analysis, output_stream=output_stream, dry_run=args.dry_run)
        if args.dry_run:
            return 0

        if decisions_in is not None:
            choices = load_decisions(
                decisions_in,
                analysis,
                bag_path=input_bag,
                urdf_path=urdf_path,
            )
        elif analysis.conflicts:
            if not _stream_is_tty(input_stream):
                raise ValueError(
                    "non-interactive input requires a complete --decisions-in file"
                )
            choices = resolve_conflicts(
                analysis,
                input_stream=input_stream,
                output_stream=output_stream,
            )
        else:
            choices = {}

        plan = build_output_plan(analysis, choices)
        _print_plan_summary(plan, output_bag, output_stream=output_stream)
        if decisions_out is not None:
            save_decisions(
                decisions_out,
                analysis,
                choices,
                bag_path=input_bag,
                urdf_path=urdf_path,
            )
            output_stream.write(f"Saved decisions: {decisions_out}\n")
        if not args.yes:
            if not _stream_is_tty(input_stream):
                raise ValueError("non-interactive writing requires --yes")
            if not confirm_write(
                output_bag,
                input_stream=input_stream,
                output_stream=output_stream,
            ):
                output_stream.write("Writing cancelled; no output bag was created.\n")
                return 0

        output_bag.parent.mkdir(parents=True, exist_ok=True)
        result = rewrite_bag(input_bag, output_bag, plan)
        output_stream.write(
            f"Wrote {result['output_messages']} messages with "
            f"{result['output_static_transforms']} static transforms; "
            f"deleted dynamic transforms={result['deleted_dynamic_transforms']}; "
            f"modified dynamic messages={result['modified_dynamic_messages']}; "
            f"fixed coverage={result['fixed_coverage']}\n"
        )
        return 0
    except (OSError, RuntimeError, ValueError, UserAbort) as exc:
        error_stream.write(f"ERROR: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
