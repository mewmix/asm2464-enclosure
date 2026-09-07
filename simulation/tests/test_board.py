import unittest
from simulation.board.model import Board,OwnershipError
from simulation.programmer.virtual import Programmer
from simulation.flash.nor import FlashError
class FlashTests(unittest.TestCase):
    def setUp(self): self.b=Board();self.p=Programmer(self.b);self.p.enter()
    def test_wren_busy_and_nor_direction(self):
        with self.assertRaises(FlashError):self.p.command(2,data=b'\x00')
        self.p.command(6);self.assertEqual(self.p.command(5,length=1),b'\x02')
        self.p.command(2,data=b'\x0f');self.assertEqual(self.p.command(5,length=1),b'\x01')
        with self.assertRaises(FlashError):self.p.read(0,1)
        self.p.wait();self.p.program(0,b'\xf0');self.assertEqual(self.p.read(0,1),b'\0')
        self.p.erase(0,0x20);self.assertEqual(self.p.read(0,1),b'\xff')
    def test_boundaries_wrdi_and_range(self):
        self.p.command(6);self.p.command(4)
        with self.assertRaises(FlashError):self.p.command(2,data=b'\0')
        self.p.command(6)
        with self.assertRaises(FlashError):self.p.command(2,address=255,data=b'12')
        with self.assertRaises(FlashError):self.p.command(0x20,address=1)
        with self.assertRaises(FlashError):self.p.read(self.b.config['flash']['capacity'],1)
    def test_power_loss_during_program(self):
        self.p.command(6);self.p.command(2,data=b'\0'*100);self.b.advance(15);self.b.brownout()
        self.b.power_up();self.assertEqual(self.p.read(0,100),b'\0'*50+b'\xff'*50)
        self.assertEqual(self.b.flash.status(),0)
    def test_reset_does_not_magically_interrupt_flash(self):
        self.p.command(6);self.p.command(2,data=b'\0');self.b.assert_reset()
        self.assertEqual(self.b.flash.status(),1);self.p.wait();self.assertEqual(self.p.read(0,1),b'\0')
    def test_erase_interruption(self):
        self.p.program(0,b'\0'*256);self.p.command(6);self.p.command(0x20)
        self.b.advance(1);self.b.brownout();self.b.power_up()
        data=self.p.read(0,256);self.assertIn(0,data);self.assertIn(255,data)
    def test_ignored_wren_and_cleared_wel(self):
        for f in ('wren_ignored','wel_cleared'):
            self.b.flash.faults={f};self.p.command(6)
            with self.assertRaises(FlashError):self.p.command(2,data=b'\0')
    def test_wrong_jedec_and_busy_timeout(self):
        self.b.flash.faults={'bad_jedec'};self.assertEqual(self.p.command(0x9f,length=3),b'\0'*3)
        self.b.flash.faults={'stuck_busy'}
        with self.assertRaises(TimeoutError):self.p.wait(limit=3)
    def test_interrupted_read(self):
        self.b.flash.faults={'read_interrupted'}
        with self.assertRaises(FlashError):self.p.read(0,1)
    def test_ownership_is_physical(self):
        self.p.leave()
        with self.assertRaises(OwnershipError):self.b.attach() # reset alone not enough
        self.b.set_links(False);self.b.attach()
        with self.assertRaises(OwnershipError):self.b.release_reset()
        with self.assertRaises(OwnershipError):self.b.set_links(True)
        self.b.asm_refuses_release=True
        self.assertEqual(self.p.command(0x9f,length=3),bytes.fromhex(self.b.config['flash']['jedec_hex']))
        self.b.detach();self.b.set_links(True);self.b.release_reset()
        with self.assertRaises(OwnershipError):self.b.spi('programmer',3,length=1)
if __name__=='__main__':unittest.main()
