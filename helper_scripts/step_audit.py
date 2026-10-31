#!/usr/bin/env python3
"""Audit a STEP file for open shells and edge-use errors.

This replaces the manual "open it in SolidCraft and look for interference"
check for the *written* file. It parses the STEP text, walks every
``MANIFOLD_SOLID_BREP`` / ``CLOSED_SHELL`` and verifies that every
``EDGE_CURVE`` is used exactly twice by the shell, once in each direction.

Text mode needs no third-party package. ``--occt`` additionally re-imports the
file with gmsh/OCCT and reports solids and volume.

Exit codes: 0 = audited and closed (and one body, if requested), 1 = open shell
or failed expectation, 2 = could not read/parse the file or OCCT unavailable.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ENTITY_RE = re.compile(
    r"#(\d+)\s*=\s*([A-Z][A-Z0-9_]*)\s*\((.*)\)\s*$", re.DOTALL
)
REF_RE = re.compile(r"#(\d+)")


def parse_entities(text: str) -> dict[int, tuple[str, str]]:
    entities: dict[int, tuple[str, str]] = {}
    for chunk in text.split(";"):
        match = ENTITY_RE.search(chunk)
        if not match:
            continue
        entity_id = int(match.group(1))
        entity_type = match.group(2).upper()
        entities[entity_id] = (entity_type, match.group(3))
    return entities


def _refs(args: str) -> list[int]:
    return [int(value) for value in REF_RE.findall(args)]


def _refs_of_type(
    entities: dict[int, tuple[str, str]],
    args: str,
    wanted: set[str],
) -> list[int]:
    result = []
    for reference in _refs(args):
        entity = entities.get(reference)
        if entity is not None and entity[0] in wanted:
            result.append(reference)
    return result


def audit_step(text: str) -> dict:
    entities = parse_entities(text)
    if not entities:
        raise ValueError("no STEP entities found")

    solids = [
        entity_id
        for entity_id, (kind, _) in entities.items()
        if kind == "MANIFOLD_SOLID_BREP"
    ]
    shells = [
        entity_id
        for entity_id, (kind, _) in entities.items()
        if kind == "CLOSED_SHELL"
    ]

    open_shells: list[dict] = []
    total_faces = 0
    total_edges = 0
    missing_references = 0

    for solid_id in solids:
        kind, args = entities[solid_id]
        shell_refs = _refs_of_type(entities, args, {"CLOSED_SHELL"})
        if not shell_refs:
            open_shells.append({"solid": solid_id, "reason": "no CLOSED_SHELL"})
            continue
        for shell_id in shell_refs:
            shell_type, shell_args = entities[shell_id]
            faces = _refs_of_type(entities, shell_args, {"FACE", "ADVANCED_FACE"})
            face_ids: set[int] = set()
            edge_uses: dict[int, list[str]] = {}
            for face_id in faces:
                face_type, face_args = entities[face_id]
                bounds = _refs_of_type(
                    entities,
                    face_args,
                    {"FACE_OUTER_BOUND", "FACE_BOUND"},
                )
                if not bounds:
                    missing_references += 1
                    continue
                for bound_id in bounds:
                    bound_type, bound_args = entities[bound_id]
                    loops = _refs_of_type(entities, bound_args, {"EDGE_LOOP", "POLY_LOOP"})
                    if not loops:
                        missing_references += 1
                        continue
                    for loop_id in loops:
                        loop_type, loop_args = entities[loop_id]
                        oriented = _refs_of_type(
                            entities, loop_args, {"ORIENTED_EDGE"}
                        )
                        if not oriented:
                            missing_references += 1
                            continue
                        for oriented_id in oriented:
                            oriented_type, oriented_args = entities[oriented_id]
                            match = re.search(
                                r"#(\d+)\s*,\s*\.([TF])\.", oriented_args
                            )
                            if not match:
                                missing_references += 1
                                continue
                            edge_id = int(match.group(1))
                            edge_uses.setdefault(edge_id, []).append(match.group(2))
                            face_ids.add(face_id)

            open_edges = 0
            reason = ""
            for edge_id, directions in edge_uses.items():
                if len(directions) != 2 or sorted(directions) != ["F", "T"]:
                    open_edges += 1
            if len(face_ids) != len(faces):
                open_edges += len(faces) - len(face_ids)
            if open_edges:
                reason = f"{open_edges} edge(s) not shared by exactly two faces"
                open_shells.append(
                    {
                        "solid": solid_id,
                        "shell": shell_id,
                        "faces": len(face_ids),
                        "edges": len(edge_uses),
                        "open_edges": open_edges,
                        "reason": reason,
                    }
                )
            total_faces += len(face_ids)
            total_edges += len(edge_uses)

    return {
        "path": None,
        "solids": len(solids),
        "shells": len(shells),
        "faces": total_faces,
        "edges": total_edges,
        "open_shells": open_shells,
        "open_edges": sum(item.get("open_edges", 0) for item in open_shells),
        "missing_references": missing_references,
        "closed": not open_shells and missing_references == 0,
    }


def occt_report(path: Path) -> dict:
    try:
        import gmsh
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("gmsh is not installed; run with --with gmsh") from exc

    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    gmsh.model.add("gcode_fea_audit")
    try:
        gmsh.open(str(path))
        solids = gmsh.model.getEntities(3)
        volume = sum(
            gmsh.model.occ.getMass(dim, tag) for dim, tag in solids
        )
        bbox = gmsh.model.getBoundingBox(-1, -1)
    finally:
        gmsh.finalize()
    return {
        "solids": len(solids),
        "volume": volume,
        "bbox": list(bbox),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("step", type=Path, help="STEP file to audit")
    parser.add_argument("--occt", action="store_true", help="also import with gmsh/OCCT")
    parser.add_argument("--expect-one-body", action="store_true")
    parser.add_argument("--json", type=Path, help="write the report here")
    args = parser.parse_args(argv)

    try:
        text = args.step.read_text()
        report = audit_step(text)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    report["path"] = str(args.step)

    if args.occt:
        try:
            report["occt"] = occt_report(args.step)
        except Exception as exc:
            print(f"error: OCCT audit failed: {exc}", file=sys.stderr)
            return 2
        if report["occt"]["solids"] != report["solids"]:
            report["closed"] = False
            report["open_shells"].append(
                {
                    "reason": (
                        f"OCCT imported {report['occt']['solids']} solid(s), "
                        f"STEP declares {report['solids']}"
                    )
                }
            )

    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")

    print(
        f"solids: {report['solids']}, shells: {report['shells']}, "
        f"faces: {report['faces']}, edges: {report['edges']}, "
        f"open shells: {len(report['open_shells'])}, "
        f"open edges: {report['open_edges']}, "
        f"missing refs: {report['missing_references']}"
    )
    if "occt" in report:
        print(
            f"occt: {report['occt']['solids']} solid(s), "
            f"volume {report['occt']['volume']:.6f} mm3"
        )
    for shell in report["open_shells"]:
        print(f"  open: {shell}", file=sys.stderr)

    ok = report["closed"]
    if args.expect_one_body and report["solids"] != 1:
        ok = False
        print(
            f"error: expected one body, found {report['solids']}",
            file=sys.stderr,
        )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
