#!/usr/bin/env python3
"""Two stock-backed modes; missing restricted input never becomes PASS."""
import argparse
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from simulation.asm2464.adapter.stock import committed_code, execute_code, full_spi
from simulation.asm2464.adapter.route import verify_source_fixture

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--source',type=Path,help='read-only opal checkout with physical baseline Git objects')
    p.add_argument('--stock-image',type=Path,help='authorized local full SPI image; never copied into output')
    args=p.parse_args()
    code=execute_code(committed_code(args.source)) if args.source else {'status':'BLOCKED_STOCK_CODE_ARTIFACT_UNAVAILABLE'}
    report={'code_reconstruction':code,'full_spi':full_spi(args.stock_image),'hardware_touched':False,
            'stock_route_provenance':verify_source_fixture(args.source) if args.source else 'BLOCKED_SOURCE_CHECKOUT_UNAVAILABLE'}
    (ROOT/'validation/stock-report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
    return 2 if any(v.get('status','').startswith('BLOCKED') or 'BLOCKED_ON' in v.get('status','') for v in (code,report['full_spi'])) else 0

if __name__=='__main__':raise SystemExit(main())
