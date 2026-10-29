"""V3 environmental synthetic outages: V1 occurrence/causes, V2 durations only.

Run with uv run python scripts/generate_synthetic_outages_v3.py.
No infrastructure inputs, municipal quotas, top-k selection or output deletion.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

import outage_environment as env
# These two functions are the ONLY reused V2 simulation mechanisms.
from generate_synthetic_outages import duration_effect, draw_durations

ROOT = Path(__file__).resolve().parents[1]
VERSION = "v3"
TARGET_TOTAL_EVENTS = 6000
RANDOM_SEED = 42
CAUSES = ("Weather", "Tree contact", "Equipment Failure", "Supply", "Animal", "Other")
DURATION_LABELS = ("Weather / Storm", "Tree Contact", "Equipment / Technical",
                   "Supply Interruption", "Animal Interference", "Other")
SOFT_TARGETS = {"Dinalupihan": .2381, "Bagac": .1416, "Samal": .1195}
V1_EVENTS = ROOT / "data/outages/synthetic_outage_events.csv"
V2_DURATION_FITS = ROOT / "data/synthetic/v2_20261024T181237_896469Z/duration_parameters.csv"
SCENARIOS = {
    "baseline": env.SCENARIOS["baseline"],
    "weather_heavier": env.SCENARIOS["weather_heavier"],
    "vegetation_heavier": env.SCENARIOS["vegetation_heavier"],
    "terrain_reduced": env.SCENARIOS["terrain_reduced"],
}


@dataclass(frozen=True)
class Config:
    seed: int = RANDOM_SEED
    target_total_events: int = TARGET_TOTAL_EVENTS
    beta: float = 4.0  # V1 response, now with separate municipal offsets.
    municipality_regularization: float = 2.0  # pseudo-event ridge penalty
    weather_target: float = .40
    animal_target: float = .03
    heat_preference: float = 4.0
    weather_preference: float = 2.0
    supply_equipment_split: float = .35  # new synthetic split of V1 .70 background
    min_duration: float = 1.0  # V2 bounds, user-requested duration-only reuse
    max_duration: float = 2886.0
    pilot_hours_per_barangay: int = 512
    start: str = env.START
    end: str = env.END


def sigmoid(x):
    x = np.asarray(x, dtype=float)
    return np.exp(-np.logaddexp(0, -x))


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def fingerprints(paths):
    return {name: {"path": str(path), "sha256": digest(path), "bytes": path.stat().st_size}
            for name, path in paths.items()}


def validate_config(cfg):
    env.require(cfg.seed >= 0 and cfg.target_total_events > 0, "Invalid seed or event target")
    env.require(cfg.beta > 0 and cfg.municipality_regularization > 0, "Invalid occurrence parameters")
    env.require(0 < cfg.weather_target < 1 and 0 < cfg.animal_target < 1
                and cfg.weather_target + cfg.animal_target < 1, "Invalid cause targets")
    env.require(cfg.heat_preference >= 0 and cfg.weather_preference >= 0, "Negative cause preference")
    env.require(0 < cfg.supply_equipment_split < 1, "Invalid synthetic equipment/supply split")
    env.require(0 < cfg.min_duration < cfg.max_duration and cfg.pilot_hours_per_barangay >= 24,
                "Invalid durations or pilot size")


def inspect_inputs(cfg):
    for path in [*env.INPUTS.values(), V1_EVENTS, V2_DURATION_FITS]:
        env.require(path.is_file(), f"Missing input: {path}")
    document = json.loads(env.INPUTS["geography"].read_text(encoding="utf-8"))
    crs = document.get("crs", {}).get("properties", {}).get("name", "RFC7946 CRS84")
    env.require(crs in {"urn:ogc:def:crs:OGC:1.3:CRS84", "EPSG:4326", "RFC7946 CRS84"},
                f"Review unsupported GeoJSON CRS: {crs}")
    data = env.load_and_validate(cfg.start, cfg.end)
    env.require(len(data.geography) == 237, "Administrative universe changed; inspect before generation")
    data.inspection["validated_geojson_crs"] = crs
    data.inspection["era5_exclusions"] = "CIN and cloud-base height excluded; only CAPE enters H. Open-Meteo supplies equivalent local weather."
    temperatures = np.concatenate([w.temperature_2m_c.to_numpy() for w in data.weather.values()])
    low, high = np.quantile(temperatures, [.50, .99])
    data.normalizers["Heat"] = env.Normalizer(float(low), float(high))
    data.inspection["normalization"]["Heat"] = {"p50": float(low), "p99": float(high)}
    data.inspection["heat_definition"] = "clip((observed temperature - pooled p50)/(pooled p99 - p50),0,1); separate from H and S"
    weather = pd.concat(data.weather.values())
    weather["month"] = weather.index.tz_convert(env.LOCAL_TZ).month
    data.inspection["monthly_weather_means"] = weather.groupby("month")[
        ["precipitation_mm", "temperature_2m_c", "wind_gusts_10m_m_s"]].mean().to_dict("index")
    data.inspection["weather_coverage"] = {
        name: {"start_utc": str(w.index.min()), "end_utc": str(w.index.max()), "rows": len(w)}
        for name, w in data.weather.items()}
    original_times = pd.read_csv(env.INPUTS["weather"], usecols=["municipality", "time"])
    data.inspection["original_weather_local_coverage"] = original_times.groupby("municipality").time.agg(["min", "max", "count"]).to_dict("index")
    data.inspection["zero_precipitation_fraction"] = float(weather.precipitation_mm.eq(0).mean())
    data.inspection["vegetation_coverage"] = {
        "years": sorted(data.vegetation.year.unique().tolist()),
        "minimum_valid_pixel_pct": float(data.vegetation.valid_coverage_pct.min()),
        "assignment": "Actual barangay-year mean; no interpolation or fabricated historical NDVI",
        "2026_source_end_exclusive": env.NDVI_2026_END_EXCLUSIVE}
    v1 = pd.read_csv(V1_EVENTS, dtype={"barangay_psgc": str, "municipality_psgc": str})
    v1_counts = v1.cause_category.value_counts()
    data.inspection["v1_cause_counts"] = v1_counts.to_dict()
    data.inspection["v1_cause_shares"] = (v1_counts / len(v1)).to_dict()
    data.inspection["v2_scope"] = "Duration parameters and duration sampling functions only; no V2 occurrence, cause priors or geographic calibration"
    fits = pd.read_csv(V2_DURATION_FITS).set_index("cause_category").loc[list(DURATION_LABELS)].reset_index()
    env.require(np.isfinite(fits[["mu", "sigma"]]).all().all() and fits.sigma.gt(0).all(),
                "Invalid inherited V2 duration fits")
    fits["v3_cause_category"] = CAUSES
    return data, fits, v1


def prepare_calibration(data, cfg):
    """One barangay at a time; only small histograms/pilot are retained."""
    names = sorted(data.geography.municipality.unique())
    edges = np.linspace(0, 1, 513)
    hist = {key: np.zeros((len(names), len(edges) - 1), dtype=np.int64) for key in SCENARIOS}
    samples = []
    for row in data.geography.itertuples(index=False):
        components, raw, weather, _ = env.component_arrays(data, row)
        mi = names.index(row.municipality)
        for key, weights in SCENARIOS.items():
            hist[key][mi] += np.histogram(env.propensity(components[:-1], weights), edges)[0]
        # Separate PSGC-stable stream; sample without replacement, no event chronology changes.
        rng = np.random.default_rng(np.random.SeedSequence([cfg.seed, int(row.barangay_psgc), 300]))
        size = min(cfg.pilot_hours_per_barangay, len(data.times) - 1)
        t = np.sort(rng.choice(len(data.times) - 1, size, replace=False))
        sample = pd.DataFrame(components[t], columns=env.SCORE_FIELDS)
        sample[["R", "W", "C", "M"]] = raw[t]
        sample["Heat"] = data.normalizers["Heat"].transform(weather.temperature_2m_c.to_numpy()[t])
        sample["municipality_index"] = mi
        sample["time_index"] = t
        sample["expansion_weight"] = (len(data.times) - 1) / size
        samples.append(sample)
    return names, (edges[:-1] + edges[1:]) / 2, hist, pd.concat(samples, ignore_index=True)


def calibrate_occurrence(hist, centers, names, cfg, blocked_hours=0., fixed_offsets=None):
    """Ridge-regularized expected-count calibration, never realized-event quotas.

    Three municipality effects, other municipalities share gamma=0. Histograms
    integrate the logistic curve; alpha is always free. Approximate dead-time
    correction q/(1+d*q) accounts for one active incident per barangay.
    """
    x = np.zeros((len(names), 4))
    x[:, 0] = 1
    for j, municipality in enumerate(SOFT_TARGETS, 1):
        x[names.index(municipality), j] = 1
    target = np.array([cfg.target_total_events,
                       *[cfg.target_total_events * p for p in SOFT_TARGETS.values()]])
    theta = np.array([np.log(cfg.target_total_events / hist.sum()) - cfg.beta * .3, 0., 0., 0.])
    if fixed_offsets is not None:
        offsets = np.asarray(fixed_offsets)
        # Only alpha recalibrates for an intentional geography sensitivity.
        for _ in range(50):
            q = sigmoid(theta[0] + cfg.beta * centers[None, :] + offsets[:, None])
            accepted = q / (1 + blocked_hours * q)
            delta = (hist * accepted).sum() - target[0]
            derivative = (hist * q * (1 - q) / (1 + blocked_hours * q) ** 2).sum()
            theta[0] -= np.clip(delta / derivative, -2, 2)
            if abs(delta) < 1e-7:
                break
    else:
        penalty = np.array([0., *([cfg.municipality_regularization] * 3)])
        for _ in range(80):
            offsets = x[:, 1:] @ theta[1:]
            q = sigmoid(theta[0] + cfg.beta * centers[None, :] + offsets[:, None])
            expected = (hist * q / (1 + blocked_hours * q)).sum(axis=1)
            derivative = (hist * q * (1 - q) / (1 + blocked_hours * q) ** 2).sum(axis=1)
            gradient = x.T @ expected - target + penalty * theta
            hessian = x.T @ (derivative[:, None] * x) + np.diag(penalty)
            step = np.linalg.solve(hessian, gradient)
            theta -= np.clip(step, -2, 2)
            if np.max(np.abs(gradient)) < 1e-7:
                break
        else:
            raise ValueError("Municipality calibration failed to converge")
        offsets = x[:, 1:] @ theta[1:]
    q = sigmoid(theta[0] + cfg.beta * centers[None, :] + offsets[:, None])
    expected = (hist * q / (1 + blocked_hours * q)).sum(axis=1)
    unadjusted = (hist * np.exp(cfg.beta * centers)[None, :]).sum(axis=1)
    table = pd.DataFrame({"municipality": names, "municipality_calibration_offset": offsets,
                          "odds_multiplier": np.exp(offsets),
                          "environment_only_expected_share": unadjusted / unadjusted.sum(),
                          "calibrated_expected_events": expected,
                          "calibrated_expected_share": expected / expected.sum()})
    return float(theta[0]), offsets, table


def cause_logits(components, raw, heat, cfg):
    h, _, _, wv, _ = np.asarray(components).T
    r, w, c, _ = np.asarray(raw).T
    # Preserve the actual V1 conditional odds before adding explicit V3 effects.
    weather = .20 + 1.6 * h + .4 * r + .4 * w + .4 * c
    tree = .15 + 3.0 * wv
    equipment = np.full(len(h), .70 * (1 - cfg.supply_equipment_split))
    supply = np.full(len(h), .70 * cfg.supply_equipment_split)
    animal = np.full(len(h), .03)  # intercept calibrated; not an observed animal prior
    other = np.full(len(h), .20)
    logits = np.log(np.column_stack([weather, tree, equipment, supply, animal, other]))
    # Wetter conditions compete with the observed dry-season gust maximum.
    # No month indicator enters these weights or occurrence.
    logits[:, 0] += cfg.weather_preference * (2 * r + c + h)
    logits[:, 2] += cfg.heat_preference * heat
    logits[:, 3] += cfg.heat_preference * heat
    return logits


def softmax(logits):
    values = np.exp(logits - np.max(logits, axis=1, keepdims=True))
    return values / values.sum(axis=1, keepdims=True)


def calibrate_causes(logits, weights, cfg):
    """Fit ONLY Weather and Animal intercepts, leave four others unconstrained."""
    weights = np.asarray(weights, float) / np.sum(weights)
    intercepts = np.zeros(6)
    indices = [0, 4]
    target = np.array([cfg.weather_target, cfg.animal_target])
    for _ in range(100):
        p = softmax(logits + intercepts)
        selected = p[:, indices]
        mean = weights @ selected
        if np.max(np.abs(mean - target)) < 1e-10:
            return intercepts, p
        hessian = np.diag(mean) - (selected * weights[:, None]).T @ selected
        intercepts[indices] -= np.clip(np.linalg.solve(hessian, mean - target), -2, 2)
    raise ValueError("Weather/Animal intercept calibration failed")


def pilot_calibration(sample, alpha, offsets, weights, cfg, fits, cause_intercepts=None):
    components = sample[env.SCORE_FIELDS].to_numpy()
    q = sigmoid(alpha + cfg.beta * (components @ weights) +
                offsets[sample.municipality_index.to_numpy(int)])
    exposure_weights = q * sample.expansion_weight.to_numpy()
    logits = cause_logits(components, sample[["R", "W", "C", "M"]].to_numpy(), sample.Heat.to_numpy(), cfg)
    if cause_intercepts is None:
        intercepts, p = calibrate_causes(logits, exposure_weights, cfg)
    else:
        intercepts = cause_intercepts
        p = softmax(logits + intercepts)
    weighted_p = p * exposure_weights[:, None]
    effects = duration_effect(components)
    duration_centers = (weighted_p * effects[:, None]).sum(axis=0) / weighted_p.sum(axis=0)
    duration_means = np.exp(fits.mu.to_numpy()[None, :] + .5 * fits.sigma.to_numpy()[None, :] ** 2
                            + effects[:, None] - duration_centers)
    blocked = np.maximum(duration_means / 60 - .5, 0)
    mean_blocked = float((weighted_p * blocked).sum() / exposure_weights.sum())
    return intercepts, duration_centers, mean_blocked, exposure_weights, p


def simulate(data, cfg, names, alpha, offsets, intercepts, duration_centers, fits,
             scenario="baseline", environmental_weights=None):
    weights = SCENARIOS.get(scenario, SCENARIOS["baseline"]) if environmental_weights is None else environmental_weights
    frames = []
    stats = {"sampled_candidates": 0, "collision_suppressed": 0, "boundary_suppressed": 0,
             "duration_rejection_draws": 0, "exact_expected_candidates": 0., "maximum_q": 0.}
    local = data.times.tz_convert(env.LOCAL_TZ)
    for row in data.geography.itertuples(index=False):
        components, raw, weather, ndvi = env.component_arrays(data, row)
        score = env.propensity(components, weights)
        gamma = offsets[names.index(row.municipality)]
        q = sigmoid(alpha + cfg.beta * score + gamma)
        q[-1] = 0  # final boundary instant cannot contain a positive-duration onset
        stats["exact_expected_candidates"] += float(q.sum())
        stats["maximum_q"] = max(stats["maximum_q"], float(q.max()))
        # Independent named streams keep scenario comparisons and seed replay stable.
        streams = [np.random.default_rng(np.random.SeedSequence([cfg.seed, int(row.barangay_psgc), key]))
                   for key in (1, 2, 3)]
        candidates = np.flatnonzero(streams[0].random(len(q)) < q)
        if not len(candidates):
            continue
        heat = data.normalizers["Heat"].transform(weather.temperature_2m_c.to_numpy()[candidates])
        p = softmax(cause_logits(components[candidates], raw[candidates], heat, cfg) + intercepts)
        cause = np.minimum((streams[1].random(len(q))[candidates, None] > p.cumsum(axis=1)).sum(axis=1), 5)
        duration, rejections = draw_durations(cause, components[candidates], streams[2], fits, duration_centers, cfg)
        # V1 one-active-event rule. Suppressed candidates are never replaced.
        accepted, collisions, boundaries = remove_collisions(candidates, duration, data.times)
        stats["sampled_candidates"] += len(candidates)
        stats["collision_suppressed"] += collisions
        stats["boundary_suppressed"] += boundaries
        stats["duration_rejection_draws"] += rejections
        positions = np.searchsorted(candidates, accepted)
        duration, cause = duration[positions], cause[positions]
        starts = local[accepted]
        event = pd.DataFrame({
            "outage_id": [f"SYN-v3-{cfg.seed}-{row.barangay_psgc}-{t.strftime('%Y%m%dT%H%M')}" for t in starts],
            "is_synthetic": True, "generator_version": VERSION, "random_seed": cfg.seed,
            "barangay_psgc": row.barangay_psgc, "barangay": row.barangay,
            "municipality_psgc": row.municipality_psgc, "municipality": row.municipality,
            "start_time": starts, "end_time": starts + pd.to_timedelta(duration, unit="m"),
            "duration_minutes": duration, "year": starts.year, "month": starts.month,
            "hour": starts.hour, "day_of_week": starts.day_name(),
            "cause_category": np.asarray(CAUSES)[cause],
            "severity_class": np.where(duration < 60, "Minor", np.where(duration < 240, "Moderate", "Major")),
            "synthetic_event_propensity": score[accepted],
            "synthetic_event_generation_probability": q[accepted],
            "synthetic_heat_stress_score": heat[positions], "municipality_calibration_offset": gamma,
            "selected_cause_probability": p[positions, cause],
            "cape_j_kg": data.cape[(row.era5_latitude, row.era5_longitude)][accepted],
            "ndvi_annual_mean": ndvi[accepted], "ndvi_year": starts.year,
            "ndvi_period_type": np.where(starts.year == 2026, "year_to_date", "full_year"),
            "ndvi_context": "retrospective annual composite; not available-at-onset vegetation",
            "ndvi_source_start": [f"{y}-01-01" for y in starts.year],
            "ndvi_source_end_exclusive": [env.NDVI_2026_END_EXCLUSIVE if y == 2026 else f"{y+1}-01-01" for y in starts.year],
            "weather_spatial_resolution": "municipality", "era5_latitude": row.era5_latitude,
            "era5_longitude": row.era5_longitude, "era5_grid_resolution_degrees": .25,
        })
        for j, field in enumerate(env.SCORE_FIELDS):
            event[field] = components[accepted, j]
        for j, field in enumerate(["rainfall_score", "wind_score", "convective_score", "soil_wetness_score"]):
            event[field] = raw[accepted, j]
        for field in env.WEATHER_FIELDS:
            event[field] = weather[field].to_numpy()[accepted]
        for field in env.TERRAIN_FIELDS:
            event[field] = getattr(row, field)
        frames.append(event)
    env.require(bool(frames), "No events generated")
    events = pd.concat(frames, ignore_index=True).sort_values(["start_time", "barangay_psgc"]).reset_index(drop=True)
    return events, stats


def remove_collisions(candidates, durations, times):
    # Exact V1 collision order; return candidate time indices, not a hard quota.
    accepted, collisions, boundaries = [], 0, 0
    active_until = times[0]
    for index, minutes in zip(candidates, durations):
        if times[index] < active_until:
            collisions += 1
            continue
        end = times[index] + pd.Timedelta(minutes=float(minutes))
        if end > times[-1]:
            boundaries += 1
            continue
        accepted.append(index)
        active_until = end
    return np.asarray(accepted, dtype=int), collisions, boundaries


def validate_events(events, data, cfg, alpha, weights=None):
    env.require(not events.empty and not events.isna().any().any(), "Empty events or missing values")
    env.require(events.outage_id.is_unique, "Duplicate outage IDs")
    env.require(events.is_synthetic.eq(True).all() and events.generator_version.eq(VERSION).all(), "Invalid version labels")
    env.require(events.random_seed.eq(cfg.seed).all(), "Seed metadata mismatch")
    env.require(set(events.cause_category) == set(CAUSES), "Expected exactly six cause labels")
    env.require(not events.duplicated(["barangay_psgc", "start_time"]).any(), "Duplicate incidents")
    joined = events.merge(data.geography[["barangay_psgc", "municipality_psgc", "barangay", "municipality"]],
                          on="barangay_psgc", suffixes=("", "_geo"), how="left", validate="many_to_one")
    for field in ["municipality_psgc", "barangay", "municipality"]:
        env.require(joined[field].eq(joined[field + "_geo"]).all(), f"Invalid PSGC/name pair: {field}")
    env.require(events.duration_minutes.between(cfg.min_duration, cfg.max_duration).all(), "Duration bounds")
    env.require((events.end_time > events.start_time).all(), "Nonpositive duration")
    env.require(np.allclose((events.end_time-events.start_time).dt.total_seconds()/60,
                            events.duration_minutes), "Duration timestamp disagreement")
    env.require((events.start_time >= data.times[0]).all() and (events.end_time <= data.times[-1]).all(), "Outside coverage")
    ordered = events.sort_values(["barangay_psgc", "start_time"])
    overlaps = int((ordered.start_time < ordered.groupby("barangay_psgc").end_time.shift()).sum())
    env.require(overlaps == 0, "Overlapping active outages")
    h = events[["rainfall_score", "wind_score", "convective_score", "soil_wetness_score"]].to_numpy() @ env.WEATHER_WEIGHTS
    s = events[env.SCORE_FIELDS].to_numpy() @ (SCENARIOS["baseline"] if weights is None else weights)
    env.require(np.allclose(events.weather_hazard_score, h), "H formula changed")
    env.require(np.allclose(events.synthetic_event_propensity, s), "S formula changed")
    env.require(np.allclose(events.wind_vegetation_interaction, events.wind_score*events.vegetation_exposure_score), "WV formula changed")
    env.require(np.allclose(events.rain_terrain_interaction, events.rainfall_score*events.terrain_exposure_score), "RT formula changed")
    expected_q = sigmoid(alpha + cfg.beta * s + events.municipality_calibration_offset)
    env.require(np.allclose(events.synthetic_event_generation_probability, expected_q), "q formula changed")
    for field in [*env.SCORE_FIELDS, "synthetic_event_propensity", "synthetic_heat_stress_score", "selected_cause_probability"]:
        env.require(events[field].between(0, 1).all(), f"Score outside 0–1: {field}")
    env.require(events.synthetic_event_generation_probability.between(0, 1, inclusive="neither").all(), "Invalid Bernoulli q")
    return {"unique_ids": True, "missing_values": 0, "overlaps": overlaps, "valid_psgc_pairs": True,
            "six_valid_causes": True, "formula_checks_passed": True, "valid_durations": True,
            "within_source_period": True, "duplicate_incidents": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=RANDOM_SEED)
    parser.add_argument("--target-total-events", type=int, default=TARGET_TOTAL_EVENTS)
    parser.add_argument("--start", default=env.START)
    parser.add_argument("--end", default=env.END)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--skip-sensitivity", action="store_true", help="Debug only; mark validation incomplete")
    args = parser.parse_args()
    cfg = Config(seed=args.seed, target_total_events=args.target_total_events, start=args.start, end=args.end)
    validate_config(cfg)
    output = args.output or env.OUTPUT_ROOT / f"v3_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')}"
    env.require(not output.exists(), f"Refusing to overwrite existing output: {output}")
    paths = {**env.INPUTS, "v1_events": V1_EVENTS, "v2_duration_fits_only": V2_DURATION_FITS,
             "v1_script": ROOT / "scripts/legacy/generate_synthetic_outages_v1.py",
             "v1_environment_port": Path(env.__file__), "v2_duration_functions": ROOT / "scripts/generate_synthetic_outages.py",
             "v3_script": Path(__file__), "v3_report_script": ROOT / "scripts/outage_v3_report.py"}
    before = fingerprints(paths)
    print("Inspecting actual sources and recovering V1 / duration-only V2 parameters", flush=True)
    data, fits, v1 = inspect_inputs(cfg)
    print("Preparing streamed environmental histograms and an independent calibration pilot", flush=True)
    names, centers, histograms, pilot = prepare_calibration(data, cfg)
    alpha, offsets, geography = calibrate_occurrence(histograms["baseline"], centers, names, cfg)
    # Two expected-loss iterations, independent of the realized event draws.
    for _ in range(2):
        intercepts, duration_centers, blocked, _, _ = pilot_calibration(
            pilot, alpha, offsets, SCENARIOS["baseline"], cfg, fits)
        alpha, offsets, geography = calibrate_occurrence(histograms["baseline"], centers, names, cfg, blocked)
    intercepts, duration_centers, blocked, exposure_weights, probabilities = pilot_calibration(
        pilot, alpha, offsets, SCENARIOS["baseline"], cfg, fits)
    print(f"Generating Bernoulli events; expected accepted count {geography.calibrated_expected_events.sum():.2f}", flush=True)
    events, sampling = simulate(data, cfg, names, alpha, offsets, intercepts, duration_centers, fits)
    integrity = validate_events(events, data, cfg, alpha)
    output.mkdir(parents=True, exist_ok=False)
    events.to_csv(output / "synthetic_outage_events_v3.csv", index=False)
    # Check the actual serialized contract, including leading zero PSGCs and local offsets.
    roundtrip = pd.read_csv(output / "synthetic_outage_events_v3.csv", dtype={"barangay_psgc": str, "municipality_psgc": str})
    for field in ["start_time", "end_time"]:
        roundtrip[field] = pd.to_datetime(roundtrip[field], format="ISO8601", utc=True).dt.tz_convert(env.LOCAL_TZ)
    validate_events(roundtrip, data, cfg, alpha)
    env.write_json(output / "input_inspection.json", data.inspection)
    geography.to_csv(output / "municipality_calibration.csv", index=False)
    fits.to_csv(output / "duration_parameters.csv", index=False)
    calibration = {"alpha": alpha, "beta": cfg.beta, "municipality_offsets": dict(zip(names, offsets)),
                   "regularization": cfg.municipality_regularization, "soft_targets": SOFT_TARGETS,
                   "cause_intercepts": dict(zip(CAUSES, intercepts)), "duration_log_effect_centers": dict(zip(CAUSES, duration_centers)),
                   "expected_blocked_hours_approximation": blocked,
                   "pilot_expected_cause_shares": dict(zip(CAUSES, np.average(probabilities, weights=exposure_weights, axis=0))),
                   "pilot_size": len(pilot), "histogram_bins": len(centers),
                   "calibration_note": "Only Weather/Animal aggregate cause targets; no realized count quotas. Collision correction is low-rate mean dead-time approximation, not exact renewal integration."}
    env.write_json(output / "calibration.json", calibration)
    from outage_v3_report import create_reports, run_sensitivity
    sensitivity = run_sensitivity(data, cfg, names, centers, histograms, pilot, fits, alpha, offsets,
                                  intercepts, duration_centers, blocked, events) if not args.skip_sensitivity else {"complete": False}
    env.write_json(output / "sensitivity.json", sensitivity)
    summary = create_reports(output, events, data, cfg, v1, geography, sampling, integrity, calibration, sensitivity, pilot)
    after = fingerprints(paths)
    env.require(before == after, "An input or source implementation changed during generation")
    env.write_json(output / "run_manifest.json", {
        "generator_version": VERSION, "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "config": asdict(cfg), "inputs": before, "input_preservation_verified": True,
        "environmental_weights_H": env.WEATHER_WEIGHTS, "environmental_weights_S": SCENARIOS["baseline"],
        "calibration": calibration, "target_event_count": cfg.target_total_events, "actual_event_count": len(events),
        "sampling": sampling, "integrity": integrity, "sensitivity_complete": sensitivity["complete"],
        "event_csv_sha256": digest(output / "synthetic_outage_events_v3.csv"),
        "runtime": {"numpy": np.__version__, "pandas": pd.__version__},
        "warning": "Synthetic simulation; not actual PENELCO history or validated forecast accuracy."})
    print(json.dumps({"output": str(output), "events": len(events), "causes": summary["causes"],
                      "warnings": summary["warnings"]}, indent=2, default=env.json_default), flush=True)


if __name__ == "__main__":
    main()
