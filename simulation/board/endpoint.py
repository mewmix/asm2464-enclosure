"""External endpoint stimulus; protocol responses remain in the pinned backend.

The upstream endpoint implementation is coupled to HardwareState. This board
object owns only presence/link stimuli. It does not invent a second NVMe engine.
"""
from dataclasses import dataclass

@dataclass
class DownstreamEndpoint:
    present: bool = True
    link_up: bool = True
    generation: int = 0
