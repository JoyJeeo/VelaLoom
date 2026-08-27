#!/usr/bin/env python3
"""Interactively connect a ROS1 bag's TF forest into one normalized tree.

Purpose:
    Inspect only the TF data already present in a ROS1 bag, deduplicate static
    transforms, and let the caller connect detached TF trees explicitly.
Input:
    One read-only ROS1 ``.bag`` containing ``/tf`` and/or ``/tf_static``.
Output:
    A different ROS1 ``.bag`` with original dynamic/non-TF records and one
    normalized latched ``/tf_static`` record.
Example:
    conda run --no-capture-output -n VelaLoom python \
      scripts/unify_rosbag_tf.py --input input.bag --output output.bag
"""

from __future__ import annotations

import argparse
import hashlib
import math
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, TextIO


Edge = tuple[str, str]
SPECIAL_FRAMES = ("map", "odom", "base_link")


@dataclass(frozen=True)
class TransformSpec:
    parent: str
    child: str
    translation: tuple[float, float, float]
    rotation: tuple[float, float, float, float]
    stamp: tuple[int, int] = (0, 0)


@dataclass(frozen=True)
class ForestGraph:
    edge_sources: dict[Edge, str]
    children: dict[str, tuple[str, ...]]
    frames: frozenset[str]
    roots: tuple[str, ...]
    component_frames: dict[str, frozenset[str]]


@dataclass(frozen=True)
class BagAnalysis:
    static_edges: dict[Edge, TransformSpec]
    dynamic_edges: frozenset[Edge]
    graph: ForestGraph
    total_messages: int
    non_tf_messages: int
    tf_messages: int
    static_messages: int
    static_input_transforms: int
    static_duplicates: int
    static_timestamp: int


@dataclass(frozen=True)
class ConnectionPlan:
    target_root: str
    bridges: tuple[TransformSpec, ...]
    final_graph: ForestGraph


class InteractionCancelled(Exception):
    """Raised when the caller aborts or closes an interactive prompt."""


def load_rosbags():
    try:
        from rosbags.highlevel import AnyReader
        from rosbags.rosbag1 import Writer
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "rosbags is required; install it in the VelaLoom environment"
        ) from exc
    return AnyReader, Writer


def _spec_from_message(transform) -> TransformSpec:
    translation = transform.transform.translation
    rotation = transform.transform.rotation
    stamp = transform.header.stamp
    spec = TransformSpec(
        parent=str(transform.header.frame_id),
        child=str(transform.child_frame_id),
        translation=(
            float(translation.x),
            float(translation.y),
            float(translation.z),
        ),
        rotation=(
            float(rotation.x),
            float(rotation.y),
            float(rotation.z),
            float(rotation.w),
        ),
        stamp=(int(stamp.sec), int(stamp.nanosec)),
    )
    if not spec.parent or not spec.child:
        raise ValueError(
            f"TF transform contains an empty frame: {spec.parent!r}->{spec.child!r}"
        )
    if not all(math.isfinite(value) for value in spec.translation + spec.rotation):
        raise ValueError(f"TF transform has a non-finite pose: {spec.parent}->{spec.child}")
    return spec


def _same_pose(first: TransformSpec, second: TransformSpec) -> bool:
    return all(
        math.isclose(left, right, rel_tol=0.0, abs_tol=1e-12)
        for left, right in zip(
            first.translation + first.rotation,
            second.translation + second.rotation,
        )
    )


def _add_static_checked(
    edges: dict[Edge, TransformSpec], spec: TransformSpec
) -> bool:
    key = (spec.parent, spec.child)
    existing = edges.get(key)
    if existing is None:
        edges[key] = spec
        return False
    if not _same_pose(existing, spec):
        raise ValueError(
            f"conflicting static transform for {spec.parent}->{spec.child}"
        )
    return True


def build_graph(
    dynamic_edges: Iterable[Edge],
    static_edges: Mapping[Edge, TransformSpec],
) -> ForestGraph:
    """Validate the combined TF topology and return a stable forest view."""
    dynamic = set(dynamic_edges)
    static = set(static_edges)
    all_edges = dynamic | static
    if not all_edges:
        raise ValueError("bag contains no TF transforms")

    edge_sources = {
        edge: "B" if edge in dynamic and edge in static else "D" if edge in dynamic else "S"
        for edge in sorted(all_edges)
    }
    parents: dict[str, str] = {}
    children_lists: dict[str, list[str]] = {}
    frames: set[str] = set()
    for parent, child in sorted(all_edges):
        if not parent or not child:
            raise ValueError(f"TF transform contains an empty frame: {parent!r}->{child!r}")
        previous = parents.setdefault(child, parent)
        if previous != parent:
            raise ValueError(
                f"TF child has multiple parents: {child} ({previous}, {parent})"
            )
        children_lists.setdefault(parent, []).append(child)
        frames.update((parent, child))

    children = {
        frame: tuple(sorted(children_lists.get(frame, ()))) for frame in sorted(frames)
    }
    state: dict[str, int] = {}

    def visit(frame: str, stack: list[str]) -> None:
        marker = state.get(frame, 0)
        if marker == 1:
            start = stack.index(frame)
            cycle = stack[start:] + [frame]
            raise ValueError("TF graph contains a cycle: " + " -> ".join(cycle))
        if marker == 2:
            return
        state[frame] = 1
        stack.append(frame)
        for child in children[frame]:
            visit(child, stack)
        stack.pop()
        state[frame] = 2

    for frame in sorted(frames):
        visit(frame, [])

    roots = tuple(sorted(frames - set(parents)))
    if not roots:
        raise ValueError("TF graph has a component without a root")

    component_frames: dict[str, frozenset[str]] = {}
    reached: set[str] = set()
    for root in roots:
        component: set[str] = set()
        pending = [root]
        while pending:
            frame = pending.pop()
            if frame in component:
                continue
            component.add(frame)
            pending.extend(reversed(children[frame]))
        component_frames[root] = frozenset(component)
        reached.update(component)
    if reached != frames:
        missing = ", ".join(sorted(frames - reached))
        raise ValueError(f"TF graph has a component without a root: {missing}")

    return ForestGraph(
        edge_sources=edge_sources,
        children=children,
        frames=frozenset(frames),
        roots=roots,
        component_frames=component_frames,
    )


def analyze_bag(path: Path) -> BagAnalysis:
    """Scan a bag without writing and return deduplicated TF topology data."""
    AnyReader, _ = load_rosbags()
    static_edges: dict[Edge, TransformSpec] = {}
    dynamic_edges: set[Edge] = set()
    total_messages = 0
    non_tf_messages = 0
    tf_messages = 0
    static_messages = 0
    static_input_transforms = 0
    static_duplicates = 0
    static_timestamp: int | None = None

    with AnyReader([path]) as reader:
        for connection, timestamp, rawdata in reader.messages():
            total_messages += 1
            if connection.topic == "/tf_static":
                static_messages += 1
                if static_timestamp is None:
                    static_timestamp = timestamp
                message = reader.deserialize(rawdata, connection.msgtype)
                for transform in message.transforms:
                    static_input_transforms += 1
                    static_duplicates += int(
                        _add_static_checked(static_edges, _spec_from_message(transform))
                    )
            elif connection.topic == "/tf":
                tf_messages += 1
                message = reader.deserialize(rawdata, connection.msgtype)
                for transform in message.transforms:
                    spec = _spec_from_message(transform)
                    dynamic_edges.add((spec.parent, spec.child))
            else:
                non_tf_messages += 1

    graph = build_graph(dynamic_edges, static_edges)
    return BagAnalysis(
        static_edges=static_edges,
        dynamic_edges=frozenset(dynamic_edges),
        graph=graph,
        total_messages=total_messages,
        non_tf_messages=non_tf_messages,
        tf_messages=tf_messages,
        static_messages=static_messages,
        static_input_transforms=static_input_transforms,
        static_duplicates=static_duplicates,
        static_timestamp=static_timestamp or 0,
    )


def recommended_root(graph: ForestGraph) -> str | None:
    for special in SPECIAL_FRAMES:
        for root in graph.roots:
            if special in graph.component_frames[root]:
                return root
    return None


def format_subtree(graph: ForestGraph, root: str) -> str:
    lines = [root]

    def append_children(frame: str, depth: int) -> None:
        for child in graph.children[frame]:
            source = graph.edge_sources[(frame, child)]
            lines.append(f"{'  ' * depth}{child} [{source}]")
            append_children(child, depth + 1)

    append_children(root, 1)
    return "\n".join(lines)


def format_forest(graph: ForestGraph, title: str) -> str:
    recommendation = recommended_root(graph)
    lines = [f"=== {title} ==="]
    for index, root in enumerate(graph.roots, start=1):
        component = graph.component_frames[root]
        markers = [frame for frame in SPECIAL_FRAMES if frame in component]
        marker_text = f" markers={','.join(markers)}" if markers else ""
        recommended = " [RECOMMENDED]" if root == recommendation else ""
        lines.append(
            f"Tree {index}: root={root} frames={len(component)}{marker_text}{recommended}"
        )
        lines.append(format_subtree(graph, root))
    return "\n".join(lines)


def _graph_with_bridges(
    graph: ForestGraph, bridges: Iterable[TransformSpec]
) -> ForestGraph:
    dynamic = {
        edge for edge, source in graph.edge_sources.items() if source in {"D", "B"}
    }
    static = {
        edge: TransformSpec(
            edge[0], edge[1], (0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0)
        )
        for edge, source in graph.edge_sources.items()
        if source in {"S", "B"}
    }
    for bridge in bridges:
        static[(bridge.parent, bridge.child)] = bridge
    return build_graph(dynamic, static)


def _read_interactive_line(
    input_stream: TextIO, output_stream: TextIO, prompt: str
) -> str:
    print(prompt, end="", file=output_stream, flush=True)
    line = input_stream.readline()
    if line == "":
        raise InteractionCancelled("input closed")
    return line.strip()


def _choose_target_root(
    graph: ForestGraph, input_stream: TextIO, output_stream: TextIO
) -> str:
    roots = graph.roots
    print("Target roots:", file=output_stream)
    for index, root in enumerate(roots, start=1):
        suffix = " [RECOMMENDED]" if root == recommended_root(graph) else ""
        print(f"  {index}. {root}{suffix}", file=output_stream)
    while True:
        response = _read_interactive_line(
            input_stream,
            output_stream,
            "Select target root by number or frame name (abort to cancel): ",
        )
        if response.lower() == "abort":
            raise InteractionCancelled("aborted by caller")
        if response.isdigit():
            index = int(response)
            if 1 <= index <= len(roots):
                return roots[index - 1]
            print(f"Invalid root number: {response}", file=output_stream)
            continue
        if response in roots:
            return response
        print(f"Unknown root: {response or '<empty>'}", file=output_stream)


def plan_connections(
    graph: ForestGraph, input_stream: TextIO, output_stream: TextIO
) -> ConnectionPlan:
    """Interactively choose a target tree and identity bridges for other roots."""
    if len(graph.roots) == 1:
        target_root = graph.roots[0]
        print(f"Single TF tree detected; target root: {target_root}", file=output_stream)
        return ConnectionPlan(target_root, (), graph)

    if not getattr(input_stream, "isatty", lambda: False)():
        roots = ", ".join(graph.roots)
        raise ValueError(
            f"multiple TF roots require interactive selection, but stdin is not a TTY; roots: {roots}"
        )

    target_root = _choose_target_root(graph, input_stream, output_stream)
    print(f"Selected target root: {target_root}", file=output_stream)
    merged_frames = set(graph.component_frames[target_root])
    bridges: list[TransformSpec] = []

    for detached_root in (root for root in graph.roots if root != target_root):
        detached_frames = graph.component_frames[detached_root]
        print(
            f"Detached tree root={detached_root} frames={len(detached_frames)}",
            file=output_stream,
        )
        print(format_subtree(graph, detached_root), file=output_stream)
        while True:
            response = _read_interactive_line(
                input_stream,
                output_stream,
                f"Parent link for {detached_root} (list/tree/abort): ",
            )
            command = response.lower()
            if command == "abort":
                raise InteractionCancelled("aborted by caller")
            if command == "list":
                print(
                    "Available parent links: " + ", ".join(sorted(merged_frames)),
                    file=output_stream,
                )
                continue
            if command == "tree":
                print(
                    format_forest(
                        _graph_with_bridges(graph, bridges), "Current TF forest"
                    ),
                    file=output_stream,
                )
                continue
            if response in detached_frames:
                print(
                    f"Invalid parent: cannot use {response}: it is inside the detached tree",
                    file=output_stream,
                )
                continue
            if response not in merged_frames:
                print(
                    f"Invalid parent: cannot use {response or '<empty>'}: "
                    "it is not in the current merged target tree",
                    file=output_stream,
                )
                continue
            bridge = TransformSpec(
                response,
                detached_root,
                (0.0, 0.0, 0.0),
                (0.0, 0.0, 0.0, 1.0),
            )
            candidate = _graph_with_bridges(graph, [*bridges, bridge])
            bridges.append(bridge)
            merged_frames.update(detached_frames)
            print(
                f"Selected mount: {response} -> {detached_root} (identity)",
                file=output_stream,
            )
            # Candidate validation above protects against cycles and multiple parents.
            if detached_root not in candidate.frames:  # pragma: no cover - defensive
                raise ValueError(f"failed to merge detached root: {detached_root}")
            break

    final_graph = _graph_with_bridges(graph, bridges)
    if final_graph.roots != (target_root,) or len(final_graph.component_frames) != 1:
        raise ValueError("planned TF graph is not a single connected tree")
    return ConnectionPlan(target_root, tuple(bridges), final_graph)


def print_scan_summary(
    analysis: BagAnalysis, input_bag: Path, output_bag: Path, stream: TextIO
) -> None:
    print(f"Input: {input_bag}", file=stream)
    print(f"Output: {output_bag}", file=stream)
    print(
        f"Messages: total={analysis.total_messages} /tf={analysis.tf_messages} "
        f"/tf_static={analysis.static_messages} non-TF={analysis.non_tf_messages}",
        file=stream,
    )
    print(f"Dynamic unique edges: {len(analysis.dynamic_edges)}", file=stream)
    print(
        "Static transforms: "
        f"input={analysis.static_input_transforms} "
        f"duplicates={analysis.static_duplicates} retained={len(analysis.static_edges)}",
        file=stream,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _make_tf_message(typestore, specs: Iterable[TransformSpec]):
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
        )
    return typestore.types["tf2_msgs/msg/TFMessage"](transforms=transforms)


def _final_static_edges(
    analysis: BagAnalysis, plan: ConnectionPlan
) -> dict[Edge, TransformSpec]:
    result = dict(analysis.static_edges)
    for bridge in plan.bridges:
        _add_static_checked(result, bridge)
    return result


def _connection_metadata(connection) -> tuple[object, ...]:
    ext = connection.ext
    msgdef = getattr(connection.msgdef, "data", connection.msgdef)
    return (
        connection.topic,
        connection.msgtype,
        msgdef,
        connection.digest,
        getattr(ext, "callerid", None),
        getattr(ext, "latching", None),
    )


def _passthrough_records(path: Path) -> list[tuple[object, ...]]:
    AnyReader, _ = load_rosbags()
    records = []
    with AnyReader([path]) as reader:
        for connection, timestamp, rawdata in reader.messages():
            if connection.topic != "/tf_static":
                records.append((_connection_metadata(connection), timestamp, rawdata))
    return records


def verify_output(
    input_bag: Path,
    output_bag: Path,
    expected_static: Mapping[Edge, TransformSpec],
    expected_root: str,
) -> BagAnalysis:
    """Reopen a candidate output and verify topology and passthrough fidelity."""
    AnyReader, _ = load_rosbags()
    output_analysis = analyze_bag(output_bag)
    if output_analysis.static_messages != 1:
        raise ValueError(
            f"output must contain one /tf_static message, found {output_analysis.static_messages}"
        )
    if output_analysis.graph.roots != (expected_root,):
        raise ValueError(
            f"output root mismatch: expected {expected_root}, found {output_analysis.graph.roots}"
        )
    if set(output_analysis.static_edges) != set(expected_static):
        raise ValueError("output static edge set does not match the approved plan")
    for edge, expected in expected_static.items():
        if not _same_pose(expected, output_analysis.static_edges[edge]):
            raise ValueError(f"output static pose mismatch: {edge[0]}->{edge[1]}")
    if _passthrough_records(input_bag) != _passthrough_records(output_bag):
        raise ValueError(
            "output changed dynamic /tf or non-TF bytes, timestamps, order, or connection metadata"
        )
    with AnyReader([output_bag]) as reader:
        static_connections = [
            connection for connection in reader.connections if connection.topic == "/tf_static"
        ]
        if len(static_connections) != 1:
            raise ValueError(
                f"output must contain one /tf_static connection, found {len(static_connections)}"
            )
        if getattr(static_connections[0].ext, "latching", None) != 1:
            raise ValueError("output /tf_static connection is not latched")
    return output_analysis


def rewrite_bag(
    input_bag: Path,
    output_bag: Path,
    analysis: BagAnalysis,
    plan: ConnectionPlan,
    expected_input_hash: str,
) -> BagAnalysis:
    """Write, verify, and atomically install the approved normalized bag."""
    AnyReader, Writer = load_rosbags()
    if _sha256(input_bag) != expected_input_hash:
        raise ValueError("input bag changed after analysis; refusing to write")
    final_static = _final_static_edges(analysis, plan)
    output_bag.parent.mkdir(parents=True, exist_ok=True)
    temp_bag = output_bag.with_name(
        f".{output_bag.stem}.{uuid.uuid4().hex}.tmp.bag"
    )
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
                    msgdef=getattr(source.msgdef, "data", source.msgdef),
                    md5sum=source.digest,
                    callerid=getattr(ext, "callerid", None),
                    latching=getattr(ext, "latching", None),
                )
            static_connection = writer.add_connection(
                "/tf_static",
                "tf2_msgs/msg/TFMessage",
                typestore=reader.typestore,
                latching=1,
            )
            specs = [final_static[edge] for edge in sorted(final_static)]
            normalized = _make_tf_message(reader.typestore, specs)
            normalized_raw = reader.typestore.serialize_ros1(
                normalized, "tf2_msgs/msg/TFMessage"
            )
            static_written = False
            for source, timestamp, rawdata in reader.messages():
                if source.topic == "/tf_static":
                    if not static_written:
                        writer.write(static_connection, timestamp, normalized_raw)
                        static_written = True
                    continue
                if not static_written and analysis.static_messages == 0:
                    writer.write(static_connection, timestamp, normalized_raw)
                    static_written = True
                writer.write(connection_map[source.id], timestamp, rawdata)
            if not static_written:  # Defensive: graph validation normally rejects an empty bag.
                writer.write(static_connection, analysis.static_timestamp, normalized_raw)

        if _sha256(input_bag) != expected_input_hash:
            raise ValueError("input bag changed while output was being written")
        output_analysis = verify_output(
            input_bag, temp_bag, final_static, plan.target_root
        )
        temp_bag.replace(output_bag)
        return output_analysis
    except Exception:
        if temp_bag.exists():
            temp_bag.unlink()
        raise


def confirm_write(input_stream: TextIO, output_stream: TextIO) -> bool:
    while True:
        try:
            response = _read_interactive_line(
                input_stream, output_stream, "Proceed [Y/n]: "
            ).lower()
        except InteractionCancelled:
            return False
        if response in {"", "y", "yes"}:
            return True
        if response in {"n", "no"}:
            return False
        print("Please answer y/yes or n/no.", file=output_stream)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, metavar="BAG")
    parser.add_argument("--output", type=Path, required=True, metavar="BAG")
    parser.add_argument("--overwrite", action="store_true", help="replace an existing output bag")
    parser.add_argument("--dry-run", action="store_true", help="interactively plan and validate without writing")
    return parser


def main(
    argv: Iterable[str] | None = None,
    *,
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
    error_stream: TextIO | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    input_stream = input_stream or sys.stdin
    output_stream = output_stream or sys.stdout
    error_stream = error_stream or sys.stderr
    input_bag = args.input.resolve()
    output_bag = args.output.resolve()
    try:
        if not input_bag.is_file() or input_bag.suffix != ".bag":
            raise ValueError(f"input is not an existing .bag file: {input_bag}")
        if output_bag.suffix != ".bag":
            raise ValueError(f"output must use the .bag extension: {output_bag}")
        if input_bag == output_bag:
            raise ValueError("output must differ from input; the input bag is read-only")
        if output_bag.exists() and not args.overwrite:
            raise ValueError(f"output exists (use --overwrite): {output_bag}")
        input_hash = _sha256(input_bag)
        analysis = analyze_bag(input_bag)
        print_scan_summary(analysis, input_bag, output_bag, output_stream)
        print(format_forest(analysis.graph, "Before repair"), file=output_stream)
        plan = plan_connections(analysis.graph, input_stream, output_stream)
        print(f"Added identity edges: {len(plan.bridges)}", file=output_stream)
        for bridge in plan.bridges:
            print(f"  {bridge.parent} -> {bridge.child} [S]", file=output_stream)
        print(format_forest(plan.final_graph, "After repair"), file=output_stream)
        print(
            f"Topology validation: PASS root={plan.target_root} "
            f"frames={len(plan.final_graph.frames)}",
            file=output_stream,
        )
        if _sha256(input_bag) != input_hash:
            raise ValueError("input bag changed during analysis")
        if args.dry_run:
            print("Result: dry-run complete; no output created", file=output_stream)
            return 0
        if not confirm_write(input_stream, output_stream):
            print("Result: cancelled; no output created", file=output_stream)
            return 0
        output_analysis = rewrite_bag(
            input_bag, output_bag, analysis, plan, input_hash
        )
        print(
            f"Result: wrote {output_bag} with "
            f"{len(output_analysis.static_edges)} static transforms",
            file=output_stream,
        )
        print(f"Input SHA-256 unchanged: {input_hash}", file=output_stream)
        return 0
    except InteractionCancelled as exc:
        print(f"Result: cancelled ({exc}); no output created", file=output_stream)
        return 0
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=error_stream)
        print("Result: failed; no output created", file=error_stream)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
