#!/usr/bin/env python3
"""Create a single-root ROS1 bag with a normalized static TF tree.

The input bag is copied without changing dynamic ``/tf`` messages or any
non-TF message.  Static transforms are collected, checked for conflicts,
deduplicated, supplemented from the selected URDF fixed joints, and written
as one latched ``/tf_static`` message.  The input is always read-only.
"""

from __future__ import annotations

import argparse
import math
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


LEGACY_HEAD_EDGES = {
    ("zhead_2_link", "head_camera_base"),
    ("head_camera_base", "head_camera_depth"),
}

REQUIRED_URDF_EDGES = (
    ("zhead_2_link", "camera_base"),
    ("zarm_l7_link", "l_camera_link"),
    ("l_camera_link", "l_d405_camera_base"),
    ("l_d405_camera_base", "l_d405_camera"),
    ("zarm_r7_link", "r_camera_link_connect"),
    ("r_camera_link_connect", "r_d405_camera_base"),
    ("r_d405_camera_base", "r_d405_camera"),
)

BRIDGE_EDGES = (
    ("camera_base", "cam_h_link"),
    ("l_d405_camera_base", "cam_l_link"),
    ("r_d405_camera_base", "cam_r_link"),
)


@dataclass(frozen=True)
class TransformSpec:
    parent: str
    child: str
    translation: tuple[float, float, float]
    rotation: tuple[float, float, float, float]
    # The first source timestamp is retained for static message placement.
    stamp: tuple[int, int] = (0, 0)


def load_rosbags():
    try:
        from rosbags.highlevel import AnyReader
        from rosbags.rosbag1 import Writer
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "rosbags is required; install it in the VelaLoom environment"
        ) from exc
    return AnyReader, Writer


def _quaternion_from_rpy(roll: float, pitch: float, yaw: float) -> tuple[float, float, float, float]:
    cr, sr = math.cos(roll / 2.0), math.sin(roll / 2.0)
    cp, sp = math.cos(pitch / 2.0), math.sin(pitch / 2.0)
    cy, sy = math.cos(yaw / 2.0), math.sin(yaw / 2.0)
    return (
        sr * cp * cy - cr * sp * sy,
        cr * sp * cy + sr * cp * sy,
        cr * cp * sy - sr * sp * cy,
        cr * cp * cy + sr * sp * sy,
    )


def read_required_urdf_edges(path: Path) -> dict[tuple[str, str], TransformSpec]:
    """Read only the seven camera installation fixed joints from ``path``."""
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        raise ValueError(f"cannot read URDF: {path}: {exc}") from exc

    by_edge: dict[tuple[str, str], TransformSpec] = {}
    wanted = set(REQUIRED_URDF_EDGES)
    for joint in root.findall("joint"):
        if joint.attrib.get("type") != "fixed":
            continue
        parent_node, child_node = joint.find("parent"), joint.find("child")
        if parent_node is None or child_node is None:
            continue
        edge = (parent_node.attrib.get("link", ""), child_node.attrib.get("link", ""))
        if edge not in wanted:
            continue
        origin = joint.find("origin")
        # xyz is a whitespace-separated attribute, not a scalar attribute.
        if origin is None:
            translation = (0.0, 0.0, 0.0)
            rpy = (0.0, 0.0, 0.0)
        else:
            try:
                translation = tuple(float(v) for v in origin.attrib.get("xyz", "0 0 0").split())
                rpy = tuple(float(v) for v in origin.attrib.get("rpy", "0 0 0").split())
            except ValueError as exc:
                raise ValueError(f"invalid numeric URDF origin for {joint.attrib.get('name')}") from exc
            if len(translation) != 3 or len(rpy) != 3:
                raise ValueError(f"URDF origin must contain three xyz/rpy values for {joint.attrib.get('name')}")
        by_edge[edge] = TransformSpec(edge[0], edge[1], translation, _quaternion_from_rpy(*rpy))

    missing = [edge for edge in REQUIRED_URDF_EDGES if edge not in by_edge]
    if missing:
        formatted = ", ".join(f"{parent}->{child}" for parent, child in missing)
        raise ValueError(f"URDF is missing required fixed joints: {formatted}")
    return by_edge


def _spec_from_message(transform, stamp: tuple[int, int]) -> TransformSpec:
    parent, child = transform.header.frame_id, transform.child_frame_id
    if not parent or not child:
        raise ValueError("TF transform has an empty parent or child frame")
    t, q = transform.transform.translation, transform.transform.rotation
    return TransformSpec(
        parent,
        child,
        (float(t.x), float(t.y), float(t.z)),
        (float(q.x), float(q.y), float(q.z), float(q.w)),
        stamp,
    )


def _same_pose(first: TransformSpec, second: TransformSpec, tolerance: float = 1e-9) -> bool:
    return all(
        abs(a - b) <= tolerance
        for left, right in ((first.translation, second.translation), (first.rotation, second.rotation))
        for a, b in zip(left, right)
    )


def _add_checked(edges: dict[tuple[str, str], TransformSpec], spec: TransformSpec, source: str) -> None:
    existing = edges.get((spec.parent, spec.child))
    if existing is not None:
        if not _same_pose(existing, spec):
            raise ValueError(f"conflicting static transform for {spec.parent}->{spec.child} ({source})")
        return
    other_parent = next((parent for parent, child in edges if child == spec.child), None)
    if other_parent is not None and other_parent != spec.parent:
        raise ValueError(
            f"static child has multiple parents: {spec.child} ({other_parent}, {spec.parent})"
        )
    edges[(spec.parent, spec.child)] = spec


def _check_graph(dynamic: set[tuple[str, str]], static: dict[tuple[str, str], TransformSpec]) -> None:
    edges = set(dynamic) | set(static)
    parents: dict[str, str] = {}
    for parent, child in edges:
        if not parent or not child:
            raise ValueError("TF tree contains an empty frame")
        previous = parents.setdefault(child, parent)
        if previous != parent:
            raise ValueError(f"TF child has multiple parents: {child} ({previous}, {parent})")
    frames = {frame for edge in edges for frame in edge}
    roots = frames - set(parents)
    if roots != {"odom"}:
        rendered = ", ".join(sorted(roots)) or "<none>"
        raise ValueError(f"normalized TF tree must have root {{odom}}, found {{{rendered}}}")


def _top_level_header_frame(message) -> str | None:
    header = getattr(message, "header", None)
    frame_id = getattr(header, "frame_id", None)
    return frame_id or None


def analyze_bag(path: Path, urdf_edges: dict[tuple[str, str], TransformSpec], keep_legacy: bool):
    """Read-only scan returning static edges, dynamic edges, and header references."""
    AnyReader, _ = load_rosbags()
    static: dict[tuple[str, str], TransformSpec] = {}
    dynamic: set[tuple[str, str]] = set()
    legacy_topics: dict[str, set[str]] = {"head_camera_base": set(), "head_camera_depth": set()}
    static_count = 0
    static_timestamp = (0, 0)
    total_messages = 0
    non_tf_messages = 0
    with AnyReader([path]) as reader:
        for connection, timestamp, rawdata in reader.messages():
            total_messages += 1
            if connection.topic == "/tf_static":
                static_count += 1
                if static_timestamp == (0, 0):
                    static_timestamp = (timestamp // 1_000_000_000, timestamp % 1_000_000_000)
                message = reader.deserialize(rawdata, connection.msgtype)
                for transform in message.transforms:
                    stamp_message = transform.header.stamp
                    stamp = (int(stamp_message.sec), int(stamp_message.nanosec))
                    spec = _spec_from_message(transform, stamp)
                    _add_checked(static, spec, "input /tf_static")
                continue
            if connection.topic == "/tf":
                message = reader.deserialize(rawdata, connection.msgtype)
                for transform in message.transforms:
                    spec = _spec_from_message(transform, (0, 0))
                    dynamic.add((spec.parent, spec.child))
                continue
            non_tf_messages += 1
            message = reader.deserialize(rawdata, connection.msgtype)
            frame_id = _top_level_header_frame(message)
            if frame_id in legacy_topics:
                legacy_topics[frame_id].add(connection.topic)

    if not keep_legacy:
        for edge in LEGACY_HEAD_EDGES:
            static.pop(edge, None)
    for edge, spec in urdf_edges.items():
        _add_checked(static, spec, "URDF")
    for parent, child in BRIDGE_EDGES:
        _add_checked(static, TransformSpec(parent, child, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0)), "bridge")

    referenced = {topic for topics in legacy_topics.values() for topic in topics}
    if referenced and not keep_legacy:
        details = "; ".join(f"{frame}: {', '.join(sorted(topics))}" for frame, topics in legacy_topics.items() if topics)
        raise ValueError(
            "bag messages reference legacy head frames; use --keep-legacy-head-chain "
            f"to retain them ({details})"
        )
    _check_graph(dynamic, static)
    return static, dynamic, static_count, static_timestamp, total_messages, non_tf_messages, legacy_topics


def _make_tf_message(typestore, specs: list[TransformSpec]):
    Header = typestore.types["std_msgs/msg/Header"]
    Time = typestore.types["builtin_interfaces/msg/Time"]
    TransformStamped = typestore.types["geometry_msgs/msg/TransformStamped"]
    Transform = typestore.types["geometry_msgs/msg/Transform"]
    Vector3 = typestore.types["geometry_msgs/msg/Vector3"]
    Quaternion = typestore.types["geometry_msgs/msg/Quaternion"]
    transforms = []
    for spec in specs:
        transforms.append(
            TransformStamped(
                header=Header(seq=0, stamp=Time(sec=spec.stamp[0], nanosec=spec.stamp[1]), frame_id=spec.parent),
                child_frame_id=spec.child,
                transform=Transform(
                    translation=Vector3(x=spec.translation[0], y=spec.translation[1], z=spec.translation[2]),
                    rotation=Quaternion(x=spec.rotation[0], y=spec.rotation[1], z=spec.rotation[2], w=spec.rotation[3]),
                ),
            )
        )
    return typestore.types["tf2_msgs/msg/TFMessage"](transforms=transforms)


def rewrite_bag(input_bag: Path, output_bag: Path, urdf: Path, keep_legacy: bool) -> dict[str, int]:
    AnyReader, Writer = load_rosbags()
    urdf_edges = read_required_urdf_edges(urdf)
    # Scan first so failures happen before any output is created.
    static, _dynamic, static_count, static_timestamp, total_messages, non_tf_messages, _ = analyze_bag(
        input_bag, urdf_edges, keep_legacy
    )
    temp_bag = output_bag.with_name(output_bag.name + ".tmp")
    if temp_bag.exists():
        temp_bag.unlink()
    specs = [static[key] for key in sorted(static)]
    written_static = False
    try:
        with AnyReader([input_bag]) as reader, Writer(temp_bag) as writer:
            connection_map = {}
            for source in reader.connections:
                if source.topic == "/tf_static":
                    continue
                ext = source.ext
                connection_map[source.id] = writer.add_connection(
                    source.topic,
                    source.msgtype,
                    typestore=reader.typestore,
                    msgdef=source.msgdef.data,
                    md5sum=source.digest,
                    callerid=getattr(ext, "callerid", None),
                    latching=getattr(ext, "latching", None),
                )
            static_connection = writer.add_connection(
                "/tf_static", "tf2_msgs/msg/TFMessage", typestore=reader.typestore, latching=1
            )
            normalized = _make_tf_message(reader.typestore, specs)
            normalized_raw = reader.typestore.serialize_ros1(normalized, "tf2_msgs/msg/TFMessage")
            for source, timestamp, rawdata in reader.messages():
                if source.topic == "/tf_static":
                    if not written_static:
                        writer.write(static_connection, timestamp, normalized_raw)
                        written_static = True
                    continue
                writer.write(connection_map[source.id], timestamp, rawdata)
            if not written_static:
                writer.write(static_connection, static_timestamp[0] * 1_000_000_000 + static_timestamp[1], normalized_raw)
        temp_bag.replace(output_bag)
    except Exception:
        if temp_bag.exists():
            temp_bag.unlink()
        raise
    return {
        "input_messages": total_messages,
        "non_tf_messages": non_tf_messages,
        "input_static_messages": static_count,
        "output_static_transforms": len(specs),
    }


def build_parser() -> argparse.ArgumentParser:
    default_urdf = Path(__file__).resolve().parents[1] / "urdf_kuavo5/urdf/biped_s300053_foxglove.urdf"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, metavar="BAG")
    parser.add_argument("--output", type=Path, required=True, metavar="BAG")
    parser.add_argument("--urdf", type=Path, default=default_urdf, metavar="URDF")
    parser.add_argument("--overwrite", action="store_true", help="replace an existing output bag")
    parser.add_argument("--dry-run", action="store_true", help="scan and validate without writing a bag")
    parser.add_argument(
        "--keep-legacy-head-chain",
        action="store_true",
        help="retain zhead_2_link -> head_camera_base -> head_camera_depth",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    input_bag, output_bag, urdf = args.input.resolve(), args.output.resolve(), args.urdf.resolve()
    try:
        if not input_bag.is_file() or input_bag.suffix != ".bag":
            raise ValueError(f"input is not an existing .bag file: {input_bag}")
        if not urdf.is_file():
            raise ValueError(f"URDF does not exist: {urdf}")
        if input_bag == output_bag:
            raise ValueError("output must differ from input; the input bag is read-only")
        if output_bag.exists() and not args.overwrite:
            raise ValueError(f"output exists (use --overwrite): {output_bag}")
        urdf_edges = read_required_urdf_edges(urdf)
        analysis = analyze_bag(input_bag, urdf_edges, args.keep_legacy_head_chain)
        static, dynamic, static_count, _timestamp, total_messages, non_tf_messages, legacy = analysis
        print(
            f"{('DRY RUN: ' if args.dry_run else '')}{input_bag} -> {output_bag}\n"
            f"  messages: {total_messages} (non-TF: {non_tf_messages})\n"
            f"  /tf dynamic edges: {len(dynamic)}\n"
            f"  /tf_static input messages: {static_count}; normalized transforms: {len(static)}"
        )
        if any(legacy.values()):
            print("  legacy head references: " + "; ".join(f"{frame}: {', '.join(sorted(topics))}" for frame, topics in legacy.items() if topics))
        if args.dry_run:
            return 0
        output_bag.parent.mkdir(parents=True, exist_ok=True)
        result = rewrite_bag(input_bag, output_bag, urdf, args.keep_legacy_head_chain)
        print(f"  wrote {result['output_static_transforms']} normalized static transforms")
        return 0
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
