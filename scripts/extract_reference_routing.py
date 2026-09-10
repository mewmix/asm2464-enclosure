#!/usr/bin/env python3
"""Extract auditable routing metrics from a converted reference KiCad PCB.

The converted board is a diagnostic intermediate only. Altium remains the source
artifact and fabrication Gerbers remain the geometry oracle. Native Altium
DifferentialPairs6 records, when supplied, are authoritative for pair membership;
converted geometry remains diagnostic until checked against source/fabrication data.
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
REF_RE = re.compile(r'\(fp_text\s+reference\s+"?([^"\s\)]+)"?', re.IGNORECASE)
PAIR_SUFFIX_RE = re.compile(r'^(.*)_([PN])$', re.IGNORECASE)

HS_NAME_RE = re.compile(
    r'^(?:PET\d+U?_[PN]|PER\d+U?_[PN]|UTX\d+(?:_CON)?_[PN]|URX\d+(?:_CON)?_[PN]|UD_[PN]|REFCLK_[PN])$',
    re.IGNORECASE,
)
USB_NAME_RE = re.compile(r'^(?:UTX\d+(?:_CON)?_[PN]|URX\d+(?:_CON)?_[PN]|UD_[PN])$', re.IGNORECASE)
PCIE_NAME_RE = re.compile(r'^(?:PET\d+U?_[PN]|PER\d+U?_[PN]|REFCLK_[PN])$', re.IGNORECASE)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _balanced_records(text: str, starts: tuple[str, ...]):
    """Yield balanced s-expression records beginning with one of ``starts``."""
    current: list[str] = []
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


def _component_endpoints(text: str) -> tuple[dict[int, set[str]], int, int]:
    endpoints: dict[int, set[str]] = defaultdict(set)
    modules_seen = 0
    pads_with_nets = 0
    for _, block in _balanced_records(text, ('(module ', '(footprint ')):
        modules_seen += 1
        rm = REF_RE.search(block)
        if not rm:
            continue
        ref = rm.group(1)
        for _, pad in _balanced_records(block, ('(pad ',)):
            nm = PAD_NET_RE.search(pad)
            if nm:
                endpoints[int(nm.group(1))].add(ref)
                pads_with_nets += 1
    return endpoints, modules_seen, pads_with_nets


def _domain_for_name(name: str) -> str:
    if USB_NAME_RE.match(name):
        return 'USB_CANDIDATE'
    if PCIE_NAME_RE.match(name):
        return 'PCIE_CANDIDATE'
    return 'UNCLASSIFIED'


def _pipe_fields(line: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for item in line.strip().split('|'):
        if '=' in item:
            key, value = item.split('=', 1)
            fields[key] = value
    return fields


def load_native_pairs(path: Path | None) -> list[dict]:
    if path is None:
        return []
    pairs = []
    for line in path.read_text(errors='replace').splitlines():
        d = _pipe_fields(line)
        if not {'POSITIVENETNAME', 'NEGATIVENETNAME', 'NAME'} <= d.keys():
            continue
        pairs.append({
            'name': d['NAME'],
            'positive_net': d['POSITIVENETNAME'],
            'negative_net': d['NEGATIVENETNAME'],
            'layer': d.get('LAYER'),
            'user_routed': d.get('USERROUTED'),
            'unique_id': d.get('UNIQUEID'),
            'evidence': 'ALTIUM_DIFFERENTIALPAIRS6',
        })
    return pairs


def _pair_rows(rows: list[dict], native_pairs: list[dict]) -> tuple[list[dict], str]:
    by_name = {r['name'].upper(): r for r in rows}
    pair_specs = []
    evidence = 'SUFFIX_PAIR_HEURISTIC_REVIEW_REQUIRED'
    if native_pairs:
        pair_specs = native_pairs
        evidence = 'ALTIUM_DIFFERENTIALPAIRS6'
    else:
        seen = set()
        for row in rows:
            m = PAIR_SUFFIX_RE.match(row['name'])
            if not m or not row['candidate_high_speed']:
                continue
            base = m.group(1)
            key = base.upper()
            if key in seen:
                continue
            p = by_name.get(f'{key}_P')
            n = by_name.get(f'{key}_N')
            if p and n:
                seen.add(key)
                pair_specs.append({
                    'name': base,
                    'positive_net': p['name'],
                    'negative_net': n['name'],
                    'layer': None,
                    'user_routed': None,
                    'unique_id': None,
                    'evidence': evidence,
                })

    result = []
    for spec in pair_specs:
        p = by_name.get(spec['positive_net'].upper())
        n = by_name.get(spec['negative_net'].upper())
        if not p or not n:
            result.append({
                'pair_base': spec['name'],
                'classification': spec['evidence'],
                'positive_net': spec['positive_net'],
                'negative_net': spec['negative_net'],
                'routing_join_status': 'MISSING_CONVERTED_NET',
            })
            continue
        domains = {p['candidate_domain'], n['candidate_domain']}
        domain = domains.pop() if len(domains) == 1 else 'AMBIGUOUS'
        result.append({
            'pair_base': spec['name'],
            'candidate_domain': domain,
            'classification': spec['evidence'],
            'native_layer': spec.get('layer'),
            'native_user_routed': spec.get('user_routed'),
            'native_unique_id': spec.get('unique_id'),
            'positive_net': p['name'],
            'negative_net': n['name'],
            'positive_endpoints': p['component_endpoints'],
            'negative_endpoints': n['component_endpoints'],
            'positive_segment_length_mm': p['segment_length_mm'],
            'negative_segment_length_mm': n['segment_length_mm'],
            'converted_straight_segment_delta_mm': round(abs(p['segment_length_mm'] - n['segment_length_mm']), 5),
            'positive_vias': p['via_count'],
            'negative_vias': n['via_count'],
            'via_count_delta': abs(p['via_count'] - n['via_count']),
            'positive_layers': p['layers'],
            'negative_layers': n['layers'],
            'positive_widths_mm': p['widths_mm'],
            'negative_widths_mm': n['widths_mm'],
            'routing_join_status': 'JOINED',
        })
    return result, evidence


def extract(path: Path, native_pairs_path: Path | None = None) -> dict:
    text = path.read_text(errors='replace')
    nets = {}
    for line in text.splitlines():
        m = NET_RE.match(line)
        if m:
            nets[int(m.group(1))] = m.group(2)

    endpoints, modules_seen, pads_with_nets = _component_endpoints(text)
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
    for net_id, data in sorted(per_net.items()):
        name = nets.get(net_id, f'__NET_{net_id}')
        candidate = bool(HS_NAME_RE.match(name))
        rows.append({
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
        })

    native_pairs = load_native_pairs(native_pairs_path)
    pair_rows, pair_evidence = _pair_rows(rows, native_pairs)
    candidates = [r for r in rows if r['candidate_high_speed']]
    return {
        'status': 'DIAGNOSTIC_CONVERTED_REFERENCE_NOT_FABRICATION_AUTHORITY',
        'input': str(path),
        'input_sha256': _sha256(path),
        'native_pair_input': str(native_pairs_path) if native_pairs_path else None,
        'pair_membership_evidence': pair_evidence,
        'module_count_seen': modules_seen,
        'pads_with_nets_seen': pads_with_nets,
        'net_count_declared': len(nets),
        'net_count_with_routing': len(rows),
        'high_speed_candidate_count': len(candidates),
        'candidate_pair_count': len(pair_rows),
        'unassigned_records': unassigned,
        'candidate_high_speed_pairs': pair_rows,
        'candidate_high_speed_nets': candidates,
        'all_routed_nets': rows,
        'interpretation': {
            'pair_membership': 'ALTIUM_DIFFERENTIALPAIRS6 is source-native evidence when present; suffix pairing is fallback only.',
            'segment_length_mm': 'Sum of straight segment geometry in converted KiCad diagnostic output; not true electrical length or verified skew.',
            'converted_straight_segment_delta_mm': 'Difference of converted straight-segment sums; do not treat as pair skew until verified against native Tracks6/Gerber.',
            'via_count': 'Count from converted KiCad diagnostic output; verify against source/fabrication files.',
            'component_endpoints': 'Pad-bearing reference designators recovered from converted KiCad output; conversion can omit or distort source data.',
            'candidate_domain': 'Signal-name heuristic only. USB/PCIe assignment is not authoritative until endpoints/source nets corroborate it.',
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('pcb', type=Path)
    ap.add_argument('--native-pairs', type=Path)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    report = extract(args.pcb, args.native_pairs)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({
        'status': report['status'],
        'modules_seen': report['module_count_seen'],
        'pads_with_nets_seen': report['pads_with_nets_seen'],
        'net_count_with_routing': report['net_count_with_routing'],
        'high_speed_candidates': report['high_speed_candidate_count'],
        'candidate_pairs': report['candidate_pair_count'],
        'pair_membership_evidence': report['pair_membership_evidence'],
        'pair_bases': [r['pair_base'] for r in report['candidate_high_speed_pairs']],
    }, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
