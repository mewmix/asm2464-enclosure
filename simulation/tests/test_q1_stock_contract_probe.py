"""Model-only probe of the exact-stock Q1 READ frontier against the Rev-A twin.

This intentionally does not change authoritative ASM semantics. It asks three
narrow questions:

1. Does the stock firmware selector B254=0x03 currently reach the imported
   legacy Q1 consumer? (It should not.)
2. If the legacy Q1 helper is invoked directly with the exact-stock READ PRP1
   0x00200000, does it transfer the payload? (It should not.)
3. Does the same helper still transfer when fed its historical synthetic PRP1
   0x00820400? (It should.)

A pass therefore means the twin reproduces the known semantic split; it is not
physical evidence and does not authorize wiring 0x03 to the old helper.
"""
import unittest

from scripts.stock_read10_boundary_soak import ready


def stage_read(cpu, prp1):
    sqe = bytearray(64)
    sqe[0] = 0x02
    sqe[2:4] = (0).to_bytes(2, "little")      # CID 0
    sqe[4:8] = (1).to_bytes(4, "little")      # NSID 1
    sqe[24:32] = int(prp1).to_bytes(8, "little")
    sqe[32:40] = (0).to_bytes(8, "little")    # PRP2 0
    sqe[40:48] = (0).to_bytes(8, "little")    # SLBA 0
    sqe[48:50] = (0).to_bytes(2, "little")    # NLB=0 => one block
    cpu.memory.xdata[0xA000:0xA040] = sqe
    cpu.hw.regs[0xB251] = 1                    # exact-stock next producer index
    return bytes(sqe)


class StockQ1ContractProbe(unittest.TestCase):
    def setUp(self):
        self.payload = bytes(((17 * i + 3) & 0xFF) for i in range(512))

    def test_selector_03_does_not_reach_legacy_q1_consumer(self):
        _, cpu = ready()
        hw = cpu.hw
        hw.nvme_namespace_data[0] = self.payload
        stage_read(cpu, 0x00200000)
        before_history = len(hw.stock_nvme_io_submission_history)
        before_data = bytes(cpu.memory.xdata[0xA400:0xA600])

        hw.write(0xB254, 0x03)

        self.assertEqual(len(hw.stock_nvme_io_submission_history), before_history)
        self.assertEqual(bytes(cpu.memory.xdata[0xA400:0xA600]), before_data)

    def test_forcing_legacy_helper_with_stock_prp_does_not_transfer(self):
        _, cpu = ready()
        hw = cpu.hw
        hw.nvme_namespace_data[0] = self.payload
        stage_read(cpu, 0x00200000)
        before_data = bytes(cpu.memory.xdata[0xA400:0xA600])

        hw._stock_nvme_io_sq_doorbell()

        self.assertEqual(bytes(cpu.memory.xdata[0xA400:0xA600]), before_data)

    def test_legacy_helper_transfers_only_with_historical_synthetic_prp(self):
        _, cpu = ready()
        hw = cpu.hw
        hw.nvme_namespace_data[0] = self.payload
        stage_read(cpu, 0x00820400)

        hw._stock_nvme_io_sq_doorbell()

        self.assertEqual(bytes(cpu.memory.xdata[0xA400:0xA600]), self.payload)


if __name__ == "__main__":
    unittest.main()
