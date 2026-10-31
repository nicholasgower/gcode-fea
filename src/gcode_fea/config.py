"""Plugin configuration schema and cache status helpers.

The OrcaSlicer adapter stores this JSON per capability. Keep validation here,
not in the plugin, so the same rules apply to the CLI and to tests.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .cache import cache_info, source_fingerprint
from .cross_section import MODES

FORMATS = ("sidecar", "report")


@dataclass(frozen=True)
class PluginConfig:
    mode: str = "volume_conserving"
    c_h: float = 0.7
    c_v: float = 0.225
    resolution: float = 0.1
    step_stride: int = 1
    step_engine: str = "auto"
    step_tolerance: float = 0.0
    max_step_faces: int = 150_000
    max_layers: int = 200
    roi: tuple[float, float, float, float, float, float] | None = None
    layer_range: tuple[int, int] | None = None
    formats: tuple[str, ...] = FORMATS
    write_stl: bool = False
    write_step: bool = False
    contacts: bool = True
    core_path: str = ""
    cache_path: str = ""
    output_dir: str = ""
    probe_path: str = ""
    gcode_path: str = ""
    open_after_export: bool = False
    notes: str = ""

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        if not 0.0 <= self.c_h <= 1.0:
            raise ValueError("c_h must be in [0, 1]")
        if not 0.0 <= self.c_v <= 1.0:
            raise ValueError("c_v must be in [0, 1]")
        if self.resolution <= 0:
            raise ValueError("resolution must be positive")
        if self.step_stride < 1:
            raise ValueError("step_stride must be at least 1")
        if self.step_engine not in ("auto", "gmsh", "planar"):
            raise ValueError("step_engine must be 'auto', 'gmsh', or 'planar'")
        if self.max_step_faces < 1:
            raise ValueError("max_step_faces must be positive")
        if self.step_tolerance < 0:
            raise ValueError("step_tolerance must not be negative")
        if self.max_layers < 1:
            raise ValueError("max_layers must be at least 1")
        unknown = set(self.formats) - set(FORMATS)
        if unknown:
            raise ValueError(f"unknown formats: {sorted(unknown)}")
        if self.roi is not None and len(self.roi) != 6:
            raise ValueError("roi must have six values")
        if self.layer_range is not None:
            if len(self.layer_range) != 2:
                raise ValueError("layer_range must have two values")
            if self.layer_range[0] > self.layer_range[1]:
                raise ValueError("layer_range start must not exceed end")

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["roi"] = list(self.roi) if self.roi is not None else None
        payload["layer_range"] = (
            list(self.layer_range) if self.layer_range is not None else None
        )
        payload["formats"] = list(self.formats)
        return payload

    @classmethod
    def from_dict(cls, payload: dict | None) -> "PluginConfig":
        values = dict(payload or {})
        if "roi" in values and values["roi"] is not None:
            values["roi"] = tuple(float(value) for value in values["roi"])
        if "layer_range" in values and values["layer_range"] is not None:
            values["layer_range"] = tuple(
                int(value) for value in values["layer_range"]
            )
        if "formats" in values:
            values["formats"] = tuple(str(value) for value in values["formats"])
        allowed = set(cls.__dataclass_fields__)
        return cls(**{key: value for key, value in values.items() if key in allowed})

    def resolve_cache_path(self, storage: Path) -> Path:
        return Path(self.cache_path) if self.cache_path else storage / "cache" / "slice.json"

    def resolve_output_dir(self, storage: Path) -> Path:
        return Path(self.output_dir) if self.output_dir else storage / "exports"

    def resolve_probe_path(self, storage: Path) -> Path | None:
        if self.probe_path:
            return Path(self.probe_path)
        candidate = storage / "cache" / "slice_probe.json"
        return candidate if candidate.exists() else None

    def resolve_gcode_path(self, storage: Path) -> Path | None:
        if self.gcode_path:
            return Path(self.gcode_path)
        candidate = storage / "cache" / "last.gcode"
        return candidate if candidate.exists() else None


def parse_config(raw: str | dict | None) -> PluginConfig:
    if raw is None or raw == "":
        return PluginConfig()
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"configuration is not valid JSON: {exc}") from exc
    else:
        payload = raw
    if not isinstance(payload, dict):
        raise ValueError("configuration must be a JSON object")
    return PluginConfig.from_dict(payload)


def cache_status(
    cache_path: str | Path,
    probe_path: str | Path | None = None,
) -> dict:
    """Cache summary plus a stale-slice verdict."""

    source = Path(cache_path)
    if not source.exists():
        return {
            "exists": False,
            "path": str(source),
            "stale": None,
            "message": "no slice cache; slice with the capture capability first",
        }
    info = cache_info(source)
    info["exists"] = True
    stale = None
    message = "cache is current"
    probe = Path(probe_path) if probe_path else None
    if probe is None:
        message = "no probe path supplied; freshness not checked"
    elif probe is not None:
        payload = json.loads(source.read_text())
        recorded = dict(payload.get("meta", {})).get("source_hash")
        if recorded:
            stale = source_fingerprint(probe) != recorded
            message = (
                "cache is stale; the probe changed since capture"
                if stale
                else "cache matches the recorded probe"
            )
        else:
            stale = None
            message = "cache has no recorded probe hash; cannot prove freshness"
    info["stale"] = stale
    info["message"] = message
    return info


def default_config_json() -> str:
    return json.dumps(PluginConfig().to_dict(), indent=2, sort_keys=True)
