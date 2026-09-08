"""Same-instance adversarial model tests using the one stock route fixture.

Q0 setup/SQE staging derive from opal 84c990c test_nvme_queue_causality.py.
These tests exercise the imported model, not handmade or physical firmware.
"""
import unittest
from simulation.board.model import Board
from simulation.asm2464.adapter.cpu import CpuBackend
from simulation.asm2464.adapter.route import configure_stock_route, tlp, FIXTURE

SEED=((0xB264,8),(0xB265,0),(0xB266,8),(0xB267,8),
      (0xB26C,8),(0xB26D,0x20),(0xB26E,8),(0xB26F,0x28),
      (0xB250,0),(0xB251,0),(0xCEF3,8),(0xCEF2,0x80),
      (0xCEF0,0),(0xCEEF,0),(0xC807,4),(0xB281,0x10))

def controller(hw):
    for off,val in ((0x14,0),(0x24,0x30003),(0x28,0x200000),
                    (0x2c,0),(0x30,0x820000),(0x34,0),(0x14,0x460001)):
        tlp(hw,0x40,hw.nvme_bar0+off,val)

def submit(cpu,qid=0,op=6,cid=1,prp=None,cdw10=None,cdw11=0):
    hw=cpu.hw;sqe=bytearray(64);sqe[0]=op;sqe[2:4]=cid.to_bytes(2,'little')
    sqe[4:8]=qid.to_bytes(4,'little')
    if prp is None:prp=0x820400 if qid else 0x200000
    if cdw10 is None:cdw10=0 if qid else 1
    sqe[24:32]=prp.to_bytes(8,'little');sqe[40:44]=cdw10.to_bytes(4,'little');sqe[44:48]=cdw11.to_bytes(4,'little')
    cpu.memory.xdata[0xA000:0xA040]=sqe
    for a,v in ((0xCEF2,0x80),(0xB294,0x30),(0xB296,255),(0xB251,1),(0xB254,qid+1)):hw.write(a,v)

def q1(cpu):
    submit(cpu,op=5,prp=0x820200,cdw10=0x30001,cdw11=1)
    submit(cpu,op=1,prp=0x200200,cdw10=0x30001,cdw11=0x10001)

def ready():
    b=Board(flash_profile='stock_physical');cpu=CpuBackend(b);cpu.prepare()
    configure_stock_route(cpu.hw)
    for a,v in SEED:cpu.hw.write(a,v)
    controller(cpu.hw);q1(cpu)
    assert cpu.hw._stock_nvme_owner(0) and cpu.hw._stock_nvme_owner(1)
    return b,cpu

def snapshot(cpu):
    return (bytes(cpu.memory.xdata[0xA400:0xA800]),bytes(cpu.memory.xdata[0xF000:0xF400]),
            bytes(cpu.memory.xdata[0xB800:0xB880]),dict(cpu.hw.nvme_namespace_data))

class CausalTests(unittest.TestCase):
    def test_fixture_exact_stock_values_and_byte_enables(self):
        b,c=ready();h=c.hw
        self.assertEqual((h.pcie_bridge_primary_bus,h.pcie_bridge_secondary_bus,h.pcie_bridge_subordinate_bus),(0,1,2))
        self.assertEqual((h.pcie_bridge_command,h.pcie_bridge_mem_base,h.pcie_bridge_mem_limit),(0x406,0xD00000,0x1DFFFFF))
        self.assertEqual((h.pcie_endpoint_bar0,h.pcie_endpoint_command,h.pcie_endpoint_devctl),(0xD00000,0x406,0x810))
        cfg=h.pcie_cfg_history
        self.assertEqual([e['byte_en'] for e in cfg[:3]],[7,3,15])
        self.assertEqual(len(FIXTURE['table_rows']),6)
    def test_route_revocation_blocks_q0_dma_cqe(self):
        b,c=ready();submit(c);c.hw.reset_pcie_configuration();before=snapshot(c)
        submit(c,cid=77);self.assertEqual(before,snapshot(c));self.assertTrue(c.hw.regs[0xB296]&1)
    def test_route_revocation_blocks_q1_dma_cqe(self):
        b,c=ready();submit(c,qid=1,op=2);c.hw.reset_pcie_configuration();before=snapshot(c)
        submit(c,qid=1,op=2,cid=88);self.assertEqual(before,snapshot(c));self.assertTrue(c.hw.regs[0xB296]&1)
    def test_link_returns_before_deferred_delivery_old_owners_stay_invalid(self):
        b,c=ready();h=c.hw;old=h.nvme_q0_owner;h.stock_nvme_defer_completions=True;submit(c)
        b.set_downstream_link(False);b.set_downstream_link(True)
        self.assertEqual(b.downstream.generation,1)
        configure_stock_route(h)
        self.assertIsNone(h._stock_nvme_owner(0));self.assertIsNone(h._stock_nvme_owner(1))
        before=snapshot(c);h.complete_deferred_nvme();self.assertEqual(before,snapshot(c))
        self.assertEqual(len(h.stock_nvme_rejected_completions),1)
        controller(h);self.assertNotEqual(h.nvme_q0_owner,old)
    def test_usb_only_msc_reset_retains_downstream_ancestry(self):
        b,c=ready();old=(c.hw.nvme_q0_owner,c.hw.nvme_q1_owner,b.downstream.generation)
        c.hw.reset_stock_msc_generation()
        self.assertEqual(old,(c.hw._stock_nvme_owner(0),c.hw._stock_nvme_owner(1),b.downstream.generation))
        submit(c);self.assertTrue(c.hw.regs[0xCEF2]&0x80)
    def test_two_consecutive_link_generations_cannot_alias(self):
        b,c=ready();h=c.hw;owners=[]
        for _ in range(2):
            owners.append(h.nvme_q0_owner);h.stock_nvme_defer_completions=True;submit(c)
            b.set_downstream_link(False);b.set_downstream_link(True)
            configure_stock_route(h);controller(h);h.stock_nvme_defer_completions=False;q1(c)
        before=snapshot(c);h.complete_deferred_nvme()
        self.assertEqual(before,snapshot(c));self.assertEqual(len(h.stock_nvme_rejected_completions),2)
        self.assertEqual(len(set(owners+[h.nvme_q0_owner])),3)
    def test_late_q0_after_new_controller_is_rejected(self):
        b,c=ready();h=c.hw;h.stock_nvme_defer_completions=True;submit(c,cid=31)
        controller(h);before=snapshot(c);h.complete_deferred_nvme()
        self.assertEqual(before,snapshot(c));self.assertEqual(len(h.stock_nvme_rejected_completions),1)
    def test_old_q1_after_delete_recreate_is_rejected(self):
        b,c=ready();h=c.hw;old_q0=h.nvme_q0_owner;old_q1=h.nvme_q1_owner
        h.stock_nvme_defer_completions=True;submit(c,qid=1,op=2,cid=41)
        h.stock_nvme_defer_completions=False
        submit(c,op=0,cdw10=1);submit(c,op=4,cdw10=1);q1(c)
        self.assertEqual(old_q0,h.nvme_q0_owner);self.assertNotEqual(old_q1,h.nvme_q1_owner)
        before=snapshot(c);h.complete_deferred_nvme();self.assertEqual(before,snapshot(c))
        submit(c,qid=1,op=2,cid=42);self.assertTrue(h.regs[0xB294]&0x10)
    def test_healthy_same_generation_deferred_event_completes(self):
        b,c=ready();h=c.hw;h.stock_nvme_defer_completions=True;submit(c,cid=123)
        before=bytes(c.memory.xdata[0xB800:0xB840]);h.complete_deferred_nvme()
        self.assertNotEqual(before,bytes(c.memory.xdata[0xB800:0xB840]));self.assertFalse(h.stock_nvme_rejected_completions)
    def test_q1_timeout_keeps_q0_in_model(self):
        b,c=ready();h=c.hw;old=h.nvme_q0_owner
        h.stock_nvme_io_completion_timeout=True;submit(c,qid=1,op=2)
        self.assertFalse(h.regs[0xB294]&0x10)
        self.assertEqual(old,h._stock_nvme_owner(0));submit(c);self.assertTrue(h.regs[0xCEF2]&0x80)
    def test_cold_mapping_without_controller_does_not_grant_queue(self):
        b=Board();c=CpuBackend(b);c.prepare();configure_stock_route(c.hw)
        for a,v in SEED:c.hw.write(a,v)
        before=snapshot(c);submit(c);self.assertEqual(before,snapshot(c));self.assertTrue(c.hw.regs[0xB296]&1)
