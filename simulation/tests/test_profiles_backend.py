import json
import subprocess
import sys
import unittest
from simulation.board.model import Board
from simulation.board.backend import Backend
from simulation.programmer.virtual import Programmer
from simulation.flash.nor import FlashError

class ProfileTests(unittest.TestCase):
    def test_stock_boundary_and_full_readback(self):
        b=Board(flash_profile='stock_physical');p=Programmer(b);p.enter()
        self.assertEqual(len(p.read(0,0x80000)),0x80000)
        p.program(0x7ffff,b'\x12');self.assertEqual(p.read(0x7ffff,1),b'\x12')
        with self.assertRaises(FlashError):p.read(0x80000,1)
        with self.assertRaises(FlashError):p.program(0x80000,b'\0')
        p.command(6)
        with self.assertRaises(FlashError):p.command(2,address=0x7ffff,data=b'\0\0')
        self.assertEqual(p.read(0x7ffff,1),b'\x12')
        p.erase(0x7f000,0x20);self.assertEqual(p.read(0x7ffff,1),b'\xff')
        with self.assertRaises(FlashError):p.erase(0x80000,0x20)
        with self.assertRaises(FlashError):p.command(0x9f,length=3)
    def test_candidate_upper_addresses_are_experiment_only(self):
        b=Board(flash_profile='rev_a_candidate');p=Programmer(b);p.enter()
        for a in (0x7ffff,0x80000,0x1fffff):
            p.program(a,b'\x33');self.assertEqual(p.read(a,1),b'\x33')
        p.erase(0x80000,0x20);self.assertEqual(p.read(0x80000,1),b'\xff')
        self.assertEqual(len(p.read(0,2097152)),2097152)
        self.assertEqual(b.config['flash']['field_evidence']['capacity'],'REV_A_DESIGN_ASSUMPTION')
    def test_per_field_evidence(self):
        f=Board(flash_profile='stock_physical').config['flash']
        self.assertEqual(f['field_evidence']['capacity'],'PHYSICALLY_PROVEN')
        self.assertIsNone(f['part']);self.assertIsNone(f['jedec_hex'])
        self.assertEqual(f['field_evidence']['program_ticks'],'EMULATOR_MODEL_ONLY')
    def test_restricted_transactions_do_not_log_payload(self):
        b=Board(flash_profile='stock_physical',restricted=True);p=Programmer(b);p.enter()
        marker=b'PRIVATE_SENTINEL';p.program(0,marker);self.assertEqual(p.read(0,len(marker)),marker)
        trace=json.dumps(b.events)
        self.assertNotIn(marker.hex(),trace);self.assertNotIn(marker.decode(),trace)
        self.assertFalse(any('data' in e for e in b.events))

class BackendTests(unittest.TestCase):
    def test_board_import_has_no_asm_or_path_side_effects(self):
        subprocess.run([sys.executable,'-c',
            'import sys; p=list(sys.path); from simulation.board.model import Board; '
            'b=Board(); assert p==sys.path; assert not hasattr(b,"controller"); '
            'assert not any("hardware" in n or "asm2464" in n for n in sys.modules)'],check=True)
    def test_cpu_import_is_namespaced_and_board_external(self):
        before=list(sys.path)
        from simulation.asm2464.adapter.cpu import CpuBackend
        self.assertEqual(before,sys.path)
        b=Board();cpu=CpuBackend(b);cpu.prepare()
        self.assertIsInstance(cpu,Backend)
        self.assertIsNot(b.downstream,cpu.hw)
        self.assertFalse(hasattr(b,'controller'))
        self.assertNotIn('hardware',sys.modules)
        self.assertNotIn('pyrite',sys.modules)
    def test_external_events_delivered_without_backend_knowledge(self):
        class RecordingBackend:
            def __init__(self):self.events=[]
            def external_event(self,*event):self.events.append(event)
        b=Board();backend=RecordingBackend();b.attach_backend(backend)
        b.set_downstream_link(False);b.set_downstream_link(True)
        b.set_downstream_link(False);b.set_downstream_link(True)
        self.assertEqual(backend.events,[('link_loss',1),('link_up',1),('link_loss',2),('link_up',2)])
