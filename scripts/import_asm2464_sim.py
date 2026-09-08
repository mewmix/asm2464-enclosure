#!/usr/bin/env python3
"""Reproduce/verify the exact vendored allowlist. No moving runtime branch."""
import argparse, hashlib, json, subprocess
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
UP = ROOT / 'simulation/asm2464/upstream'
def main():
    p=argparse.ArgumentParser(); p.add_argument('--source', type=Path, help='local git checkout containing the pinned commit'); args=p.parse_args()
    lock=json.loads((UP/'import-lock.json').read_text())
    if lock['commit'] != 'da87259d6bc0e2b86ff8bd0deb22938bd9fa89d7':
        raise ValueError('unreviewed source pin; review script and lock together')
    if lock['physical_baseline_commit'] != '47aed2cd0fba21011b4f69ad34781e2c924b7f48':
        raise ValueError('physical baseline mismatch')
    for entry in lock['files']:
        path=entry['path']; dest=UP/path
        data=subprocess.check_output(['git','-C',str(args.source),'show',f"{lock['commit']}:{path}"]) if args.source else dest.read_bytes()
        assert hashlib.sha256(data).hexdigest()==entry['sha256'], f'content mismatch: {path}'
        assert hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()==entry['git_blob_sha1'], path
        if args.source: dest.parent.mkdir(parents=True,exist_ok=True); dest.write_bytes(data)
    print(f"Verified {len(lock['files'])} pinned files at {lock['commit']}")
if __name__=='__main__': main()
