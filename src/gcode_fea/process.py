"""G-code process extraction and per-bead process records.

The plugin graph does not expose per-path speed, temperature, or fan, so the
Phase 4 process data comes from the sliced G-code. This parser is deliberately
conservative: it reads the common OrcaSlicer/PrusaSlicer comments plus M104,
M109, M106, and G1 moves, and ignores anything it does not understand. Every
unknown construct is reported in ``warnings`` rather than guessed.

Times are estimates. Travel and extrusion times are summed from the commanded
feedrate; acceleration, jerk, and firmware limits are not modelled, so layer
times are lower bounds. That is acceptable for a relative bond-quality model but
not for absolute thermal history.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .model import SliceModel

_NUMBER = re.compile(r"([A-Za-z])([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)")


@dataclass(frozen=True)
class ToolpathSegment:
    layer_index: int
    z: float
    role: str
    width: float | None
    height: float | None
    speed_mm_s: float
    nozzle_temp_c: float
    fan_percent: float
    start: tuple[float, float, float]
    end: tuple[float, float, float]

    @property
    def length(self) -> float:
        return math.dist(self.start, self.end)

    @property
    def midpoint(self) -> tuple[float, float, float]:
        return (
            (self.start[0] + self.end[0]) / 2.0,
            (self.start[1] + self.end[1]) / 2.0,
            (self.start[2] + self.end[2]) / 2.0,
        )


@dataclass
class GcodeProcess:
    source: str
    segments: list[ToolpathSegment]
    layer_times_s: dict[int, float]
    layer_z: dict[int, float]
    settings: dict
    warnings: list[str] = field(default_factory=list)

    @property
    def segment_count(self) -> int:
        return len(self.segments)

    @property
    def total_time_s(self) -> float:
        return sum(self.layer_times_s.values())

    def layer_time(self, layer_index: int) -> float:
        if not self.layer_times_s:
            return 0.0
        if layer_index in self.layer_times_s:
            return self.layer_times_s[layer_index]
        nearest = min(
            self.layer_times_s,
            key=lambda candidate: abs(candidate - layer_index),
        )
        return self.layer_times_s[nearest]

    def layers(self) -> dict[int, list[ToolpathSegment]]:
        result: dict[int, list[ToolpathSegment]] = {}
        for segment in self.segments:
            result.setdefault(segment.layer_index, []).append(segment)
        return dict(sorted(result.items()))

    def summary(self) -> dict:
        roles: dict[str, int] = {}
        for segment in self.segments:
            roles[segment.role] = roles.get(segment.role, 0) + 1
        return {
            "source": self.source,
            "layers": len(self.layer_times_s),
            "segments": self.segment_count,
            "role_counts": dict(sorted(roles.items())),
            "total_time_s": self.total_time_s,
            "layer_time_min_s": min(self.layer_times_s.values())
            if self.layer_times_s
            else 0.0,
            "layer_time_max_s": max(self.layer_times_s.values())
            if self.layer_times_s
            else 0.0,
            "settings": dict(self.settings),
            "warnings": list(self.warnings),
        }


@dataclass
class BeadProcess:
    bead_id: str
    layer_index: int
    speed_mm_s: float
    nozzle_temp_c: float
    fan_percent: float
    layer_time_s: float
    matched_segments: int
    source: str = "gcode"

    def as_dict(self) -> dict:
        return asdict(self)


def parse_gcode(
    path: str | Path,
    default_temp_c: float = 210.0,
    default_speed_mm_s: float = 50.0,
    default_fan_percent: float = 0.0,
) -> GcodeProcess:
    source = Path(path)
    text = source.read_text(errors="replace")
    warnings: list[str] = []

    layer_index = -1
    z = 0.0
    height: float | None = None
    width: float | None = None
    role = "unknown"
    nozzle_temp = default_temp_c
    bed_temp = 0.0
    fan = default_fan_percent
    speed_mm_s = default_speed_mm_s
    position = [0.0, 0.0, 0.0]
    segments: list[ToolpathSegment] = []
    layer_times: dict[int, float] = {}
    layer_z: dict[int, float] = {}
    absolute_xyz = True
    seen_temps: list[float] = []
    seen_fans: list[float] = []
    seen_bed: list[float] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(";"):
            if line.startswith(";LAYER_CHANGE"):
                layer_index += 1
                if layer_index not in layer_times:
                    layer_times[layer_index] = 0.0
            elif line.startswith(";Z:"):
                try:
                    z = float(line[3:])
                    layer_z[layer_index] = z
                except ValueError:
                    warnings.append(f"unparsed Z comment: {line}")
            elif line.startswith(";HEIGHT:"):
                try:
                    height = float(line[8:])
                except ValueError:
                    warnings.append(f"unparsed HEIGHT comment: {line}")
            elif line.startswith(";WIDTH:"):
                try:
                    width = float(line[7:])
                except ValueError:
                    warnings.append(f"unparsed WIDTH comment: {line}")
            elif line.startswith(";TYPE:"):
                role = line[6:].strip() or "unknown"
            continue

        code, params = _parse_command(line)
        if code == "G90":
            absolute_xyz = True
        elif code == "G91":
            absolute_xyz = False
        elif code == "M104" or code == "M109":
            if "S" in params:
                nozzle_temp = params["S"]
                if nozzle_temp > 0:
                    seen_temps.append(nozzle_temp)
        elif code == "M140" or code == "M190":
            if "S" in params:
                bed_temp = params["S"]
                if bed_temp > 0:
                    seen_bed.append(bed_temp)
        elif code == "M106":
            value = params.get("S", 255.0)
            fan = max(0.0, min(100.0, value / 255.0 * 100.0))
            seen_fans.append(fan)
        elif code in ("G0", "G1"):
            if "F" in params and params["F"] > 0:
                speed_mm_s = params["F"] / 60.0
            new = list(position)
            for axis, index in (("X", 0), ("Y", 1), ("Z", 2)):
                if axis in params:
                    value = params[axis]
                    new[index] = value if absolute_xyz else new[index] + value
            travel = math.dist(position, new)
            if layer_index >= 0 and travel > 0 and speed_mm_s > 0:
                layer_times[layer_index] = layer_times.get(layer_index, 0.0) + (
                    travel / speed_mm_s
                )
            extruding = params.get("E", 0.0) > 0
            if extruding and travel > 1e-9:
                segments.append(
                    ToolpathSegment(
                        layer_index=max(layer_index, 0),
                        z=z,
                        role=role,
                        width=width,
                        height=height,
                        speed_mm_s=speed_mm_s,
                        nozzle_temp_c=nozzle_temp,
                        fan_percent=fan,
                        start=(position[0], position[1], position[2]),
                        end=(new[0], new[1], new[2]),
                    )
                )
            position = new
        elif code in ("G2", "G3"):
            warnings.append("arc move (G2/G3) ignored")

    if not segments:
        raise ValueError(f"{source} contains no extrusion moves")
    if not layer_times:
        warnings.append("no LAYER_CHANGE markers; layer times unavailable")

    settings = {
        "nozzle_temp_c": max(seen_temps) if seen_temps else nozzle_temp,
        "bed_temp_c": max(seen_bed) if seen_bed else bed_temp,
        "fan_percent": max(seen_fans) if seen_fans else fan,
        "speed_mm_s": speed_mm_s,
        "layer_count": len(layer_times),
        "layer_height": height,
        "path_width": width,
    }
    return GcodeProcess(
        source=source.name,
        segments=segments,
        layer_times_s=layer_times,
        layer_z=layer_z,
        settings=settings,
        warnings=sorted(set(warnings)),
    )


def default_process(
    model: SliceModel,
    gcode: GcodeProcess | None = None,
    nozzle_temp_c: float = 210.0,
    fan_percent: float = 0.0,
    speed_mm_s: float = 50.0,
    layer_time_s: float = 10.0,
) -> dict[str, BeadProcess]:
    """Uniform fallback when no G-code is available."""

    result: dict[str, BeadProcess] = {}
    for bead in model.sorted_beads():
        layer_time = (
            gcode.layer_time(bead.layer_index) if gcode is not None else layer_time_s
        )
        result[bead.bead_id] = BeadProcess(
            bead_id=bead.bead_id,
            layer_index=bead.layer_index,
            speed_mm_s=speed_mm_s,
            nozzle_temp_c=nozzle_temp_c,
            fan_percent=fan_percent,
            layer_time_s=layer_time,
            matched_segments=0,
            source="default",
        )
    return result


def attach_process(
    model: SliceModel,
    gcode: GcodeProcess,
    tolerance: float = 0.5,
    origin_offset: tuple[float, float] | None = None,
) -> tuple[dict[str, BeadProcess], dict]:
    """Match G-code segments to beads by layer and spatial proximity.

    Slicer G-code is in printer plate coordinates while the probe paths are
    object-centred. When ``origin_offset`` is omitted it is estimated from the
    model and G-code XY bounding-box centres, so plate placement does not break
    the match.
    """

    segments_by_layer = gcode.layers()
    layer_by_z = {
        round(z, 4): index for index, z in gcode.layer_z.items()
    }
    if origin_offset is None:
        origin_offset = _estimate_offset(model, gcode)
    result: dict[str, BeadProcess] = {}
    matched_beads = 0
    for bead in model.sorted_beads():
        layer_index = _nearest_layer(bead, layer_by_z)
        segments = segments_by_layer.get(layer_index, [])
        half = bead.cross_section.width_total / 2.0
        threshold = half + tolerance
        matches = [
            segment
            for segment in segments
            if _segment_distance(segment, bead, origin_offset) <= threshold
        ]
        if not matches:
            # Fall back to the layer's median values rather than dropping data.
            if segments:
                matches = segments
                source = "layer_median"
            else:
                result[bead.bead_id] = BeadProcess(
                    bead_id=bead.bead_id,
                    layer_index=bead.layer_index,
                    speed_mm_s=gcode.settings.get("speed_mm_s", 50.0),
                    nozzle_temp_c=gcode.settings.get("nozzle_temp_c", 210.0),
                    fan_percent=gcode.settings.get("fan_percent", 0.0),
                    layer_time_s=gcode.layer_time(bead.layer_index),
                    matched_segments=0,
                    source="default",
                )
                continue
        else:
            source = "gcode"
            matched_beads += 1
        result[bead.bead_id] = BeadProcess(
            bead_id=bead.bead_id,
            layer_index=layer_index,
            speed_mm_s=_median([segment.speed_mm_s for segment in matches]),
            nozzle_temp_c=_median([segment.nozzle_temp_c for segment in matches]),
            fan_percent=_median([segment.fan_percent for segment in matches]),
            layer_time_s=gcode.layer_time(layer_index),
            matched_segments=len(matches),
            source=source,
        )

    summary = {
        "beads": len(model.beads),
        "matched": matched_beads,
        "coverage": matched_beads / len(model.beads) if model.beads else 0.0,
        "fallback": len(model.beads) - matched_beads,
        "tolerance": tolerance,
        "origin_offset": list(origin_offset),
    }
    return result, summary


def process_report(
    model: SliceModel,
    bead_process: dict[str, BeadProcess],
) -> dict:
    speeds = [item.speed_mm_s for item in bead_process.values()]
    temps = [item.nozzle_temp_c for item in bead_process.values()]
    fans = [item.fan_percent for item in bead_process.values()]
    times = [item.layer_time_s for item in bead_process.values()]
    sources: dict[str, int] = {}
    for item in bead_process.values():
        sources[item.source] = sources.get(item.source, 0) + 1
    return {
        "bead_count": len(bead_process),
        "beads_in_model": len(model.beads),
        "sources": dict(sorted(sources.items())),
        "speed_mm_s": _distribution(speeds),
        "nozzle_temp_c": _distribution(temps),
        "fan_percent": _distribution(fans),
        "layer_time_s": _distribution(times),
    }


def _parse_command(line: str) -> tuple[str, dict[str, float]]:
    parts = line.split(";", 1)[0].split()
    if not parts:
        return "", {}
    code = parts[0].upper()
    params: dict[str, float] = {}
    for token in parts[1:]:
        match = _NUMBER.fullmatch(token)
        if match:
            params[match.group(1).upper()] = float(match.group(2))
    return code, params


def _nearest_layer(bead, layer_by_z: dict[float, int]) -> int:
    if not layer_by_z:
        return bead.layer_index
    target = round(bead.print_z, 4)
    if target in layer_by_z:
        return layer_by_z[target]
    nearest = min(layer_by_z, key=lambda value: abs(value - target))
    if abs(nearest - target) <= 0.11:
        return layer_by_z[nearest]
    return bead.layer_index


def _segment_distance(
    segment: ToolpathSegment,
    bead,
    origin_offset: tuple[float, float] = (0.0, 0.0),
) -> float:
    """Minimum distance between a G-code segment midpoint and the bead path."""

    raw = segment.midpoint
    midpoint = (raw[0] + origin_offset[0], raw[1] + origin_offset[1], raw[2])
    points = bead.points
    best = math.inf
    for start, end in zip(points, points[1:]):
        best = min(best, _point_segment_distance(midpoint, start, end))
    if len(points) == 1:
        best = math.dist(midpoint, points[0])
    return best


def _point_segment_distance(point, start, end) -> float:
    ax, ay = start[0], start[1]
    bx, by = end[0], end[1]
    px, py = point[0], point[1]
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq <= 1e-18:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _estimate_offset(
    model: SliceModel,
    gcode: GcodeProcess,
) -> tuple[float, float]:
    bbox = model.bbox()
    model_center = ((bbox[0] + bbox[3]) / 2.0, (bbox[1] + bbox[4]) / 2.0)
    xs = [
        point[0]
        for segment in gcode.segments
        for point in (segment.start, segment.end)
    ]
    ys = [
        point[1]
        for segment in gcode.segments
        for point in (segment.start, segment.end)
    ]
    if not xs:
        return (0.0, 0.0)
    gcode_center = ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0)
    return (model_center[0] - gcode_center[0], model_center[1] - gcode_center[1])


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return 0.5 * (ordered[middle - 1] + ordered[middle])


def _distribution(values: list[float]) -> dict:
    if not values:
        return {"min": 0.0, "median": 0.0, "max": 0.0, "mean": 0.0}
    ordered = sorted(values)
    return {
        "min": ordered[0],
        "median": _median(values),
        "max": ordered[-1],
        "mean": sum(values) / len(values),
    }
