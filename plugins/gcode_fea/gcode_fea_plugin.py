# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy", "shapely>=2.0", "manifold3d>=3.5"]
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

try:  # imported at plugin load, before any audited capability call
    from gcode_fea import cache as _core_cache
    from gcode_fea import config as _core_config
    from gcode_fea import plugin_export as _core_export
    from gcode_fea import save_dialog as _core_dialog
    from gcode_fea import transfer as _core_transfer
except Exception:  # single-file install without the bundled core
    _core_cache = _core_config = _core_export = _core_dialog = _core_transfer = None

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
            "step_engine": "auto",
            "step_tolerance": 0.0,
            "max_step_faces": 150000,
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
            "ask_output_dir": True,
            "trim_overlaps": False,
            "max_step_triangles": 60000,
        }

    def execute(self) -> orca.ExecutionResult:
        try:
            config = _merged_config(self)
            core = _core(config)
            plugin_config = core["parse_config"](config)
        except Exception as exc:
            return _failure("configuration error", exc)

        storage = _storage()
        cache_path = plugin_config.resolve_cache_path(storage)
        output_dir = plugin_config.resolve_output_dir(storage)
        dialog_note = ""
        if plugin_config.ask_output_dir:
            choice = core["choose_directory"](
                output_dir, title="Choose the FEA export folder"
            )
            if choice.cancelled:
                return orca.ExecutionResult.skipped("Export cancelled by user")
            if choice.path is not None:
                output_dir = choice.path
            elif not choice.available:
                dialog_note = (
                    "\n(no native folder dialog available; used the "
                    "configured output folder)"
                )

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

        staging = _staging_dir(storage, "export")
        try:
            summary = core["export_from_cache"](
                cache_path,
                staging,
                plugin_config,
                progress=progress,
                gcode_path=plugin_config.resolve_gcode_path(storage),
            )
            core["copy_directory"](staging, output_dir)
            summary["files"] = {
                key: str(output_dir / Path(value).name)
                for key, value in summary["files"].items()
            }
            summary["output_dir"] = str(output_dir)
            shutil.rmtree(staging, ignore_errors=True)
        except Exception as exc:
            return _write_failure("export failed", exc, output_dir)
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
        message += dialog_note
        return orca.ExecutionResult.success(
            message, data=json.dumps(summary, sort_keys=True)
        )


class ExportSTEPViewable(orca.script.ScriptPluginCapabilityBase):
    """Export only a viewable STEP file from the captured slice cache."""

    def get_name(self) -> str:
        return "Export STEP (viewable)"

    def get_default_config(self) -> dict:
        return {
            "step_stride": 1,
            "step_engine": "merged",
            "step_tolerance": 0.005,
            "max_step_faces": 150000,
            "max_step_triangles": 60000,
            "roi": None,
            "layer_range": None,
            "core_path": "",
            "cache_path": "",
            "output_dir": "",
            "save_path": "",
            "ask_save_path": True,
            "trim_overlaps": False,
        }

    def execute(self) -> orca.ExecutionResult:
        try:
            config = _merged_config(self)
            core = _core(config)
            plugin_config = core["parse_config"](config)
        except Exception as exc:
            return _failure("configuration error", exc)

        storage = _storage()
        cache_path = plugin_config.resolve_cache_path(storage)
        if not cache_path.exists():
            return orca.ExecutionResult.failure(
                orca.PluginResult.RecoverableError,
                "No slice cache yet. Slice the plate once with "
                "'Capture Slice Cache' enabled, then run Export STEP again.",
            )
        output_dir = plugin_config.resolve_output_dir(storage)
        target = plugin_config.resolve_save_path(storage)
        dialog_note = ""
        if plugin_config.ask_save_path:
            choice = core["choose_save_file"](
                target,
                title="Save STEP file",
                filter_name="STEP files",
                patterns=("*.step", "*.stp"),
            )
            if choice.cancelled:
                return orca.ExecutionResult.skipped("STEP export cancelled by user")
            if choice.path is not None:
                target = choice.path
            elif not choice.available:
                dialog_note = (
                    "\n(no native save dialog available; used the configured "
                    "save path)"
                )
        output_dir = target.parent
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return _write_failure("STEP export failed", exc, output_dir)

        cancelled = {"value": False}

        def progress(fraction: float, message: str) -> None:
            if handle is not None and not handle.update(
                max(0, min(100, int(fraction * 100))), message
            ):
                cancelled["value"] = True

        handle = None
        try:
            handle = _progress_dialog("Export STEP (viewable)")
        except Exception:
            handle = None
        staging = _staging_dir(storage, "step")
        stage_file = staging / "beads.step"
        try:
            summary = core["export_step_from_cache"](
                cache_path, stage_file, plugin_config, progress=progress
            )
            core["copy_file"](stage_file, target)
            summary["file"] = str(target)
            summary["staged"] = str(stage_file)
            shutil.rmtree(staging, ignore_errors=True)
        except Exception as exc:
            return _write_failure("STEP export failed", exc, target.parent)
        finally:
            if handle is not None:
                try:
                    handle.close()
                except Exception:
                    pass

        if cancelled["value"]:
            return orca.ExecutionResult.skipped("STEP export cancelled by user")
        message = (
            f"STEP exported to {summary['file']}\n"
            f"{summary['solids']} solids, {summary['faces']:,} faces, "
            f"{summary['bytes'] / 1e6:.1f} MB, engine {summary['engine']}"
        )
        if summary.get("volume_relative_error") is not None:
            message += f", volume error {summary['volume_relative_error']:.2e}"
        message += dialog_note
        return orca.ExecutionResult.success(
            message, data=json.dumps(summary, sort_keys=True)
        )


@orca.plugin
class GCodeFEAPlugin(orca.base):
    def register_capabilities(self) -> None:
        # The wheel bundles the core, so its imports live under the plugin
        # folder and the Python runtime roots. Declaring them up front turns a
        # first-run flood of per-file prompts into one combined dialog.
        orca.request_permissions(process=_process_roots())
        orca.register_capability(CaptureSliceCache)
        orca.register_capability(ExportFEAGeometry)
        orca.register_capability(ExportSTEPViewable)


def _process_roots() -> list[str]:
    """Native dialog helpers, when present, so spawning them asks once."""

    import shutil

    roots = set()
    for name in ("zenity", "kdialog", "osascript", "powershell", "sh", "cmd"):
        path = shutil.which(name)
        if path:
            roots.add(path)
    if _core_transfer is not None:
        roots.update(str(command) for command in _core_transfer.process_commands())
    if _core_dialog is not None:
        roots.update(str(command) for command in _core_dialog.process_commands())
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


def _staging_dir(storage: Path, name: str) -> Path:
    path = storage / "staging" / name
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


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
    """Resolve the core, preferring the wheel-bundled modules imported at load."""

    if _core_config is not None:
        return _core_bindings()

    core_path = str(config.get("core_path", "") or "")
    if core_path and core_path not in sys.path:
        sys.path.insert(0, core_path)
        importlib.invalidate_caches()
    try:
        import gcode_fea.cache as cache_module
        import gcode_fea.config as config_module
        import gcode_fea.plugin_export as export_module
        import gcode_fea.save_dialog as dialog_module
        import gcode_fea.transfer as transfer_module
    except ImportError as exc:
        raise RuntimeError(
            "the gcode_fea core is not importable; install the GCode-FEA "
            "plugin wheel, or set 'core_path' to the repository src directory"
        ) from exc
    globals().update(
        _core_cache=cache_module,
        _core_config=config_module,
        _core_export=export_module,
        _core_dialog=dialog_module,
        _core_transfer=transfer_module,
    )
    return _core_bindings()


def _core_bindings() -> dict:
    return {
        "parse_config": _core_config.parse_config,
        "cache_status": _core_config.cache_status,
        "model_from_probe_data": _core_cache.model_from_probe_data,
        "save_cache": _core_cache.save_cache,
        "load_probe": _core_cache.load_probe,
        "export_from_cache": _core_export.export_from_cache,
        "export_step_from_cache": _core_export.export_step_from_cache,
        "choose_save_file": _core_dialog.choose_save_file,
        "choose_directory": _core_dialog.choose_directory,
        "copy_file": _core_transfer.copy_file,
        "copy_directory": _core_transfer.copy_directory,
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


def _write_failure(
    prefix: str,
    exc: BaseException,
    location,
) -> "orca.ExecutionResult":
    text = str(exc)
    if isinstance(exc, PermissionError) or "audited operation" in text:
        return orca.ExecutionResult.failure(
            orca.PluginResult.RecoverableError,
            f"{prefix}: OrcaSlicer blocked writing to {location}. Accept the "
            "permission prompt and run again, or choose a folder under your "
            "home directory, temp, or a mounted drive. Details: " + text,
        )
    return _failure(prefix, exc)
