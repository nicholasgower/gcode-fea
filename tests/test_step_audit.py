from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "helper_scripts"))

import step_audit  # noqa: E402
from gcode_fea.cache import load_probe  # noqa: E402
from gcode_fea.step_planar import write_step_planar  # noqa: E402

PROBE = REPO_ROOT / "tests" / "fixtures" / "phase0" / "phase0_probe_obj102.json"


def _gmsh_available() -> bool:
    try:
        import gmsh  # noqa: F401
    except Exception:
        return False
    return True


HAVE_GMSH = _gmsh_available()

OPEN_SHELL_STEP = """ISO-10303-21;
HEADER;
ENDSEC;
DATA;
#1 = CLOSED_SHELL('',(#10));
#10 = ADVANCED_FACE('',(#11),#20,.T.);
#11 = FACE_OUTER_BOUND('',#12,.T.);
#12 = EDGE_LOOP('',(#13,#14,#15));
#13 = ORIENTED_EDGE('',*,*,#30,.T.);
#14 = ORIENTED_EDGE('',*,*,#31,.T.);
#15 = ORIENTED_EDGE('',*,*,#32,.T.);
#20 = PLANE('',#21);
#21 = AXIS2_PLACEMENT_3D('',#22,#23,#24);
#30 = EDGE_CURVE('',#33,#34,#35,.T.);
#31 = EDGE_CURVE('',#34,#36,#35,.T.);
#32 = EDGE_CURVE('',#36,#33,#35,.T.);
#40 = MANIFOLD_SOLID_BREP('',#1);
ENDSEC;
END-ISO-10303-21;
"""


class StepAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.model = load_probe(PROBE)

    def test_planar_step_is_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "beads.step"
            result = write_step_planar(self.model, path, tolerance=0.005)
            report = step_audit.audit_step(path.read_text())
            self.assertTrue(report["closed"])
            self.assertEqual(report["open_edges"], 0)
            self.assertEqual(report["missing_references"], 0)
            self.assertEqual(report["solids"], 3)
            self.assertEqual(report["solids"], result["solids"])
            self.assertEqual(report["faces"], result["faces"])

    def test_open_shell_is_detected(self) -> None:
        report = step_audit.audit_step(OPEN_SHELL_STEP)
        self.assertFalse(report["closed"])
        self.assertEqual(report["solids"], 1)
        self.assertEqual(len(report["open_shells"]), 1)
        self.assertEqual(report["open_edges"], 3)

    def test_expect_one_body_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "beads.step"
            write_step_planar(self.model, path, tolerance=0.005)
            self.assertEqual(step_audit.main([str(path)]), 0)
            self.assertEqual(
                step_audit.main([str(path), "--expect-one-body"]), 1
            )

    def test_json_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "beads.step"
            write_step_planar(self.model, path, tolerance=0.005)
            report_path = Path(tmp) / "audit.json"
            self.assertEqual(
                step_audit.main([str(path), "--json", str(report_path)]), 0
            )
            self.assertTrue(report_path.exists())
            self.assertIn('"closed": true', report_path.read_text())

    def test_missing_file_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                step_audit.main([str(Path(tmp) / "missing.step")]), 2
            )

    @unittest.skipUnless(HAVE_GMSH, "gmsh is not installed or could not be loaded")
    def test_occt_solid_count_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "beads.step"
            write_step_planar(self.model, path, tolerance=0.005)
            report = step_audit.occt_report(path)
            self.assertEqual(report["solids"], 3)
            self.assertGreater(report["volume"], 0.0)


if __name__ == "__main__":
    unittest.main()
