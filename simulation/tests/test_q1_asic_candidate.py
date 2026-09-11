"""Model-only tests for the isolated Q1 ASIC contract candidate."""
import unittest

from simulation.experiments.q1_asic_contract import (
    CandidateQ1Engine,
    Completion,
    QueueGeometry,
    ReadCommand,
    advance_index_phase,
    decode_shifted_queue_base,
    ring_interval,
    staging_address,
)


def exact_read(cid=0, prp1=0x00200000, slba=0):
    sqe = bytearray(64)
    sqe[0] = 0x02
    sqe[2:4] = int(cid).to_bytes(2, "little")
    sqe[4:8] = (1).to_bytes(4, "little")
    sqe[24:32] = int(prp1).to_bytes(8, "little")
    sqe[32:40] = (0).to_bytes(8, "little")
    sqe[40:48] = int(slba).to_bytes(8, "little")
    sqe[48:50] = (0).to_bytes(2, "little")
    return bytes(sqe)


class Q1AsicCandidateTests(unittest.TestCase):
    def setUp(self):
        self.regs = {
            0xB264: 0x08, 0xB265: 0x00,
            0xB266: 0x08, 0xB267: 0x08,
            0xB26C: 0x08, 0xB26D: 0x20,
            0xB26E: 0x08, 0xB26F: 0x28,
        }
        self.payload = bytes(((i * 37 + 11) & 0xFF) for i in range(512))

    def test_b26x_candidate_decode_is_generic(self):
        self.assertEqual(decode_shifted_queue_base(0x08, 0x00), 0x00800000)
        self.assertEqual(decode_shifted_queue_base(0x08, 0x08), 0x00808000)
        self.assertEqual(decode_shifted_queue_base(0x08, 0x20), 0x00820000)
        self.assertEqual(decode_shifted_queue_base(0x08, 0x28), 0x00828000)
        g = QueueGeometry.from_b26x(self.regs)
        self.assertEqual((g.sq_base, g.cq_base, g.depth), (0x00820000, 0x00828000, 32))

    def test_b26x_bit_change_changes_candidate_geometry_not_silently_repaired(self):
        changed = dict(self.regs)
        changed[0xB26D] ^= 1
        g = QueueGeometry.from_b26x(changed)
        self.assertNotEqual(g.sq_base, 0x00820000)
        self.assertEqual(g.sq_base, 0x00821000)

    def test_ring_interval_supports_single_batch_wrap_and_zero_distance(self):
        self.assertEqual(ring_interval(0, 1), [0])
        self.assertEqual(ring_interval(4, 7), [4, 5, 6])
        self.assertEqual(ring_interval(31, 0), [31])
        self.assertEqual(ring_interval(8, 8), [])

    def test_firmware_staging_addresses_are_64_byte_stride(self):
        self.assertEqual(staging_address(0, 0), 0xA000)
        self.assertEqual(staging_address(0, 31), 0xA7C0)
        self.assertEqual(staging_address(1, 0), 0xA800)
        self.assertEqual(staging_address(1, 31), 0xAFC0)

    def test_exact_stock_read_decodes_without_legacy_prp_substitution(self):
        cmd = ReadCommand.decode(exact_read())
        self.assertEqual(cmd.opcode, 0x02)
        self.assertEqual(cmd.cid, 0)
        self.assertEqual(cmd.nsid, 1)
        self.assertEqual(cmd.slba, 0)
        self.assertEqual(cmd.blocks, 1)
        self.assertEqual(cmd.prp1, 0x00200000)
        self.assertEqual(cmd.prp2, 0)

    def test_stock_03_candidate_consumes_slot_zero_when_next_index_is_one(self):
        g = QueueGeometry.from_b26x(self.regs)
        e = CandidateQ1Engine(g, media={0: self.payload})
        e.stage(0, 0, exact_read())
        consumed = e.notify_producer(0, 1)
        self.assertEqual(consumed, [0])
        self.assertEqual(e.hw_producer, 1)
        self.assertEqual(e.transport_log[0]["staging_address"], 0xA000)
        self.assertEqual(e.transport_log[0]["candidate_downstream_address"], 0x00820000)

    def test_stock_prp_is_abstract_host_memory_not_implicit_a400(self):
        g = QueueGeometry.from_b26x(self.regs)
        e = CandidateQ1Engine(g, media={0: self.payload})
        e.stage(0, 0, exact_read(prp1=0x00200000))
        e.notify_producer(0, 1)
        self.assertEqual(e.host_memory.read(0x00200000, 512), self.payload)
        self.assertEqual(e.host_memory.read(0x0000A400, 512), bytes(512))
        self.assertEqual(e.host_memory.read(0x00820400, 512), bytes(512))

    def test_multi_entry_producer_notification_consumes_interval(self):
        g = QueueGeometry.from_b26x(self.regs)
        e = CandidateQ1Engine(g, media={0: self.payload})
        for slot in range(3):
            e.stage(0, slot, exact_read(cid=slot, prp1=0x00200000 + slot * 0x1000))
        self.assertEqual(e.notify_producer(0, 3), [0, 1, 2])
        self.assertEqual([x["slot"] for x in e.transport_log], [0, 1, 2])

    def test_producer_wrap_31_to_zero_consumes_slot_31(self):
        g = QueueGeometry.from_b26x(self.regs)
        e = CandidateQ1Engine(g, media={0: self.payload})
        e.hw_producer = 31
        e.stage(0, 31, exact_read(prp1=0x00200000))
        self.assertEqual(e.notify_producer(0, 0), [31])
        self.assertEqual(e.transport_log[-1]["candidate_downstream_address"], 0x00820000 + 31 * 64)

    def test_completion_projection_matches_proven_phase_status_extraction(self):
        c = Completion(cid=0x1234, status_code=0xA5, status_type=5, phase=1)
        b80c, b80d, b80e, b80f = c.project_b80c_f()
        self.assertEqual((b80c, b80d), (0x34, 0x12))
        self.assertEqual(b80e & 1, 1)
        status = (b80e >> 1) | ((b80f & 1) << 7)
        status_type = (b80f >> 1) & 7
        self.assertEqual(status, 0xA5)
        self.assertEqual(status_type, 5)

    def test_candidate_read_publishes_projectable_completion(self):
        g = QueueGeometry.from_b26x(self.regs)
        e = CandidateQ1Engine(g, media={0: self.payload})
        e.stage(0, 0, exact_read(cid=0x3456))
        e.notify_producer(0, 1)
        b = e.project_completion(0)
        self.assertEqual(b[0:2], b"\x56\x34")
        self.assertEqual(b[2] & 1, 1)

    def test_phase_toggles_at_32_entry_wrap(self):
        self.assertEqual(advance_index_phase(30, 1, 1), (31, 1))
        self.assertEqual(advance_index_phase(31, 1, 1), (0, 0))
        self.assertEqual(advance_index_phase(0, 0, 32), (0, 1))

    def test_batched_consumer_notification_releases_three_slots(self):
        g = QueueGeometry.from_b26x(self.regs)
        e = CandidateQ1Engine(g, media={0: self.payload})
        for slot in range(3):
            e.stage(0, slot, exact_read(cid=slot, prp1=0x00200000 + slot * 0x1000))
        e.notify_producer(0, 3)
        self.assertEqual(e.notify_consumer(3), [0, 1, 2])
        self.assertEqual(e.hw_consumer, 3)
        self.assertTrue(all(e.completions[i] is None for i in range(3)))

    def test_repeated_consumer_notification_is_zero_distance(self):
        g = QueueGeometry.from_b26x(self.regs)
        e = CandidateQ1Engine(g, media={0: self.payload})
        e.hw_consumer = 7
        self.assertEqual(e.notify_consumer(7), [])
        self.assertEqual(e.hw_consumer, 7)


if __name__ == "__main__":
    unittest.main()
