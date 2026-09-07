#!/usr/bin/env python3
"""Execute fault corpus with real CPU where relevant; record unsupported gates."""
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from simulation.board.model import Board,OwnershipError
from simulation.flash.nor import FlashError
from simulation.programmer.virtual import Programmer
from simulation.asm2464.adapter.cpu import CpuBackend
FW=ROOT/'simulation/asm2464/firmware/reference/build'
def run():
    m=json.loads((FW/'manifest.json').read_text());image=(FW/'flash.bin').read_bytes();rows=[]
    def record(name,expected,fn):
        try: result=fn();status='PASS'
        except Exception as e: result=repr(e);status='FAIL'
        rows.append(dict(case=name,cpu_result=status,observed=result,rtl_result='BLOCKED: source absent',expected_model_behavior=expected,confidence='model-level only',evidence='executed case in scripts/fault_matrix.py; generic profile assumptions in hardware/rev-a/board.json'))
    def setup():
        b=Board();p=Programmer(b);p.enter();p.install(image);p.leave();return b,p,CpuBackend(b)
    def boot(b,cpu):
        try:cpu.reset(m['firmware_size']);return cpu.run(limit=15000)
        except (FlashError,OwnershipError):return False
    def blank():
        b=Board();cpu=CpuBackend(b);assert not boot(b,cpu);return 'No BOOT=OK within 15000 instructions'
    record('blank flash','normal boot fails',blank)
    def corrupt(single=False):
        b,p,cpu=setup();p.enter()
        if single:
            # Reset LJMP opcode 02 -> NOP 00, exactly one 1-to-0 change.
            p.program(256,b'\0')
        else:p.erase(0,0x20)
        p.leave();ok=boot(b,cpu)
        if not single:assert not ok
        return 'BOOT=OK survives single-bit reset-opcode corruption; no integrity enforcement' if ok else 'normal boot fails'
    record('corrupt reset/boot region','normal boot fails',corrupt)
    record('single-bit corruption','outcome depends on instruction affected; no hash enforcement',lambda:corrupt(True))
    rows.append(dict(case='invalid firmware hash',cpu_result='UNSUPPORTED',observed='Reference boot does not enforce a hash; programmer performs offline SHA-256 verification',rtl_result='BLOCKED',expected_model_behavior='No hardware hash behavior inferred',confidence='explicit firmware scope',evidence='reference/main.c'))
    for name,fault in [('bad JEDEC ID','bad_jedec'),('flash stuck BUSY','stuck_busy'),('reset during flash access','read_interrupted')]:
        def case(f=fault):
            b,p,cpu=setup();b.flash.faults.add(f);assert not boot(b,cpu);return 'No successful normal boot'
        record(name,'normal boot fails or read is interrupted',case)
    for name,fault in [('WREN ignored','wren_ignored'),('WEL unexpectedly cleared','wel_cleared')]:
        def case(f=fault):
            b,p,cpu=setup();p.enter();b.flash.faults.add(f)
            try:p.program(0x100,b'\0');raise AssertionError('mutation accepted')
            except FlashError:pass
            assert p.read(0,len(image))==image;p.leave();b.flash.faults.clear();assert boot(b,cpu);return 'Write rejected, image intact, CPU boots'
        record(name,'programming rejected and data intact',case)
    for name,op in [('program interrupted',2),('erase interrupted',0x20)]:
        def case(op=op):
            b,p,cpu=setup();p.enter();p.command(6)
            if op==2:p.command(op,address=256,data=b'\0'*128)
            else:p.command(op,address=0)
            b.advance(15 if op==2 else 150);b.brownout();b.power_up()
            assert p.read(0,len(image))!=image
            p.install(image);p.leave();assert boot(b,cpu);return 'Partial mutation retained, programmer repair verified, CPU boots'
        record(name,'interruption produces partial data; external repair restores boot',case)
    def reset_boot():
        b,p,cpu=setup();cpu.reset(m['firmware_size']);assert not cpu.run(limit=20)
        b.assert_reset();assert not cpu.run(limit=20);assert boot(b,cpu);return 'CPU stops under reset; fresh reset boots'
    record('reset during boot','CPU stops and later clean reset boots',reset_boot)
    def brownout():
        b,p,cpu=setup();cpu.reset(m['firmware_size']);cpu.run(limit=20);b.brownout()
        assert not cpu.run(limit=20);b.power_up();assert boot(b,cpu);return 'Power loss stops execution; power-up and reset boot'
    record('brownout','execution stops while unpowered, retained image boots after power-up',brownout)
    def rapid():
        b,p,cpu=setup()
        for _ in range(5):cpu.reset(m['firmware_size']);cpu.run(limit=10);b.assert_reset()
        assert boot(b,cpu);return 'Five partial boots followed by successful clean boot'
    record('rapid reset','clean final reset boots',rapid)
    def early():
        b,p,cpu=setup();assert boot(b,cpu)
        try:b.attach();raise AssertionError('early attach accepted')
        except OwnershipError:pass
        return 'Attachment rejected and contention event retained'
    record('programmer attached too early','reject unsafe attachment',early)
    def late():
        b,p,cpu=setup();p.enter()
        try:b.release_reset();raise AssertionError('late programmer ignored')
        except OwnershipError:pass
        p.leave();assert boot(b,cpu);return 'Reset release rejected until programmer disconnected'
    record('programmer detached too late','reject premature CPU ownership',late)
    def refuses():
        b,p,cpu=setup();b.asm_refuses_release=True;p.enter();p.install(image);p.leave();assert boot(b,cpu)
        return 'Open physical shunts isolate controller regardless of reset drive behavior'
    record('ASM refuses to relinquish SPI','physical disconnection allows recovery',refuses)
    def contention():
        b,p,cpu=setup();p.enter();b.links['CLK']=True # deliberate physical fault
        try:p.read(0,1);raise AssertionError('contention accepted')
        except OwnershipError:pass
        assert any(e['event']=='BUS_CONTENTION' for e in b.events);return 'Shorted CLK link blocks programming'
    record('ASM/programmer contention','detect a closed link during external access',contention)
    path=ROOT/'validation/fault-matrix.json';path.write_text(json.dumps(rows,indent=2)+'\n')
    failures=[r for r in rows if r['cpu_result']=='FAIL'];print(f'{len(rows)} fault cases; {len(failures)} failures; hash enforcement unsupported; RTL blocked')
    if failures:raise AssertionError(failures)
    return rows
if __name__=='__main__':run()
