#!/usr/bin/env python3
"""One reproducible CAD + CPU/board regression entry point."""
import argparse,gzip,hashlib,json,os,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from simulation.board.config import sha256
from simulation.board.traces import compare
from scripts.build_reference import build
from scripts.run_simulation import run
from scripts.fault_matrix import run as faults

def main():
    p=argparse.ArgumentParser();p.add_argument('--require-rtl',action='store_true',help='fail the overall gate unless both execution backends are available');args=p.parse_args()
    subprocess.run([sys.executable,'scripts/import_asm2464_sim.py'],cwd=ROOT,check=True)
    a=build();b=build()
    assert a['firmware_sha256']==b['firmware_sha256'] and a['flash_image_sha256']==b['flash_image_sha256'],'firmware build not deterministic'
    subprocess.run([sys.executable,'-m','unittest','discover','-s','simulation/tests','-v'],cwd=ROOT,check=True)
    sim=run();matrix=faults()
    subprocess.run([sys.executable,'mechanical/rev-a/model.py'],cwd=ROOT,check=True)
    subprocess.run([sys.executable,'scripts/render_cad.py'],cwd=ROOT,check=True)
    cadpath=ROOT/'mechanical/rev-a/build';cad=json.loads((cadpath/'cad-validation.json').read_text())
    assert cad['passed'] and cad['board_config_sha256']==sim['board_config_sha256']==sha256()
    # Compare the two real CPU boot event segments. This is a repeatability check,
    # never mislabeled as CPU/RTL equivalence.
    trace=ROOT/'simulation/traces/cpu-recovery.jsonl'
    events=[json.loads(line) for line in trace.read_text().splitlines()]
    releases=[i for i,e in enumerate(events) if e['event']=='RESET_RELEASE']
    def segment(start):
        result=[];uart=bytearray()
        for e in events[start:]:
            result.append(e)
            if e['event']=='UART_TX':uart.append(e['value'])
            if b'BOOT=OK\n' in uart:break
        return result
    repeat=compare(segment(releases[0]),segment(releases[-1]));assert repeat['equal'],repeat
    (ROOT/'validation/cpu-recovery.jsonl.gz').write_bytes(gzip.compress(trace.read_bytes(),mtime=0))
    report=dict(status='CPU/CAD milestone PASS; full dual-backend milestone BLOCKED',board_config_sha256=sha256(),simulation=sim,cad=cad,deterministic_firmware=True,cpu_boot_repeatability=repeat,fault_cases=len(matrix),unsupported=[r['case'] for r in matrix if r['cpu_result']=='UNSUPPORTED'],rtl='BLOCKED: no RTL files in authoritative source pin',fabrication_ready=False)
    (ROOT/'validation/twin-report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(report['status'])
    if args.require_rtl:return 2
    return 0
if __name__=='__main__':raise SystemExit(main())
