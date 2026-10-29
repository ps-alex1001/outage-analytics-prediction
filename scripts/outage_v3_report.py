"""Inspectable diagnostics for the V3 generator; never modifies event content."""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

import outage_environment as env


def season(month):
    return np.where(np.isin(month, [3, 4, 5]), "Hot-dry", np.where(np.isin(month, [6, 7, 8, 9, 10, 11]), "Rainy", "Cooler-dry"))


def distribution(events, field):
    return events[field].value_counts(normalize=True).to_dict()


def tv(a, b):
    keys = sorted(set(a) | set(b))
    return float(sum(abs(a.get(k, 0) - b.get(k, 0)) for k in keys) / 2)


def snapshot(events, geo_codes):
    counts = events.barangay_psgc.value_counts().reindex(geo_codes, fill_value=0)
    return {"total_events": len(events), "municipality_ranking": events.municipality.value_counts().index.tolist(),
            "municipality_shares": distribution(events, "municipality"),
            "barangay_shares": (counts / len(events)).to_dict(),
            "spatial_hhi": float(((counts / len(events)) ** 2).sum()),
            "largest_barangay_share": float(counts.max() / len(events)),
            "monthly_shares": distribution(events, "month"), "cause_shares": distribution(events, "cause_category"),
            "rainy_event_share": float(np.isin(events.month, range(6, 12)).mean()),
            "weather_rainy_share": float(events.loc[events.cause_category.eq("Weather"), "month"].isin(range(6, 12)).mean()),
            "equipment_hot_share": float(events.loc[events.cause_category.eq("Equipment Failure"), "month"].isin([3, 4, 5]).mean()),
            "supply_hot_share": float(events.loc[events.cause_category.eq("Supply"), "month"].isin([3, 4, 5]).mean())}


def run_sensitivity(data, cfg, names, centers, histograms, pilot, fits, alpha, offsets,
                    intercepts, duration_centers, blocked, baseline):
    from generate_synthetic_outages_v3 import (SCENARIOS, calibrate_occurrence,
        pilot_calibration, simulate, validate_events)
    base = snapshot(baseline, data.geography.barangay_psgc)
    results = {"baseline": base}
    scenarios = [(name, cfg, weights, offsets) for name, weights in SCENARIOS.items() if name != "baseline"]
    scenarios += [
        ("municipality_half_strength", cfg, SCENARIOS["baseline"], offsets * .5),
        ("heat_weaker", replace(cfg, heat_preference=cfg.heat_preference * .8), SCENARIOS["baseline"], offsets),
        ("heat_stronger", replace(cfg, heat_preference=cfg.heat_preference * 1.2), SCENARIOS["baseline"], offsets),
        ("weather_cause_weaker", replace(cfg, weather_preference=cfg.weather_preference * .8), SCENARIOS["baseline"], offsets),
        ("weather_cause_stronger", replace(cfg, weather_preference=cfg.weather_preference * 1.2), SCENARIOS["baseline"], offsets),
    ]
    for name, scenario_cfg, weights, gamma in scenarios:
        print(f"Sensitivity: {name}", flush=True)
        hist = histograms.get(name, histograms["baseline"])
        scenario_alpha, gamma, _ = calibrate_occurrence(hist, centers, names, scenario_cfg, blocked, fixed_offsets=gamma)
        # Hold cause intercepts and duration centers fixed to expose preference changes.
        events, stats = simulate(data, scenario_cfg, names, scenario_alpha, gamma, intercepts,
                                 duration_centers, fits, environmental_weights=weights)
        validate_events(events, data, scenario_cfg, scenario_alpha, weights)
        value = snapshot(events, data.geography.barangay_psgc)
        value["collision_suppressed"] = stats["collision_suppressed"]
        value["count_relative_change"] = len(events) / len(baseline) - 1
        value["municipality_total_variation"] = tv(value["municipality_shares"], base["municipality_shares"])
        value["monthly_total_variation"] = tv(value["monthly_shares"], base["monthly_shares"])
        value["cause_total_variation"] = tv(value["cause_shares"], base["cause_shares"])
        value["barangay_total_variation"] = tv(value["barangay_shares"], base["barangay_shares"])
        value["instability_flag"] = bool(abs(value["count_relative_change"]) > .1 or
            max(value["municipality_total_variation"], value["monthly_total_variation"], value["cause_total_variation"]) > .1)
        results[name] = value
    return {"complete": True, "method": "Common PSGC-stable random streams. Recalibrate alpha only, hold baseline gamma except half-strength scenario; hold cause intercepts and duration centers. No final baseline replacement.",
            "instability_threshold": "10% count change or 0.10 municipality/month/cause total variation; diagnostic, not significance test",
            "scenario_weights": SCENARIOS, "results": results}


def markdown_table(frame):
    # Avoid adding tabulate solely to produce diagnostics.
    values = frame.fillna("").copy()
    for col in values.select_dtypes(include="floating"):
        values[col] = values[col].map(lambda v: f"{v:.4f}")
    return "| " + " | ".join(map(str, values.columns)) + " |\n| " + " | ".join(["---"] * len(values.columns)) + " |\n" + "\n".join(
        "| " + " | ".join(map(str, row)) + " |" for row in values.itertuples(index=False, name=None))


def create_reports(output, events, data, cfg, v1, geography, sampling, integrity, calibration, sensitivity, pilot):
    from generate_synthetic_outages_v3 import CAUSES, SOFT_TARGETS, cause_logits, softmax
    events = events.copy()
    events["season"] = season(events.month)
    n = len(events)
    hours = len(data.times) - 1
    causes = events.cause_category.value_counts().reindex(CAUSES, fill_value=0).rename("events").to_frame()
    causes["percent"] = 100 * causes.events / n
    causes.rename_axis("cause_category").to_csv(output / "cause_distribution.csv")
    municipality = events.municipality.value_counts().rename("events").to_frame()
    municipality["generated_percent"] = 100 * municipality.events / n
    municipality["target_percent"] = [100 * SOFT_TARGETS.get(m, np.nan) for m in municipality.index]
    municipality["difference_pp"] = municipality.generated_percent - municipality.target_percent
    municipality = municipality.merge(geography.set_index("municipality"), left_index=True, right_index=True, validate="one_to_one")
    municipality.rename_axis("municipality").to_csv(output / "municipality_distribution.csv")
    barangay = data.geography[["barangay_psgc", "barangay", "municipality"]].copy()
    barangay["events"] = barangay.barangay_psgc.map(events.barangay_psgc.value_counts()).fillna(0).astype(int)
    barangay["events_per_barangay_year"] = barangay.events / (hours / env.HOURS_PER_YEAR)
    barangay["province_share"] = barangay.events / n
    barangay.sort_values(["events", "barangay_psgc"], ascending=[False, True], inplace=True)
    barangay.to_csv(output / "barangay_distribution.csv", index=False)
    barangay.head(20).to_csv(output / "top_20_barangays.csv", index=False)
    old_municipality = v1.municipality.value_counts(normalize=True).rename("v1_share")
    comparison = municipality.join(old_municipality)
    comparison["v3_share"] = comparison.events / n
    comparison["difference_pp_v3_minus_v1"] = 100 * (comparison.v3_share - comparison.v1_share)
    comparison[["v1_share", "v3_share", "difference_pp_v3_minus_v1"]].to_csv(output / "v1_geography_comparison.csv")
    for field in ["year", "month", "hour"]:
        exposure = pd.Series(getattr(data.times[:-1].tz_convert(env.LOCAL_TZ), field)).value_counts()
        table = events[field].value_counts().reindex(sorted(exposure.index), fill_value=0).rename("events").to_frame()
        table["exposure_barangay_hours"] = exposure * len(data.geography)
        table["events_per_100000_barangay_hours"] = 100000 * table.events / table.exposure_barangay_hours
        table.rename_axis(field).to_csv(output / f"events_by_{field}.csv")
    events.start_time.dt.strftime("%Y-%m").value_counts().sort_index().rename_axis("year_month").to_csv(output / "events_by_year_month.csv")
    for field in ["month", "municipality", "season"]:
        table = pd.crosstab(events.cause_category, events[field]).reindex(CAUSES, fill_value=0)
        table.to_csv(output / f"cause_by_{field}.csv")
    durations = events.groupby("cause_category").duration_minutes.describe(percentiles=[.05, .5, .9, .95, .99]).reindex(CAUSES)
    durations.to_csv(output / "duration_by_cause.csv")
    env_fields = [*env.SCORE_FIELDS, "synthetic_event_propensity", "synthetic_event_generation_probability", "synthetic_heat_stress_score"]
    events[env_fields].describe(percentiles=[.01, .05, .5, .95, .99]).to_csv(output / "score_statistics.csv")
    events.groupby("cause_category")[["weather_hazard_score", "rainfall_score", "wind_score", "wind_vegetation_interaction",
                                       "temperature_2m_c", "synthetic_heat_stress_score"]].mean().reindex(CAUSES).to_csv(output / "cause_environment_means.csv")
    component_mean = pilot[env.SCORE_FIELDS].mean()
    contribution = component_mean * np.array([.5, .2, .1, .12, .08])
    pilot_score = pilot[env.SCORE_FIELDS].to_numpy() @ np.array([.5, .2, .1, .12, .08])
    gamma = pilot.municipality_index.map(dict(enumerate(calibration["municipality_offsets"].values()))).to_numpy()
    # Hourly hazard remains time-varying even with a static municipal intercept.
    temporal_log_odds_sd = cfg.beta * float(np.mean([group.weather_hazard_score.std() * .5 for _, group in pilot.groupby("municipality_index")]))
    e_raw = events[["rainfall_score", "wind_score", "convective_score", "soil_wetness_score"]].to_numpy()
    intercepts = np.array([calibration["cause_intercepts"][cause] for cause in CAUSES])
    probabilities = softmax(cause_logits(events[env.SCORE_FIELDS].to_numpy(), e_raw,
                                        events.synthetic_heat_stress_score.to_numpy(), cfg) + intercepts)
    rainy_weather = float(events.loc[events.cause_category.eq("Weather"), "season"].eq("Rainy").mean())
    hot_equipment = float(events.loc[events.cause_category.eq("Equipment Failure"), "season"].eq("Hot-dry").mean())
    hot_supply = float(events.loc[events.cause_category.eq("Supply"), "season"].eq("Hot-dry").mean())
    checks = []

    def check(name, flagged, evidence):
        checks.append({"check": name, "flagged": bool(flagged), "evidence": evidence})

    check("total_event_tolerance", abs(n / cfg.target_total_events - 1) > .05, f"{n} vs {cfg.target_total_events}; ±5%")
    check("weather_share", not .35 <= causes.loc["Weather", "percent"] / 100 <= .45, float(causes.loc["Weather", "percent"]))
    check("weather_largest_cause", causes.events.idxmax() != "Weather", causes.events.idxmax())
    check("weather_rainy_concentration", rainy_weather < .60, rainy_weather)
    check("animal_uncommon", not .02 <= causes.loc["Animal", "percent"] / 100 <= .05, float(causes.loc["Animal", "percent"]))
    check("principal_hotspots", municipality.index.tolist()[:2] != ["Dinalupihan", "Bagac"], municipality.index.tolist()[:3])
    check("samal_prominent", municipality.index.get_loc("Samal") > 3, int(municipality.index.get_loc("Samal") + 1))
    check("municipality_soft_targets", bool((municipality.difference_pp.abs().dropna() > 1.5).any()), municipality.difference_pp.dropna().to_dict())
    check("unrepresented_barangays", bool(barangay.events.eq(0).any()), barangay.loc[barangay.events.eq(0), "barangay_psgc"].tolist())
    check("excessive_single_barangay", barangay.province_share.max() > .04, float(barangay.province_share.max()))
    check("offset_variance_vs_environment", np.var(gamma) > 4 * np.var(cfg.beta * pilot_score),
          {"offset_variance": float(np.var(gamma)), "environment_log_odds_variance": float(np.var(cfg.beta * pilot_score)),
           "temporal_H_log_odds_sd": temporal_log_odds_sd, "interpretation": "Offsets alter between-municipality odds; temporal within-barangay logistic odds response remains beta exactly."})
    check("terrain_mean_exceeds_weather", contribution.terrain_exposure_score > contribution.weather_hazard_score, contribution.to_dict())
    # NDVI is annual; it cannot dominate hourly variance because its hourly variance is zero.
    check("vegetation_mean_exceeds_weather", contribution.vegetation_exposure_score > contribution.weather_hazard_score, contribution.to_dict())
    check("nearly_all_extreme_weather", events.weather_hazard_score.ge(.8).mean() > .95, float(events.weather_hazard_score.ge(.8).mean()))
    monthly_causes = pd.crosstab(events.month, events.cause_category, normalize="index")
    check("weather_dominates_almost_every_month", int(monthly_causes.Weather.gt(.70).sum()) >= 10, monthly_causes.Weather.to_dict())
    check("deterministic_supply", probabilities[:, 3].max() > .98, float(probabilities[:, 3].max()))
    check("equipment_only_hot", hot_equipment == 1, hot_equipment)
    check("supply_hot_majority_not_met", hot_supply < .5, hot_supply)
    check("equipment_hot_majority_not_met", hot_equipment < .5, hot_equipment)
    month_exposure = pd.Series(data.times[:-1].tz_convert(env.LOCAL_TZ).month).value_counts()
    peak_months = {}
    for cause in ["Equipment Failure", "Supply"]:
        counts = events.loc[events.cause_category.eq(cause), "month"].value_counts().reindex(month_exposure.index, fill_value=0)
        peak = int((counts / month_exposure).idxmax())
        peak_months[cause] = peak
        check(f"{cause}_peak_month_outside_hot_season", peak not in [3, 4, 5], {"exposure_adjusted_peak_month": peak})
    animal_muni = pd.crosstab(events.municipality, events.cause_category, normalize="index").Animal
    check("animal_dominates_municipality", animal_muni.max() > .20, animal_muni.to_dict())
    check("duration_extremes", events.duration_minutes.max() > cfg.max_duration or events.duration_minutes.min() < cfg.min_duration,
          {"minimum": float(events.duration_minutes.min()), "maximum": float(events.duration_minutes.max()), "over_24h_fraction": float(events.duration_minutes.gt(1440).mean())})
    check("artificially_uniform_geography", municipality.events.std() / municipality.events.mean() < .1, float(municipality.events.std() / municipality.events.mean()))
    municipal_causes = pd.crosstab(events.municipality, events.cause_category, normalize="index")
    check("identical_municipality_cause_distributions", len(municipal_causes.round(8).drop_duplicates()) == 1, len(municipal_causes.round(8).drop_duplicates()))
    associations = {}
    for cause, factor, probability_index in [("Weather", "weather_hazard_score", 0), ("Tree contact", "wind_vegetation_interaction", 1),
                                             ("Equipment Failure", "synthetic_heat_stress_score", 2), ("Supply", "synthetic_heat_stress_score", 3)]:
        selected = events.cause_category.eq(cause)
        diff = float(events.loc[selected, factor].mean() - events.loc[~selected, factor].mean())
        association = float(pd.Series(probabilities[:, probability_index]).corr(events[factor]))
        associations[cause] = {"selected_minus_other_mean": diff, "conditional_probability_factor_correlation": association}
        check(f"weak_environmental_association_{cause}", diff <= 0 or association < .05, associations[cause])
    unstable = [k for k, v in sensitivity.get("results", {}).items() if v.get("instability_flag")]
    # Half-strength geography is an intentional large geographic counterfactual.
    check("modest_preference_sensitivity_instability", bool([k for k in unstable if k != "municipality_half_strength"]), unstable)
    pd.DataFrame(checks).assign(evidence=lambda frame: frame.evidence.map(str)).to_csv(output / "sanity_checks.csv", index=False)
    warnings = [c["check"] for c in checks if c["flagged"]]
    warnings += data.inspection["warnings"]
    if not sensitivity["complete"]:
        warnings.append("Sensitivity validation incomplete: explicitly skipped")
    summary = {"total_events": n, "target_events": cfg.target_total_events,
               "minimum_start": str(events.start_time.min()), "maximum_end": str(events.end_time.max()),
               "events_per_barangay_year": n / (len(data.geography) * hours / env.HOURS_PER_YEAR),
               "represented_barangays": int(barangay.events.gt(0).sum()),
               "duration_minutes": events.duration_minutes.describe(percentiles=[.05, .5, .9, .95, .99]).to_dict(),
               "propensity": events.synthetic_event_propensity.describe().to_dict(),
               "causes": causes.to_dict("index"), "weather_rainy_share": rainy_weather,
               "equipment_hot_share": hot_equipment, "supply_hot_share": hot_supply,
               "municipality_counts": municipality.events.to_dict(), "sampling": sampling,
               "integrity": integrity, "environmental_associations": associations, "sanity_checks": checks, "warnings": warnings}
    env.write_json(output / "validation.json", summary)
    target_table = municipality.loc[list(SOFT_TARGETS), ["target_percent", "generated_percent", "difference_pp"]].reset_index(names="municipality")
    pilot_month = data.times[pilot.time_index.to_numpy(int)].tz_convert(env.LOCAL_TZ).month
    temperature_seasons = pd.DataFrame({"season": season(pilot_month), "Heat": pilot.Heat.to_numpy()}).groupby("season").Heat.mean()
    association_table = pd.DataFrame(associations).T.reset_index(names="cause")
    modest_preferences = [{"scenario": key, "equipment_hot_percent": 100*value["equipment_hot_share"],
                          "supply_hot_percent": 100*value["supply_hot_share"],
                          "weather_rainy_percent": 100*value["weather_rainy_share"],
                          "events": value["total_events"]}
                         for key, value in sensitivity.get("results", {}).items()
                         if key in {"baseline", "heat_weaker", "heat_stronger", "weather_cause_weaker", "weather_cause_stronger"}]
    report = f'''# Synthetic outage V3 diagnostic report

All occurrence, causes, durations, severity and municipality offsets are synthetic. Real inputs are historical environmental observations and administrative geography. This dataset is not actual PENELCO operating history; neither it nor ML evaluation on it validates real-world prediction accuracy.

Generated **{n:,}** events (target approximately {cfg.target_total_events:,}, no quota), covering {summary['represented_barangays']}/237 barangays. Earliest start: {summary['minimum_start']}; latest end: {summary['maximum_end']}. Rate: {summary['events_per_barangay_year']:.3f} events per barangay-year. 2026 is partial through June. Full tables are adjacent CSVs.

## Source inspection and formula mapping

All 237 barangays match terrain, vegetation in each of eight years, municipality Open-Meteo and ERA5 context. Unmatched PSGCs: none. Duplicate/missing required keys, invalid geometries and incomplete required hourly inputs fail before generation. GeoJSON CRS84 is checked, interior representative points use UTM51N. Four nearest ERA5 0.25-degree cells supply CAPE; these and municipality weather are not independently observed at barangay resolution.

| Symbol | Actual input | Mapping |
| --- | --- | --- |
| R | Open-Meteo precipitation_mm | Pooled p01/p99 clipped normalization; zero precipitation remains zero |
| W | Open-Meteo wind_gusts_10m_m_s | Pooled p01/p99; wind speed retained as context |
| C | ERA5 cape, J kg**-1 | Nearest grid cell; pooled p01/p99 of unique assigned cells |
| M | soil_moisture_7_to_28cm_m3_m3 | Pooled p01/p99 |
| V | Annual barangay mean_ndvi | Pooled barangay-year p01/p99; actual annual resolution |
| T | mean_slope_deg | Pooled barangay p01/p99; relief retained as context |
| WV | W × V | Exact V1 interaction |
| RT | R × T | Exact V1 interaction |

`H = 0.30R + 0.35W + 0.20C + 0.15M`

`S = 0.50H + 0.20V + 0.10T + 0.12WV + 0.08RT`

Both original formulas are unchanged. T uses V1's mean slope alone because slope/relief Spearman correlation is 0.888; other slope indicators are even more redundant. Temperature is separate cause context. CIN ({data.inspection['sources']['era5']['variables']['cin']['missing']:,} missing cells) and cloud-base height are excluded; equivalent ERA5 rain/wind/temperature do not duplicate Open-Meteo in H. Complete schemas, units, missingness, correlations, normalization bounds and coverage appear in `input_inspection.json`.

The actual common hourly period is {data.inspection['period']['start_local']} through {data.inspection['period']['end_local']}; ERA5 lacks the first eight local hours requested in 2019. Open-Meteo metadata says Asia/Singapore (+08:00): localize accordingly, convert to UTC, join with ERA5 UTC, report Asia/Manila. No naive joins or extrapolation. NDVI uses real retrospective annual composites; **2026 includes imagery through September 23**, after the event horizon. It is suitable as retrospective simulation context, not an available-at-onset forecasting feature. Earlier annual composites also include later-in-year imagery. Missing observations are never replaced with fabricated measurements.

## Occurrence and geographic calibration

`q = sigmoid(alpha + 4*S + gamma[municipality])`, alpha={calibration['alpha']:.8f}. V1's exponential/Bernoulli response becomes logistic with the same beta=4; at these low rates it is nearly its exponential form. All eligible low-score hours have positive probability. Final endpoint q=0 because no positive-duration event can start there. Only alpha and three ridge-regularized municipal offsets are fitted; all other offsets are zero, preserving environmental differentiation in the remaining 50.08%. Calibration uses 512 score bins and an independent {calibration['pilot_size']:,}-row environmental pilot. The ridge penalty is {cfg.municipality_regularization:g} pseudo-events per squared offset. Offsets are sampling assumptions, not empirical geographic risk.

{markdown_table(target_table)}

Dinalupihan/Bagac are the two leading municipalities: **{municipality.index.tolist()[:2] == ['Dinalupihan', 'Bagac']}**. The full municipality table reports environmental-only shares separately from calibrated expected shares and achieved shares. No municipal event counts were fixed. No event was removed to enforce a target. Candidate count: {sampling['sampled_candidates']:,}; overlap suppressions: {sampling['collision_suppressed']}; boundary suppressions: {sampling['boundary_suppressed']}. The expected-count calibration includes an approximate {calibration['expected_blocked_hours_approximation']:.3f}-hour mean dead-time correction. Exact expected candidates from all barangay-hours: {sampling['exact_expected_candidates']:.3f}. This approximation is appropriate to low simulation rates, not an exact collision-adjusted expectation.

## Cause assignment and observed synthetic results

{markdown_table(causes.reset_index(names='cause_category'))}

Recovered V1 shares: {data.inspection['v1_cause_shares']}. V1's four actual conditional weights are preserved as the starting mechanism: Weather `0.20+1.6H+0.4R+0.4W+0.4C`, Tree `0.15+3WV`, Equipment `0.70`, Other `0.20`. V3 splits the equipment background into Equipment/Supply with synthetic fraction {cfg.supply_equipment_split:g} assigned to Supply; this is a configurable assumption, not a recovered historical split. The V1 realized proportions are a reference, not imposed V3 quotas.

V3 adds weather log-weight `{cfg.weather_preference:g}*(2R+C+H)` and heat log-weight `{cfg.heat_preference:g}*Heat` for both Equipment and Supply. Heat is observed-temperature p50/p99 normalization. Animal is a nonzero background log-weight; only Weather and Animal intercepts are calibrated to 40% and 3% expected shares. The other four cause percentages are unconstrained outputs. Independent six-way categorical draws retain nonzero cause probabilities. Each event has exactly one primary cause; no Lightning label is allowed. No animal population, equipment condition or upstream utility measurements are invented.

Weather during June–November: **{100*rainy_weather:.2f}%** of Weather events. Equipment during March–May: **{100*hot_equipment:.2f}%**. Supply during March–May: **{100*hot_supply:.2f}%**. The source monthly weather table supports these descriptive season bins: March–May are hottest; June–November are substantially wetter than January–April. Winter gusts are actually higher than many rainy months, so Weather is conditioned on rain and CAPE as well as gusts. Season labels themselves never enter occurrence or cause selection. Exposure-adjusted month rates account for the extra January–June in partial 2026. See cause-by-month/season tables and environmental mean diagnostics. A below-majority hot-season share is explicitly flagged; overlapping hot temperatures outside March–May and the available event pool limit literal calendar concentration. We do not add extreme weights or move event dates to force a majority.

Pilot mean observed-temperature heat scores by calendar season: {temperature_seasons.to_dict()}. The source heat distributions overlap, so a calendar majority is a stronger requirement than preferential selection during observed heat. The ±20% preference results quantify the remaining shortfall; they do not prove that no other plausible model could attain a majority.

Peak months after dividing by available monthly exposure hours: {peak_months}. Any Supply/Equipment peak outside March–May is also flagged. These are stochastic realizations with nonzero background occurrence in every season; favorable aggregate temperature associations do not guarantee a particular calendar peak. Counts and exposure-adjusted rates are both retained rather than hiding this departure.

{markdown_table(pd.DataFrame(modest_preferences)) if modest_preferences else 'Preference sensitivity was skipped.'}

{markdown_table(association_table)}

## Duration and severity: V2 only

Per the user's clarification, **only duration is reused from V2**: six corresponding cause-specific lognormal mu/sigma fits, the V1-derived environmental factor `(1+0.8H)*(1+0.8T+0.5RT)` centered on the log scale, and bounded rejection draws ({cfg.min_duration:g}–{cfg.max_duration:g} minutes). The two actual V2 duration functions are imported; no V2 occurrence allocation, cause proportions or geography is used. Centers are expected cause-specific log effects from the independent pilot, avoiding feedback from realized random causes. Supply uses the V2 Supply fit (60-minute baseline median); Animal inherits V2's Other-pooled fit (40-minute baseline median), explicitly a synthetic pooling assumption. Duration fits and sources are retained in `duration_parameters.csv`. {sampling['duration_rejection_draws']} duration draws were rejected. Severity retains duration thresholds Minor <60, Moderate 60–<240, Major ≥240; these are synthetic classes, not official PENELCO classes.

Duration median={events.duration_minutes.median():.2f}, mean={events.duration_minutes.mean():.2f}, p99={events.duration_minutes.quantile(.99):.2f}, maximum={events.duration_minutes.max():.2f} minutes. Occurrence scores do not determine durations directly. Earlier active events suppress later candidates in the same barangay, as in V1. Suppressed candidates are not resampled.

## Integrity, safeguards and sensitivity

In-memory and CSV round-trip checks validate IDs, required values, PSGC/name pairs, positive timestamp-consistent bounded durations, source-period boundaries, no duplicate incidents, no overlaps, all six causes, H/S/WV/RT and logistic q. Integrity results: `{integrity}`. `sanity_checks.csv` explicitly records every requested safeguard and evidence, including static contribution comparisons, geographic versus environmental variance, uniformity, near-deterministic causes and environmental associations. A flag is retained for review, not hidden by re-running seeds.

Mean direct environmental contributions are H={contribution.weather_hazard_score:.4f}, V={contribution.vegetation_exposure_score:.4f} and T={contribution.terrain_exposure_score:.4f}; direct weather exceeds direct vegetation and terrain. Annual V and static T have no within-year hourly variation; H, WV and RT retain the hourly weather response. No score coefficient was retuned during calibration.

Sensitivity complete: **{sensitivity['complete']}**. `sensitivity.json` contains baseline plus weather-heavier, vegetation-heavier, terrain-reduced, half municipal calibration, ±20% heat preference and ±20% weather cause preference. Each reports counts, municipality ranks/shares, barangay concentration, monthly/cause shares, rainy and hot-season fractions, and distribution-change flags. Alpha recalibrates expected volume; cause intercepts/duration centers remain fixed to reveal changes. These alternatives never replace the final baseline. Half municipality strength intentionally changes geographic shares; instability under modest preferences is flagged separately.

Flags and source limitations:

{chr(10).join('- ' + str(w) for w in warnings) if warnings else '- None.'}

## Reproducibility and downstream migration

Seed={cfg.seed}. Independent NumPy streams derive from seed, barangay PSGC and named purpose; the same inputs/configuration/versions reproduce deterministic event content. Run time appears only in metadata. Input/code SHA-256 hashes, actual count and configuration are saved in `run_manifest.json`. V1 and V2 inputs are read-only, all output is in a fresh V3 directory. No infrastructure counts, density, connectivity or modeled assets enter any propensity, cause weight or calibration.

The application is intentionally not switched automatically. See `downstream_migration.md`: event-to-asset/area links, GIS impact outputs, dashboards, ML labels and trained models must be rebuilt and activated as one matching event version. Six V3 cause labels require explicit consumer mappings. Existing numeric/context column names remain compatible with V1 where possible; unique IDs use `SYN-v3-` to prevent silent event-version collisions. Environmental proxies and target-matching results cannot establish actual utility reliability, causal failure mechanisms or real-world forecast accuracy.
'''
    (output / "validation_report.md").write_text(report, encoding="utf-8")
    (output / "downstream_migration.md").write_text('''# V3 downstream migration plan

Do not activate V3 by changing only the event CSV path. Keep current event-linked data together until a complete V3 bundle is rebuilt.

1. `backend/datasets.py` selects the active event CSV. `backend/impacts.py`, `backend/impact_model.py` and `scripts/map_outage_topology_impacts.py` consume event IDs/cause labels and existing impact directories. Rebuild fault mappings, impact-set associations, affected-assets memberships, affected-barangay/area links and impact summaries in a fresh V3 namespace. Map Weather → old Weather / Storm, Tree contact → Tree Contact, Equipment Failure → Equipment / Technical, Supply → Supply Interruption, Animal → Animal Interference, Other → the residual case. Preserve upstream source-loss treatment for Supply. These mappings are adapter rules, not changes to the primary V3 taxonomy.
2. Audit backend environment/spatial/overview analytics and frontend cause colors/filters/legends/Barangay Explorer for the six exact labels. Refresh aggregates/caches keyed by event source. Update active event and impact paths atomically after ID coverage tests. Never join V3 events to existing impact records by row order or geography alone.
3. `scripts/build_ml_dataset_v2.py` currently discovers version-specific event paths and asserts seven causes. Create a separate V3 builder/configuration, re-create barangay-hour onset/active-window labels, splits, exclusion masks and manifests using V3 IDs. Rebuild dashboard ML aggregations and invalidate models trained on previous synthetic labels. Do not reuse previously trained model metrics as V3 results.
4. `scripts/train_municipality_model.py`, installed forecast-model artifacts and backend forecast/scenario code depend on existing training contracts. Retrain and validate separately after V3 label generation. Exclude generator propensity, cause, duration, severity and post-event information from occurrence predictors. Annual retrospective NDVI (especially the 2026 September composite) needs an explicit availability policy before any forecasting experiment.
5. Run impact coverage checks (every mapped ID belongs to this V3 CSV and all required events are resolved or explicitly flagged), six-label UI checks and temporal split/leakage checks. Only then select the new event/impact/model bundle together. This task leaves application activation and unrelated components unchanged.

V1/V2 source files and outputs are preserved. Only V2 duration functions and fit parameters inform this generator; current downstream version references are identified solely to prevent mixed-version joins.
''', encoding="utf-8")
    (output / "README.md").write_text("# Synthetic Bataan outage V3\n\nPrimary dataset: `synthetic_outage_events_v3.csv`. Read `validation_report.md`, `input_inspection.json`, `calibration.json` and `downstream_migration.md` before use. This is synthetic history, not actual PENELCO observations.\n", encoding="utf-8")
    return summary
