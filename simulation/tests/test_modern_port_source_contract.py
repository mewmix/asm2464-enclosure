"""Throwaway source-to-twin contract validation for modern storage port."""
from pathlib import Path
import re
import unittest

from simulation.experiments.q1_asic_contract import CandidateQ1Engine, QueueGeometry

ROOT = Path(__file__).resolve().parents[2]
IO = (ROOT / "validation/modern-port-fixtures/nvme_io.h").read_text()
ADMIN = (ROOT / "validation/modern-port-fixtures/nvme_admin.h").read_text()

def define(text, name):
    m = re.search(r"^#define\\s+" + re.escape(name) + r"\\s+([^\\s/]+)", text, re.M)
    if not m:
        raise AssertionError(f"missing define {name}")
    return m.group(1)

def intval(token):
    token = token.rstrip("ULul")
    return int(token, 0)

def exact_read(cid=0, prp1=0x00200000, slba=0):
    sqe = bytearray(64)
    sqe[0] = 0x02
    sqe[2:4] = int(cid).to_bytes(2, "little")
    sqe[4:8] = (1).to_bytes(4, "little")
    sqe[24:32] = int(prp1).to_bytes(8, "little")
    sqe[40:48] = int(slba).to_bytes(8, "little")
    sqe[48:50] = (0).to_bytes(2, "little")
    return bytes(sqe)

class ModernPortTwinContract(unittest.TestCase):
    def test_source_contract_matches_modern_q1_geometry(self):
        self.assertEqual(intval(define(IO, "NVME_IO_QUEUE_DEPTH")), 32)
        self.assertEqual(intval(define(IO, "NVME_IO_SQ_DMA")), 0x00820000)
        self.assertEqual(intval(define(IO, "NVME_IO_CQ_DMA")), 0x00828000)
        self.assertEqual(intval(define(IO, "NVME_IO_DATA_IN_DMA")), 0x00200000)
        self.assertIn("NVME_IO_STAGING_BASE(context)", IO)
        self.assertIn("0xA800U : 0xA000U", IO)
        self.assertIn("NVME_IO_CONTEXT 0U", IO)
        self.assertIn("NVME_IO_PRODUCER(NVME_IO_CONTEXT)", IO)
        self.assertIn("NVME_IO_CONSUMER(NVME_IO_CONTEXT)", IO)
        self.assertIn("0xB80C", IO)
        self.assertIn("0xB80D", IO)
        self.assertIn("0xB80E", IO)
        self.assertIn("0xB80F", IO)

    def test_admin_source_matches_reconstructed_q0_contract(self):
        self.assertEqual(intval(define(ADMIN, "NVME_ADMIN_SQ_DMA")), 0x00800000)
        self.assertEqual(intval(define(ADMIN, "NVME_ADMIN_CQ_DMA")), 0x00808000)
        self.assertEqual(intval(define(ADMIN, "NVME_IDENTIFY_DATA_IN_DMA")), 0x00200000)
        self.assertIn("0x00030003UL", ADMIN)
        self.assertIn("NVME_ADMIN_CREATE_IO_CQ", IO)
        self.assertIn("NVME_ADMIN_CREATE_IO_SQ", IO)
        self.assertIn("NVME_IO_QUEUE_DEPTH - 1U", IO)

    def test_current_port_read_runs_through_candidate_engine_without_legacy_alias(self):
        regs = {
            0xB264:0x08, 0xB265:0x00, 0xB266:0x08, 0xB267:0x08,
            0xB26C:0x08, 0xB26D:0x20, 0xB26E:0x08, 0xB26F:0x28,
        }
        payload = bytes(((i * 37 + 11) & 0xFF) for i in range(512))
        g = QueueGeometry.from_b26x(regs)
        self.assertEqual((g.sq_base, g.cq_base, g.depth),
                         (int(define(IO, "NVME_IO_SQ_DMA").rstrip("UL"), 0),
                          int(define(IO, "NVME_IO_CQ_DMA").rstrip("UL"), 0),
                          int(define(IO, "NVME_IO_QUEUE_DEPTH").rstrip("U"), 0)))
        e = CandidateQ1Engine(g, media={0: payload})
        prp = intval(define(IO, "NVME_IO_DATA_IN_DMA"))
        e.stage(0, 0, exact_read(cid=0x0101, prp1=prp))
        self.assertEqual(e.notify_producer(0, 1), [0])
        self.assertEqual(e.host_memory.read(prp, 512), payload)
        self.assertEqual(e.host_memory.read(0xA400, 512), bytes(512))
        self.assertEqual(e.host_memory.read(0x00820400, 512), bytes(512))
        c = e.project_completion(0)
        self.assertEqual(c[0:2], b"\\x01\\x01")
        self.assertEqual(c[2] & 1, 1)
        self.assertEqual(e.notify_consumer(1), [0])

if __name__ == "__main__":
    unittest.main()
