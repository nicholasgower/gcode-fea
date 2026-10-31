from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gcode_fea.contacts import contact_stats, detect_contacts
from gcode_fea.cross_section import solve_cross_section
from gcode_fea.model import Bead, SliceModel


def make_bead(bead_id: str, layer: int, y: float, z: float) -> Bead:
    cs = solve_cross_section(0.08, 0.2, "volume_conserving", 0.45)
    return Bead(
        bead_id,
        0,
        layer,
        z + 0.2,
        "test",
        ((0.0, y, z), (10.0, y, z)),
        0.45,
        0.2,
        0.08,
        cs,
    )


class ContactTests(unittest.TestCase):
    def test_intralayer(self) -> None:
        model = SliceModel([make_bead("a", 0, 0.0, 0.0), make_bead("b", 0, 0.4, 0.0)])
        contacts = detect_contacts(model, resolution=0.1)
        self.assertEqual(len(contacts), 1)
        contact = contacts[0]
        self.assertEqual(contact.kind, "intralayer")
        self.assertGreater(contact.area, 0.0)
        self.assertAlmostEqual(contact.normal[0], 0.0, places=6)
        self.assertAlmostEqual(contact.normal[1], 1.0, places=6)
        stats = contact_stats(contacts, 0.1, 0.45)
        self.assertEqual(stats["intralayer_count"], 1)
        self.assertGreater(stats["intralayer_area"], 0.0)

    def test_no_contact_when_far_apart(self) -> None:
        model = SliceModel([make_bead("a", 0, 0.0, 0.0), make_bead("b", 0, 2.0, 0.0)])
        self.assertEqual(detect_contacts(model, resolution=0.1), [])

    def test_interlayer(self) -> None:
        model = SliceModel([make_bead("a", 0, 0.0, 0.0), make_bead("b", 1, 0.0, 0.2)])
        contacts = detect_contacts(model, resolution=0.1)
        self.assertEqual(len(contacts), 1)
        contact = contacts[0]
        self.assertEqual(contact.kind, "interlayer")
        self.assertGreater(contact.area, 0.5)
        self.assertEqual(contact.normal, (0.0, 0.0, 1.0))
        self.assertAlmostEqual(contact.centroid[2], 0.2, places=6)


if __name__ == "__main__":
    unittest.main()
