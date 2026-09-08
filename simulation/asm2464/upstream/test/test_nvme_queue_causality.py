"""Causal model contracts, not stock or compiled-firmware qualification.

All enabling state is earned by configuration TLPs, MMIO, the inherited ASIC
seed, and Admin commands. Faults act on the same HardwareState instance.
"""
import unittest
from unittest.mock import patch

from emulate.emu import Emulator


SEED = (
    (0xB264, 8), (0xB265, 0), (0xB266, 8), (0xB267, 8),
    (0xB26C, 8), (0xB26D, 0x20), (0xB26E, 8), (0xB26F, 0x28),
    (0xB250, 0), (0xB251, 0), (0xCEF3, 8), (0xCEF2, 0x80),
    (0xCEF0, 0), (0xCEEF, 0), (0xC807, 4), (0xB281, 0x10),
)


def tlp(hw, fmt, address, value):
    for addr, byte in ((0xB213, 1), (0xB216, 0x20), (0xB217, 0x0F),
                       (0xB210, fmt)):
        hw.write(addr, byte)
    for i, byte in enumerate(address.to_bytes(4, "big")):
        hw.write(0xB218 + i, byte)
    for i, byte in enumerate(value.to_bytes(4, "big")):
        hw.write(0xB220 + i, byte)
    hw.write(0xB296, 0xFF)
    hw.write(0xB254, 0x80)
    assert hw.regs[0xB22A] == 0, "fixture TLP rejected"


def configure_route(hw):
    tlp(hw, 0x44, 0x18, 0x00010100)
    tlp(hw, 0x44, 0x20, 0x00F00000)
    tlp(hw, 0x44, 4, 0x406)
    tlp(hw, 0x45, 0x01000010, 0x00D00000)
    tlp(hw, 0x45, 0x01000004, 0x406)


def controller(hw):
    for offset, value in ((0x14, 0), (0x24, 0x30003),
                          (0x28, 0x200000), (0x2C, 0),
                          (0x30, 0x820000), (0x34, 0),
                          (0x14, 0x460001)):
        tlp(hw, 0x40, hw.nvme_bar0 + offset, value)


def submit(emu, qid=0, opcode=6, cid=1, prp=None, cdw10=None, cdw11=0):
    hw = emu.hw
    sqe = bytearray(64)
    sqe[0] = opcode
    sqe[2:4] = cid.to_bytes(2, "little")
    sqe[4:8] = (qid if qid else 0).to_bytes(4, "little")
    if prp is None:
        prp = 0x820400 if qid else 0x200000
    if cdw10 is None:
        cdw10 = 0 if qid else 1
    sqe[24:32] = prp.to_bytes(8, "little")
    sqe[40:44] = cdw10.to_bytes(4, "little")
    sqe[44:48] = cdw11.to_bytes(4, "little")
    emu.memory.xdata[0xA000:0xA040] = sqe
    hw.write(0xCEF2, 0x80)
    hw.write(0xB294, 0x30)
    hw.write(0xB296, 0xFF)
    hw.write(0xB251, 1)
    hw.write(0xB254, qid + 1)


def create_q1(emu):
    submit(emu, opcode=5, prp=0x820200, cdw10=0x30001, cdw11=1)
    submit(emu, opcode=1, prp=0x200200, cdw10=0x30001, cdw11=0x10001)


def ready_emulator():
    e = Emulator(log_uart=False)
    configure_route(e.hw)
    for addr, value in SEED:
        e.hw.write(addr, value)
    controller(e.hw)
    create_q1(e)
    # Confirm the fixture actually earns both completion paths.
    submit(e)
    assert e.hw.regs[0xCEF2] & 0x80
    submit(e, qid=1, opcode=2)
    assert e.hw.regs[0xB294] & 0x10
    return e


def data_snapshot(emu):
    return (bytes(emu.memory.xdata[0xA400:0xA800]),
            bytes(emu.memory.xdata[0xF000:0xF400]),
            bytes(emu.memory.xdata[0xB800:0xB880]),
            dict(emu.hw.nvme_namespace_data))


class QueueCausalityTests(unittest.TestCase):
    def test_mutating_mapping_requires_admin_reconstruction(self):
        emu = ready_emulator()
        emu.hw.write(0xB264, 0)
        for addr, value in SEED:
            emu.hw.write(addr, value)
        submit(emu)
        self.assertTrue(emu.hw.regs[0xB296] & 1)
        controller(emu.hw)
        submit(emu)
        self.assertTrue(emu.hw.regs[0xCEF2] & 0x80)

    def test_mutation_replacing_ancestry_with_sticky_owner_is_detected(self):
        emu = ready_emulator()
        old = emu.hw._stock_nvme_owner(0)
        emu.hw.reset_pcie_configuration()
        with patch.object(emu.hw, "_stock_nvme_owner", return_value=old):
            submit(emu)
            with self.assertRaises(AssertionError):
                self.assertTrue(emu.hw.regs[0xB296] & 1)

    def test_mutation_accepting_old_completion_owner_is_detected(self):
        emu = ready_emulator()
        old = emu.hw._stock_nvme_owner(0)
        emu.hw.stock_nvme_defer_completions = True
        submit(emu)
        controller(emu.hw)
        emu.hw.write(0xCEF2, 0x80)
        with patch.object(emu.hw, "_stock_nvme_owner", return_value=old):
            emu.hw.complete_deferred_nvme()
            with self.assertRaises(AssertionError):
                self.assertFalse(emu.hw.regs[0xCEF2] & 0x80)

    def test_seed_alone_does_not_grant_a_queue(self):
        emu = Emulator(log_uart=False)
        for addr, value in SEED:
            emu.hw.write(addr, value)
        self.assertTrue(emu.hw.stock_nvme_queue_enabled)
        submit(emu)
        self.assertEqual(emu.hw.stock_nvme_submission_history, [])
        self.assertTrue(emu.hw.regs[0xB296] & 1)

    def test_each_admin_register_is_required(self):
        required = ((0x24, 0x30003), (0x28, 0x200000), (0x2C, 0),
                    (0x30, 0x820000), (0x34, 0))
        for omitted, _ in required:
            with self.subTest(omitted=hex(omitted)):
                emu = ready_emulator()
                tlp(emu.hw, 0x40, emu.hw.nvme_bar0 + 0x14, 0)
                for offset, value in required:
                    if offset != omitted:
                        tlp(emu.hw, 0x40, emu.hw.nvme_bar0 + offset, value)
                tlp(emu.hw, 0x40, emu.hw.nvme_bar0 + 0x14, 0x460001)
                submit(emu)
                self.assertTrue(emu.hw.regs[0xB296] & 1)

    def test_each_routing_prerequisite_is_required(self):
        faults = (
            (0x44, 4, 0x400, 0x406),   # bridge MSE + BME
            (0x44, 4, 0x402, 0x406),   # bridge BME alone
            (0x44, 0x18, 0x00020100, 0x00010100),  # routing identity
            (0x44, 0x20, 0, 0x00F00000),  # bridge window
            (0x45, 0x01000004, 0x400, 0x406),
            (0x45, 0x01000004, 0x402, 0x406),
            (0x45, 0x01000010, 0, 0x00D00000),
            (0x45, 0x01000010, 0xFFFFFFFF, 0x00D00000),
        )
        for fmt, address, bad, restored in faults:
            for qid in (0, 1):
                with self.subTest(address=hex(address), bad=hex(bad), qid=qid):
                    emu = ready_emulator()
                    old = emu.hw.pcie_route_generation
                    tlp(emu.hw, fmt, address, bad)
                    # Re-enable before any queue handler samples readiness.
                    tlp(emu.hw, fmt, address, restored)
                    self.assertGreater(emu.hw.pcie_route_generation, old)
                    before = data_snapshot(emu)
                    submit(emu, qid=qid, opcode=2 if qid else 6)
                    self.assertEqual(data_snapshot(emu), before)
                    self.assertTrue(emu.hw.regs[0xB296] & 1)

    def test_legacy_pio_opt_out_does_not_bypass_queue_ancestry(self):
        emu = ready_emulator()
        emu.hw.pcie_routing_gate_enforced = False
        emu.hw.reset_pcie_configuration()
        submit(emu)
        self.assertTrue(emu.hw.regs[0xB296] & 1)

    def test_controller_restart_preserves_route_but_revokes_q1(self):
        emu = ready_emulator()
        old = emu.hw._stock_nvme_owner(0)
        controller(emu.hw)
        new = emu.hw._stock_nvme_owner(0)
        self.assertEqual(new[0], old[0])
        self.assertGreater(new[1], old[1])
        self.assertGreater(new[2], old[2])
        self.assertIsNone(emu.hw._stock_nvme_owner(1))

    def test_usb_msc_reset_retains_downstream_ancestry(self):
        emu = ready_emulator()
        old = emu.hw._stock_nvme_owner(1)
        emu.hw.reset_stock_msc_generation()
        self.assertEqual(emu.hw._stock_nvme_owner(1), old)
        submit(emu)
        self.assertTrue(emu.hw.regs[0xCEF2] & 0x80)
        submit(emu, qid=1, opcode=2)
        self.assertTrue(emu.hw.regs[0xB294] & 0x10)

    def test_pcie_reset_sources_revoke_even_with_ltssm_already_down(self):
        for addr, value in ((0xB401, 1), (0xB480, 0), (0xC656, 0)):
            with self.subTest(addr=hex(addr)):
                emu = ready_emulator()
                emu.hw.write(addr, value)
                self.assertIsNone(emu.hw._stock_nvme_owner(0))
                self.assertIsNone(emu.hw._stock_nvme_owner(1))
                configure_route(emu.hw)
                submit(emu)
                self.assertTrue(emu.hw.regs[0xB296] & 1)

    def test_q1_timeout_does_not_revoke_model_q0(self):
        # Firmware R4 is still open; this tests the independent device model.
        emu = ready_emulator()
        old = emu.hw._stock_nvme_owner(0)
        emu.hw.stock_nvme_io_completion_timeout = True
        submit(emu, qid=1, opcode=2)
        self.assertFalse(emu.hw.regs[0xB294] & 0x10)
        submit(emu)
        self.assertTrue(emu.hw.regs[0xCEF2] & 0x80)
        self.assertEqual(emu.hw._stock_nvme_owner(0), old)

    def test_old_dma_owner_is_rejected_after_new_controller(self):
        emu = ready_emulator()
        old = emu.hw._stock_nvme_owner(0)
        controller(emu.hw)
        before = data_snapshot(emu)
        self.assertFalse(emu.hw._stock_nvme_dma_write(0x200000, b"old", old))
        self.assertEqual(data_snapshot(emu), before)

    def test_deferred_cqe_delivery_requires_current_ancestry(self):
        for qid in (0, 1):
            with self.subTest(qid=qid):
                emu = ready_emulator()
                emu.hw.stock_nvme_defer_completions = True
                submit(emu, qid=qid, opcode=2 if qid else 6, cid=0x1234)
                self.assertEqual(len(emu.hw.stock_nvme_pending_completions), 1)
                emu.hw.reset_pcie_configuration()
                configure_route(emu.hw)
                controller(emu.hw)
                emu.hw.stock_nvme_defer_completions = False
                create_q1(emu)
                # Identical CID/phase is deliberate: ancestry must be the gate.
                submit(emu, qid=qid, opcode=2 if qid else 6, cid=0x1234)
                before = data_snapshot(emu)
                tails = (emu.hw.stock_nvme_cq_tail, emu.hw.stock_nvme_io_cq_tail)
                emu.hw.complete_deferred_nvme()
                self.assertEqual(len(emu.hw.stock_nvme_rejected_completions), 1)
                self.assertEqual(data_snapshot(emu), before)
                self.assertEqual((emu.hw.stock_nvme_cq_tail,
                                  emu.hw.stock_nvme_io_cq_tail), tails)

    def test_same_generation_deferred_cqe_still_completes(self):
        for qid in (0, 1):
            with self.subTest(qid=qid):
                emu = ready_emulator()
                emu.hw.stock_nvme_defer_completions = True
                submit(emu, qid=qid, opcode=2 if qid else 6)
                emu.hw.complete_deferred_nvme()
                self.assertEqual(emu.hw.stock_nvme_rejected_completions, [])
                self.assertTrue(emu.hw.regs[0xB294 if qid else 0xCEF2] &
                                (0x10 if qid else 0x80))

    def test_q1_recreation_rejects_old_cqe_without_poisoning_q0(self):
        for delete_cq in (False, True):
            with self.subTest(delete_cq=delete_cq):
                emu = ready_emulator()
                q0 = emu.hw._stock_nvme_owner(0)
                q1 = emu.hw._stock_nvme_owner(1)
                emu.hw.stock_nvme_defer_completions = True
                submit(emu, qid=1, opcode=2)
                emu.hw.stock_nvme_defer_completions = False
                submit(emu, opcode=0, prp=0, cdw10=1)
                if delete_cq:
                    submit(emu, opcode=4, prp=0, cdw10=1)
                    create_q1(emu)
                else:
                    submit(emu, opcode=1, prp=0x200200, cdw10=0x30001, cdw11=0x10001)
                new = emu.hw._stock_nvme_owner(1)
                self.assertEqual(emu.hw._stock_nvme_owner(0), q0)
                self.assertEqual(new[:3], q1[:3])
                self.assertGreater(new[3], q1[3])
                before = data_snapshot(emu)
                emu.hw.complete_deferred_nvme()
                self.assertEqual(data_snapshot(emu), before)
                self.assertEqual(len(emu.hw.stock_nvme_rejected_completions), 1)
                submit(emu, qid=1, opcode=2)
                self.assertTrue(emu.hw.regs[0xB294] & 0x10)

    def test_rejected_creation_does_not_allocate_q1(self):
        emu = ready_emulator()
        controller(emu.hw)
        emu.hw.stock_nvme_force_sc = 2
        create_q1(emu)
        self.assertFalse(emu.hw.nvme_io_cq_created)
        self.assertFalse(emu.hw.nvme_io_sq_created)
        self.assertIsNone(emu.hw._stock_nvme_owner(1))

    def test_revoked_route_cannot_dma_or_complete(self):
        for qid in (0, 1):
            with self.subTest(qid=qid):
                emu = ready_emulator()
                emu.hw.reset_pcie_configuration()
                before = data_snapshot(emu)
                submit(emu, qid=qid, opcode=2 if qid else 6)
                self.assertEqual(data_snapshot(emu), before)
                self.assertTrue(emu.hw.regs[0xB296] & 1)
                self.assertFalse(emu.hw.regs[0xCEF2] & 0x80)
                self.assertFalse(emu.hw.regs[0xB294] & 0x10)

    def test_route_return_alone_cannot_resurrect_queues(self):
        for qid in (0, 1):
            with self.subTest(qid=qid):
                emu = ready_emulator()
                emu.hw.reset_pcie_configuration()
                configure_route(emu.hw)
                before = data_snapshot(emu)
                submit(emu, qid=qid, opcode=2 if qid else 6)
                self.assertEqual(data_snapshot(emu), before)
                self.assertTrue(emu.hw.regs[0xB296] & 1)

    def test_two_bad_generations_require_new_controller_and_q1(self):
        emu = ready_emulator()
        for _ in range(2):
            emu.hw.reset_pcie_configuration()
            configure_route(emu.hw)
            controller(emu.hw)
            submit(emu)
            self.assertTrue(emu.hw.regs[0xCEF2] & 0x80)
            submit(emu, qid=1, opcode=2)
            self.assertTrue(emu.hw.regs[0xB296] & 1)
            create_q1(emu)
            submit(emu, qid=1, opcode=2)
            self.assertTrue(emu.hw.regs[0xB294] & 0x10)

