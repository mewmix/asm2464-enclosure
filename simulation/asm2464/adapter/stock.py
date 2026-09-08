"""Offline stock input boundaries. No raw stock artifact is vendored or emitted."""
import hashlib
import json
import os
import subprocess
from pathlib import Path
from simulation.board.config import ROOT
from simulation.board.model import Board
from simulation.flash.profiles import STOCK_SIZE, PHYSICAL_BASELINE
from simulation.programmer.virtual import Programmer
from simulation.asm2464.adapter.cpu import CpuBackend

CODE_SIZE=98006
CODE_SHA256='1a4734745f0c00235754a00bd7e3e995064e1e6ae6f88a6aff163683a22d0037'

def manifest(data,source_class,profile):
    lock=json.loads((ROOT/'simulation/asm2464/upstream/import-lock.json').read_text())
    return dict(image_size=len(data),image_sha256=hashlib.sha256(data).hexdigest(),
                source_class=source_class,expected_flash_profile=profile,
                simulator_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                upstream_emulator_commit=lock['commit'],physical_baseline_commit=PHYSICAL_BASELINE,
                hardware_touched=False)

def committed_code(source):
    # Read only this exact Git object; never discover or inspect private dumps.
    data=subprocess.check_output(['git','-C',str(source),'show',PHYSICAL_BASELINE+':fw.bin'])
    if len(data)!=CODE_SIZE or hashlib.sha256(data).hexdigest()!=CODE_SHA256:
        raise ValueError('stock code artifact hash/size mismatch')
    return data

def execute_code(data):
    if len(data)!=CODE_SIZE or hashlib.sha256(data).hexdigest()!=CODE_SHA256:
        raise ValueError('stock code artifact hash/size mismatch')
    b=Board(flash_profile='stock_physical');backend=CpuBackend(b);backend.prepare()
    backend.memory.code[:]=b'\xff'*len(backend.memory.code)
    backend.memory.load_firmware(data);backend.cpu.reset()
    backend.cpu.step()
    if backend.cpu.pc!=0x436B:raise AssertionError('stock reset LJMP mismatch')
    # Execute exact bank-zero helper up to its RET, without synthesizing a caller.
    # No bank-one offset correction or omitted callee synthesis is introduced.
    backend.cpu.pc=0xCF91;start=len(b.events);count=0
    while backend.cpu.pc!=0xCFE3 and count<128:
        if not 0xCF91<=backend.cpu.pc<0xCFE3:raise AssertionError('helper escaped')
        backend.cpu.step();count+=1
    if backend.cpu.pc!=0xCFE3:raise AssertionError('stock helper timed out')
    writes=[(e['address'],e['value']) for e in b.events[start:] if e['event']=='MMIO_WRITE']
    expected=[(0xB264,8),(0xB265,0),(0xB266,8),(0xB267,8),
              (0xB26C,8),(0xB26D,0x20),(0xB26E,8),(0xB26F,0x28),
              (0xB250,0),(0xB251,0),(0xCEF3,8),(0xCEF2,0x80),
              (0xCEF0,0),(0xCEEF,0),(0xC807,4),(0xB281,0x10)]
    if writes!=expected:raise AssertionError('stock ordered MMIO mismatch')
    result=manifest(data,'STOCK_CODE_ARTIFACT','stock_physical')
    result.update(status='STOCK_CODE_EXECUTION_PROVEN_WITH_BOOT_ROM_BYPASSED',
                  reset_vector_target=0x436B,helper_range=[0xCF91,0xCFE3],
                  helper_instructions=count,ordered_mmio_writes=writes,
                  evidence='INSTRUCTION_PROVEN bytes; execution EMULATOR_MODEL_ONLY',
                  bank1='BLOCKED: upstream BANK1_FILE_BASE uses wrapper offset 0xFF6B after loader strips four-byte wrapper; body offset is 0xFF67. No local correction.',
                  boot_rom='UNMODELED',stock_storage_boot='NOT_VALIDATED')
    return result

def full_spi(path=None):
    path=path or os.environ.get('ASM2464_STOCK_SPI_IMAGE')
    if not path:return dict(status='BLOCKED_STOCK_FULL_SPI_IMAGE_UNAVAILABLE',hardware_touched=False)
    # Bounded read; no supplied filename or raw data goes into output/errors.
    try:
        with Path(path).open('rb') as stream:data=stream.read(STOCK_SIZE+1)
    except OSError:
        return dict(status='BLOCKED_STOCK_FULL_SPI_IMAGE_UNAVAILABLE',hardware_touched=False)
    if len(data)!=STOCK_SIZE:raise ValueError('physical-stock image must be exactly 524288 bytes')
    result=manifest(data,'STOCK_FULL_SPI_IMAGE','stock_physical')
    b=Board(flash_profile='stock_physical',restricted=True);p=Programmer(b);p.enter()
    digest=p.install(data)
    if digest!=result['image_sha256']:raise AssertionError('full-image readback mismatch')
    # Deliberately damage only virtual flash and repair it through the same bus.
    if data[-1]==255:p.program(STOCK_SIZE-1,b'\0')
    else:p.erase(STOCK_SIZE-4096,0x20)
    if p.read(0,STOCK_SIZE)==data:raise AssertionError('virtual corruption not observed')
    repaired=p.install(data)
    if repaired!=digest:raise AssertionError('virtual recovery mismatch')
    p.leave()
    result.update(status='STOCK_FULL_SPI_BOOT_BLOCKED_ON_BOOT_ROM_MODEL',
                  programmer_readback='PASS_EMULATOR_MODEL_ONLY',
                  virtual_corruption_recovery='PASS_EMULATOR_MODEL_ONLY',
                  jedec_validation='BLOCKED_UNKNOWN_ID',
                  trace_payloads='SUPPRESSED',raw_image_copied=False,
                  preservation_scope='virtual flash program and full readback only; no stock startup execution')
    return result
