import hashlib, json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
CONFIG_PATH=ROOT/'hardware/rev-a/board.json'
def load(): return json.loads(CONFIG_PATH.read_text())
def sha256(): return hashlib.sha256(CONFIG_PATH.read_bytes()).hexdigest()
