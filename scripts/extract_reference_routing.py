#!/usr/bin/env python3
"""Extract auditable routing metrics from a converted reference KiCad PCB.

The converted board is a diagnostic intermediate only. Altium remains the source
artifact and fabrication Gerbers remain the geometry oracle. This script never
turns heuristic high-speed name matching into electrical proof.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import defaultdict
from pathlib import Path

NET_RE = re.compile(r'^\s*\(net\s+(\d+)\s+"(.*)"\)\s*$')
START_RE = re.compile(r'\(start\s+(-?[0-9.]+)\s+(-?[0-9.]+)\)')
END_RE = re.compile(r'\(end\s+(-?[0-9.]+)\s+(-?[0-9.]+)\)')
LAYER_RE = re.compile(r'\(layer\s+"?([^"\s\)]+)"?\)')
LAYERS_RE = re.compile(r'\(layers\s+"?([^"\s\)]+)"?\s+"?([^"\s\)]+)"?\)')
NET_ID_RE = re.compile(r'\(net\s+(\d+)\)')
WIDTH_RE = re.compile(r'\(width\s+([0-9.]+)\)')
VIA_AT_RE = re.compile(r'\(at\s+(-?[0-9.]+)\s+(-?[0-9.]+)')

# Deliberately broad candidate classifier. Results are REVIEW_REQUIRED, not proof.
HS_NAME_RE = re.compile(
    r'(USB|PCIE|PCI_E|SSTX|SSRX|TX\d*[PN]|RX\d*[PN]|PET[PN]|PER[PN]|TB[RT]X|DP$|DN$)',
    re.IGNORECASE,
)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _record_lines(text: str):
    """Yield balanced top-level segment/via records, including multiline forms."""
    current = []
    depth = 0
    active = False
    kind = None
    for raw in text.splitlines():
        stripped = raw.lstrip()
        if not active and (stripped.startswith('(segment ') or stripped.startswith('(via ')):
            active = True
            kind = 'segment' if stripped.startswith('(segment ') else 'via'
            current = [raw]
            depth = raw.count('(') - raw.count(')')
            if depth <= 0:
                yield kind, ' '.join(current)
                active = False
            continue
        if active:
            current.append(raw)
            depth += raw.count('(') - raw.count(')')
            if depth <= 0:
                yield kind, ' '.join(current)
                active = False
    if active:
        raise ValueError('unterminated segment/via record')


def extract(path: Path) -> dict:
    text = path.read_text(errors='replace')
    nets = {}
    for line in text.splitlines():
        m = NET_RE.match(line)
        if m:
            nets[int(m.group(1))] = m.group(2)

    per_net = defaultdict(lambda: {
        'segment_count': 0,
        'segment_length_mm': 0.0,
        'via_count': 0,
        'layers': set(),
        'widths_mm': set(),
        'via_transitions': defaultdict(int),
    })
    unassigned = {'segments': 0, 'vias': 0}

    for kind, record in _record_lines(text):
        nm = NET_ID_RE.search(record)
        if not nm:
            unassigned['segments' if kind == 'segment' else 'vias'] += 1
            continue
        net_id = int(nm.group(1))
        entry = per_net[net_id]
        if kind == 'segment':
            sm, em = START_RE.search(record), END_RE.search(record)
            if sm and em:
                x1, y1 = float(sm.group(1)), float(sm.group(2))
                x2, y2 = float(em.group(1)), float(em.group(2))
                entry['segment_length_mm'] += math.hypot(x2 - x1, y2 - y1)
            lm = LAYER_RE.search(record)
            if lm:
                entry['layers'].add(lm.group(1))
            wm = WIDTH_RE.search(record)
            if wm:
                entry['widths_mm'].add(float(wm.group(1)))
            entry['segment_count'] += 1
        else:
            entry['via_count'] += 1
            lm = LAYERS_RE.search(record)
            if lm:
                a, b = lm.group(1), lm.group(2)
                entry['layers'].update((a, b))
                entry['via_transitions'][f'{a}->{b}'] += 1
            # Force parse of coordinates when present to catch malformed conversion.
            am = VIA_AT_RE.search(record)
            if am:
                float(am.group(1)); float(am.group(2))

    rows = []
    for net_id, data in sorted(per_net.items()):
        name = nets.get(net_id, f'__NET_{net_id}')
        rows.append({
            'net_id': net_id,
            'name': name,
            'candidate_high_speed': bool(HS_NAME_RE.search(name)),
            'classification': 'HEURISTIC_NAME_MATCH_REVIEW_REQUIRED' if HS_NAME_RE.search(name) else 'UNCLASSIFIED',
            'segment_count': data['segment_count'],
            'segment_length_mm': round(data['segment_length_mm'], 5),
            'via_count': data['via_count'],
            'layers': sorted(data['layers']),
            'widths_mm': sorted(round(v, 6) for v in data['widths_mm']),
            'via_transitions': dict(sorted(data['via_transitions'].items())),
        })

    candidates = [r for r in rows if r['candidate_high_speed']]
    return {
        'status': 'DIAGNOSTIC_CONVERTED_REFERENCE_NOT_FABRICATION_AUTHORITY',
        'input': str(path),
        'input_sha256': _sha256(path),
        'net_count_declared': len(nets),
        'net_count_with_routing': len(rows),
        'high_speed_candidate_count': len(candidates),
        'unassigned_records': unassigned,
        'candidate_high_speed_nets': candidates,
        'all_routed_nets': rows,
        'interpretation': {
            'segment_length_mm': 'Sum of straight segment geometry in converted KiCad diagnostic output; verify against Altium/Gerber before design reuse.',
            'via_count': 'Count from converted KiCad diagnostic output; verify against source/fabrication files.',
            'candidate_high_speed': 'Name-based heuristic only; pair/domain membership requires source-net evidence.',
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('pcb', type=Path)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    report = extract(args.pcb)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({
        'status': report['status'],
        'net_count_with_routing': report['net_count_with_routing'],
        'high_speed_candidate_count': report['high_speed_candidate_count'],
        'candidate_names': [r['name'] for r in report['candidate_high_speed_nets']],
    }, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
