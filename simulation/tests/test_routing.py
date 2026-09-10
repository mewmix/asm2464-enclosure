import copy
import unittest

from simulation.board.routing import chord_mm, load, validate


class RoutingContractTests(unittest.TestCase):
    def setUp(self):
        self.contract = load()

    def test_current_contract_is_conservative_and_valid(self):
        result = validate(self.contract)
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["errors"], [])
        self.assertGreater(result["placement_chord_lower_bounds_mm"]["usb_c_to_asm"], 0)
        self.assertGreater(result["placement_chord_lower_bounds_mm"]["asm_to_m2"], 0)
        self.assertGreater(result["placement_chord_lower_bounds_mm"]["asm_to_flash"], 0)

    def test_chord_is_only_a_geometric_lower_bound(self):
        d = chord_mm(self.contract, "ASM2464PD", "SPI_FLASH")
        self.assertGreater(d, 0)
        self.assertIn("lower bounds", validate(self.contract)["note"])

    def test_rejects_wrong_authoritative_asm_ball(self):
        c = copy.deepcopy(self.contract)
        next(r for r in c["debug_routes"] if r["name"] == "ASM_SPI_CLK")["asm_ball"] = "A6"
        result = validate(c)
        self.assertFalse(result["passed"])
        self.assertTrue(any("expected ASM ball A5" in e for e in result["errors"]))

    def test_rejects_programmer_on_controller_side_of_spi_isolation(self):
        c = copy.deepcopy(self.contract)
        next(r for r in c["debug_routes"] if r["name"] == "ASM_SPI_CS_N")["programmer_attachment"] = "CONTROLLER_SIDE"
        result = validate(c)
        self.assertFalse(result["passed"])
        self.assertTrue(any("flash side of isolation" in e for e in result["errors"]))

    def test_rejects_high_speed_metrics_before_netmap_and_stackup(self):
        c = copy.deepcopy(self.contract)
        c["high_speed_domains"]["USB4"]["actual_metrics"]["trace_length_mm"] = 12.34
        result = validate(c)
        self.assertFalse(result["passed"])
        self.assertTrue(any("fabricated route metrics" in e for e in result["errors"]))

    def test_rejects_claimed_routing_while_topology_is_blocked(self):
        c = copy.deepcopy(self.contract)
        c["high_speed_domains"]["PCIE"]["geometry_status"] = "ROUTED"
        result = validate(c)
        self.assertFalse(result["passed"])
        self.assertTrue(any("cannot claim routed geometry" in e for e in result["errors"]))

    def test_service_header_ground_and_vref_contract(self):
        c = copy.deepcopy(self.contract)
        c["service_connector"]["pins"]["2"] = "3V3_TARGET_POWER"
        result = validate(c)
        self.assertFalse(result["passed"])
        self.assertTrue(any("VREF_SENSE" in e for e in result["errors"]))


if __name__ == "__main__":
    unittest.main()
