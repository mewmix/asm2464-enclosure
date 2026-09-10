#!/usr/bin/env python3
"""Offline stock-first validation; diagnostic and missing-input results separated."""
import argparse
import gzip
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from simulation.board.config import sha256
from simulation.board.routing import validate as validate_routing
from simulation.board.kicad_constraints import validate as validate_kicad_constraints
from simulation.board.traces import compare
from simulation.asm2464.adapter.stock import committed_code,execute_code,full_spi
from simulation.asm2464.adapter.route import verify_source_fixture
from scripts.build_reference import build
from scripts.run_simulation import run
from scripts.fault_matrix import run as faults

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',type=Path,help='read-only opal checkout with exact baseline code object')
    parser.add_argument('--stock-image',type=Path,help='authorized local full SPI image')
    parser.add_argument('--reference-bundle',action='store_true',help='replay preserved diagnostic image; no fresh-build claim')
    parser.add_argument('--require-rtl',action='store_true',help='missing RTL always blocks the full gate')
    args=parser.parse_args()
    subprocess.run([sys.executable,'scripts/import_asm2464_sim.py'],cwd=ROOT,check=True)
    report={'hardware_touched':False,'fabrication_ready':False,'board_config_sha256':sha256()}
    report['routing']=validate_routing()
    report['kicad_constraints']=validate_kicad_constraints()
    code=execute_code(committed_code(args.source)) if args.source else {'status':'BLOCKED_STOCK_CODE_ARTIFACT_UNAVAILABLE'}
    report['stock']={'code_reconstruction':code,'full_spi':full_spi(args.stock_image)}
    report['stock_route_provenance']=verify_source_fixture(args.source) if args.source else 'BLOCKED_SOURCE_CHECKOUT_UNAVAILABLE'
    suite=unittest.defaultTestLoader.discover(str(ROOT/'simulation/tests'))
    tests=unittest.TextTestRunner(verbosity=1).run(suite)
    report['tests']={'run':tests.testsRun,'failures':len(tests.failures),'errors':len(tests.errors),
                     'skipped':len(tests.skipped),'expected_failures':len(tests.expectedFailures),
                     'unexpected_successes':len(tests.unexpectedSuccesses)}
    upstream=subprocess.run([sys.executable,'-m','unittest','discover','-s',
                            'simulation/asm2464/upstream/test','-p','test_nvme_queue_causality.py','-q'],
                            cwd=ROOT,env={**os.environ,'PYTHONPATH':str(ROOT/'simulation/asm2464/upstream')},
                            text=True,capture_output=True)
    count=re.search(r'Ran (\d+) tests',upstream.stderr)
    report['upstream_model_tests']={'run':int(count[1]) if count else None,'exit_code':upstream.returncode}
    if upstream.returncode:print(upstream.stderr,file=sys.stderr)
    if args.reference_bundle:
        from scripts.restore_reference import restore
        restore()
        report['firmware_build']='BLOCKED_SDCC_UNAVAILABLE' if not shutil.which('sdcc') else 'NOT_REQUESTED_ARCHIVED_REPLAY'
        reference_ready=True
    elif shutil.which('sdcc'):
        a=build();b=build()
        assert a['firmware_sha256']==b['firmware_sha256'] and a['flash_image_sha256']==b['flash_image_sha256']
        report['firmware_build']='PASS_DETERMINISTIC_GENERIC_REFERENCE';reference_ready=True
    else:
        report['firmware_build']='BLOCKED_SDCC_UNAVAILABLE';reference_ready=False
    if reference_ready:
        report['generic_diagnostic']=run(archived_reference=args.reference_bundle)
        matrix=faults()
        report['generic_faults']={'total':len(matrix),'passed':sum(r['cpu_result']=='PASS' for r in matrix),
                                  'unsupported':[r['case'] for r in matrix if r['cpu_result']=='UNSUPPORTED']}
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
        report['generic_repeatability']=repeat
        (ROOT/'validation/cpu-recovery.jsonl.gz').write_bytes(gzip.compress(trace.read_bytes(),mtime=0))
    if importlib.util.find_spec('cadquery'):
        subprocess.run([sys.executable,'mechanical/rev-a/model.py'],cwd=ROOT,check=True)
        subprocess.run([sys.executable,'scripts/render_cad.py'],cwd=ROOT,check=True)
        report['cad']=json.loads((ROOT/'mechanical/rev-a/build/cad-validation.json').read_text())
        assert report['cad']['passed'] and report['cad']['board_config_sha256']==sha256()
    else:report['cad']={'status':'BLOCKED_CADQUERY_UNAVAILABLE','historical_review':'mechanical/rev-a/review; not regenerated'}
    report['rtl']='BLOCKED_SOURCE_ABSENT'
    report['differential']='BLOCKED_SECOND_EXECUTION_BACKEND_ABSENT'
    contracts_pass=(
        tests.wasSuccessful()
        and upstream.returncode==0
        and report['routing']['passed']
        and report['kicad_constraints']['passed']
    )
    report['status']='OFFLINE_CONTRACTS_PASS_WITH_STOCK_STARTUP_AND_TOOLCHAIN_BLOCKERS' if contracts_pass else 'OFFLINE_CONTRACT_FAILURE'
    (ROOT/'validation/twin-report.json').write_text(json.dumps(report,indent=2)+'\n')
    (ROOT/'validation/stock-report.json').write_text(json.dumps(report['stock'],indent=2)+'\n')
    print(report['status'])
    return 2 if contracts_pass else 1

if __name__=='__main__':raise SystemExit(main())
