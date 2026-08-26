#!/usr/bin/env python3
"""Synchronize ``header.frame_id`` values in one or more ROS1 bags.

The input bag(s) are never modified.  Messages are selected by exact topic
name and written to a new bag with the requested frame id.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable


LOOM_SUFFIX_RE = re.compile(r"_loom_\d{8}_\d{6}$")


def parse_mapping(value: str) -> tuple[str, str]:
    topic, separator, frame_id = value.partition("=")
    if not separator or not topic or not frame_id:
        raise argparse.ArgumentTypeError(
            "mapping must have the form TOPIC=FRAME_ID"
        )
    return topic, frame_id


def collect_bags(inputs: list[Path], recursive: bool, excluded_dir: Path | None = None) -> list[Path]:
    """Expand one or more bag files/directories and remove duplicates."""
    collected: list[Path] = []
    seen: set[Path] = set()
    for input_path in inputs:
        if input_path.is_file():
            if input_path.suffix != ".bag":
                raise ValueError(f"input is not a .bag file: {input_path}")
            candidates = [input_path]
        elif input_path.is_dir():
            pattern = "**/*.bag" if recursive else "*.bag"
            candidates = sorted(path for path in input_path.glob(pattern) if path.is_file())
        else:
            raise ValueError(f"input does not exist: {input_path}")
        for candidate in candidates:
            resolved = candidate.resolve()
            if excluded_dir is not None and resolved.is_relative_to(excluded_dir):
                continue
            if resolved not in seen:
                seen.add(resolved)
                collected.append(resolved)
    return collected


def output_name(source: Path, output_dir: Path, overwrite: bool, reserved: set[Path]) -> Path:
    candidate = output_dir / source.name
    # ``--overwrite`` may replace an existing file, but two inputs in one
    # recursive batch must never be assigned the same output path.
    if overwrite and candidate not in reserved:
        return candidate
    if candidate not in reserved and not candidate.exists():
        return candidate

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = source.stem
    if LOOM_SUFFIX_RE.search(stem):
        stem = LOOM_SUFFIX_RE.sub(f"_loom_{timestamp}", stem)
    else:
        stem = f"{stem}_loom_{timestamp}"
    candidate = output_dir / f"{stem}{source.suffix}"
    index = 1
    while candidate in reserved or candidate.exists():
        candidate = output_dir / f"{stem}_{index}{source.suffix}"
        index += 1
    return candidate


def load_rosbags():
    try:
        from rosbags.highlevel import AnyReader
        from rosbags.rosbag1 import Writer
    except ImportError as exc:  # pragma: no cover - depends on local environment
        raise RuntimeError(
            "rosbags is required; install it in the VelaLoom environment"
        ) from exc
    return AnyReader, Writer


def scan_bag(path: Path, mappings: dict[str, str]) -> tuple[dict[str, int], set[str]]:
    AnyReader, _ = load_rosbags()
    counts = {topic: 0 for topic in mappings}
    seen: set[str] = set()
    with AnyReader([path]) as reader:
        for source, _timestamp, rawdata in reader.messages():
            if source.topic not in mappings:
                continue
            seen.add(source.topic)
            message = reader.deserialize(rawdata, source.msgtype)
            if not hasattr(message, "header") or not hasattr(message.header, "frame_id"):
                raise ValueError(
                    f"topic has no header.frame_id: {source.topic} ({source.msgtype})"
                )
            counts[source.topic] += 1
    return counts, set(mappings) - seen


def rewrite_bag(input_bag: Path, output_bag: Path, mappings: dict[str, str]) -> tuple[dict[str, int], set[str]]:
    AnyReader, Writer = load_rosbags()
    counts = {topic: 0 for topic in mappings}
    seen: set[str] = set()
    temp_bag = output_bag.with_name(output_bag.name + ".tmp")
    if temp_bag.exists():
        temp_bag.unlink()
    try:
        with AnyReader([input_bag]) as reader, Writer(temp_bag) as writer:
            typestore = reader.typestore
            connection_map = {}
            for source in reader.connections:
                ext = source.ext
                connection_map[source.id] = writer.add_connection(
                    source.topic,
                    source.msgtype,
                    typestore=typestore,
                    msgdef=source.msgdef.data,
                    md5sum=source.digest,
                    callerid=getattr(ext, "callerid", None),
                    latching=getattr(ext, "latching", None),
                )
            for source, timestamp, rawdata in reader.messages():
                outdata = rawdata
                if source.topic in mappings:
                    message = reader.deserialize(rawdata, source.msgtype)
                    if not hasattr(message, "header") or not hasattr(message.header, "frame_id"):
                        raise ValueError(
                            f"topic has no header.frame_id: {source.topic} ({source.msgtype})"
                        )
                    message.header.frame_id = mappings[source.topic]
                    outdata = typestore.serialize_ros1(message, source.msgtype)
                    counts[source.topic] += 1
                    seen.add(source.topic)
                writer.write(connection_map[source.id], timestamp, outdata)
        temp_bag.replace(output_bag)
    except Exception:
        if temp_bag.exists():
            temp_bag.unlink()
        raise
    return counts, set(mappings) - seen


def report(path: Path, output: Path, counts: dict[str, int], missing: set[str], dry_run: bool) -> None:
    prefix = "DRY RUN: " if dry_run else ""
    print(f"{prefix}{path} -> {output}")
    for topic, count in counts.items():
        print(f"  {topic} -> {count} messages")
    if missing:
        print(f"  unmatched topics: {', '.join(sorted(missing))}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", type=Path, nargs="+", required=True,
        metavar="PATH", help="one or more ROS1 bags and/or directories",
    )
    parser.add_argument("--output", type=Path, required=True, help="output directory")
    parser.add_argument("--recursive", action="store_true", help="scan input directories recursively")
    parser.add_argument(
        "--map",
        dest="mappings",
        action="append",
        nargs="+",
        type=parse_mapping,
        required=True,
        metavar="TOPIC=FRAME_ID",
        help="exact topic-to-frame mapping; accepts multiple values and is repeatable",
    )
    parser.add_argument("--dry-run", action="store_true", help="scan and report without writing bags")
    parser.add_argument("--overwrite", action="store_true", help="overwrite an existing output file")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    mappings: dict[str, str] = {}
    for topic, frame_id in (
        mapping
        for mapping_group in args.mappings
        for mapping in mapping_group
    ):
        if topic in mappings and mappings[topic] != frame_id:
            parser.error(f"topic mapped to multiple frame_ids: {topic}")
        mappings[topic] = frame_id

    try:
        output_dir = args.output.resolve()
        input_dirs = [path.resolve() for path in args.input if path.is_dir()]
        excluded_dir = output_dir if any(output_dir != input_dir and output_dir.is_relative_to(input_dir) for input_dir in input_dirs) else None
        bags = collect_bags(args.input, args.recursive, excluded_dir)
        if not bags:
            print("No .bag files found.")
            return 0
        if not args.dry_run:
            output_dir.mkdir(parents=True, exist_ok=True)
        reserved: set[Path] = set()
        failures: list[tuple[Path, str]] = []
        print(f"Bags: {len(bags)} | dry-run: {args.dry_run} | overwrite: {args.overwrite}")
        for bag in bags:
            source = bag.resolve()
            output = output_name(source, output_dir, args.overwrite, reserved)
            if output == source:
                raise ValueError(f"output would overwrite input bag: {source}")
            reserved.add(output)
            try:
                if args.dry_run:
                    counts, missing = scan_bag(source, mappings)
                else:
                    counts, missing = rewrite_bag(source, output, mappings)
                report(source, output, counts, missing, args.dry_run)
            except Exception as exc:
                failures.append((source, str(exc)))
                print(f"ERROR: {source}: {exc}", file=sys.stderr)
        if failures:
            print(f"Failed: {len(failures)}", file=sys.stderr)
            return 1
        return 0
    except (RuntimeError, ValueError, OSError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
