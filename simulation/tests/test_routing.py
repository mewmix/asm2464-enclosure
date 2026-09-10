import copy
import unittest

from simulation.board.routing import chord_mm, load, load_reference_xref, validate


class RoutingContractTests(unittest.TestCase):
    def setUp(self):
        self.contract = load()
        self.xref = load_reference_xref()

    def test_current_contract_is_conservative_and_valid(self):
        result = validate(self.contract, self.xref)
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["reference_pair_count"], 22)
        self.assertEqual(set(result["reference_topology_domains"]), {"USB2", "USB4", "PCIE"})
        self.assertGreater(result["placement_chord_lower_bounds_mm"]["usb_c_to_asm"], 0)
        self.assertGreater(result["placement_chord_lower_bounds_mm"]["asm_to_m2"], 0)
        self.assertGreater(result["placement_chord_lower_bounds_mm"]["asm_to_flash"], 0)

    def test_reference_stackup_is_evidence_not_our_route_geometry(self):
        ref = self.contract["reference_manufacturing"]
        self.assertEqual(ref["layers"], 4)
        self.assertEqual(ref["board_thickness_mm"], 1.6)
        self.assertEqual(ref["stackup_code"], "04161H02-1080")
        self.assertEqual(ref["reference_diff_impedance_ohms_approx"], 85)
        self.assertEqual(ref["transfer_status"], "REFERENCE_ONLY_NOT_OUR_FAB_STACKUP")
        self.assertEqual(self.contract["reference_high_speed_topology"]["pair_count"], 22)
        for domain in ("USB4", "PCIE"):
            d = self.contract["high_speed_domains"][domain]
            self.assertEqual(d["topology_status"], "REFERENCE_TOPOLOGY_RECOVERED_REV_A_NETS_PENDING")
            self.assertEqual(d["reference_profile"]["diff_impedance_ohms_approx"], 85)
            self.assertIsNone(d["actual_metrics"]["target_diff_ohms"])
            self.assertEqual(d["geometry_status"], "UNROUTED")

    def test_source_native_reference_pairs_are_complete(self):
        self.assertEqual(self.xref["pair_membership_evidence"], "ALTIUM_DIFFERENTIALPAIRS6")
        names = {p["name"] for p in self.xref["pairs"]}
        self.assertEqual(len(names), 22)
        self.assertIn("UD", names)
        self.assertIn("RefCLK", names)
        for lane in range(4):
            self.assertIn(f"PET{lane}", names)
            self.assertIn(f"PET{lane}U", names)
            self.assertIn(f"PER{lane}", names)

    def test_chord_is_only_a_geometric_lower_bound(self):
        d = chord_mm(self.contract, "ASM2464PD", "SPI_FLASH")
        self.assertGreater(d, 0)
        self.assertIn("lower bounds", validate(self.contract, self.xref)["note"])

    def test_rejects_wrong_authoritative_asm_ball(self):
        c = copy.deepcopy(self.contract)
        next(r for r in c["debug_routes"] if r["name"] == "ASM_SPI_CLK")["asm_ball"] = "A6"
        result = validate(c, self.xref)
        self.assertFalse(result["passed"])
        self.assertTrue(any("expected ASM ball A5" in e for e in result["errors"]))

    def test_rejects_programmer_on_controller_side_of_spi_isolation(self):
        c = copy.deepcopy(self.contract)
        next(r for r in c["debug_routes"] if r["name"] == "ASM_SPI_CS_N")["programmer_attachment"] = "CONTROLLER_SIDE"
        result = validate(c, self.xref)
        self.assertFalse(result["passed"])
        self.assertTrue(any("flash side of isolation" in e for e in result["errors"]))

    def test_rejects_claimed_metrics_while_rev_a_geometry_is_unrouted(self):
        c = copy.deepcopy(self.contract)
        c["high_speed_domains"]["USB4"]["actual_metrics"]["trace_length_mm"] = 12.34
        result = validate(c, self.xref)
        self.assertFalse(result["passed"])
        self.assertTrue(any("claimed route metrics" in e for e in result["errors"]))

    def test_rejects_claimed_routing_when_topology_is_blocked(self):
        c = copy.deepcopy(self.contract)
        c["high_speed_domains"]["PCIE"]["topology_status"] = "BLOCKED_TEST_TOPOLOGY"
        c["high_speed_domains"]["PCIE"]["geometry_status"] = "ROUTED"
        result = validate(c, self.xref)
        self.assertFalse(result["passed"])
        self.assertTrue(any("cannot claim routed geometry" in e for e in result["errors"]))

    def test_rejects_reference_pair_loss(self):
        xref = copy.deepcopy(self.xref)
        xref["pairs"] = [p for p in xref["pairs"] if p["name"] != "RefCLK"]
        result = validate(self.contract, xref)
        self.assertFalse(result["passed"])
        self.assertTrue(any("missing differential pairs" in e for e in result["errors"]))

    def test_rejects_reference_topology_domain_drift(self):
        xref = copy.deepcopy(self.xref)
        usb4 = next(d for d in xref["reference_topology"] if d["domain"] == "USB4")
        usb4["logical_link"] = "USBC1<->CN1"
        result = validate(self.contract, xref)
        self.assertFalse(result["passed"])
        self.assertTrue(any("USB4 topology" in e for e in result["errors"]))

    def test_service_header_ground_and_vref_contract(self):
        c = copy.deepcopy(self.contract)
        c["service_connector"]["pins"]["2"] = "3V3_TARGET_POWER"
        result = validate(c, self.xref)
        self.assertFalse(result["passed"])
        self.assertTrue(any("VREF_SENSE" in e for e in result["errors"]))


if __name__ == "__main__":
    unittest.main()
