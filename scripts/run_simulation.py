#!/usr/bin/env python3
import argparse,hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from simulation.board.model import Board
from simulation.programmer.virtual import Programmer
from simulation.asm2464.adapter.cpu import CpuBackend
from simulation.board.config import sha256
OUT=ROOT/'simulation/traces';FW=ROOT/'simulation/asm2464/firmware/reference/build'
def run(archived_reference=False):
    m=json.loads((FW/'manifest.json').read_text());image=(FW/'flash.bin').read_bytes()
    assert hashlib.sha256(image).hexdigest()==m['flash_image_sha256']
    if archived_reference:
        from scripts.restore_reference import verify_compatibility
        verify_compatibility(m)
    else:
        assert m['board_config_sha256']==sha256(),'rebuild firmware after board configuration change'
    b=Board();p=Programmer(b);cpu=CpuBackend(b)
    p.enter();p.install(image);p.leave()
    cpu.reset(m['firmware_size']);assert cpu.run(),'good image did not boot'
    assert b.uart.decode()==m['expected_uart_banner'],b.uart
    first=bytes(b.uart);first_instructions=cpu.instructions
    p.enter();p.erase(0,0x20);p.leave()
    cpu.reset(m['firmware_size']);assert not cpu.run(limit=15000),'corrupt image booted'
    b.emit('BRICK_CONFIRMED',instructions=cpu.instructions)
    p.enter();digest=p.install(image);p.leave()
    cpu.reset(m['firmware_size']);assert cpu.run(),'recovered image did not boot'
    assert bytes(b.uart)==first
    assert not any(e['event']=='BUS_CONTENTION' for e in b.events)
    # Large SPI payloads are still retained in raw trace; summary excludes timing.
    OUT.mkdir(parents=True,exist_ok=True)
    raw=OUT/'cpu-recovery.jsonl'
    with raw.open('w') as f:
        for e in b.events:f.write(json.dumps(e,sort_keys=True)+'\n')
    summary=dict(cpu_boot='PASS',cpu_brick='PASS',cpu_recovery='PASS',rtl='BLOCKED: no RTL in pinned source',differential='BLOCKED: second execution backend absent',upstream_commit=m['upstream_commit'],board_config_sha256=sha256(),firmware_sha256=m['firmware_sha256'],flash_sha256=digest,uart=first.decode(),first_boot_instructions=first_instructions,recovered_boot_instructions=cpu.instructions,events=len(b.events),trace_sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),physical_validation='NOT PERFORMED',boot_rom='unmodeled; uses authoritative explicit cold-load convention')
    summary.update(source_class='GENERIC_REFERENCE_FIRMWARE',evidence='EMULATOR_MODEL_ONLY',
                   reference_artifact_upstream_commit=m['upstream_commit'],
                   upstream_commit=json.loads((ROOT/'simulation/asm2464/upstream/import-lock.json').read_text())['commit'],
                   build_mode='preserved artifact replay; no fresh build' if archived_reference else 'fresh SDCC build',
                   stock_compatibility='NOT_TESTED',hardware_touched=False)
    for key in ('cpu_boot','cpu_brick','cpu_recovery'):
        summary['generic_'+key]=summary.pop(key)
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2));return summary
if __name__=='__main__':run()
