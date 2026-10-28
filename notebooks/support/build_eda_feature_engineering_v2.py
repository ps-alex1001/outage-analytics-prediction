"""Author the requested teaching notebook with nbformat; execute separately."""
from pathlib import Path
import nbformat as nbf

ROOT=Path(__file__).resolve().parents[2] if Path(__file__).parent.name=='support' else Path(__file__).resolve().parents[1]
cells=[]
def md(text):cells.append(nbf.v4.new_markdown_cell(text.strip()))
def code(text):cells.append(nbf.v4.new_code_cell(text.strip()))

md(r'''
# Synthetic Outage V2: EDA, feature engineering and ML-ready data

**GIS-Based Electrical Outage Analytics and Prediction System · Bataan, Philippines**  
Audience: undergraduate data-science students and thesis reviewers. One barangay × one hourly cutoff; one-hour-ahead **synthetic onset classification**. No model is trained in this notebook.

## 1. Introduction and study objective

We investigate the event simulator and environmental sources, turn past conditions into interpretable candidate predictors, audit leakage, and validate the resulting V2 panel. The canonical panel preserves every barangay-hour and every eligible negative. Excluded observations have a null target and an explicit reason.

**Read this distinction first.** The transformations obey observation-time ordering, but historical reanalysis is delivered and revised later. These files support retrospective research, not a verified live forecasting contract. Synthetic associations cannot demonstrate real PENELCO failure mechanisms or forecasting accuracy.

### Executive summary — populated from independently verified outputs

The next cell reads the completed builder's small audit artifacts. It does not load or rebuild the full panel. If they are missing, first run the commands in Section 16. Use the repository's uv-managed interpreter; there are no installation or network cells.
''')
code('''
from pathlib import Path
import json, sys, hashlib
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import matplotlib.pyplot as plt
from IPython.display import display, Markdown

ROOT = Path.cwd()
if not (ROOT / 'scripts/build_ml_dataset_v2.py').is_file():
    ROOT = ROOT.parent
assert (ROOT / 'scripts/build_ml_dataset_v2.py').is_file(), 'Start in the repository root or notebooks/'
sys.path.insert(0, str(ROOT / 'scripts'))
from build_ml_dataset_v2 import (engineer_weather, engineer_cape, calendar_features,
    label_arrays, local, markdown_table, CANDIDATES, ENV_ENGINEERED, CALENDAR, RAW, HOUR, TZ)

OUT = ROOT / 'data/ml/v2'
AUDIT = OUT / 'audit'
meta = json.loads((OUT / 'ml_dataset_metadata.json').read_text())
assert hashlib.sha256((ROOT/'scripts/build_ml_dataset_v2.py').read_bytes()).hexdigest() == meta['code_sha256']
validation = json.loads((AUDIT / 'validation.json').read_text())
assert validation['status'] == 'passed' and validation['rows'] == meta['rows']
quality = json.loads((AUDIT / 'source_inspection.json').read_text())
legacy = json.loads((AUDIT / 'legacy_audit.json').read_text())
manifest = pd.read_csv(OUT / 'ml_feature_manifest.csv')
balance = pd.read_csv(OUT / 'ml_class_balance.csv', dtype={'year': str})
predictors = json.loads((OUT / 'ml_predictor_columns.json').read_text())
splits = json.loads((OUT / 'ml_split_manifest.json').read_text())
FIGURES = AUDIT / 'figures'
FIGURES.mkdir(exist_ok=True)
pd.set_option('display.max_columns', 12)
pd.set_option('display.max_rows', 25)
plt.rcParams.update({'figure.figsize': (10, 4.8), 'font.size': 11,
    'axes.spines.top': False, 'axes.spines.right': False,
    'axes.grid': True, 'grid.alpha': .18, 'axes.axisbelow': True,
    'savefig.dpi': 150, 'figure.facecolor': 'white'})
BLUE, ORANGE, CHARCOAL = '#28618f', '#c17428', '#263746'

def finish(name):
    plt.tight_layout()
    plt.savefig(FIGURES / f'{name}.png', bbox_inches='tight')
    plt.show()

def source_path(name):
    original = Path(meta['sources'][name]['path'])
    if original.is_file(): return original
    matches = list((ROOT/'data').rglob(original.name))
    assert len(matches) == 1, f'Resolve relocated source {name}'
    return matches[0]

prevalence = 100 * meta['positive_rows'] / meta['eligible_rows']
display(Markdown(f"""**Verified panel:** {meta['rows']:,} rows × {meta['columns']} columns across
{quality['geography']['barangays']} barangays and {quality['geography']['municipalities']} municipalities.
**Eligible:** {meta['positive_rows']:,} positive and {meta['negative_rows']:,} negative hours
({prevalence:.5f}% positive); {meta['excluded_rows']:,} rows are masked.
**Features:** {meta['engineered_created']} engineered candidates created, {meta['engineered_retained']} retained
within an explicit {len(predictors)}-predictor matrix. NDVI and the wind–NDVI interaction are deferred.
**Validation:** every key, label, eligibility state and all {validation['feature_values_checked']:,}
retained feature values were independently recomputed. Chronological splits are 2019–2023 / 2024 / 2025–June 2026.
The underlying observations remain retrospective; no model or forecasting claim is produced."""))
''')
md('''
## 2. Datasets and data provenance

**Question:** Which actual files control the analysis? **Why:** similarly named V1, V2, and topology-impact files have different purposes. **Method:** verify source hashes and inspect the recorded source inventory. V2's original origin-barangay event CSV supplies labels; boundaries supply the geographic universe; municipal weather, ERA5, terrain and annual NDVI supply analytical context. The old panel is independently scanned for its structure and quality, and its labels are discarded.

Only small audits and a clearly identified comparison sample are loaded for presentation. The reusable builder processes monthly partitions. Read the full paths and SHA-256 hashes in `ml_dataset_metadata.json` when transferring the project.
''')
code('''
inventory = pd.DataFrame([{'source': k, 'file': Path(v['path']).name,
    'MiB': round(v['bytes']/2**20, 3), 'SHA256 prefix': v['sha256'][:16]}
    for k,v in meta['sources'].items()])
display(inventory)
events = pd.read_csv(source_path('events'), dtype={'barangay_psgc': str, 'municipality_psgc': str})
for col in ['start_time', 'end_time']:
    events[col] = pd.to_datetime(events[col], utc=True, format='mixed').dt.tz_convert(TZ)
assert hashlib.sha256(source_path('events').read_bytes()).hexdigest() == meta['sources']['events']['sha256']
terrain = pd.read_csv(source_path('terrain'), dtype={'barangay_psgc': str,'municipality_psgc': str})
ndvi = pd.read_csv(source_path('ndvi'), dtype={'barangay_psgc': str})
geo = pd.read_csv(AUDIT/'geographic_crosswalk.csv', dtype={'barangay_psgc': str,'municipality_psgc': str})
display(Markdown(f"""**Observed:** {len(events):,} V2 events; the previous analytical panel contains
{legacy['rows']:,} rows and {legacy['columns']} columns in {legacy['row_groups']} monthly row groups.
Its {legacy['positive_v1_same_hour_rows']:,} old same-hour positive rows are not the new one-hour-forward labels.
**Decision:** reuse the verified geographic/hourly framework and source-transform approach; rebuild all labels from V2."""))
''')
md(r'''
## 3. Understanding the ML prediction target

**Question:** What exactly is being predicted, and when? For cutoff $t$, define

$$y_{b,t}=\mathbb{1}\{\exists\ e:\ e.\mathrm{origin}=b,\ t<e.\mathrm{start}\leq t+1\mathrm{h}\}.$$

An event at 10:00 belongs to cutoff **09:00**, not 10:00. A subhour start at 10:30 belongs to cutoff 10:00. The general mapping is `ceil(start_time, 1 hour) - 1 hour`.

An outage is active on **[start, end)**: onset is included; restoration is excluded. An active cutoff is outside the at-risk onset population, so its target is **null**, never an ordinary negative. The final cutoff lacks a complete future window and is also null. The one cutoff before each new split is purged because its right-closed label touches that split. Cutoff calendar features consistently describe $t$.

**Information constraint:** every environmental window ends at an observation label no later than $t-1$ hour. This prevents future-valid-time leakage but does not establish historical publication availability. The distinction is carried in the dataset metadata and Section 16.
''')
code('''
reconciliation = meta['label_reconciliation']
display(pd.DataFrame([{'check':k,'count':v} for k,v in reconciliation.items()]))
display(Markdown(f"""**Observed:** {reconciliation['events_outside_cutoff_grid']} source event(s) have no preceding
cutoff on the reused grid; {reconciliation['events_in_masked_windows']} event(s) map to masked windows;
{reconciliation['multiple_start_windows']} windows contain multiple starts.
**Decision:** report these losses explicitly rather than forcing the number of positives to equal the source event count.
The last source timestamp is retained for audit, with an incomplete-window mask."""))
''')
md('''
## 4. Initial dataset inspection

**Question:** Are the events and geographic identifiers internally consistent? **Method:** the builder checks all event IDs, origin PSGCs and their authoritative parents, required fields, dates, durations and categories. Shared timestamps in different barangays represent concurrent simulated events and are not duplicate keys. PSGCs retain leading zeros and stay string metadata.
''')
code('''
overview = pd.DataFrame([
    ['Event rows', len(events)], ['Event fields', len(events.columns)],
    ['First event (Manila)', events.start_time.min()], ['Last event (Manila)', events.start_time.max()],
    ['Duplicate outage IDs', events.outage_id.duplicated().sum()],
    ['Duplicate origin/start pairs', events.duplicated(['barangay_psgc','start_time']).sum()],
    ['Missing event cells', events.isna().sum().sum()],
    ['Nonpositive durations', events.duration_minutes.le(0).sum()],
    ['Starts exactly on the hour', events.start_time.eq(events.start_time.dt.floor('h')).sum()],
    ['Barangays / municipalities', f'{len(geo)} / {geo.municipality_psgc.nunique()}']
], columns=['Check','Actual result'])
display(overview)
display(events[['outage_id','barangay_psgc','municipality_psgc','start_time','duration_minutes','cause_category']].head(5))
display(Markdown('**Decision:** retain all validated V2 events, including Unknown and Other causes. Causes, duration and IDs remain analytical outcomes, never predictors. The source synthetic/generator provenance is retained unchanged outside the feature matrix.'))
''')
md('''
## 5. Data quality and missingness

**Question:** Does missingness represent a broken join, unavailable data, or a deliberately incomplete history? These require different responses. **Method:** profile every source, scan all old-panel row groups, and count new candidate nulls by feature, year and municipality. Source fields are never silently set to zero. Complete 24-hour windows require 24 observations; missing/early history remains null.

The old panel audit verifies its complete ordered rectangular grid across row-group boundaries. The new panel independently checks cardinality and schema for every partition. Full breakdowns are in `audit/*missingness*.csv`; displayed tables are bounded.

Old `outcome_*` fields are structurally null on non-event rows because no event outcome exists there; these are excluded, never imputed as predictors. Prior-completed-year NDVI is unavailable in the first year, while CIN/cloud-base gaps originate in ERA5. These mechanisms differ from the new table's short rolling-window warm-up.
''')
code('''
new_missing = manifest.loc[manifest.feature_type.ne('source_only') & manifest.missing_pct.notna(),
    ['feature_name','missing_count','missing_pct','training_missing_pct']].sort_values('missing_pct',ascending=False)
display(new_missing.head(12).round(5))
old_quality = pd.read_csv(AUDIT/'legacy_feature_quality.csv')
display(old_quality.loc[old_quality.missing_pct.gt(0)].sort_values('missing_pct',ascending=False).head(10))
era_quality = pd.DataFrame(quality['era5']['variables']).T
era_quality['missing_pct'] = 100*era_quality['missing'].astype(float)/era_quality['values'].astype(float)
fig,axes = plt.subplots(1,2,figsize=(12,4.5))
era_quality.missing_pct.sort_values().plot.barh(ax=axes[0],color=BLUE)
axes[0].set(title='ERA5 source missingness: all 12 grid cells',xlabel='Missing values (%)')
top = new_missing.head(8).set_index('feature_name').missing_pct.sort_values()
top.plot.barh(ax=axes[1],color=ORANGE)
axes[1].set(title='V2 candidate missingness: full panel',xlabel='Missing values (%)')
finish('01_missingness')
display(Markdown(f"""**Observed:** CAPE is complete at source; CIN is {era_quality.loc['cin','missing_pct']:.2f}% missing
and cloud base is {era_quality.loc['cbh','missing_pct']:.2f}% missing. New leading nulls arise from strict warm-up
and the ERA5 source beginning at 08:00 Manila, not from failed geographic joins.
**Decision:** omit CIN/cloud base, retain null warm-up values, and expose `feature_complete` as metadata.
No imputer or scaler is fitted. Later preprocessing must be learned on eligible training data only."""))
''')
code('''
missing_year = pd.read_csv(AUDIT/'missingness_by_year.csv')
missing_muni = pd.read_csv(AUDIT/'missingness_by_municipality.csv',dtype={'municipality_psgc':str})
display(missing_year.loc[missing_year.missing.gt(0)].sort_values('missing',ascending=False).head(8))
display(missing_muni.loc[missing_muni.missing.gt(0)].sort_values('missing',ascending=False).head(8))
display(Markdown(f"""**Coverage decision:** all {legacy['rows']:,} old-panel keys were scanned: zero gaps,
duplicate keys or invalid parent relationships. Municipal weather has {quality['weather']['rows']:,} rows and no nulls,
duplicate location-hours or gaps. Terrain matches every barangay. NDVI covers {len(ndvi):,} barangay-years,
but temporal publication validity remains unresolved, so completeness alone does not make it safe."""))
''')
md('''
## 6. Synthetic outage EDA

**Question:** What did the simulator generate by cause, and how long do events last? **Method:** count all seven causes, show percentages, and inspect the complete duration distribution and cause-specific spread. Long right tails can be plausible in a simulator and are not a reason to delete observations. Animal Interference is a synthetic-only assumption. Scheduled PENELCO records are not occurrence labels.
''')
code('''
causes = events.cause_category.value_counts().rename_axis('Cause').to_frame('Events')
causes['Percent'] = 100*causes.Events/len(events)
display(causes.round(2))
ax = causes.Events.sort_values().plot.barh(color=BLUE,figsize=(10,4.8))
ax.set(title='Synthetic V2 causes · 2019–June 2026',xlabel='Simulated events')
for i,value in enumerate(causes.Events.sort_values()):
    ax.text(value+35,i,f'{value:,}',va='center')
ax.set_xlim(0,causes.Events.max()*1.18)
finish('02_causes')
display(Markdown(f"""**Observed:** {causes.index[0]} is the largest category ({causes.iloc[0].Events:,.0f} events,
{causes.iloc[0].Percent:.2f}%). Unknown and Other remain eligible unplanned events.
**Decision:** no cause filtering or cause-based predictor is introduced."""))
''')
code('''
duration_stats = events.duration_minutes.describe(percentiles=[.01,.5,.9,.95,.99])
display(duration_stats.rename('Duration (minutes)').round(2))
cause_duration = events.groupby('cause_category').duration_minutes.agg(['count','mean','median','std','min','max'])
display(cause_duration.round(2))
fig,axes = plt.subplots(1,2,figsize=(13,4.8))
axes[0].hist(events.duration_minutes,bins=60,color=BLUE,edgecolor='white')
axes[0].set(title='Complete duration distribution',xlabel='Minutes',ylabel='Events',yscale='log')
order=causes.index.tolist()
axes[1].boxplot([events.loc[events.cause_category.eq(c),'duration_minutes'] for c in order],
    orientation='horizontal',tick_labels=order,showfliers=True,flierprops={'markersize':2})
axes[1].set(title='Duration by cause (log x-axis)',xlabel='Minutes',xscale='log')
finish('03_durations')
display(Markdown(f"""**Observed:** mean {duration_stats['mean']:.2f} min exceeds median {duration_stats['50%']:.2f} min;
P99 is {duration_stats['99%']:.2f} min and the maximum is {duration_stats['max']:.2f} min.
**Decision:** retain the full synthetic tail. Durations are used only for active-state eligibility and event EDA."""))
''')
md('''
## 7. Spatial and temporal patterns

**Question:** Do time or geography dominate synthetic allocation? **Method:** compare event counts alongside the actual generation-exposure period. The 2026 period stops in June, so raw counts are never presented as a like-for-like annual trend. These event-EDA denominators are all potential generation hours, including active conditions; the onset model's eligible at-risk denominators appear in Sections 8 and 15.

Seasons are deliberately descriptive: **November–April = drier-month group**, **May–October = wetter-month group**. They are not event-specific monsoon classifications. Barangay assignments are probabilistic; apparent hotspots do not establish verified PENELCO reliability differences.
''')
code('''
coverage_start = pd.Timestamp(meta['date_coverage']['first_cutoff'])
coverage_end = pd.Timestamp(meta['date_coverage']['last_cutoff'])
generation_hours = pd.date_range(coverage_start,coverage_end,freq='h',inclusive='left')
annual = events.groupby(events.start_time.dt.year).size().rename('Events').to_frame()
annual['Exposure hours/barangay'] = pd.Series(generation_hours.year).value_counts().sort_index()
annual['Events / 10000 barangay-hours'] = 10000*annual.Events/(annual['Exposure hours/barangay']*len(geo))
display(annual.round(4))
fig,axes = plt.subplots(1,2,figsize=(12,4.3))
annual.Events.plot.bar(ax=axes[0],color=BLUE,rot=0)
axes[0].set(title='Annual event totals · 2026 is January–June',ylabel='Events',xlabel='Year')
annual['Events / 10000 barangay-hours'].plot.bar(ax=axes[1],color=ORANGE,rot=0)
axes[1].set(title='Annual exposure-normalized event frequency',ylabel='Events / 10,000 barangay-hours',xlabel='Year')
finish('04_annual')
display(Markdown(f"""**Observed:** 2026 contains {annual.loc[2026,'Events']:,} events across
{annual.loc[2026,'Exposure hours/barangay']:,} hours per barangay, compared with complete years earlier.
**Decision:** interpret the rate alongside the denominator. Neither curve measures actual utility performance."""))
''')
code('''
monthly = events.set_index('start_time').resample('MS').size()
fig,axes = plt.subplots(2,2,figsize=(12,7))
axes[0,0].plot(monthly.index,monthly.values,color=BLUE,lw=1.5)
axes[0,0].set(title='Monthly simulated starts',ylabel='Events')
axes[0,0].set_xlim(monthly.index.min(),coverage_end)
events.groupby(events.start_time.dt.hour).size().reindex(range(24),fill_value=0).plot.bar(ax=axes[0,1],color=BLUE,rot=0)
axes[0,1].set(title='Start-hour pattern (Manila)',xlabel='Hour',ylabel='Events')
events.groupby(events.start_time.dt.dayofweek).size().reindex(range(7),fill_value=0).set_axis(['Mon','Tue','Wed','Thu','Fri','Sat','Sun']).plot.bar(ax=axes[1,0],color=BLUE,rot=0)
axes[1,0].set(title='Day-of-week allocation',xlabel='Local weekday',ylabel='Events')
season = np.where(events.start_time.dt.month.isin([5,6,7,8,9,10]),'May–Oct','Nov–Apr')
season_hours=np.where(generation_hours.month.isin([5,6,7,8,9,10]),'May–Oct','Nov–Apr')
season_table = pd.Series(season).value_counts().rename('events').to_frame()
season_table['barangay_hours'] = pd.Series(season_hours).value_counts()*len(geo)
season_table['events_per_10000_hours']=10000*season_table.events/season_table.barangay_hours
season_table.events_per_10000_hours.plot.bar(ax=axes[1,1],color=ORANGE,rot=0)
axes[1,1].set(title='Descriptive seasonal grouping',ylabel='Events / 10,000 barangay-hours',xlabel='Month grouping')
finish('05_temporal')
display(season_table.round(4))
peak_hour=int(events.start_time.dt.hour.value_counts().idxmax())
display(Markdown(f'**Observed:** the largest monthly count is {monthly.max():,} in {monthly.idxmax():%B %Y}; the most frequently selected onset hour is {peak_hour:02d}:00 Manila. **Decision:** calendar structure is part of the simulation and environmental sampling. Retain cyclic hour/month encodings and a known weekend flag as research candidates; do not claim that generated cycles reproduce observed utility trends.'))
''')
code('''
municipal = events.groupby('municipality').size().reindex(geo.municipality.unique(),fill_value=0).sort_values()
barangay_counts = geo[['barangay_psgc','barangay','municipality']].merge(
    events.groupby('barangay_psgc').size().rename('events'),on='barangay_psgc',how='left',validate='one_to_one').fillna({'events':0})
barangay_counts['events']=barangay_counts.events.astype(int)
ranked=barangay_counts.sort_values(['events','barangay_psgc'],ascending=[False,True])
fig,axes=plt.subplots(1,2,figsize=(13,6))
municipal.plot.barh(ax=axes[0],color=BLUE)
axes[0].set(title='Municipality allocation',xlabel='Synthetic events',ylabel='')
selected=pd.concat([ranked.head(6),ranked.tail(6)]).sort_values('events')
axes[1].barh(selected.barangay+' ('+selected.municipality+')',selected.events,color=ORANGE)
axes[1].set(title='Six highest and six lowest barangay counts',xlabel='Synthetic events')
finish('06_geography')
display(ranked.head(5))
display(ranked.tail(5))
counts=np.sort(barangay_counts.events.to_numpy());n=len(counts)
gini=(2*np.sum(np.arange(1,n+1)*counts)/(n*counts.sum()))-(n+1)/n
top_share=100*ranked.head(10).events.sum()/len(events)
display(Markdown(f"""**Observed:** the top 10 selected barangays account for {top_share:.2f}% of events;
count-concentration Gini is {gini:.3f}, and {int(barangay_counts.events.eq(0).sum())} barangays have no starts.
**Decision:** keep PSGCs as metadata; do not feed their numeric values to ML or call these verified outage hotspots.
Municipality shares are only partially PENELCO-calibrated; barangay allocations remain simulated."""))
''')
md('''
## 8. Environmental EDA

**Question:** How do event and eligible non-event conditions differ? **Why:** comparing only outages cannot establish a higher event rate. **Method:** inspect physical source distributions once per municipality-hour, then compare all eligible positive cutoffs with a deterministic sample of up to 600 negatives per month. Negative sampling weights recover each month's full negative exposure for distribution plots. Rates are calculated separately over **all** eligible rows, with exact denominators.

The municipal observations are shared by multiple barangays; ERA5 is shared across coarse grid cells. Copied weather values are not independent stations. The NDVI comparison below is explicitly **same-year retrospective context** and is not a predictor. No causal claim or statistical independence claim is made.
''')
code('''
weather = pd.read_csv(source_path('weather'))
weather['time']=pd.to_datetime(weather.time).dt.tz_localize('Asia/Singapore').dt.tz_convert(TZ)
weather['municipality']=weather.municipality.replace({'Balanga':'City of Balanga'})
weather_summary = weather[list(RAW)].describe(percentiles=[.5,.95,.99,.999]).T
display(weather_summary[['count','mean','50%','95%','99%','99.9%','max']].round(3))
fig,axes=plt.subplots(1,2,figsize=(12,4.5))
rain=weather.loc[weather.precipitation_mm.gt(0),'precipitation_mm']
axes[0].hist(np.log1p(rain),bins=45,color=BLUE,edgecolor='white')
axes[0].set(title=f'Wet municipal hours (n={len(rain):,})',xlabel='log(1 + precipitation in mm)',ylabel='Municipality-hours')
axes[1].hist(weather.wind_gusts_10m_m_s,bins=60,color=ORANGE,edgecolor='white')
axes[1].set(title=f'Hourly gust values (n={len(weather):,})',xlabel='Supplied gust (m/s)',ylabel='Municipality-hours')
finish('07_weather_distributions')
display(Markdown(f"""**Observed:** maximum source precipitation is {weather.precipitation_mm.max():.2f} mm per hour
and gust is {weather.wind_gusts_10m_m_s.max():.2f} m/s. These pass nonnegative/finite physical checks.
Humidity is bounded 0–100%, soil moisture 0–1 m³/m³, slope 0–90°. Statistical rarity alone cannot identify a source error.
**Decision:** preserve extremes; no winsorization or unexplained unit conversion is applied."""))
extreme_columns=['municipality','time','precipitation_mm','wind_speed_10m_m_s','wind_gusts_10m_m_s']
display(weather.nlargest(5,'precipitation_mm')[extreme_columns])
wind_discrepancy=weather.wind_gusts_10m_m_s.lt(weather.wind_speed_10m_m_s)
unique_coordinates=weather[['latitude','longitude']].drop_duplicates().shape[0]
display(weather.loc[wind_discrepancy,extreme_columns].head(5))
display(Markdown(f'**Additional quality finding:** the 12 municipal series use only **{unique_coordinates} distinct coordinate pairs**. There are **{wind_discrepancy.sum():,} municipality-hours ({100*wind_discrepancy.mean():.4f}%)** with supplied gust below supplied wind speed. The largest difference is {(weather.wind_gusts_10m_m_s-weather.wind_speed_10m_m_s).min():.2f} m/s. This unresolved measurement/interval consistency issue cannot be explained by rounding alone. Retain original values, report the issue, and verify source model/interval compatibility before deployment; do not invent a correction. High rain is clustered in concurrent municipal records and must not be counted as independent stations.'))
''')
code('''
# Separate training-period spatial variation from variation over time.
training_weather=weather.loc[weather.time.lt(local('2024-01-01'))]
variability=[]
for feature in ['precipitation_mm','wind_gusts_10m_m_s','temperature_2m_c','soil_moisture_7_to_28cm_m3_m3']:
    grouped=training_weather.groupby('municipality')[feature]
    variability.append({'source feature':feature,'between-location SD of means':grouped.mean().std(),
        'mean within-location temporal SD':grouped.std().mean(),'spatial units':'12 municipal series; shared points'})
display(pd.DataFrame(variability).round(4))
display(terrain[['mean_slope_deg','mean_elevation_m','elevation_relief_p95_p05_m']].agg(['min','median','max','std']).round(3))
ndvi_coverage=ndvi.groupby(['year','period_type']).agg(barangays=('barangay_psgc','nunique'),
    mean_ndvi=('mean_ndvi','mean'),minimum_valid_coverage_pct=('valid_coverage_pct','min')).reset_index()
display(ndvi_coverage.round(4))
display(Markdown(f'**Observed and decision:** weather varies over time and across municipal representative points; terrain varies across {len(terrain)} barangays but is static over the panel. NDVI has annual observation windows rather than hourly freshness. The ERA5 assignment uses {quality["era5"]["assigned_cells"]} coarse cells. Preserve these grains when interpreting associations; repeated hourly copies do not create new measurement locations.'))
''')
code('''
comparison=pd.read_parquet(AUDIT/'eda_comparison_sample.parquet')
positive=comparison.outage_next_hour.eq(1);negative=comparison.outage_next_hour.eq(0)
dimensions=[('rainfall_24h_sum','24h precipitation (mm)'),('wind_gust_3h_max','3h gust max (m/s)'),
    ('soil_moisture_24h_mean','24h soil moisture (m³/m³)'),('cape_3h_max','3h CAPE max (J/kg)'),
    ('temperature_c_lag1h','Temperature (°C)'),('humidity_pct_lag1h','Humidity (%)'),
    ('mean_slope_deg','Mean slope (degree)'),('ndvi_same_year_context','NDVI · retrospective only')]
fig,axes=plt.subplots(4,2,figsize=(12,12))
for ax,(feature,label) in zip(axes.flat,dimensions):
    vals=comparison[feature].dropna();bins=np.histogram_bin_edges(vals,bins=35)
    for mask,name,color in [(negative,'Eligible negative',BLUE),(positive,'Positive',ORANGE)]:
        valid=mask & comparison[feature].notna()
        ax.hist(comparison.loc[valid,feature],bins=bins,weights=comparison.loc[valid,'comparison_weight'],
            density=True,histtype='step',linewidth=1.6,color=color,label=name)
    ax.set(xlabel=label,ylabel='Weighted density')
axes[0,0].legend(frameon=False)
fig.suptitle(f'Cutoff conditions · {positive.sum():,} positive and {negative.sum():,} sampled negative hours',y=1.01)
finish('08_positive_negative')
condition_means=[]
for feature,label in dimensions:
    values=[]
    for mask in [positive,negative]:
        valid=mask & comparison[feature].notna()
        values.append(np.average(comparison.loc[valid,feature],weights=comparison.loc[valid,'comparison_weight']))
    condition_means.append({'condition (units)':label,'positive mean':values[0],
        'negative weighted-sample mean':values[1]})
display(pd.DataFrame(condition_means).round(3))
display(Markdown('**Interpretation and decision:** curves describe simulated associations at the prediction cutoff. Monthly negative weights correct the sample design; they are for EDA only and never rebalance the canonical dataset. Same-year NDVI can reveal how the generator used vegetation, but its future imagery disqualifies it from the ML matrix.'))
''')
code('''
rates=pd.read_csv(AUDIT/'environmental_rates.csv')
fig,axes=plt.subplots(2,2,figsize=(13,8))
for ax,feature,label in zip(axes.flat,
    ['rainfall_mm_lag1h','wind_gust_3h_max','cape_3h_max','mean_slope_deg'],
    ['Precipitation at t−1h (mm)','Recent gust max (m/s)','Recent CAPE max (J/kg)','Mean slope (degree)']):
    r=rates.loc[rates.feature.eq(feature)].copy()
    def low_bound(text):return float(text.split(',')[0].replace('[','').replace('(',''))
    r=r.sort_values('bin',key=lambda x:x.map(low_bound))
    ax.bar(np.arange(len(r)),r.onsets_per_10000_hours,color=BLUE)
    ax.set_xticks(np.arange(len(r)),[x.replace('inf','∞') for x in r.bin],rotation=25,ha='right')
    ax.set(title=label,ylabel='Onsets / 10,000 eligible hours')
finish('09_environment_rates')
display(rates.loc[rates.feature.eq('wind_gust_3h_max'),['bin','eligible_hours','positives','onsets_per_10000_hours']].round(4))
gust_rates=rates.loc[rates.feature.eq('wind_gust_3h_max')]
highest=gust_rates.loc[gust_rates.onsets_per_10000_hours.idxmax()]
display(Markdown(f'**Observed:** the highest gust-bin simulated onset rate is {highest.onsets_per_10000_hours:.3f} per 10,000 eligible hours in {highest["bin"]} m/s, from {highest.positives:,} positives / {highest.eligible_hours:,} eligible hours. **Decision:** use full-population denominators for rates and make no causal or real utility forecasting claim. Bins are prespecified physical ranges, not optimized on final test labels. NDVI rates are descriptive post hoc context only.'))
''')
md('''
## 9. Feature correlation and redundancy

**Question:** Are we exporting multiple names for almost the same information? **Method:** calculate Spearman correlation (rank similarity), unique values and modal proportions on an eligible training-only sample (1,500 rows/month). Exact missingness is measured on the full training population. Static terrain is repeated over time, so these correlations describe this panel population, not independent terrain measurements.

No target correlation, validation metric, final-test optimization or model fitting selects features. Thresholds within the rain/wind families remove redundant middle windows; the 3h and 24h contexts remain interpretable hypotheses. Near-constant precipitation at zero is physically meaningful and is not automatically deleted.
''')
code('''
corr=pd.read_csv(AUDIT/'training_spearman_correlations.csv',index_col='feature')
train_quality=pd.read_csv(AUDIT/'training_feature_quality.csv')
selected=['rainfall_mm_lag1h','rainfall_3h_sum','rainfall_6h_sum','rainfall_24h_sum',
    'rainfall_24h_max','wind_gust_m_s_lag1h','wind_gust_3h_max','wind_gust_6h_max',
    'soil_moisture_m3_m3_lag1h','soil_moisture_24h_mean','mean_slope_deg','p90_slope_deg']
fig,ax=plt.subplots(figsize=(11,9))
image=ax.imshow(corr.loc[selected,selected],vmin=-1,vmax=1,cmap='BrBG')
labels=[x.replace('_lag1h','').replace('_',' ') for x in selected]
ax.set_xticks(range(len(selected)),labels,rotation=55,ha='right')
ax.set_yticks(range(len(selected)),labels)
ax.grid(False)
for i in range(len(selected)):
    for j in range(len(selected)):
        value=corr.loc[selected[i],selected[j]]
        ax.text(j,i,f'{value:.2f}',ha='center',va='center',fontsize=8,color='white' if abs(value)>.65 else CHARCOAL)
fig.colorbar(image,ax=ax,label='Training-sample Spearman correlation',shrink=.75)
ax.set_title('Selected redundancy checks · eligible training only')
finish('10_correlations')
display(train_quality[['feature','sample_unique_values','sample_mode_pct','training_missing_pct']].sort_values('sample_mode_pct',ascending=False).head(10).round(4))
display(manifest.loc[manifest.status.eq('dropped') & manifest.feature_type.ne('source_only'),['feature_name','selection_reason']])
display(Markdown('**Decision:** keep distinct physical summaries and cyclic encodings, drop documented redundancy, and defer unsupported availability. These unsupervised choices freeze an initial research matrix; later train/validation ablation may establish whether candidates add predictive value.'))
''')
md(r'''
## 10. Feature engineering

**Question:** Can recent weather history provide more context than one observation? A single dry hour may follow a day of rain; one wind value may miss a recent peak. We therefore summarize trailing, complete histories **within each source location before copying values to barangays**.

With a one-hour lag, a $W$-hour sum at cutoff $t$ uses source labels $t-W,\ldots,t-1$. For precipitation, whose label is the end of the preceding hour, the physical interval is $(t-W-1,t-1]$:

$$R_W(t)=\sum_{j=1}^{W}p(t-j\mathrm{h}).$$

Means and maxima use the same label bounds. Gust change is $g(t-1)-g(t-4)$; rainy streak counts consecutive values **strictly above 0.1 mm**, capped at 72h. An unknown wet left edge stays null until a dry hour or cap establishes its value. No centered window, future value, backward fill, interpolation or partial-window aggregate is allowed.

The rainfall–slope candidate is `rainfall_24h_sum × mean_slope_deg` (mm·degree). It is an interpretable empirical interaction hypothesis, not a dimensional physical failure law. A wind–NDVI candidate cannot be made temporally valid from the available NDVI publication metadata and is deferred. The EDA-only retrospective illustration is explicitly named differently.
''')
code('''
municipality=weather.municipality.iloc[0]
raw=weather.loc[weather.municipality.eq(municipality)].set_index('time').sort_index()
demo=engineer_weather(raw,
    rain_threshold=meta['transformations']['rain_threshold_mm_strict_greater'],
    streak_cap=meta['transformations']['rain_streak_cap_hours'])
t=raw.index[30]
hand_sum=raw.loc[t-pd.Timedelta(hours=24):t-HOUR,'precipitation_mm'].sum()
assert np.isclose(demo.loc[t,'rainfall_24h_sum'],hand_sum)
future_changed=raw.copy();future_changed.loc[t:,list(RAW)]=9999
pd.testing.assert_frame_equal(demo.loc[:t],engineer_weather(future_changed).loc[:t])
display(demo.loc[t-pd.Timedelta(hours=2):t+pd.Timedelta(hours=2),
    ['rainfall_mm_lag1h','rainfall_3h_sum','rainfall_6h_sum','rainfall_24h_sum','wind_gust_3h_max']].round(3))
display(Markdown(f'**Observed:** at {t}, hand-summed prior-24-hour precipitation is **{hand_sum:.3f} mm**, matching the shared builder definition. Changing source values at/after the cutoff leaves all earlier feature values unchanged. **Decision:** reuse this exact function in the notebook and builder.'))
''')
md(r'''
### Calendar features and units

23:00 and 00:00 are neighbors in time. Encoding hour as an ordinary number would place them far apart. Sine/cosine represent position around a circle:

$$h_{\sin}=\sin(2\pi h/24),\quad h_{\cos}=\cos(2\pi h/24).$$

Month uses $(m-1)/12$, so December and January are adjacent. `is_weekend` is known from the cutoff clock. All five are known in advance and need no fitted transformation. Raw PSGCs are identifiers and do not have a meaningful continuous numerical distance.
''')
code('''
display(manifest.loc[manifest.feature_type.eq('engineered'),['feature_name','unit','status','calculation']])
wrap_times=pd.DatetimeIndex([local('2023-12-31 23:00'),local('2024-01-01 00:00')])
display(calendar_features(wrap_times).round(4))
display(Markdown(f'**Decision:** {meta["engineered_created"]} valid-time-causal engineered candidates are computed; {meta["engineered_retained"]} enter the initial matrix. Wind × NDVI remains deferred; no vegetation value or release date is fabricated.'))
''')
md('''
## 11. Before-and-after feature comparisons

**Question:** What does a rolling feature add to a raw hourly measurement? **Method:** select an actual eligible positive cutoff from the audit sample and show the recent source history. The first panel is lagged hourly precipitation; the second is its complete trailing 24h accumulation. They have different interpretations and are not interchangeable. Lines beyond the highlighted cutoff are descriptive context, not inputs to that prediction.
''')
code('''
example=comparison.loc[comparison.outage_next_hour.eq(1) & comparison.rainfall_24h_sum.gt(0)].iloc[0]
cutoff=example.prediction_time
municipal_name=geo.set_index('municipality_psgc').loc[example.municipality_psgc,'municipality']
if isinstance(municipal_name,pd.Series):municipal_name=municipal_name.iloc[0]
source=weather.loc[weather.municipality.eq(municipal_name)].set_index('time').sort_index()
engineered=engineer_weather(source)
window=engineered.loc[cutoff-pd.Timedelta(hours=36):cutoff+pd.Timedelta(hours=12)]
fig,axes=plt.subplots(2,1,figsize=(12,6),sharex=True)
axes[0].plot(window.index,window.rainfall_mm_lag1h,color=BLUE,marker='.',label='Rain at t−1h')
axes[0].set(ylabel='Hourly amount (mm)',title=f'Actual positive cutoff · {municipal_name} · {cutoff}')
axes[1].plot(window.index,window.rainfall_24h_sum,color=ORANGE,label='Preceding complete 24h')
axes[1].set(ylabel='24h accumulation (mm)',xlabel='Prediction cutoff · Asia/Manila')
for ax in axes:
    ax.axvline(cutoff,color=CHARCOAL,linestyle='--',label='Selected cutoff')
    ax.axvspan(cutoff,cutoff+HOUR,color=ORANGE,alpha=.12)
    ax.legend(frameon=False,loc='upper left')
finish('11_before_after')
display(example[['prediction_time','barangay_psgc','rainfall_mm_lag1h','rainfall_24h_sum','wind_gust_3h_max','mean_slope_deg','rainfall_x_slope']].to_frame('Actual value'))
display(Markdown('**Decision:** retain prior-history context where it adds a distinct interpretable summary; remove the redundant middle window when training-period rank correlation crosses the specified threshold. Shaded time is the future label window, never realized-weather input.'))
''')
md('''
## 12. Feature selection and excluded columns

**Question:** Which fields are actually supplied to a future estimator? **Method:** use an explicit predictor whitelist, separate from metadata, target, eligibility flags and analytical outcomes. The next table shows the final physical/calendar subset. The CSV manifest records **every** candidate and source-only exclusion with status, reason, source, units, calculation, availability and missingness.

The initial matrix excludes outage IDs, cause, duration, restoration/end time, severity, generator scores/probabilities/seeds/versions, fault asset assignments, affected assets/barangays and downstream impact statistics. Uncertain static infrastructure densities are deferred. These fields are preserved in their original sources. Geographic categoricals would need a separately justified experiment addressing fixed synthetic geographic memorization.
''')
code('''
display(manifest.loc[manifest.retained,['feature_name','feature_type','unit','missing_pct']].round(6))
display(manifest.loc[manifest.status.eq('deferred') & manifest.feature_type.ne('source_only'),
    ['feature_name','selection_reason']])
assert predictors == manifest.loc[manifest.retained,'feature_name'].tolist()
assert not set(predictors).intersection(meta['metadata_columns']+['outage_next_hour'])
display(Markdown(f'**Decision:** freeze **{len(predictors)}** predictors; **{int((~manifest.retained).sum())}** registered candidate/source fields are not retained. See `ml_feature_manifest.csv` for the full explicit exclusion list. Feature selection does not inspect test labels or train a model.'))
''')
md('''
## 13. Label construction and validation

**Question:** Do event starts reconcile with binary positive hours and all exclusion rules? **Method:** the builder groups only synthetic **origin barangays** into the right-closed next-hour window, then applies active, coverage and split masks. Multiple eligible starts in one window yield one binary positive. A separate validator reads every exported partition and recomputes every label by sorted timestamp searches, independently of the builder's label helper.

This small example shows an actual event's origin before, during and after onset. An event at the cutoff is active; it does not count as a future start. A restored barangay re-enters the onset population at its exact restoration boundary.
''')
code('''
actual_event=events.loc[events.barangay_psgc.eq(example.barangay_psgc) & events.start_time.eq(cutoff+HOUR)].iloc[0]
clock=pd.date_range(cutoff-HOUR,actual_event.end_time.ceil('h')+HOUR,freq='h')
origin_events=events.loc[events.barangay_psgc.eq(actual_event.barangay_psgc)]
counts,active,eligible,reasons=label_arrays(origin_events,clock,[actual_event.barangay_psgc],
    coverage_start,coverage_end,local('2024-01-01'),local('2025-01-01'))
demo_labels=pd.DataFrame({'cutoff':clock,'starts_in_(t,t+1h]':counts[:,0],
    'active_at_cutoff':active[:,0],'is_eligible':eligible[:,0],'reason':reasons[:,0]})
demo_labels['target']=pd.array((counts[:,0]>0).astype('uint8'),dtype='UInt8')
demo_labels.loc[~demo_labels.is_eligible,'target']=pd.NA
display(demo_labels.head(12))
display(pd.DataFrame([{'independent check':k,'result':v} for k,v in validation.items() if isinstance(v,(str,int,bool))]))
display(Markdown(f'**Verified result:** {meta["positive_rows"]:,} positive hours remain after explicit boundary/mask reconciliation. Unknown/Other causes remain eligible; topology impact relationships never generate positives. No V1 occurrence labels enter the output.'))
''')
md('''
## 14. Final dataset structure

**Question:** Can another researcher load the table safely? **Method:** inspect a monthly file's Arrow schema and a compact CSV preview. Partition folders encode calendar year/month; they do not create additional model predictors. `outage_next_hour` is nullable uint8, PSGCs are strings, weather/terrain/calendar predictors are float32, and `prediction_time` has the Asia/Manila timezone. Null targets must always be filtered with `is_eligible` before ML use.

The canonical dataset includes masked rows to make exclusions inspectable. `feature_complete` identifies retained-feature nulls without treating them as ordinary zeros. Null-aware estimators can use them; an estimator requiring imputation must learn it on training only. Do not silently drop all incomplete eligible negatives or use metadata/status columns as features.
''')
code('''
first_partition=OUT/meta['partitions'][0]['path']
schema=pq.ParquetFile(first_partition).schema_arrow
display(pd.DataFrame([{'column':field.name,'Arrow dtype':str(field.type),
    'role':'predictor' if field.name in predictors else 'target' if field.name=='outage_next_hour' else 'metadata'} for field in schema]))
preview=pd.read_csv(OUT/'ml_dataset_v2_sample.csv',dtype={'barangay_psgc':str,'municipality_psgc':str})
display(preview[['prediction_time','barangay_psgc','municipality_psgc']+predictors[:4]+['outage_next_hour']].head(6))
display(Markdown(f'**Observed:** {len(meta["partitions"])} monthly partitions contain **{meta["rows"]:,} × {meta["columns"]}** values by row/column dimensions, with {meta["eligible_rows"]:,} eligible rows. The CSV is a small positive/negative preview, not a prevalence estimate.'))
''')
md('''
## 15. Class imbalance and chronological splits

**Question:** Would high accuracy mean useful prediction? At this prevalence, a classifier saying “no outage” every hour would be almost always correct while finding no events. We report exact positive/negative exposure rather than rebalance the population. A future model study should evaluate precision, recall, PR-AUC, calibration and alert burden against simple training-only baselines.

**Method:** use the same cutoff-date boundaries across every barangay. Train is 2019–2023; validation is 2024; test is 2025–June 2026. Purge the cutoff immediately before each boundary so its right-closed outcome window cannot touch the following split. Past-only weather history can initialize validation/test; it does not require an embargo. The final incomplete horizon is masked. No random row splitting or validation/test resampling occurs.
''')
code('''
by_split=balance.loc[balance.year.eq('ALL')].copy()
display(by_split[['split','eligible_rows','positives','negatives','excluded_rows','positive_pct','incomplete_feature_rows']].round(5))
display(balance.loc[balance.year.ne('ALL'),['year','split','eligible_rows','positives','negatives','excluded_rows','positive_pct']].round(5))
fig,axes=plt.subplots(1,2,figsize=(12,4.3))
ordered=by_split.set_index('split').reindex(['train','validation','test'])
axes[0].bar(ordered.index,ordered.positive_pct,color=ORANGE)
axes[0].set(title='Natural positive class prevalence',ylabel='Eligible rows with onset (%)')
axes[1].bar(ordered.index,ordered.eligible_rows/1e6,color=BLUE)
axes[1].set(title='Eligible exposure by chronological split',ylabel='Million barangay-hours')
finish('12_class_balance')
always_negative=100*(meta['negative_rows']/meta['eligible_rows'])
display(Markdown(f'**Observed:** an always-negative rule would obtain **{always_negative:.5f}% accuracy** and zero recall. **Decision:** preserve all {meta["negative_rows"]:,} eligible negatives and freeze the chronological split. No ML model is trained or tuned.'))
display(pd.DataFrame([{'split':k,**splits[k]} for k in ['train','validation','test']]))
''')
md('''
## 16. Conclusions, limitations and next steps

The builder, teaching examples and production definitions share one implementation; independent NumPy/timestamp-search validation provides a separate check on every retained value and target. Feature selection uses training-period diagnostics only. The matrix is an initial interpretable research subset; no predictive usefulness or forecasting performance has been established.

**Do not equate valid time with availability time.** Open-Meteo historical files are retrospective and lack source model/run/publication metadata. ERA5T typically arrives about five days later; final ERA5 is revised later. One-hour lagging prevents future-valid-time lookahead, but it does not make those values deployable. Reconstruct compatible predictors from archived forecasts with verified issue/publication times before an operational backtest. Annual NDVI, especially the 2026 composite extending through September 23, cannot be used as contemporaneous January–June vegetation information.

Current field semantics were checked against [Open-Meteo's official hourly documentation](https://open-meteo.com/en/docs/historical-weather-api) and [ECMWF's ERA5 documentation](https://confluence.ecmwf.int/spaces/CKB/pages/76414402/ERA5+data+documentation) on 2026-10-26. The CSV's original extraction model/version remains unrecorded, so provider gust semantics are not promoted into a measured subhour peak. Local NetCDF units and GRIB step types were inspected directly.

### Reproduce and validate

From the repository root, using the existing uv environment:

```powershell
uv --cache-dir .venv/uv-cache run --offline python scripts/build_ml_dataset_v2.py --output data/ml/v2_reproduced
uv --cache-dir .venv/uv-cache run --offline python scripts/validate_ml_dataset_v2.py --output data/ml/v2_reproduced
uv --cache-dir .venv/uv-cache run --offline python -m unittest discover -s scripts/tests -p test_ml_v2.py -v
uv --cache-dir .venv/uv-cache run --offline python notebooks/support/execute_eda_feature_engineering_v2.py
```

Existing outputs are never overwritten by the builder. `--help` exposes the source/event/legacy panel and split paths. Notebook execution reads `data/ml/v2` by default and renders small audited artifacts. When source files move, the notebook resolves a unique same-named local file and verifies the event checksum. If uv's launcher is locked, the same installed interpreter is `.venv/Scripts/python.exe`; no package installation is needed.

### Glossary

| Term | Meaning here |
|---|---|
| Prediction cutoff | Time at which the model would receive its inputs |
| Horizon | One hour after the cutoff |
| Leakage | Using information from the future or from the event outcome to predict that event |
| Rolling window | A summary over a fixed number of complete preceding hourly observations |
| Warm-up | Leading period before enough past observations exist |
| Exposure denominator | Number of eligible barangay-hours in which an onset could be counted |
| Reanalysis | Retrospective reconstruction of atmospheric/weather conditions, revised after valid time |
| PSGC | Administrative code used as a string join/grouping identifier |
| Purge | Exclusion of cutoffs whose outcome window touches a later chronological split |

**Next steps require a separate modeling study:** source availability/forecast-vintage audit, training-only preprocessing, simple baselines, validation ablations and calibration, then one frozen test evaluation. Do not infer actual PENELCO forecasting accuracy from synthetic performance.
''')
code('''
display(Markdown('\\n'.join('- '+text for text in meta['limitations'])))
display(Markdown(f"""**Final decisions:** {meta['engineered_created']} engineered candidates created,
{meta['engineered_retained']} retained; {len(predictors)} total predictors. Exact-year/split class counts,
source hashes, feature definitions, exclusion rules and full independent validation are saved beside the dataset.
The builder verified **{meta['source_preservation']['existing_files_hashed']}** existing files unchanged.
Synthetic V2, topology and application files were not modified. No ML model was trained."""))
assert validation['status']=='passed'
assert set(predictors)==set(manifest.loc[manifest.retained,'feature_name'])
assert meta['positive_rows']+meta['negative_rows']+meta['excluded_rows']==meta['rows']
print('Notebook integrity checks passed; all reported results come from saved, validated outputs.')
''')

nb=nbf.v4.new_notebook(cells=cells,metadata={
    'kernelspec':{'display_name':'Python (project uv environment)','language':'python','name':'python3'},
    'language_info':{'name':'python','version':'3.14'},
    'title':'Synthetic Outage V2 — EDA, Feature Engineering and ML-Ready Dataset'})
nbf.validate(nb)
destination=ROOT/'notebooks/01_eda_feature_engineering_v2.ipynb'
nbf.write(nb,destination)
print(f'Authored {destination.name}: {len(cells)} cells')
