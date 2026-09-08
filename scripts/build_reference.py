#!/usr/bin/env python3
import hashlib,json,os,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from simulation.board.config import load,sha256
SRC=ROOT/'simulation/asm2464/firmware/reference';OUT=SRC/'build'
def digest(b): return hashlib.sha256(b).hexdigest()
def build():
    cfg=load();OUT.mkdir(parents=True,exist_ok=True)
    code=(SRC/'main.c').read_bytes();build_id=digest(code+bytes.fromhex(sha256()))[:12]
    jedec=bytes.fromhex(cfg['flash']['jedec_hex'])
    (OUT/'profile.h').write_text(f'#define BUILD_ID "{build_id}"\n'+''.join(f'#define JEDEC{i} {v}\n' for i,v in enumerate(jedec)))
    # Same compiler, architecture, code origin and low XDATA convention as upstream handmade/Makefile.
    command=['sdcc','-mmcs51','--model-small','--code-loc','0x0000','--code-size','0x10000','--xram-loc','0x0b60','--xram-size','0xa0','--iram-size','0x100','--no-xinit-opt','-I.',str(SRC/'main.c'),'-o','asm2464_ref.ihx']
    subprocess.run(command,cwd=OUT,check=True,env={**os.environ,'SOURCE_DATE_EPOCH':'0'})
    memory={};upper=0
    for line in (OUT/'asm2464_ref.ihx').read_text().splitlines():
        r=bytes.fromhex(line[1:]);assert sum(r)%256==0
        count=r[0];address=(r[1]<<8)|r[2];typ=r[3]
        if typ==0:
            for i,v in enumerate(r[4:4+count]):memory[upper+address+i]=v
        elif typ==4:upper=int.from_bytes(r[4:6],'big')<<16
    raw=bytes(memory.get(i,255) for i in range(max(memory)+1))
    image=bytearray(b'\xff'*cfg['flash']['capacity']);image[0x80:0x84]=b'REF1'
    offset=cfg['boot']['flash_offset'];image[offset:offset+len(raw)]=raw
    (OUT/'asm2464_ref.bin').write_bytes(raw);(OUT/'flash.bin').write_bytes(image)
    banner=f'ASM2464 REF FW\nBUILD={build_id}\nFLASH_ID={jedec.hex().upper()}\nFLASH_STATUS=00\nBOOT=OK\n'
    manifest=dict(source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),source_sha256=digest(code),board_config_sha256=sha256(),upstream_commit=json.loads((ROOT/'simulation/asm2464/upstream/import-lock.json').read_text())['commit'],toolchain=subprocess.check_output(['sdcc','--version'],text=True).splitlines()[0],build_command=command,firmware_sha256=digest(raw),flash_image_sha256=digest(image),firmware_size=len(raw),flash_offset=offset,load_address=0,expected_uart_banner=banner,build_id=build_id,elf='not applicable: SDCC mcs51 emits IHX/map/sym; no ELF invented',boot_rom='unmodeled: uses upstream explicit cold-load convention',hash_enforcement='not implemented by reference boot; offline SHA-256 verifies programmer readback')
    (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (OUT/'SHA256SUMS').write_text(f'{digest(raw)}  asm2464_ref.bin\n{digest(image)}  flash.bin\n')
    print(f'Built {len(raw)} bytes, id={build_id}');return manifest
if __name__=='__main__':build()
