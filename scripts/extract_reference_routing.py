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
PAD_NET_RE = re.compile(r'\(net\s+(\d+)(?:\s+"[^"]*"|\s+[^\s\)]+)?\)')
WIDTH_RE = re.compile(r'\(width\s+([0-9.]+)\)')
VIA_AT_RE = re.compile(r'\(at\s+(-?[0-9.]+)\s+(-?[0-9.]+)')
REF_RE = re.compile(r'\(fp_text\s+reference\s+"?([^"\s\)]+)"?')

# Deliberately broad candidate classifier. Results are REVIEW_REQUIRED, not proof.
HS_NAME_RE = re.compile(
    r'^(?:PET\d+U?_[PN]|PER\d+U?_[PN]|UTX\d+(?:_CON)?_[PN]|URX\d+(?:_CON)?_[PN]|UD_[PN]|REFCLK_[PN])$',
    re.IGNORECASE,
)
USB_NAME_RE = re.compile(r'^(?:UTX\d+(?:_CON)?_[PN]|URX\d+(?:_CON)?_[PN]|UD_[PN])$', re.IGNORECASE)
PCIE_NAME_RE = re.compile(r'^(?:PET\d+U?_[PN]|PER\d+U?_[PN]|REFCLK_[PN])$', re.IGNORECASE)
PAIR_RE = re.compile(r'^(.*)_([PN])$', re.IGNORECASE)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _balanced_records(text: str, starts: tuple[str, ...]):
    """Yield balanced s-expression records beginning with one of ``starts``."""
    current = []
    depth = 0
    active = False
    kind = None
    for raw in text.splitlines():
        stripped = raw.lstrip()
        if not active:
            matched = next((s for s in starts if stripped.startswith(s)), None)
            if matched is None:
                continue
            active = True
            kind = matched[1:].split()[0]
            current = [raw]
            depth = raw.count('(') - raw.count(')')
            if depth <= 0:
                yield kind, '\n'.join(current)
                active = False
            continue
        current.append(raw)
        depth += raw.count('(') - raw.count(')')
        if depth <= 0:
            yield kind, '\n'.join(current)
            active = False
    if active:
        raise ValueError(f'unterminated {kind} record')


def _component_endpoints(text: str) -> tuple[dict[int, set[str]], int]:
    endpoints: dict[int, set[str]] = defaultdict(set)
    modules_seen = 0
    for _, block in _balanced_records(text, ('(module ', '(footprint ')):
        modules_seen += 1
        rm = REF_RE.search(block)
        if not rm:
            continue
        ref = rm.group(1)
        for line in block.splitlines():
            if '(pad ' not in line or '(net ' not in line:
                continue
            nm = PAD_NET_RE.search(line)
            if nm:
                endpoints[int(nm.group(1))].add(ref)
    return endpoints, modules_seen


def _domain_for_name(name: str) -> str:
    if USB_NAME_RE.match(name):
        return 'USB_CANDIDATE'
    if PCIE_NAME_RE.match(name):
        return 'PCIE_CANDIDATE'
    return 'UNCLASSIFIED'


def extract(path: Path) -> dict:
    text = path.read_text(errors='replace')
    nets = {}
    for line in text.splitlines():
        m = NET_RE.match(line)
        if m:
            nets[int(m.group(1))] = m.group(2)

    endpoints, modules_seen = _component_endpoints(text)
    per_net = defaultdict(lambda: {
        'segment_count': 0,
        'segment_length_mm': 0.0,
        'via_count': 0,
        'layers': set(),
        'widths_mm': set(),
        'via_transitions': defaultdict(int),
    })
    unassigned = {'segments': 0, 'vias': 0}

    for kind, record in _balanced_records(text, ('(segment ', '(via ')):
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
            am = VIA_AT_RE.search(record)
            if am:
                float(am.group(1)); float(am.group(2))

    rows = []
    by_name = {}
    for net_id, data in sorted(per_net.items()):
        name = nets.get(net_id, f'__NET_{net_id}')
        candidate = bool(HS_NAME_RE.match(name))
        row = {
            'net_id': net_id,
            'name': name,
            'candidate_high_speed': candidate,
            'candidate_domain': _domain_for_name(name),
            'classification': 'HEURISTIC_NAME_MATCH_REVIEW_REQUIRED' if candidate else 'UNCLASSIFIED',
            'component_endpoints': sorted(endpoints.get(net_id, set())),
            'segment_count': data['segment_count'],
            'segment_length_mm': round(data['segment_length_mm'], 5),
            'via_count': data['via_count'],
            'layers': sorted(data['layers']),
            'widths_mm': sorted(round(v, 6) for v in data['widths_mm']),
            'via_transitions': dict(sorted(data['via_transitions'].items())),
        }
        rows.append(row)
        by_name[name.upper()] = row

    pairs = []
    seen_bases = set()
    for row in rows:
        m = PAIR_RE.match(row['name'])
        if not m or not row['candidate_high_speed']:
            continue
        base = m.group(1)
        key = base.upper()
        if key in seen_bases:
            continue
        p = by_name.get(f'{key}_P')
        n = by_name.get(f'{key}_N')
        if not p or not n:
            continue
        seen_bases.add(key)
        domains = {p['candidate_domain'], n['candidate_domain']}
        domain = domains.pop() if len(domains) == 1 else 'AMBIGUOUS'
        pairs.append({
            'pair_base': base,
            'candidate_domain': domain,
            'classification': 'SUFFIX_PAIR_HEURISTIC_REVIEW_REQUIRED',
            'p_net': p['name'],
            'n_net': n['name'],
            'p_endpoints': p['component_endpoints'],
            'n_endpoints': n['component_endpoints'],
            'p_length_mm': p['segment_length_mm'],
            'n_length_mm': n['segment_length_mm'],
            'intra_pair_length_delta_mm': round(abs(p['segment_length_mm'] - n['segment_length_mm']), 5),
            'p_vias': p['via_count'],
            'n_vias': n['via_count'],
            'via_count_delta': abs(p['via_count'] - n['via_count']),
            'p_layers': p['layers'],
            'n_layers': n['layers'],
            'p_widths_mm': p['widths_mm'],
            'n_widths_mm': n['widths_mm'],
        })

    candidates = [r for r in rows if r['candidate_high_speed']]
    return {
        'status': 'DIAGNOSTIC_CONVERTED_REFERENCE_NOT_FABRICATION_AUTHORITY',
        'input': str(path),
        'input_sha256': _sha256(path),
        'module_count_seen': modules_seen,
        'net_count_declared': len(nets),
        'net_count_with_routing': len(rows),
        'high_speed_candidate_count': len(candidates),
        'candidate_pair_count': len(pairs),
        'unassigned_records': unassigned,
        'candidate_high_speed_pairs': pairs,
        'candidate_high_speed_nets': candidates,
        'all_routed_nets': rows,
        'interpretation': {
            'segment_length_mm': 'Sum of straight segment geometry in converted KiCad diagnostic output; verify against Altium/Gerber before design reuse.',
            'via_count': 'Count from converted KiCad diagnostic output; verify against source/fabrication files.',
            'component_endpoints': 'Pad-bearing reference designators recovered from converted KiCad output; conversion can omit or distort source data.',
            'candidate_domain': 'Signal-name heuristic only. USB/PCIe assignment is not authoritative until endpoints/source nets corroborate it.',
            'candidate_high_speed_pairs': 'P/N suffix pairing heuristic only; native Altium DifferentialPairs6 is the preferred pair-membership evidence.',
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
        'modules_seen': report['module_count_seen'],
        'net_count_with_routing': report['net_count_with_routing'],
        'high_speed_candidate_count': report['high_speed_candidate_count'],
        'candidate_pair_count': report['candidate_pair_count'],
        'candidate_names': [r['name'] for r in report['candidate_high_speed_nets']],
        'pair_bases': [r['pair_base'] for r in report['candidate_high_speed_pairs']],
    }, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
