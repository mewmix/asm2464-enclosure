"""Replay the committed diagnostic artifact without claiming a fresh SDCC build."""
import hashlib
import json
import subprocess
import tarfile
from pathlib import Path
from simulation.board.config import ROOT, load

START='a0c437eba1ed8a92c02221b60495988628861602'
FW=ROOT/'simulation/asm2464/firmware/reference/build'
BUNDLE_SHA='95b4736fe58942fcc219a085f3698f924df0d1cb1c98399aea553b42d9f09b30'

def restore():
    path=ROOT/'validation/reference-firmware.tar.gz'
    if hashlib.sha256(path.read_bytes()).hexdigest()!=BUNDLE_SHA:raise ValueError('reference bundle mismatch')
    allowed={'asm2464_ref.bin','asm2464_ref.ihx','asm2464_ref.map','asm2464_ref.sym','flash.bin','manifest.json','SHA256SUMS'}
    with tarfile.open(path) as archive:
        if set(archive.getnames())!=allowed:raise ValueError('unexpected archive members')
        FW.mkdir(parents=True,exist_ok=True)
        for member in archive.getmembers():
            if not member.isfile():raise ValueError('archive member not regular')
            (FW/member.name).write_bytes(archive.extractfile(member).read())
    verify_compatibility(json.loads((FW/'manifest.json').read_text()))

def verify_compatibility(manifest):
    old=json.loads(subprocess.check_output(['git','show',START+':hardware/rev-a/board.json'],cwd=ROOT))
    new=load()
    # Review metadata changed; all executable candidate values and geometry must
    # still match the build's exact original board profile.
    for key,value in old['flash'].items():
        if key!='evidence' and new['flash'][key]!=value:raise ValueError('reference flash ABI changed; rebuild required')
    for key in ('flash_offset','code_address'):
        if old['boot'][key]!=new['boot'][key]:raise ValueError('reference boot layout changed')
    if old['geometry']!=new['geometry'] or old['recovery']!=new['recovery']:raise ValueError('board topology changed')
    old_hash=hashlib.sha256(subprocess.check_output(['git','show',START+':hardware/rev-a/board.json'],cwd=ROOT)).hexdigest()
    if manifest['board_config_sha256']!=old_hash:raise ValueError('unrecognized original board manifest')
    if hashlib.sha256((ROOT/'simulation/asm2464/firmware/reference/main.c').read_bytes()).hexdigest()!=manifest['source_sha256']:raise ValueError('reference source changed')
    for filename,key in [('asm2464_ref.bin','firmware_sha256'),('flash.bin','flash_image_sha256')]:
        if hashlib.sha256((FW/filename).read_bytes()).hexdigest()!=manifest[key]:raise ValueError('reference image mismatch')
