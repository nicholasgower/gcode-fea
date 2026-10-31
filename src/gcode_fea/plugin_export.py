"""Headless export worker used by the OrcaSlicer plugin.

The plugin adapter must stay thin: it captures a cache in the slicing pipeline,
then this module turns the cache into sidecar/report/STL/STEP. Everything here
runs without OrcaSlicer, so the plugin flow is testable from the CLI.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from .adhesion import AdhesionModel, apply_bond_quality
from .cache import load_cache
from .config import PluginConfig
from .contacts import contact_stats, detect_contacts
from .export import export_step, write_stl_model
from .interfaces import extract_interfaces, interface_report
from .process import attach_process, parse_gcode
from .report import write_report, write_sidecar
from .sweep import mesh_stats

Progress = Callable[[float, str], None]


def export_from_cache(
    cache_path: str | Path,
    out_dir: str | Path,
    config: PluginConfig | None = None,
    progress: Progress | None = None,
    gcode_path: str | Path | None = None,
) -> dict:
    """Run the export pipeline from a slice cache.

    Returns a summary dict. Raises ``ValueError``/``OSError`` for actionable
    failures; the plugin converts those into ``ExecutionResult.failure``.
    """

    config = config or PluginConfig()
    cache = Path(cache_path)
    out = Path(out_dir)
    if not cache.exists():
        raise ValueError(
            "no slice cache found; run a slice with the capture capability first"
        )
    _report(progress, 0.02, "Loading slice cache")
    model = load_cache(cache)
    model = model.filter_roi(config.roi, config.layer_range)
    if not model.beads:
        raise ValueError("the region of interest contains no beads")
    out.mkdir(parents=True, exist_ok=True)

    _report(progress, 0.15, "Detecting contacts")
    contacts = detect_contacts(model, resolution=config.resolution)

    _report(progress, 0.3, "Meshing beads")
    mesh_summary = mesh_stats(model)

    bead_process = None
    process_summary = None
    adhesion = None
    interfaces = None
    gcode = Path(gcode_path) if gcode_path else config.resolve_gcode_path(out)
    if gcode is not None and gcode.exists():
        _report(progress, 0.4, "Scoring process-informed adhesion")
        try:
            parsed = parse_gcode(gcode)
            bead_process, match = attach_process(model, parsed)
            interfaces = extract_interfaces(
                model, resolution=config.resolution
            )
            interfaces, adhesion = apply_bond_quality(
                interfaces, bead_process, AdhesionModel()
            )
            process_summary = {
                "match": match,
                "gcode": parsed.summary(),
            }
        except (ValueError, OSError) as exc:
            process_summary = {"warning": f"process data skipped: {exc}"}

    _report(progress, 0.55, "Writing sidecar and report")
    sidecar_path = out / "beads.json"
    report_path = out / "report.json"
    write_sidecar(
        model,
        contacts,
        sidecar_path,
        mesh_summary=mesh_summary,
        interfaces=interfaces,
        bead_process=bead_process,
        process_summary=process_summary,
        adhesion=adhesion,
    )
    write_report(
        model,
        contacts,
        report_path,
        resolution=config.resolution,
        mesh_summary=mesh_summary,
        roi=config.roi,
        interfaces=interfaces,
        adhesion=adhesion,
    )

    files = {"sidecar": str(sidecar_path), "report": str(report_path)}
    stats = contact_stats(
        contacts,
        config.resolution,
        min(bead.cross_section.width_total for bead in model.beads),
    )

    if config.write_stl:
        _report(progress, 0.7, "Writing STL")
        files["stl"] = str(write_stl_model(model, out / "beads.stl"))

    if config.write_step:
        from .step_planar import estimate_faces

        estimated = estimate_faces(
            model, config.step_stride, config.step_tolerance
        )
        if estimated > config.max_step_faces:
            raise ValueError(
                f"STEP preflight: about {estimated:,} planar faces exceeds the "
                f"limit of {config.max_step_faces:,}. Use an ROI, a layer range, "
                "a larger step_stride or step_tolerance, or raise max_step_faces."
            )
        _report(progress, 0.8, "Writing STEP (this can take a while)")
        step = export_step(
            model,
            out / "beads.step",
            verify=True,
            profile_stride=config.step_stride,
            step_engine=config.step_engine,
            step_tolerance=config.step_tolerance,
        )
        files["step"] = step["path"]

    _report(progress, 1.0, "Done")
    summary = {
        "schema": "gcode_fea.plugin_export.v1",
        "cache": str(cache),
        "output_dir": str(out),
        "bead_count": len(model.beads),
        "layer_count": len(model.layers()),
        "volume": model.volume,
        "contacts": stats["count"],
        "interfaces": interface_report(interfaces) if interfaces else None,
        "mean_bond_quality": (
            sum(item.bond_quality for item in interfaces) / len(interfaces)
            if interfaces
            else None
        ),
        "files": files,
        "warnings": (
            ["process data skipped"]
            if process_summary and "warning" in process_summary
            else []
        ),
    }
    return summary


def export_step_from_cache(
    cache_path: str | Path,
    step_path: str | Path,
    config: PluginConfig | None = None,
    progress: Progress | None = None,
) -> dict:
    """Export only a viewable STEP file from a slice cache.

    This is the fast path used by the plugin's "Export STEP (viewable)"
    capability: no contact detection, no mesh statistics, and no sidecar.
    """

    config = config or PluginConfig()
    cache = Path(cache_path)
    target = Path(step_path)
    if not cache.exists():
        raise ValueError(
            "no slice cache found; run a slice with the capture capability first"
        )
    _report(progress, 0.05, "Loading slice cache")
    model = load_cache(cache)
    model = model.filter_roi(config.roi, config.layer_range)
    if not model.beads:
        raise ValueError("the region of interest contains no beads")

    from .step_planar import estimate_faces

    estimated = estimate_faces(
        model, config.step_stride, config.step_tolerance
    )
    if estimated > config.max_step_faces:
        raise ValueError(
            f"STEP preflight: about {estimated:,} planar faces exceeds the "
            f"limit of {config.max_step_faces:,}. Use an ROI, a layer range, "
            "a larger step_stride or step_tolerance, or raise max_step_faces."
        )
    _report(progress, 0.3, f"Writing {estimated:,} planar faces")
    result = export_step(
        model,
        target,
        verify=True,
        profile_stride=config.step_stride,
        step_engine=config.step_engine,
        step_tolerance=config.step_tolerance,
    )
    _report(progress, 1.0, "Done")
    return {
        "schema": "gcode_fea.plugin_step.v1",
        "cache": str(cache),
        "bead_count": len(model.beads),
        "layer_count": len(model.layers()),
        "engine": result.get("engine"),
        "solids": result.get("solids", result.get("segment_prisms")),
        "faces": estimate_faces(
            model, config.step_stride, config.step_tolerance
        ),
        "bytes": target.stat().st_size,
        "volume": result.get("volume"),
        "volume_relative_error": result.get("volume_relative_error"),
        "file": str(target),
    }


def export_from_probe(
    probe_path: str | Path,
    out_dir: str | Path,
    config: PluginConfig | None = None,
    progress: Progress | None = None,
) -> dict:
    """Convenience for CLI/tests: probe -> cache -> full export."""

    from .cache import load_probe, save_cache

    config = config or PluginConfig()
    model = load_probe(
        probe_path,
        mode=config.mode,
        c_h=config.c_h,
        c_v=config.c_v,
    )
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    cache = out / "slice_cache.json"
    save_cache(model, cache)
    return export_from_cache(cache, out, config, progress)


def _report(progress: Progress | None, fraction: float, message: str) -> None:
    if progress is not None:
        progress(fraction, message)
