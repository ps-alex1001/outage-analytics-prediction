"""Read-only PENELCO audit and conservative, evidence-based calibration.

Reported areas are substation/load-center labels (codes.xls, Sheet1, row 8).
Reference descriptions indicate possible service, never verified outage footprints.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

from outage_environment import require

CAUSE_MAP = {
    'supply': 'Supply Interruption', 'trees': 'Tree Contact',
    'major storm disaster': 'Weather / Storm', 'lightning': 'Weather / Storm',
    'equipment': 'Equipment / Technical', 'overload': 'Equipment / Technical',
    'other': 'Other', 'human being': 'Other', 'error': 'Other', 'unknown': 'Unknown',
}
CAUSES = ['Supply Interruption', 'Tree Contact', 'Weather / Storm',
          'Equipment / Technical', 'Other', 'Unknown', 'Animal Interference']


def read_codebook(path: Path) -> pd.DataFrame:
    """Extract all four code lists from actual legacy XLS cells, preserving codes."""
    sheet = pd.read_excel(path, sheet_name='Sheet1', header=None, dtype=object)
    rows = []
    for kind, a, b in [('cause', 0, 1), ('weather', 2, 3),
                       ('isolation_device', 5, 6), ('equipment_failed', 8, 9)]:
        for row in sheet.itertuples(index=False, name=None):
            code, label = str(row[a]).strip(), str(row[b]).strip()
            if re.fullmatch(r'\d{1,3}', code) and label != 'nan':
                rows.append({'code_type': kind, 'code': code.zfill(3), 'label': label.casefold()})
    result = pd.DataFrame(rows)
    require(not result.duplicated(['code_type', 'code']).any(), 'Duplicate codebook keys')
    require(len(result.query("code_type == 'cause'")) == 12, 'Unexpected cause codebook')
    return result


def location_mapping(area: str, circuit: str, names: dict) -> dict:
    """Curated interpretation of the retained reported listing.

    No inferred network topology or asset count enters the interpretation. Single
    municipality service descriptions give medium-confidence proxies only. Any
    listed boundary crossing or unsupported circuit is left ambiguous/unresolved.
    """
    candidates, evidence = [], ''
    a = area.strip().casefold()
    # These area/circuit combinations have explicit single-municipality service
    # descriptions in the corresponding station section of the supplied listing.
    rules = {
        'bagac': ({'1', '2', '3', '4'}, ['Bagac'], 'BAGAC feeders 1-4: Bagac barangays'),
        'abucay': ({'1', '2', '3', '4'}, ['Abucay'], 'ABUCAY feeders 1-4: Abucay locations'),
        'limay': ({'1', '2', '3', '4'}, ['Limay'], 'LIMAY OLD feeders 1-4: Limay locations'),
        'mariveles': ({'1', '2', '3'}, ['Mariveles'], 'MARIVELES feeders 1-3: Mariveles locations'),
        'cabcaben': ({'1', '2', '3'}, ['Mariveles'], 'CABCABEN feeders 1-3: Cabcaben/Lucanin/Batangas II'),
        'alas-asin': ({'1', '3', '4'}, ['Mariveles'], 'ALAS-ASIN feeders 1/3/4: Alas-asin/Sisiman'),
        'san benito': ({'2', '4'}, ['Dinalupihan'], 'SAN BENITO feeders 2/4: Luacan/Saguing/San Ramon'),
        'morong': ({'1', '2', '3', '4'}, ['Morong'], 'MORONG feeders 1-4: Sabang/Binaritan/Nagbalayong'),
        'pilar': ({'1', '2', '3', '4'}, ['Pilar'], 'PILAR feeders 1-4: Pilar locations; excludes Vistamall section'),
        'orani': ({'1', '2', '4'}, ['Orani'], 'ORANI feeders 1/2/4: Orani locations'),
        'samal': ({'1', '2'}, ['Samal'], 'SAMAL feeders 1/2: Samal locations'),
        'gugo': ({'1', '2'}, ['Samal'], 'GUGO feeders 1/2: Imelda/Gugo/Palili'),
        'dinalupihan': ({'3', '4'}, ['Dinalupihan'], 'TABACAN Dinalupihan feeders 3/4: Dinalupihan locations'),
        'hermosa': ({'2'}, ['Hermosa'], 'Both HERMOSA station feeder-2 descriptions lie in Hermosa'),
        'orion': ({'2', '3'}, ['Orion'], 'CALUNGUSAN feeders 2/3: Orion locations; feeder 4 includes Pilar'),
    }
    balanga = {'balanga tuyo', 'balanga new', 'balanga ibayo', 'balanga main', 'balanga mirror', 'tuyo'}
    if a in balanga:
        rules[a] = ({'1', '2', '3', '4'}, ['City of Balanga'],
                    'BALANGA MAIN/TUYO/IBAYO/MIRROR sections: named Balanga locations')
    parts = set(re.findall(r'\d+', str(circuit)))
    numeric = bool(parts) and not re.search(r'[A-Za-z]', str(circuit))
    if a in rules:
        supported, c, e = rules[a]
        if numeric and parts <= supported:
            candidates, evidence = c, e
    if not candidates:
        if a == 'gugo' and numeric and parts == {'3'}:
            candidates, evidence = ['Orani'], 'GUGO feeder 3 explicitly lists Portion of Orani'
        elif a == 'gugo' and numeric and parts == {'4'}:
            candidates, evidence = ['Abucay'], 'GUGO feeder 4 lists Abucay residence and Bangkal'
        elif a == 'samal':
            candidates, evidence = ['Samal', 'Orani'], 'SAMAL feeder 3 crosses into Orani; unsupported/composite circuit'
        elif a == 'gugo':
            candidates, evidence = ['Samal', 'Orani', 'Abucay'], 'GUGO feeders span three municipalities'
        elif a == 'pinulot':
            candidates, evidence = ['Dinalupihan', 'Hermosa'], 'PINULOT 3/4 includes Roosevelt/Colo and Tipo/Mabiga'
        elif a == 'hermosa':
            candidates, evidence = ['Hermosa', 'Dinalupihan'], 'HERMOSA 33MVA feeder 1 includes Layac/Daan Bago; station unspecified'
        elif a in {'balanga', 'balanga city', 'balanga cupang', 'cupang'}:
            candidates, evidence = ['City of Balanga', 'Pilar'], 'Generic Balanga/Cupang cannot distinguish VISTAMALL feeder 3 (Pilar)'
        elif a == 'orion':
            candidates, evidence = ['Orion', 'Pilar'], 'CALUNGUSAN feeder 4 includes Gacile (Pilar); circuit unsupported'
        elif a == 'dinalupihan':
            candidates, evidence = ['Dinalupihan', 'outside_or_unresolved'], 'TABACAN feeder 1 lists Bodega/Dampe/Batiawan; extent unverified'
        elif a in rules:
            candidates, evidence = rules[a][1] + ['outside_or_unresolved'], 'Area resembles station but reported circuit lacks matching service evidence'
        else:
            evidence = 'No defensible station/circuit crosswalk; no assignment (includes corrupted Doña and Palihan/Lamao aliases)'
    resolved = len(candidates) == 1 and candidates[0] in names
    return {'mapping_status': 'resolved' if resolved else ('ambiguous' if candidates else 'unresolved'),
            'municipality': candidates[0] if resolved else '',
            'municipality_psgc': names[candidates[0]] if resolved else '',
            'candidate_municipalities': json.dumps(candidates),
            'mapping_confidence': 'medium_reference_proxy' if resolved else 'insufficient',
            'mapping_evidence': evidence,
            'mapping_source': 'penelco_reported_listing.txt; codes.xls Sheet1 load-center header',
            'verified_outage_footprint': False}


def inspect_penelco(path: Path, codebook: Path, geography: pd.DataFrame):
    d = pd.read_csv(path, dtype=str, keep_default_na=False)
    required = ['record_id', 'interruption_start', 'duration_minutes', 'reported_area', 'circuit', 'cause']
    require(all(k in d for k in required), 'Missing PENELCO schema')
    require(d.record_id.is_unique, 'Duplicate PENELCO IDs need review')
    require(d[required].ne('').all().all(), 'Missing PENELCO required values')
    d['start_time'] = pd.to_datetime(d.interruption_start, errors='raise').dt.tz_localize('Asia/Manila')
    d['duration_minutes'] = pd.to_numeric(d.duration_minutes, errors='raise')
    require(np.isfinite(d.duration_minutes).all() and d.duration_minutes.gt(0).all(), 'Invalid observed duration')
    cb = read_codebook(codebook)
    for kind in ['cause', 'weather', 'isolation_device', 'equipment_failed']:
        lookup = cb[cb.code_type.eq(kind)].set_index('code').label
        codes = d[kind + '_code'].str.zfill(3)
        matches = codes.map(lookup).eq(d[kind].str.casefold().str.strip())
        d[kind + '_codebook_valid'] = matches
        # Cause mismatches would invalidate calibration. Other code domains
        # remain auditable context; an unmapped device must not erase an event.
        if kind == 'cause':
            require(matches.all(), 'Cause code/label mismatch affects calibration')
    d['scheduled'] = d.cause.str.casefold().eq('scheduled')
    d['cause_category'] = d.cause.str.casefold().map(CAUSE_MAP).fillna('Scheduled')
    require(set(d.cause.str.casefold()) <= set(CAUSE_MAP) | {'scheduled'}, 'Unmapped observed cause')
    keys = [c for c in d.columns if c not in {'record_id', 'start_time', 'scheduled', 'cause_category'} and not c.endswith('_codebook_valid')]
    d['questionable_duplicate'] = d.duplicated(keys, keep=False)
    # Retain questionable records: no source evidence distinguishes legitimate repeats.
    names = geography[['municipality', 'municipality_psgc']].drop_duplicates().set_index('municipality').municipality_psgc.to_dict()
    mapping = d[['reported_area', 'circuit']].drop_duplicates().sort_values(['reported_area', 'circuit'])
    mapping = pd.concat([mapping.reset_index(drop=True), pd.DataFrame([
        location_mapping(a, c, names) for a, c in mapping.itertuples(index=False, name=None)])], axis=1)
    d = d.merge(mapping, on=['reported_area', 'circuit'], validate='many_to_one')
    d['year'] = d.start_time.dt.year
    d['month'] = d.start_time.dt.month
    d['hour'] = d.start_time.dt.hour
    d['season'] = np.where(d.month.between(5, 10), 'May-October descriptive bin', 'November-April descriptive bin')
    return d, cb, mapping


def calibration_tables(observed: pd.DataFrame, v1: pd.DataFrame, geography: pd.DataFrame,
                       animal_share: float, geo_mode='auto'):
    eligible = observed.loc[~observed.scheduled]
    mapped = eligible.loc[eligible.mapping_status.eq('resolved')]
    require(len(mapped) > 0, 'No supportable geographical evidence')
    municipalities = geography[['municipality_psgc', 'municipality']].drop_duplicates().sort_values('municipality_psgc')
    table = municipalities.copy()
    table['resolved_observed_count'] = table.municipality.map(mapped.municipality.value_counts()).fillna(0).astype(int)
    table['resolved_observed_share'] = table.resolved_observed_count / len(mapped)
    table['v1_share'] = table.municipality.map(v1.municipality.value_counts(normalize=True)).fillna(0)
    require(np.isclose(table.v1_share.sum(), 1), 'V1 geographic reference not complete')
    coverage = len(mapped) / len(eligible)
    sufficient = coverage >= .9 and table.resolved_observed_count.ge(20).all()
    mode = ('resolved' if sufficient else 'conservative_mixture') if geo_mode == 'auto' else geo_mode
    require(mode in {'resolved', 'conservative_mixture'}, 'Invalid geography mode')
    # Unresolved mass uses V1's existing environmental simulation as a prior,
    # not an imputed location for any real record. No observed row is reassigned.
    table['applied_target_share'] = (table.resolved_observed_share if mode == 'resolved' else
                                    coverage * table.resolved_observed_share + (1 - coverage) * table.v1_share)
    table['geographic_allocation_mode'] = mode
    causes = eligible.cause_category.value_counts().reindex(CAUSES, fill_value=0)
    ct = pd.DataFrame({'cause_category': CAUSES, 'observed_count': causes.to_numpy()})
    ct['observed_share'] = ct.observed_count / len(eligible)
    ct['target_share'] = ct.observed_share
    require(0 <= animal_share < float(ct.loc[ct.cause_category.eq('Other'), 'target_share'].iloc[0]), 'Animal share must come from Other')
    ct.loc[ct.cause_category.eq('Other'), 'target_share'] -= animal_share
    ct.loc[ct.cause_category.eq('Animal Interference'), 'target_share'] = animal_share
    ct['synthetic_only_assumption'] = ct.cause_category.eq('Animal Interference')
    meta = {'total_records': len(observed), 'scheduled_excluded': int(observed.scheduled.sum()),
            'eligible_unscheduled': len(eligible), 'resolved_records': len(mapped), 'resolved_fraction': coverage,
            'ambiguous_records': int(eligible.mapping_status.eq('ambiguous').sum()),
            'unresolved_records': int(eligible.mapping_status.eq('unresolved').sum()),
            'geographic_mode': mode, 'provincewide_calibration_supported': bool(sufficient),
            'minimum_coverage_criterion': .9, 'minimum_resolved_per_municipality': 20,
            'penelco_timezone': 'Asia/Manila assumed from local utility reporting; source has no UTC offset',
            'duplicate_policy': 'Retain all unique source IDs, flag identical-content records; provide exclusion sensitivity',
            'codebook_validation': {kind: {'mismatched_records': int((~observed[kind + '_codebook_valid']).sum())}
                                   for kind in ['cause', 'weather', 'isolation_device', 'equipment_failed']},
            'location_semantics': 'Substation/load-center field. Resolved means reference-supported municipality proxy, not verified footprint',
            'selection_bias': 'Resolution depends on station and circuit; cross-municipality service and NGCP/MF cases disproportionately excluded',
            'annual_reporting': 'Completeness unknown. 2026 observed through August is partial. No annual observed frequency targets',
            'animal_share': animal_share}
    return table.reset_index(drop=True), ct, meta


def fit_duration_models(observed: pd.DataFrame):
    """Cause-specific lognormal median/p90 fit; small categories pool to Other."""
    eligible = observed.loc[~observed.scheduled]
    rows = []
    for c in CAUSES:
        x = eligible.loc[eligible.cause_category.eq(c), 'duration_minutes']
        source = c
        if len(x) < 30:
            x = eligible.loc[eligible.cause_category.eq('Other'), 'duration_minutes']
            source = 'Other pooled (synthetic-only or n<30)'
        median, q90 = float(x.median()), float(x.quantile(.9))
        rows.append({'cause_category': c, 'fit_count': len(x), 'fit_source': source,
                     'mu': np.log(median), 'sigma': max(.15, np.log(q90 / median) / 1.2815515655446004),
                     'observed_median_minutes': median, 'observed_p90_minutes': q90})
    return pd.DataFrame(rows)
