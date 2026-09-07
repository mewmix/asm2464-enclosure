import hashlib
class Programmer:
    def __init__(self,board): self.board=board
    def enter(self):
        self.board.assert_reset();self.board.set_links(False);self.board.attach()
    def leave(self):
        self.board.detach();self.board.set_links(True)
    def command(self,*args,**kwargs): return self.board.spi('programmer',*args,**kwargs)
    def wait(self,limit=10000):
        for _ in range(limit):
            if not self.command(0x05,length=1)[0]&1: return
            self.board.advance(10)
        raise TimeoutError('flash BUSY timeout')
    def read(self,address,length): return self.command(0x03,address=address,length=length)
    def program(self,address,data):
        page=self.board.config['flash']['page_size']
        while data:
            count=min(len(data),page-address%page)
            self.command(0x06);self.command(0x02,address=address,data=data[:count]);self.wait()
            address+=count;data=data[count:]
    def erase(self,address=0,opcode=0xc7):
        self.command(0x06);self.command(opcode,address=address);self.wait()
    def install(self,image):
        if len(image)!=self.board.config['flash']['capacity']: raise ValueError('require full flash image')
        if self.command(0x9f,length=3)!=bytes.fromhex(self.board.config['flash']['jedec_hex']): raise ValueError('wrong JEDEC ID')
        backup=self.read(0,len(image));self.board.emit('BACKUP_HASH',sha256=hashlib.sha256(backup).hexdigest())
        self.erase()
        page=self.board.config['flash']['page_size']
        for offset in range(0,len(image),page):
            chunk=image[offset:offset+page]
            if chunk!=b'\xff'*len(chunk): self.program(offset,chunk)
        actual=self.read(0,len(image))
        if actual!=image: raise ValueError('full readback mismatch')
        digest=hashlib.sha256(actual).hexdigest();self.board.emit('VERIFY_HASH',sha256=digest);return digest
