"""Deterministic command-level SPI NOR, shared by every bus master.

Timing/interruption policy is explicit simulation behavior, not silicon evidence.
"""
class FlashError(RuntimeError): pass
class NorFlash:
    def __init__(self, config, emit):
        self.cfg=config; self.emit=emit
        self._data=bytearray(b'\xff'*config['capacity'])
        self.wel=False; self.pending=None; self.time=0; self.powered=True
        self.faults=set()
    def status(self): return int(self.pending is not None or 'stuck_busy' in self.faults) | (int(self.wel)<<1)
    def advance(self,ticks):
        self.time+=ticks
        if self.pending and self.time>=self.pending['end'] and 'stuck_busy' not in self.faults:
            self._commit(1.0)
    def _commit(self,fraction):
        op=self.pending; count=int(op['length']*fraction)
        for i in range(count):
            address=op['address']+i
            if op['kind']=='program': self._data[address]&=op['payload'][i]
            else: self._data[address]=255
        self.emit('FLASH_COMPLETE' if fraction==1 else 'FLASH_INTERRUPTED',kind=op['kind'],address=op['address'],length=count)
        self.pending=None
    def interrupt(self):
        if self.pending:
            op=self.pending; fraction=min(1,max(0,(self.time-op['start'])/(op['end']-op['start'])))
            self._commit(fraction)
        self.wel=False
    def power(self,on):
        if not on: self.interrupt()
        self.powered=on; self.wel=False
    def _range(self,address,length):
        if address<0 or length<0 or address+length>len(self._data): raise FlashError('out of range')
    def transaction(self,opcode,address=0,data=b'',length=0):
        if not self.powered: raise FlashError('flash unpowered')
        if opcode==0x05:
            status=self.status(); self.emit('FLASH_STATUS',value=status); return bytes([status])*length
        if self.status()&1: raise FlashError('command while BUSY')
        if opcode==0x9f:
            if self.cfg['jedec_hex'] is None: raise FlashError('JEDEC ID UNKNOWN for selected profile')
            return (b'\0\0\0' if 'bad_jedec' in self.faults else bytes.fromhex(self.cfg['jedec_hex']))[:length]
        if opcode==0x06:
            self.wel='wren_ignored' not in self.faults; self.emit('FLASH_WREN',wel=self.wel); return b''
        if opcode==0x04: self.wel=False; self.emit('FLASH_WRDI'); return b''
        if opcode==0x03:
            self._range(address,length)
            if 'read_interrupted' in self.faults: raise FlashError('read interrupted')
            self.emit('FLASH_READ',address=address,length=length); return bytes(self._data[address:address+length])
        if opcode not in (0x02,0x20,0x52,0xd8,0xc7,0x60): raise FlashError(f'unsupported opcode {opcode:02x}')
        if 'wel_cleared' in self.faults: self.wel=False
        if not self.wel: raise FlashError('mutation without WEL')
        if opcode==0x02:
            size=len(data); self._range(address,size)
            if not size or address//self.cfg['page_size']!=(address+size-1)//self.cfg['page_size']: raise FlashError('page boundary violation')
            kind='program'; duration=self.cfg['program_ticks']; payload=bytes(data)
        else:
            size={0x20:self.cfg['sector_size'],0x52:32768,0xd8:self.cfg['block_size'],0xc7:len(self._data),0x60:len(self._data)}[opcode]
            if address%size: raise FlashError('erase boundary violation')
            self._range(address,size);kind='erase';payload=b''
            duration=self.cfg['sector_erase_ticks'] if opcode==0x20 else self.cfg['chip_erase_ticks'] if opcode in (0xc7,0x60) else self.cfg['block_erase_ticks']
        self.wel=False
        self.pending=dict(kind=kind,address=address,length=size,payload=payload,start=self.time,end=self.time+duration)
        self.emit('FLASH_PROGRAM' if kind=='program' else 'FLASH_ERASE',address=address,length=size)
        return b''
