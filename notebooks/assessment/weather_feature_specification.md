# Weather windows: investigation and computation specification

Updated 13 October 2026. This extends the agreed notebook sequence. No model has been trained, no new training panel has been generated, and no feature has yet passed predictive selection. The windows below are candidate definitions, not validated electrical failure thresholds.

## Source facts and purpose

The existing `data/weather/open_meteo_bataan_2019_01_to_2026_06.csv` supplies municipal hourly precipitation, wind speed/gusts, temperature, humidity, pressure and two soil-moisture layers. The original Abucay CSV header and `D:/PyCharmProjects/Outage Prediction/scripts/merge_open_meteo.py` confirm precipitation in mm and winds in m/s. The merge renames fields and preserves local clocks; it does not reconstruct interval bounds or release times. Metadata records Asia/Singapore, UTC+8: localize that naive clock, then convert to Asia/Manila. The existing source assessment covers 12 locations and 788,544 rows; full window calculations are deferred to lesson 02.

Use rainfall windows to summarize storm accumulation and earlier wetness, wind averages to describe sustained exposure, and gust maxima to describe peaks among supplied hourly gust values. Earlier rain and soil moisture offer wetness context, not measurements of flooding, landslides or tree-root stability. These are physical hypotheses to investigate, not established PENELCO failure mechanisms. The simulator already uses rain, gusts, CAPE and deeper soil moisture; learning their imposed relationships is not independent scientific discovery.

Open-Meteo defines historical precipitation as a preceding-hour amount and wind speed as an instantaneous sample. Historical gust semantics depend on the product; the historical table describes an indicated-hour value, with a preceding-hour maximum specifically for CERRA. The forecast table describes a preceding-hour gust maximum. Confirm the original model/endpoint and metadata before equating them. A rolling maximum is therefore initially called **maximum supplied hourly gust**, not a measured continuous-time maximum. [Historical variable documentation](https://open-meteo.com/en/docs/historical-weather-api), [forecast variable documentation](https://open-meteo.com/en/docs).

## Timestamp contract

- `issue_time` is when the application's prediction is made; `valid_time` is when weather applies. Store both as offset-aware timestamps. Keep UTC internally; display Asia/Manila. Never attach Manila directly to a timestamp already representing UTC.
- Normalize precipitation to amounts over explicit one-hour intervals `(interval_start, interval_end]`. The right-end label represents the preceding hour. Other provider conventions require an explicit adapter, not a blind timestamp shift. ERA5 values in meters require multiplication by 1,000 after accumulation semantics are verified; do not sum cumulative-since-run amounts without deaccumulation/reset handling. [ECMWF interval definitions](https://confluence.ecmwf.int/pages/viewpage.action?pageId=111150607).
- Historical exploratory mode initially uses a documented **assumed 1h lag**, consistent with the existing panel: cutoff `c = issue_time − 1h`. This is conservative event-window ordering, not verified publication latency. Label results `retrospective_reconstruction`, not operationally available.
- Operational mode requires `available_at <= issue_time` for every input, and interval ends no later than the historical cutoff. Use a fixed versioned cutoff policy. If required recent hours are unavailable, return null plus freshness/coverage flags; do not silently move the window backward or change its meaning.
- A future forecast value can have `valid_time > issue_time` if its specific run was already published/available at issue time. Preserve initialization, publication/availability and valid times. Initialization time alone does not establish publication. Do not replace archived run values with later forecasts or realized weather.
- An event timeline can show weather before and after onset. Mark onset and label post-onset values **retrospective only**. Predictive features for that event must end at the issue-time cutoff, not at restoration time.

For one-hourly complete records at cutoff `c`, define `p[c−j]` as the amount for `(c−j−1h, c−jh]`, `u` as scalar wind speed, `g` as the supplied hourly gust and `s` as soil moisture. Sum/magnitude units do not change merely because the window length changes.

## Candidate definitions

| Feature names | Formula / units | Purpose and limitations |
|---|---|---|
| `rain_sum_6h_mm`, `rain_sum_24h_mm`, `rain_sum_72h_mm` | `R_W(c) = Σ(j=0…W−1) p[c−jh]`, W=6,24,72; mm | Short storm accumulation vs multiday antecedent context. All amounts precede the issue cutoff. These nested windows are strongly redundant; investigate individually and jointly. Total precipitation is used as a rainfall proxy in this local demonstration; it is not a verified rain-only field. |
| `wind_mean_6h_m_s`, `wind_mean_24h_m_s` | `U_W(c) = Σ u[c−jh] / W`; m/s | Average of scalar hourly samples, not continuous-time or vector-mean wind. Wind direction is absent from this CSV. Sustained exposure hypothesis; averaging can hide peaks. |
| `gust_max_6h_m_s`, `gust_max_24h_m_s` | `G_W(c) = max(j=0…W−1) g[c−jh]`; m/s | Largest supplied hourly gust value in the window. Historical/forecast gust temporal semantics must match before model use. |
| `antecedent_rain_24to72h_mm` | `A(c)=Σ(j=24…71) p[c−jh] = R_72(c) − R_24(c)`; mm | Earlier 48 hours, separated from the most recent 24. Represents precipitation over `(c−72h,c−24h]`. Do not include this and both parent sums automatically: they are exactly linearly dependent. |
| `rain_hourly_max_24h_mm` | `max(j=0…23) p[c−jh]`; mm per one-hour interval | Largest hourly amount; a proxy for intensity at 1h resolution, not a subhourly peak. If displayed as a rate, divide explicitly by 1h and label mm/h. |
| `soil_moisture_7to28cm_latest_m3_m3` | `s[c]`; m³/m³ | Latest available modeled deeper-layer wetness; compare separately with shallow 0–7 cm. Not observed soil saturation/flooding and not calibrated locally. Availability and forecast support must be verified. |
| `pressure_change_3h_hpa` | `pressure[c] − pressure[c−3h]`; hPa | Exploratory short-term pressure tendency. Needs only both endpoints; keep the sign. Do not assume it identifies a typhoon or causes outages. |
| `rain_decay_168h_half48h_mm` | `Σ(j=0…167) 2^(−j/48) p[c−jh]`; mm | Optional finite antecedent-rain index with a trial 48h half-life and 168h history. This is an uncalibrated rainfall-memory proxy, not a validated hydrologic water-balance model. Do not equate pandas `ewm.mean` with this weighted sum. |

The decay example is a project-defined sensitivity feature. Its parameters are not derived from Bataan soil measurements. Hydrological research supports investigating rainfall history and drainage together, but does not validate these parameters for electrical outages. [USGS research on soil wetting/drying](https://www.usgs.gov/publications/wetting-and-drying-soil-response-precipitation-data-analysis-modeling-and-forecasting).

Optional later candidates include raw CAPE (J/kg) and a 6h maximum, shallow soil moisture, temperature/humidity means, and hours above a prespecified gust/rain threshold. Defer them until source compatibility and scientific purpose are clear. No generator-normalized hazard scores, severity-derived features, whole-period percentile thresholds, invented critical wind speeds, or unverified weather interactions are allowed. Any learned quantile/scaler/decay parameter comes from training only; validation chooses among prespecified candidates. Current CAPE comes from ERA5, not the municipal CSV.

## Time-aware computation and missingness

1. Compute once per **weather location**, before mapping to barangays. Use an explicit stable location ID with coordinates, provider/model and source version. For municipal weather, verify one coordinate pair per municipality and map Balanga to City of Balanga via the inspected key crosswalk. ERA5 uses grid-cell IDs; forecast runs add model and run ID to grouping. Never roll on concatenated barangay copies or mix weather grids with municipal points.
2. Sort timestamps within each location/source group. Reject duplicate valid-time keys, unexpected timezone changes, negative precipitation/wind, nonfinite numbers and changed coordinates. Diagnose source errors before normalization. Do not merge provider/model versions silently.
3. Reindex each group to a one-hour grid, leaving absent hours as null. A 24-row window is not automatically 24 hours. Then use trailing time windows with `closed='right'`, `center=False` and a complete expected-hour count. Windows are `(c−Wh,c]` in valid-end labels.
4. Require W valid hourly values for a W-hour sum, mean or maximum. Early history and any missing constituent produce null plus `expected_hours`, `valid_hours` and `coverage_fraction`. In a six-hour window, five hours of rain must not masquerade as a six-hour total, and a partial maximum can underestimate a storm peak. Never convert missing precipitation into zero, interpolate rain peaks, backward-fill from the future or use centered windows.
5. The disjoint antecedent feature needs 48 earlier values. Direct calculation can remain valid when recent 24h rain is missing; deriving it as `R72−R24` requires both complete parent windows and is more restrictive. State which computation is used. The pressure-change feature requires two finite endpoints; intervening nulls do not change that endpoint definition.
6. The 168h weighted sum requires all 168 values. Missing values do not reset its history to zero. At dataset start, retain warm-up nulls; do not fetch/create earlier weather without documenting the source.
7. For ML, fit any imputer and missingness handling on training only, preserve coverage indicators, and report coverage by split/location. For API display, null stays null with a readable reason. Zero is a valid computed precipitation amount only when every required interval is present.

Readable pandas sketch for one already validated, hourly-reindexed location (not a completed backend implementation):

```python
# hourly_weather.index is a timezone-aware hourly DatetimeIndex of valid-end times.
# Repeat independently inside a loop/group for every location/source version.
rainfall = hourly_weather['precipitation_mm']
rain_24h = rainfall.rolling('24h', closed='right', min_periods=24).sum()
wind_6h = hourly_weather['wind_speed_10m_m_s'].rolling(
    '6h', closed='right', min_periods=6).mean()
gust_24h = hourly_weather['wind_gusts_10m_m_s'].rolling(
    '24h', closed='right', min_periods=24).max()
history_features = pd.DataFrame({'rain_sum_24h_mm': rain_24h,
                                'wind_mean_6h_m_s': wind_6h,
                                'gust_max_24h_m_s': gust_24h})
# Relabel cutoff c as exploratory issue c+1h; do not shift values across missing rows.
history_features.index = history_features.index + pd.Timedelta(hours=1)
history_features.index.name = 'issue_time'
# This lagged historical reconstruction does NOT prove real-time data availability.
```

At an issue of 2026-06-01 12:00+08:00, the exploratory cutoff is 11:00. The 6h rainfall window uses hourly labels 06:00…11:00, covering `(05:00,11:00]`; it excludes the interval ending 12:00. Validate this with a hand-summed window, deliberately remove one hour, insert a second location with contrasting values, and alter future hours to confirm past results do not change. These meaningful checks belong beside the notebook computations.

## Which features serve which screen or analysis?

| Use | Initial investigation / display set | Guardrail |
|---|---|---|
| Descriptive outage analytics | Rain 6/24/72h, mean wind 6/24h, supplied gust max 6/24h, earlier 24–72h rainfall; optionally soil wetness | Compare exposure-matched non-event times if discussing occurrence. Report grain/sample sizes; hourly autocorrelation and shared municipal weather preclude treating copied barangay values as independent observations. |
| Individual outage timelines | Hourly precipitation and gust series; 24h accumulated rain; 6h mean wind; onset/end markers; optional soil moisture | Explain each window's ending time. Separate before-onset context from post-onset hindsight. Never average across unrelated locations. |
| Machine learning | Candidate families above, tested by ablation against calendar/static/lagged-weather baselines | None selected yet. Need issue-time availability, no generator internals/outcomes, chronological evaluation and deployable source compatibility. Retrospective performance cannot certify operational forecasting. |
| Forecast visualization and interpretation | Forecast hourly rain/gusts, forecast-only next-24/48/72h precipitation totals, next-window maximum gust; history shown separately | One published forecast vintage per issue. Label totals' exact valid intervals, model/run and data freshness. Do not sum nested 24h, 48h and 72h totals together or portray a deterministic weather forecast as an outage probability. |

Forecast-only rainfall for horizon H is `Σ(k=1…H) forecast_p[issue+k hours]`, covering `(issue,issue+H]`, **only** for aligned complete hourly intervals from a run available by issue. Partial first/last intervals need an explicit adapter or aligned issue time, not arbitrary proportional allocation. Max forecast gust uses the same vintage/window. Display estimated weather, not realized future values. Keep forecast fields separate from historical ones; blended observed/forecast windows require a further versioned specification.

Open-Meteo's Single Runs API preserves forecasts by initialization time, while its Historical Forecast API stitches model runs. A valid 24–72h backtest must recover the forecast available at each issue, including publication delay, available variables and archive coverage. The current local historical weather file does not supply that contract. Do not promise a full 2019–2026 forecast-vintage backtest before archive coverage is checked. [Single Runs API](https://open-meteo.com/en/docs/single-runs-api), [Historical Forecast API](https://open-meteo.com/en/docs/historical-forecast-api).

## Performance selection — planned for Phase C

Freeze the issue/horizon target and split dates in Phase B. Current hourly panels label onset in the same hour; they are not ready-made 24–72h targets. Define whether the target is any start in `(issue,issue+H]` or a particular future band, and aggregate to daily units only after comparing task utility. Purge issue rows whose outcome windows cross train/validation/test boundaries; consider nonoverlapping issues or time-block uncertainty estimates for dependence. Past history before a split may initialize a rolling feature if legitimately available; future target windows may not cross the split.

Use the same eligible evaluation rows and horizons for all comparisons. Rain72 warm-up must not give one model an easier evaluation population. Report coverage losses and event prevalence. Never undersample validation/test; if training negatives are sampled, record sampling weights and evaluate/calibrate on the original prevalence.

| Comparison | Question |
|---|---|
| Constant training prevalence and training-only calendar/location frequency | Does added weather exceed a defensible simple reference? |
| Calendar + approved static environmental fields | Does weather add information beyond geography and season? |
| Same model + safe latest/lagged raw weather | Does a window improve over the same weather without aggregation? |
| Add rainfall family, wind/gust family, then earlier-wetness family separately | Which family provides incremental validation benefit? |
| Remove candidate windows/features from the combined model | Is each retained feature useful, or merely redundant? |
| Repeat shortlisted comparisons with logistic regression and a suitable tree model | Is improvement robust to model family? |

Assess PR-AUC with the averaging definition fixed (for example, average precision), precision/recall at validation-chosen thresholds, Brier score and reliability plots. Define a practical alert burden (alerts per day / location and fraction of positive issues alerted) before choosing a threshold. Use training prevalence for the constant-probability baseline; the no-skill precision/average-precision reference is the positive fraction in the evaluation population, which may differ. Accuracy alone is misleading. Use time-block comparisons/uncertainty when feasible, with block size considering horizon overlap and weather autocorrelation, rather than independent-row tests on 15M dependent rows. Document split-specific sample sizes, positives, calibration and geographic holdout limitations.

Select the smallest useful set based on prespecified validation criteria and stable gains across chronological folds, calibration, missingness, forecast compatibility and interpretability. Thresholds, hyperparameters, window lengths and optional decay settings are selected without the final test. Test the frozen choice once. If windows do not improve validation or cannot be supplied at prediction time, exclude them. Future results concern synthetic labels only.

## Small reusable outputs and FastAPI handoff

The companion `weather_feature_contract.json` is a draft computation contract, not an endpoint or a fitted model signature. Each eventual feature summary should carry: schema version, feature name/value/unit, location and source/model IDs, issue time, cutoff/window bounds, forecast initialization/publication times when applicable, expected/valid sample count, completeness/freshness, input provenance, and availability status. Emit null with a reason such as `insufficient_history`, `missing_hours`, `not_available_at_issue`, or `source_semantics_unverified`.

Use separate small products when the lesson supports them: a 12-location coverage summary; a feature-selection table (candidate, units, availability, validation delta, decision/reason); and a bounded single-event timeline. Compute municipal windows before barangay joins; select columns/periods from existing Parquet in place. No full duplicate feature panel is authorized. No API application implementation is included.

Notebook 02 will explain and hand-check historical windows/timelines; 04–05 will enforce feature availability and forecast contracts; 06–08 will evaluate/select them; 09 will export the evaluated feature whitelist and precise input schema. All eight mandatory teaching sections apply. Phase approval gates remain in place.
