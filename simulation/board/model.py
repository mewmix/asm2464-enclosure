"""One board for CPU and future imported RTL; no backend-owned flash."""
from simulation.board.config import load, sha256
from simulation.flash.nor import NorFlash, FlashError
from simulation.board.endpoint import DownstreamEndpoint
class OwnershipError(RuntimeError): pass
class Board:
    def __init__(self,config=None, *, flash_profile=None, restricted=False):
        self.config=config or load(flash_profile); self.events=[];self.tick=0
        self.restricted=restricted; self.backend=None
        self.downstream=DownstreamEndpoint()
        self.flash=NorFlash(self.config['flash'],self.emit)
        self.reset_asserted=True; self.links={n:True for n in self.config['recovery']['links']}
        self.programmer_attached=False;self.asm_refuses_release=False;self.uart=bytearray()
        self.emit('BOARD_CONFIG',sha256=sha256(),revision=self.config['revision'])
    def attach_backend(self,backend):
        if self.backend is not None and self.backend is not backend:
            raise OwnershipError('board already has a backend')
        self.backend=backend
    def set_downstream_link(self,up):
        if up == self.downstream.link_up: return
        self.downstream.link_up=up
        if not up: self.downstream.generation+=1
        self.emit('DOWNSTREAM_LINK_UP' if up else 'DOWNSTREAM_LINK_LOSS',generation=self.downstream.generation,evidence='EMULATOR_MODEL_ONLY')
        if self.backend: self.backend.external_event('link_up' if up else 'link_loss',self.downstream.generation)
    def emit(self,event,**fields): self.events.append(dict(tick=self.tick,event=event,**fields))
    def advance(self,ticks=1): self.tick+=ticks;self.flash.advance(ticks)
    def assert_reset(self):
        self.reset_asserted=True;self.uart.clear();self.emit('RESET_ASSERT')
        if self.backend: self.backend.external_event('reset_assert',self.downstream.generation)
    def release_reset(self):
        if self.programmer_attached or not all(self.links.values()): raise OwnershipError('restore shunts and disconnect programmer before boot')
        if not self.flash.powered: raise OwnershipError('board unpowered')
        self.reset_asserted=False;self.emit('RESET_RELEASE')
    def set_links(self,closed):
        if not self.reset_asserted: raise OwnershipError('hold reset before changing shunts')
        if self.programmer_attached: raise OwnershipError('disconnect programmer before changing shunts')
        self.links={n:closed for n in self.links};self.emit('SHUNTS_INSTALLED' if closed else 'SHUNTS_REMOVED',links=list(self.links))
    def attach(self):
        if not self.reset_asserted or any(self.links.values()):
            self.emit('BUS_CONTENTION',stage='attach');raise OwnershipError('programmer requires reset plus four open shunts')
        self.programmer_attached=True;self.emit('PROGRAMMER_ATTACH')
    def detach(self): self.programmer_attached=False;self.emit('PROGRAMMER_DETACH')
    def spi(self,master,opcode,address=0,data=b'',length=0):
        if master=='asm':
            if self.reset_asserted or not all(self.links.values()) or self.programmer_attached: raise OwnershipError('ASM not connected/active')
        elif master=='programmer':
            if not self.programmer_attached or any(self.links.values()):
                self.emit('BUS_CONTENTION',stage='transaction');raise OwnershipError('programmer not isolated')
        else: raise ValueError(master)
        self.emit('SPI_CS_ASSERT',master=master)
        self.emit('SPI_TX',master=master,opcode=opcode,address=address,
                  **({'data_length':len(data)} if self.restricted else {'data':data.hex()}),read_length=length)
        try:
            result=self.flash.transaction(opcode,address,data,length)
            self.emit('SPI_RX',master=master,**({'data_length':len(result)} if self.restricted else {'data':result.hex()}));return result
        except FlashError as e:
            self.emit('FLASH_ILLEGAL',reason=str(e));raise
        finally: self.emit('SPI_CS_RELEASE',master=master)
    def uart_tx(self,value):
        if self.reset_asserted: raise OwnershipError('UART while reset')
        if self.restricted:
            raise OwnershipError('restricted full-image CPU execution blocked until boot ROM and trace policy are modeled')
        self.uart.append(value);self.emit('UART_TX',value=value)
    def brownout(self):
        self.assert_reset();self.set_downstream_link(False);self.flash.power(False);self.emit('POWER_OFF')
    def power_up(self): self.flash.power(True);self.emit('POWER_ON')
