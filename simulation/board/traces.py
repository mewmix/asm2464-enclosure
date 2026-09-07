"""Conservative functional comparison; never guesses which backend is wrong."""
def normalize(events):
    return [{k:v for k,v in e.items() if k!='tick'} for e in events]
def compare(a,b):
    if a==b:return dict(classification='exact match',equal=True)
    if normalize(a)==normalize(b):return dict(classification='timing-only difference',equal=True)
    n=min(len(a),len(b))
    first=next((i for i in range(n) if normalize([a[i]])!=normalize([b[i]])),n)
    return dict(classification='unknown/unmodeled behavior',equal=False,first_difference=first,
                reason='Evidence is required to attribute discrepancy to CPU or RTL; no event reordering is silently discarded.')
