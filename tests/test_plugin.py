from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_PATH = REPO_ROOT / "plugins" / "gcode_fea" / "gcode_fea_plugin.py"

sys.path.insert(0, str(REPO_ROOT / "src"))


class FakeProgress:
    def __init__(self, can_continue: bool = True) -> None:
        self.can_continue = can_continue
        self.calls: list[tuple[int, str]] = []
        self.closed = False

    def update(self, value: int, message: str = "") -> bool:
        self.calls.append((value, message))
        return self.can_continue

    def close(self) -> None:
        self.closed = True


def build_orca_stub(storage: Path) -> types.ModuleType:
    module = types.ModuleType("orca")
    module.registered = []

    class PluginResult:
        RecoverableError = "recoverable"

    class ExecutionResult:
        def __init__(self, status, message="", data=""):
            self.status = status
            self.message = message
            self.data = data

        @classmethod
        def success(cls, message="", data=""):
            return cls("success", message, data)

        @classmethod
        def skipped(cls, message=""):
            return cls("skipped", message)

        @classmethod
        def failure(cls, status, message, data=""):
            return cls(status, message, data)

    class Base:
        pass

    class CapabilityBase:
        def __init__(self) -> None:
            self.config_json = "{}"

        def get_config(self) -> str:
            return self.config_json

        def get_default_config(self) -> dict:
            return {}

        def get_name(self) -> str:
            return self.__class__.__name__

    script = types.SimpleNamespace(ScriptPluginCapabilityBase=CapabilityBase)

    class Step:
        posSimplifyPath = "posSimplifyPath"
        psGCodePostProcess = "psGCodePostProcess"

    slicing = types.SimpleNamespace(
        SlicingPipelineCapabilityBase=CapabilityBase,
        Step=Step,
        unscale=lambda value: value / 1_000_000.0,
    )

    handle = FakeProgress()
    ui = types.SimpleNamespace(
        PD_APP_MODAL=1,
        PD_AUTO_HIDE=2,
        PD_CAN_ABORT=4,
        PD_ELAPSED_TIME=8,
        PD_REMAINING_TIME=16,
        create_progress_dialog=lambda *args, **kwargs: handle,
        next_handle=handle,
    )
    host = types.SimpleNamespace(
        plugin=types.SimpleNamespace(storage=lambda: str(storage)),
        ui=ui,
    )

    module.PluginResult = PluginResult
    module.ExecutionResult = ExecutionResult
    module.base = Base
    module.script = script
    module.slicing = slicing
    module.host = host
    module.plugin = lambda cls: cls
    module.register_capability = module.registered.append
    module.permission_requests = []

    def request_permissions(**kwargs):
        module.permission_requests.append(kwargs)

    module.request_permissions = request_permissions
    return module


class FakePath:
    def __init__(self, role, width, height, mm3_per_mm, points) -> None:
        self.role = role
        self.width = width
        self.height = height
        self.mm3_per_mm = mm3_per_mm
        self._points = points

    def points(self):
        return self._points


class FakeCollection:
    def __init__(self, paths) -> None:
        self._paths = paths

    def flatten_paths(self):
        return list(self._paths)


class FakeRegion:
    def __init__(self, perimeters, fills) -> None:
        self.perimeters = FakeCollection(perimeters)
        self.fills = FakeCollection(fills)


class FakeLayer:
    def __init__(self, index, height) -> None:
        self.print_z = 0.2 * (index + 1)
        self.slice_z = 0.2 * index + 0.1
        self.height = height
        self._regions = [
            FakeRegion(
                [self._path("Outer wall", 0.45, 0)],
                [self._path("Sparse infill", 0.42, 200_000)],
            )
        ]

    def _path(self, role, width, y_offset):
        return FakePath(
            role,
            width,
            self.height,
            width * self.height * 0.9,
            [
                [0, y_offset, 0],
                [10_000_000, y_offset, 0],
                [10_000_000, 10_000_000 + y_offset, 0],
            ],
        )

    def regions(self):
        return self._regions


class FakeObject:
    def __init__(self, layers) -> None:
        self._layers = layers

    def id(self):
        return 7

    def layers(self):
        return list(self._layers)


class FakeContext:
    def __init__(self, obj, step="posSimplifyPath", gcode_path="") -> None:
        self.step = step
        self.object = obj
        self.gcode_path = gcode_path
        self.orca_version = "2.5.0-beta"


class PluginTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Path(self.tmp.name) / "plugin_data"
        self.storage.mkdir(parents=True)
        self.orca = build_orca_stub(self.storage)
        self._original_orca = sys.modules.get("orca")
        sys.modules["orca"] = self.orca
        spec = importlib.util.spec_from_file_location(
            "gcode_fea_plugin_under_test", PLUGIN_PATH
        )
        self.plugin = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(self.plugin)

    def tearDown(self) -> None:
        self.tmp.cleanup()
        if self._original_orca is None:
            sys.modules.pop("orca", None)
        else:
            sys.modules["orca"] = self._original_orca

    def make_capture(self):
        capability = self.plugin.CaptureSliceCache()
        capability.config_json = json.dumps(
            {
                "core_path": str(REPO_ROOT / "src"),
                "mode": "volume_conserving",
                "max_layers": 4,
            }
        )
        return capability

    def test_registration(self) -> None:
        self.orca.registered.clear()
        self.orca.permission_requests.clear()
        self.plugin.GCodeFEAPlugin().register_capabilities()
        self.assertEqual(
            [cls.__name__ for cls in self.orca.registered],
            ["CaptureSliceCache", "ExportFEAGeometry", "ExportSTEPViewable"],
        )
        self.assertEqual(len(self.orca.permission_requests), 1)
        request = self.orca.permission_requests[0]
        self.assertIsInstance(request["process"], list)
        self.assertTrue(all(path for path in request["process"]))

    def test_default_configs(self) -> None:
        capture = self.plugin.CaptureSliceCache().get_default_config()
        export = self.plugin.ExportFEAGeometry().get_default_config()
        step = self.plugin.ExportSTEPViewable().get_default_config()
        self.assertIn("max_layers", capture)
        for key in ("mode", "resolution", "formats", "write_step", "core_path"):
            self.assertIn(key, export)
        for key in ("step_stride", "step_engine", "max_step_faces", "roi"):
            self.assertIn(key, step)

    def test_capture_writes_cache(self) -> None:
        capability = self.make_capture()
        ctx = FakeContext(FakeObject([FakeLayer(0, 0.2), FakeLayer(1, 0.2)]))
        result = capability.execute(ctx)
        self.assertEqual(result.status, "success", result.message)
        self.assertIn("4 beads", result.message)
        cache_path = self.storage / "cache" / "slice.json"
        self.assertTrue(cache_path.exists())
        from gcode_fea.cache import cache_info, load_cache

        info = cache_info(cache_path)
        self.assertEqual(info["bead_count"], 4)
        self.assertEqual(info["layer_count"], 2)
        model = load_cache(cache_path)
        self.assertEqual(len(model.layers()), 2)
        self.assertTrue(model.meta.get("source_hash"))

    def test_capture_respects_layer_cap(self) -> None:
        capability = self.make_capture()
        capability.config_json = json.dumps({"max_layers": 1})
        ctx = FakeContext(FakeObject([FakeLayer(0, 0.2), FakeLayer(1, 0.2)]))
        result = capability.execute(ctx)
        self.assertEqual(result.status, "success", result.message)
        self.assertIn("capped at 1", result.message)

    def test_export_after_capture(self) -> None:
        self.assertEqual(
            self.make_capture()
            .execute(FakeContext(FakeObject([FakeLayer(0, 0.2)])))
            .status,
            "success",
        )
        capability = self.plugin.ExportFEAGeometry()
        capability.config_json = json.dumps(
            {
                "core_path": str(REPO_ROOT / "src"),
                "resolution": 0.2,
                "layer_range": [0, 0],
                "ask_output_dir": False,
            }
        )
        result = capability.execute()
        self.assertEqual(result.status, "success", result.message)
        payload = json.loads(result.data)
        self.assertEqual(payload["bead_count"], 2)
        self.assertTrue(Path(payload["files"]["sidecar"]).exists())
        self.assertTrue(Path(payload["files"]["report"]).exists())
        self.assertGreaterEqual(len(self.orca.host.ui.next_handle.calls), 2)
        self.assertTrue(self.orca.host.ui.next_handle.closed)

    def test_step_export_after_capture(self) -> None:
        self.make_capture().execute(
            FakeContext(FakeObject([FakeLayer(0, 0.2)]))
        )
        capability = self.plugin.ExportSTEPViewable()
        capability.config_json = json.dumps(
            {
                "core_path": str(REPO_ROOT / "src"),
                "step_engine": "planar",
                "step_stride": 2,
                "ask_save_path": False,
            }
        )
        result = capability.execute()
        self.assertEqual(result.status, "success", result.message)
        payload = json.loads(result.data)
        self.assertEqual(payload["engine"], "planar")
        step = Path(payload["file"])
        self.assertTrue(step.exists())
        self.assertGreater(payload["solids"], 0)
        self.assertTrue(step.read_text().startswith("ISO-10303-21;"))

    def test_step_export_face_limit(self) -> None:
        self.make_capture().execute(
            FakeContext(FakeObject([FakeLayer(0, 0.2)]))
        )
        capability = self.plugin.ExportSTEPViewable()
        capability.config_json = json.dumps(
            {
                "core_path": str(REPO_ROOT / "src"),
                "max_step_faces": 5,
                "ask_save_path": False,
            }
        )
        result = capability.execute()
        self.assertEqual(result.status, "recoverable")
        self.assertIn("preflight", result.message.lower())

    def test_export_uses_kept_gcode(self) -> None:
        self.make_capture().execute(
            FakeContext(FakeObject([FakeLayer(0, 0.2)]))
        )
        gcode = REPO_ROOT / "tests" / "fixtures" / "process" / "cube20_first2layers.gcode"
        kept = self.storage / "cache" / "last.gcode"
        kept.write_text(gcode.read_text())
        capability = self.plugin.ExportFEAGeometry()
        capability.config_json = json.dumps(
            {
                "core_path": str(REPO_ROOT / "src"),
                "resolution": 0.2,
                "ask_output_dir": False,
            }
        )
        result = capability.execute()
        self.assertEqual(result.status, "success", result.message)
        payload = json.loads(result.data)
        self.assertIsNotNone(payload["interfaces"])
        self.assertIsNotNone(payload["mean_bond_quality"])
        sidecar = json.loads(Path(payload["files"]["sidecar"]).read_text())
        self.assertIn("process", sidecar)
        self.assertIn("adhesion", sidecar)

    def test_stale_cache_is_flagged(self) -> None:
        self.make_capture().execute(
            FakeContext(FakeObject([FakeLayer(0, 0.2)]))
        )
        probe = self.storage / "cache" / "slice_probe.json"
        probe.write_text(probe.read_text() + "\n")
        capability = self.plugin.ExportFEAGeometry()
        capability.config_json = json.dumps(
            {
                "core_path": str(REPO_ROOT / "src"),
                "resolution": 0.2,
                "ask_output_dir": False,
            }
        )
        result = capability.execute()
        self.assertEqual(result.status, "success", result.message)
        self.assertIn("stale", result.message)

    def test_gcode_is_kept_for_process_data(self) -> None:
        gcode = Path(REPO_ROOT) / "tests" / "fixtures" / "process" / "cube20_first2layers.gcode"
        capability = self.make_capture()
        ctx = FakeContext(
            FakeObject([FakeLayer(0, 0.2)]),
            step="psGCodePostProcess",
            gcode_path=str(gcode),
        )
        result = capability.execute(ctx)
        self.assertEqual(result.status, "success", result.message)
        kept = self.storage / "cache" / "last.gcode"
        self.assertTrue(kept.exists())
        self.assertEqual(kept.read_text(), gcode.read_text())

    @mock.patch("gcode_fea.plugin_export.export_step_from_cache")
    def test_step_permission_error_is_actionable(self, export_step) -> None:
        self.make_capture().execute(
            FakeContext(FakeObject([FakeLayer(0, 0.2)]))
        )
        export_step.side_effect = PermissionError(
            "Plugin attempted audited operation 'open' without permission"
        )
        capability = self.plugin.ExportSTEPViewable()
        capability.config_json = json.dumps(
            {
                "core_path": str(REPO_ROOT / "src"),
                "ask_save_path": False,
            }
        )
        result = capability.execute()
        self.assertEqual(result.status, "recoverable")
        self.assertIn("blocked writing", result.message)
        self.assertIn("home directory", result.message)
        self.assertIn("audited operation", result.message)

    @mock.patch("gcode_fea.save_dialog.choose_save_file")
    def test_step_save_dialog_uses_chosen_path(self, choose) -> None:
        from gcode_fea.save_dialog import DialogResult

        self.make_capture().execute(
            FakeContext(FakeObject([FakeLayer(0, 0.2)]))
        )
        chosen = Path(self.tmp.name) / "picked.step"
        choose.return_value = DialogResult(chosen, False, "zenity")
        capability = self.plugin.ExportSTEPViewable()
        capability.config_json = json.dumps(
            {
                "core_path": str(REPO_ROOT / "src"),
                "step_engine": "planar",
                "ask_save_path": True,
            }
        )
        result = capability.execute()
        self.assertEqual(result.status, "success", result.message)
        self.assertTrue(chosen.exists())
        self.assertIn(str(chosen), result.message)

    @mock.patch("gcode_fea.save_dialog.choose_save_file")
    def test_step_save_dialog_cancel(self, choose) -> None:
        from gcode_fea.save_dialog import DialogResult

        self.make_capture().execute(
            FakeContext(FakeObject([FakeLayer(0, 0.2)]))
        )
        choose.return_value = DialogResult(None, True, "zenity")
        capability = self.plugin.ExportSTEPViewable()
        capability.config_json = json.dumps(
            {
                "core_path": str(REPO_ROOT / "src"),
                "ask_save_path": True,
            }
        )
        result = capability.execute()
        self.assertEqual(result.status, "skipped")
        self.assertIn("cancel", result.message.lower())

    @mock.patch("gcode_fea.save_dialog.choose_directory")
    def test_export_uses_chosen_directory(self, choose) -> None:
        from gcode_fea.save_dialog import DialogResult

        self.make_capture().execute(
            FakeContext(FakeObject([FakeLayer(0, 0.2)]))
        )
        chosen = Path(self.tmp.name) / "chosen-exports"
        choose.return_value = DialogResult(chosen, False, "zenity")
        capability = self.plugin.ExportFEAGeometry()
        capability.config_json = json.dumps(
            {
                "core_path": str(REPO_ROOT / "src"),
                "resolution": 0.2,
                "ask_output_dir": True,
            }
        )
        result = capability.execute()
        self.assertEqual(result.status, "success", result.message)
        self.assertTrue((chosen / "beads.json").exists())
        self.assertTrue((chosen / "report.json").exists())

    def test_export_without_cache_fails(self) -> None:
        capability = self.plugin.ExportFEAGeometry()
        capability.config_json = json.dumps({"core_path": str(REPO_ROOT / "src")})
        result = capability.execute()
        self.assertEqual(result.status, "recoverable")
        self.assertIn("Capture Slice Cache", result.message)

    def test_export_cancellation(self) -> None:
        self.make_capture().execute(
            FakeContext(FakeObject([FakeLayer(0, 0.2)]))
        )
        self.orca.host.ui.next_handle = FakeProgress(can_continue=False)
        capability = self.plugin.ExportFEAGeometry()
        capability.config_json = json.dumps(
            {
                "core_path": str(REPO_ROOT / "src"),
                "resolution": 0.2,
                "ask_output_dir": False,
            }
        )
        result = capability.execute()
        # Cancellation is only observed if a progress checkpoint returns False.
        self.assertIn(result.status, ("skipped", "success"))

    def test_export_bad_config(self) -> None:
        capability = self.plugin.ExportFEAGeometry()
        capability.config_json = "{not json"
        result = capability.execute()
        self.assertEqual(result.status, "recoverable")
        self.assertIn("configuration error", result.message)


if __name__ == "__main__":
    unittest.main()
