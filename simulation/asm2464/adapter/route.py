"""Canonical stock-aligned configuration fixture, never a completion oracle.

Rows are extracted verbatim from physical-baseline pcie_pio.h pre_bar_tab.
This bounded write projection omits discovery/capability traversal; synthetic
endpoint DevFn=0 and cap=0x40 are explicit fixture inputs, not physical facts.
"""
import json
import hashlib
import re
import subprocess
from pathlib import Path
FIXTURE=json.loads(Path(__file__).with_name('stock-route.json').read_text())

def verify_source_fixture(source):
    """Reproduce the canonical rows and inherited ledger from exact Git objects."""
    contents={}
    for entry in FIXTURE['sources']:
        data=subprocess.check_output(['git','-C',str(source),'show',FIXTURE['physical_baseline_commit']+':'+entry['path']])
        if hashlib.sha256(data).hexdigest()!=entry['sha256']:raise ValueError('stock route source hash mismatch')
        if hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()!=entry['git_blob_sha1']:raise ValueError('stock route source blob mismatch')
        contents[entry['path']]=data
    table=contents['handmade/src/pcie_pio.h'].decode().split('pre_bar_tab[6][6] = {',1)[1].split('};',1)[0]
    rows=[[int(v.strip(),0) for v in row.split(',')] for row in re.findall(r'\{([^}]+)\}',table)]
    if rows!=FIXTURE['table_rows']:raise ValueError('stock route rows differ from pinned source')
    audit=json.loads(contents['audit/stock_pcie_enumeration_reconstruction_20260904.json'])
    if audit['pre_bar_transaction_sequence']!=FIXTURE['stock_transaction_ledger']:raise ValueError('stock route ledger mismatch')
    return 'PASS_EXACT_PINNED_SOURCE'

def tlp(hw, fmt, address, value=0, byte_en=15):
    for addr,byte in ((0xB213,1),(0xB216,0x20),(0xB217,byte_en),(0xB210,fmt)):
        hw.write(addr,byte)
    for i,byte in enumerate(address.to_bytes(4,'big')):hw.write(0xB218+i,byte)
    for i,byte in enumerate(value.to_bytes(4,'big')):hw.write(0xB220+i,byte)
    hw.write(0xB296,255);hw.write(0xB254,0x80)
    if hw.regs[0xB22A] or (fmt in (0x44,0x45,5) and hw.regs[0xB22B]!=4):
        raise RuntimeError('fixture TLP rejected')

def configure_stock_route(hw):
    def row(index,endpoint=False):
        reg,be,*data=FIXTURE['table_rows'][index]
        tlp(hw,0x45 if endpoint else 0x44,(0x01000000 if endpoint else 0)+reg*4,int.from_bytes(bytes(data),'big'),be)
    row(0);row(1);row(2)
    row(3,True);row(4,True)
    tlp(hw,5,0x01000010)
    row(5,True)
    for reg in range(5,10):tlp(hw,0x45,0x01000000+reg*4,0)
    row(1,True)
    tlp(hw,0x45,0x01000048,0x0810,3)
    row(2)
