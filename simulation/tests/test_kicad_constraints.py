import copy
import unittest

from simulation.board.kicad_constraints import load, validate


class KicadConstraintScaffoldTests(unittest.TestCase):
    def setUp(self):
        self.scaffold = load()

    def test_current_scaffold_is_conservative_and_valid(self):
        result = validate(self.scaffold)
        self.assertTrue(result["passed"], result)
        self.assertFalse(result["stackup_locked"])
        self.assertEqual(result["planned_pair_count"], 14)
        self.assertEqual(result["kicad_emit_status"], "BLOCKED")

    def test_reference_widths_are_not_production_rules(self):
        self.assertEqual(
            self.scaffold["reference_geometry_guard"]["status"],
            "REFERENCE_ONLY_NOT_REV_A_TARGETS",
        )
        for domain in ("usb4", "pcie", "usb2"):
            geom = self.scaffold["production_geometry"][domain]
            self.assertIsNone(geom["trace_width_mm"])
            self.assertIsNone(geom["pair_gap_mm"])
            self.assertIsNone(geom["target_diff_ohms"])

    def test_rejects_copying_leaves_width_before_stackup_lock(self):
        s = copy.deepcopy(self.scaffold)
        s["production_geometry"]["usb4"]["trace_width_mm"] = 0.110744
        result = validate(s)
        self.assertFalse(result["passed"])
        self.assertTrue(any("before fabricator stackup is locked" in e for e in result["errors"]))
        self.assertTrue(any("copied a Leaves232 reference width" in e for e in result["errors"]))

    def test_rejects_numeric_impedance_before_stackup_lock(self):
        s = copy.deepcopy(self.scaffold)
        s["production_geometry"]["pcie"]["target_diff_ohms"] = 85
        result = validate(s)
        self.assertFalse(result["passed"])
        self.assertTrue(any("pcie has production routing values" in e for e in result["errors"]))

    def test_rejects_net_names_before_schematic_lock(self):
        s = copy.deepcopy(self.scaffold)
        pair = next(p for p in s["planned_pairs"] if p["name"] == "PCIE_RX0")
        pair["positive_net"] = "PCIE_RX0_P"
        pair["negative_net"] = "PCIE_RX0_N"
        result = validate(s)
        self.assertFalse(result["passed"])
        self.assertTrue(any("before schematic connectivity is locked" in e for e in result["errors"]))

    def test_rejects_kicad_emit_while_stackup_unlocked(self):
        s = copy.deepcopy(self.scaffold)
        s["kicad_emit"]["status"] = "READY"
        result = validate(s)
        self.assertFalse(result["passed"])
        self.assertTrue(any("output generation must remain blocked" in e for e in result["errors"]))


if __name__ == "__main__":
    unittest.main()
