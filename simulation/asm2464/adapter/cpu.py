"""Imported CPU/Memory/HardwareState with board-facing flash/UART adapters.

C8A9 kick, C8AA opcode, big-endian length C8A3:C8A4 and 7000 buffer
are taken from upstream HardwareState._flash_csr_write, not invented MMIO.
"""
from simulation.asm2464.adapter.imports import CPU8051, Memory, HardwareState, create_hardware_hooks
class CpuBackend:
    name='cpu'
    def __init__(self,board):
        if board.restricted: raise ValueError('STOCK_FULL_SPI_BOOT_BLOCKED_ON_BOOT_ROM_MODEL')
        self.board=board;self.instructions=0;self.hw=None
        board.attach_backend(self)
    def available_capabilities(self):
        return frozenset({'generic_reference', 'stock_bank0_helper', 'controller_mmio', 'external_link_events'})
    def read_trace(self): return list(self.board.events)
    def external_event(self,event,generation):
        if self.hw is None: return
        if event in ('link_loss','reset_assert'):
            self.hw.reset_pcie_configuration()
        self.hw.pcie_endpoint_present=self.board.downstream.present and self.board.downstream.link_up
    def prepare(self):
        b=self.board
        if not b.reset_asserted: b.assert_reset()
        self.memory=Memory();self.memory.reset()
        self.hw=HardwareState(log_reads=False,log_writes=False,log_uart=False)
        self.hw.pcie_endpoint_present=b.downstream.present and b.downstream.link_up
        self.hw.usb_connect_delay=10**15
        self.hw._memory=self.memory
        create_hardware_hooks(self.memory,self.hw)
        self.cpu=CPU8051(**{n:getattr(self.memory,n) for n in ('read_code','read_xdata','write_xdata','read_idata','write_idata','read_sfr','write_sfr','read_bit','write_bit')})
        self.hw._cpu_ref=self.cpu
        self.hw.write_callbacks[0xc000]=lambda hw,a,v:b.uart_tx(v)
        self.hw.write_callbacks[0xc001]=lambda hw,a,v:b.uart_tx(v)
        self.hw.write_callbacks[0xc8a9]=self._spi_kick
        self.hw.read_callbacks[0xc8a9]=lambda hw,a:0  # controller transaction done; NOR WIP is polled via 05
        for addr in list(self.memory.xdata_read_hooks):
            old=self.memory.xdata_read_hooks[addr]
            self.memory.xdata_read_hooks[addr]=self._read(old)
        for addr in list(self.memory.xdata_write_hooks):
            old=self.memory.xdata_write_hooks[addr]
            self.memory.xdata_write_hooks[addr]=self._write(old)
        b.release_reset()
    def reset(self,firmware_size):
        """GENERIC_REFERENCE_FIRMWARE only; manifest-owned loading convention."""
        self.prepare();b=self.board
        image=b.spi('asm',0x03,address=b.config['boot']['flash_offset'],length=firmware_size)
        self.memory.code[:]=b'\xff'*len(self.memory.code)
        self.memory.load_firmware(image);self.cpu.reset();self.instructions=0
        b.emit('CODE_LOAD',address=0,length=firmware_size,source_class='GENERIC_REFERENCE_FIRMWARE',convention='project manifest; boot ROM bypassed')
    def _read(self,old):
        def read(a):
            v=old(a);self.board.emit('MMIO_READ',address=a,value=v);return v
        return read
    def _write(self,old):
        def write(a,v): self.board.emit('MMIO_WRITE',address=a,value=v);return old(a,v)
        return write
    def _spi_kick(self,hw,addr,value):
        hw.regs[addr]=value
        if not value&1: return
        r=hw.regs;op=r.get(0xc8aa,0);address=(r.get(0xc8ab,0)<<16)|(r.get(0xc8a2,0)<<8)|r.get(0xc8a1,0)
        count=(r.get(0xc8a3,0)<<8)|r.get(0xc8a4,0)
        offset=(r.get(0xc8af,0)<<8)|r.get(0xc8ae,0)
        if count>4096 or offset+count>4096: raise ValueError('SPI DMA buffer overflow')
        data=bytes(self.memory.xdata[0x7000+offset:0x7000+offset+count]) if op==2 else b''
        result=self.board.spi('asm',op,address=address,data=data,length=count if op in (3,5,0x9f) else 0)
        self.memory.xdata[0x7000:0x7000+len(result)]=result
        hw.regs[addr]=0
    def run(self,limit=200000,stop=b'BOOT=OK\n'):
        for _ in range(limit):
            if self.board.reset_asserted: return False
            cycles=self.cpu.step();self.instructions+=1
            self.hw.tick(cycles,self.cpu);self.board.advance(max(cycles,1))
            if stop in self.board.uart: return True
            if self.cpu.halted: break
        return False
class RtlBackend:
    name='rtl'
    def __init__(self,board):
        raise RuntimeError('BLOCKED: authoritative pinned source tree contains no RTL implementation; replacement ASM RTL is forbidden')
