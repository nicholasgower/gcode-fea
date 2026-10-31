# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy"]
#
# [tool.orcaslicer.plugin]
# name = "GCode-FEA"
# description = "Capture the sliced bead mesostructure and export FEA geometry, contacts, and interfaces."
# author = "GCode-FEA"
# version = "0.1.0"
# ///
"""OrcaSlicer adapter for GCode-FEA.

Two capabilities:

- ``Capture Slice Cache`` (slicing pipeline, background thread) serializes the
  live bead graph at ``posSimplifyPath`` into the core cache schema.
- ``Export FEA Geometry`` (script, UI thread) reads that cache and writes the
  sidecar, report, optional STL/STEP, and process-informed interfaces.

The adapter is deliberately thin. All geometry, export, and adhesion logic
lives in the ``gcode_fea`` core; this file only captures, configures, reports,
and shows progress.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import shutil
import sys
from pathlib import Path

import orca

CACHE_DIRNAME = "cache"
CACHE_FILENAME = "slice.json"
PROBE_FILENAME = "slice_probe.json"


class CaptureSliceCache(orca.slicing.SlicingPipelineCapabilityBase):
    """Serialize the live slicing graph into a GCode-FEA cache."""

    def get_name(self) -> str:
        return "Capture Slice Cache"

    def get_default_config(self) -> dict:
        return {
            "mode": "volume_conserving",
            "c_h": 0.7,
            "c_v": 0.225,
            "max_layers": 200,
            "cache_path": "",
        }

    def execute(self, ctx) -> orca.ExecutionResult:
        if ctx.step == orca.slicing.Step.psGCodePostProcess:
            return self._store_gcode(ctx)
        if ctx.step != orca.slicing.Step.posSimplifyPath:
            return orca.ExecutionResult.success()
        obj = ctx.object
        if obj is None:
            return orca.ExecutionResult.success()

        try:
            config = _merged_config(self)
        except Exception as exc:
            return _failure("configuration error", exc)
        max_layers = int(config.get("max_layers", 200))
        storage = _storage()
        layers = []
        truncated = False
        for layer_index, layer in enumerate(obj.layers()):
            paths = _collect_paths(layer)
            if not paths:
                continue
            layers.append(
                {
                    "layer_index": layer_index,
                    "print_z": float(layer.print_z),
                    "slice_z": float(layer.slice_z),
                    "height": float(layer.height),
                    "paths": paths,
                }
            )
            if len(layers) >= max_layers:
                truncated = True
                break

        if not layers:
            return orca.ExecutionResult.success(
                "capture: no extrusion paths at posSimplifyPath"
            )

        payload = {
            "probe_version": 3,
            "orcaslicer_version": ctx.orca_version,
            "object_id": int(obj.id()),
            "layer_count": len(layers),
            "truncated": truncated,
            "layers": layers,
        }
        try:
            core = _core(config)
            model = core["model_from_probe_data"](
                payload,
                source_name="live slice",
                mode=config.get("mode", "volume_conserving"),
                c_h=float(config.get("c_h", 0.7)),
                c_v=float(config.get("c_v", 0.225)),
            )
            probe_path = storage / CACHE_DIRNAME / PROBE_FILENAME
            probe_path.parent.mkdir(parents=True, exist_ok=True)
            probe_text = json.dumps(payload, sort_keys=True) + "\n"
            probe_path.write_text(probe_text)
            model.meta["source_hash"] = hashlib.sha256(
                probe_text.encode("utf-8")
            ).hexdigest()
            model.meta["source"] = "live slice"
            cache_path = _cache_path(config, storage)
            core["save_cache"](model, cache_path)
        except Exception as exc:  # the plugin boundary must report, not crash
            return _failure("capture failed", exc)

        message = (
            f"captured {len(model.beads)} beads in {len(layers)} layer(s) "
            f"to {cache_path}"
        )
        if truncated:
            message += f" (capped at {max_layers} layers)"
        return orca.ExecutionResult.success(message)

    def _store_gcode(self, ctx) -> orca.ExecutionResult:
        source = getattr(ctx, "gcode_path", "")
        if not source:
            return orca.ExecutionResult.success()
        source_path = Path(str(source))
        if not source_path.exists():
            return orca.ExecutionResult.success()
        try:
            target = _storage() / CACHE_DIRNAME / "last.gcode"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path, target)
        except Exception as exc:
            return _failure("could not keep the G-code for process data", exc)
        return orca.ExecutionResult.success(f"kept process G-code at {target}")


class ExportFEAGeometry(orca.script.ScriptPluginCapabilityBase):
    """Export FEA geometry from the last captured slice cache."""

    def get_name(self) -> str:
        return "Export FEA Geometry"

    def get_default_config(self) -> dict:
        return {
            "mode": "volume_conserving",
            "c_h": 0.7,
            "c_v": 0.225,
            "resolution": 0.1,
            "step_stride": 1,
            "roi": None,
            "layer_range": None,
            "formats": ["sidecar", "report"],
            "write_stl": False,
            "write_step": False,
            "contacts": True,
            "core_path": "",
            "cache_path": "",
            "output_dir": "",
            "gcode_path": "",
        }

    def execute(self) -> orca.ExecutionResult:
        try:
            config = _merged_config(self)
            core = _core(config)
            plugin_config = core["parse_config"](config)
        except Exception as exc:
            return _failure("configuration error", exc)
        except OSError as exc:
            return _failure("configuration error", exc)

        storage = _storage()
        cache_path = plugin_config.resolve_cache_path(storage)
        output_dir = plugin_config.resolve_output_dir(storage)

        if not cache_path.exists():
            probe_path = plugin_config.resolve_probe_path(storage)
            if probe_path is not None and probe_path.exists():
                try:
                    model = core["load_probe"](
                        probe_path,
                        mode=plugin_config.mode,
                        c_h=plugin_config.c_h,
                        c_v=plugin_config.c_v,
                    )
                    core["save_cache"](model, cache_path)
                except Exception as exc:
                    return _failure("could not convert the probe", exc)
            else:
                return orca.ExecutionResult.failure(
                    orca.PluginResult.RecoverableError,
                    "No slice cache yet. Slice the plate once with "
                    "'Capture Slice Cache' enabled, then run Export again.",
                )

        cancelled = {"value": False}

        def progress(fraction: float, message: str) -> None:
            if handle is not None and not handle.update(
                max(0, min(100, int(fraction * 100))), message
            ):
                cancelled["value"] = True

        handle = None
        try:
            handle = _progress_dialog("Export FEA Geometry")
        except Exception:
            handle = None

        try:
            summary = core["export_from_cache"](
                cache_path,
                output_dir,
                plugin_config,
                progress=progress,
                gcode_path=plugin_config.resolve_gcode_path(storage),
            )
        except Exception as exc:
            return _failure("export failed", exc)
        finally:
            if handle is not None:
                try:
                    handle.close()
                except Exception:
                    pass

        if cancelled["value"]:
            return orca.ExecutionResult.skipped("Export cancelled by user")

        try:
            status = core["cache_status"](
                cache_path, plugin_config.resolve_probe_path(storage)
            )
        except Exception:
            status = {}
        message = (
            f"Exported {summary['bead_count']} beads in "
            f"{summary['layer_count']} layer(s) to {output_dir}\n"
            f"contacts: {summary['contacts']}"
        )
        if summary.get("mean_bond_quality") is not None:
            message += f"\nmean bond quality: {summary['mean_bond_quality']:.3f}"
        files = summary.get("files", {})
        if files:
            message += "\n" + "\n".join(str(path) for path in files.values())
        if status.get("stale"):
            message += "\nwarning: the cache is stale; slice again before trusting the result"
        return orca.ExecutionResult.success(
            message, data=json.dumps(summary, sort_keys=True)
        )


@orca.plugin
class GCodeFEAPlugin(orca.base):
    def register_capabilities(self) -> None:
        # The wheel bundles the core, so its imports live under the plugin
        # folder and the Python runtime roots. Declaring them up front turns a
        # first-run flood of per-file prompts into one combined dialog.
        orca.request_permissions(fs_read=_read_roots())
        orca.register_capability(CaptureSliceCache)
        orca.register_capability(ExportFEAGeometry)


def _read_roots() -> list[str]:
    import site

    roots = set()
    for root in (sys.prefix, sys.base_prefix):
        if root:
            roots.add(str(root))
    try:
        roots.update(str(path) for path in site.getsitepackages())
    except Exception:
        pass
    try:
        roots.add(str(Path.home() / ".cache" / "uv"))
    except Exception:
        pass
    return sorted(roots)


def _collect_paths(layer) -> list[dict]:
    paths: list[dict] = []
    for region in layer.regions():
        for collection in (region.perimeters, region.fills):
            for path in collection.flatten_paths():
                points = path.points()
                if points is None or len(points) == 0:
                    continue
                points_mm = [
                    [orca.slicing.unscale(int(value)) for value in row[:3]]
                    for row in points
                ]
                paths.append(
                    {
                        "role": str(path.role),
                        "width": float(path.width),
                        "height": float(path.height),
                        "mm3_per_mm": float(path.mm3_per_mm),
                        "points_mm": points_mm,
                    }
                )
    return paths


def _storage() -> Path:
    try:
        storage = Path(str(orca.host.plugin.storage()))
    except Exception:
        storage = (
            Path.home()
            / ".config"
            / "OrcaSlicer"
            / "orca_plugins"
            / "plugin_data"
            / "gcode_fea"
        )
    storage.mkdir(parents=True, exist_ok=True)
    return storage


def _cache_path(config: dict, storage: Path) -> Path:
    configured = str(config.get("cache_path", "") or "")
    if configured:
        path = Path(configured)
    else:
        path = storage / CACHE_DIRNAME / CACHE_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _merged_config(capability) -> dict:
    defaults = capability.get_default_config()
    stored = json.loads(capability.get_config() or "{}")
    if not isinstance(stored, dict):
        raise ValueError("configuration must be a JSON object")
    return {**defaults, **stored}


def _core(config: dict) -> dict:
    """Import the core, honouring an optional ``core_path`` configuration."""

    core_path = str(config.get("core_path", "") or "")
    if core_path and core_path not in sys.path:
        sys.path.insert(0, core_path)
        importlib.invalidate_caches()
    try:
        package = importlib.import_module("gcode_fea")
    except ImportError as exc:
        raise RuntimeError(
            "the gcode_fea core is not importable; install the GCode-FEA "
            "plugin wheel, or set 'core_path' to the repository src directory"
        ) from exc
    return {
        "package": package,
        "parse_config": importlib.import_module("gcode_fea.config").parse_config,
        "model_from_probe_data": importlib.import_module(
            "gcode_fea.cache"
        ).model_from_probe_data,
        "save_cache": importlib.import_module("gcode_fea.cache").save_cache,
        "load_probe": importlib.import_module("gcode_fea.cache").load_probe,
        "export_from_cache": importlib.import_module(
            "gcode_fea.plugin_export"
        ).export_from_cache,
        "cache_status": importlib.import_module("gcode_fea.config").cache_status,
    }


def _progress_dialog(title: str):
    ui = orca.host.ui
    style = (
        ui.PD_APP_MODAL
        | ui.PD_AUTO_HIDE
        | ui.PD_CAN_ABORT
        | ui.PD_ELAPSED_TIME
        | ui.PD_REMAINING_TIME
    )
    return ui.create_progress_dialog(
        title, "Preparing export...", maximum=100, style=style
    )


def _failure(prefix: str, exc: BaseException) -> "orca.ExecutionResult":
    return orca.ExecutionResult.failure(
        orca.PluginResult.RecoverableError, f"{prefix}: {exc}"
    )
