"""Capacity evidence does not establish a flash part or its command timings."""
from copy import deepcopy

STOCK_SIZE = 0x80000
PHYSICAL_BASELINE = '47aed2cd0fba21011b4f69ad34781e2c924b7f48'

def select(candidate, name):
    profile = deepcopy(candidate)
    profile['profile'] = name
    profile['field_evidence'] = {key: 'REV_A_DESIGN_ASSUMPTION' for key in candidate}
    for key in candidate:
        if key.endswith('_ticks'):
            profile['field_evidence'][key] = 'EMULATOR_MODEL_ONLY'
    if name == 'stock_physical':
        profile.update(capacity=STOCK_SIZE, part=None, jedec_hex=None,
                       evidence='PHYSICAL_GEOMETRY_CONFIRMED_BY_TWO_FULL_DEVICE_DUMPS',
                       physical_baseline_commit=PHYSICAL_BASELINE)
        profile['field_evidence'].update(capacity='PHYSICALLY_PROVEN',part='UNKNOWN',jedec_hex='UNKNOWN')
        for key in ('page_size','sector_size','block_size'):
            profile['field_evidence'][key]='EMULATOR_MODEL_ONLY'
    elif name != 'rev_a_candidate':
        raise ValueError('unknown flash profile')
    profile['boundary_policy']='CONSERVATIVE_SAFETY_POLICY: reject out-of-range, page-crossing and unaligned erase; no wrap inferred'
    return profile
