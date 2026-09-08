import hashlib, json
from simulation.flash.profiles import select
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
CONFIG_PATH=ROOT/'hardware/rev-a/board.json'
def load(flash_profile=None):
    config=json.loads(CONFIG_PATH.read_text())
    config['flash']=select(config['flash'],flash_profile or config['default_flash_profile'])
    return config
def sha256(): return hashlib.sha256(CONFIG_PATH.read_bytes()).hexdigest()
