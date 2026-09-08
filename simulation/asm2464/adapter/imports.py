"""Explicit namespaced loader for unchanged upstream legacy imports.

Only the two legacy dependency names used by HardwareState/Pyrite are mapped.
No sys.path mutation, top-level module alias, emulator CLI or live transport.
"""
import builtins
import importlib.util
import sys
from pathlib import Path

UP = Path(__file__).resolve().parents[1] / 'upstream'

def _load(name, path, dependencies=None):
    name = '_enclosure_asm2464_' + name
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, UP / path)
    module = importlib.util.module_from_spec(spec)
    original = builtins.__import__
    def importing(name, globals=None, locals=None, fromlist=(), level=0):
        if not level and dependencies and name in dependencies:
            return dependencies[name]
        return original(name, globals, locals, fromlist, level)
    module.__dict__['__builtins__'] = {**vars(builtins), '__import__': importing}
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        del sys.modules[name]
        raise
    return module

CPU8051 = _load('cpu', 'emulate/cpu.py').CPU8051
Memory = _load('memory', 'emulate/memory.py').Memory
_tcg = _load('tcg', 'tools/tcg_session.py')
_pyrite = _load('pyrite', 'emulate/pyrite.py', {'tools.tcg_session': _tcg})
_hardware = _load('hardware', 'emulate/hardware.py', {'pyrite': _pyrite})
HardwareState = _hardware.HardwareState
create_hardware_hooks = _hardware.create_hardware_hooks
