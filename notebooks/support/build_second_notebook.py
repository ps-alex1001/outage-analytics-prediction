"""Create the approved event-weather lesson. No source or application writes."""
from pathlib import Path
from textwrap import dedent
import nbformat as nbf

ROOT = Path(__file__).resolve().parents[2]
DESTINATION = ROOT / "notebooks/02_weather_coverage_and_event_context.ipynb"
if DESTINATION.exists():
    raise FileExistsError("Lesson 02 already exists; inspect it before editing.")
cells = []
def md(text):
    cells.append(nbf.v4.new_markdown_cell(dedent(text).strip()))
def code(text):
    source = dedent(text).strip()
    compile(source, "lesson02_code_cell", "exec")
    cells.append(nbf.v4.new_code_cell(source))

md("""
# 02 — Historical Weather and Outage Event Analysis

**Guided lesson · Phase A · 13 October 2026**

We follow one actual row from the **synthetic** outage dataset. Weather patterns around this event describe the simulation; they do not establish its real-world cause or PENELCO forecasting accuracy.

## 1. Learning objectives

- Select an event and match its municipal weather point.
- Extract 24h before onset, the outage itself, and 12h after restoration.
- Calculate and hand-check rain 6/24h, mean wind 6h and maximum supplied gust 6h.
- Check timestamps, units, missing hours and leakage boundaries.

Run cells in order using this project's `.venv/Scripts/python.exe` in PyCharm. Change `selected_outage_id` later for practice; choose an event away from the weather dataset's beginning/end.
""")
md("""
## 2. Dataset introduction

| Existing source | Fields used / purpose |
|---|---|
| `data/outages/synthetic_outage_events.csv` | Event ID, PSGCs, names, start/end and duration |
| `data/boundaries/bataan_barangays.geojson` | Check barangay and municipality codes/names |
| `data/weather/open_meteo_bataan_2019_01_to_2026_06.csv` | Hourly municipal point, clock/zone/offset, rain (mm), wind and gust (m/s) |
| `notebooks/assessment/weather_feature_contract.json` | Approved window and assumed-lag definitions |

The original weather export headers confirm the units; the merge script renamed columns without converting them. The recorded zone is Asia/Singapore (UTC+8), which we convert to Asia/Manila; this historical file has no publication times or forecast-run IDs.
""")
code("""
from pathlib import Path
from zoneinfo import ZoneInfo
import hashlib
import json
import math

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from IPython.display import Markdown, display

MANILA = ZoneInfo("Asia/Manila")
BLUE, GOLD, GREY = "#28618C", "#B67820", "#555555"
plt.rcParams.update({"font.size": 11, "axes.spines.top": False, "axes.spines.right": False})
""")
code("""
root_candidates = [Path.cwd(), *Path.cwd().parents]
project_root = next((path for path in root_candidates
    if (path / "data/outages/synthetic_outage_events.csv").is_file()), None)
assert project_root is not None, "Set PyCharm's working directory to this repository."
sources = {
    "events": project_root / "data/outages/synthetic_outage_events.csv",
    "boundaries": project_root / "data/boundaries/bataan_barangays.geojson",
    "weather": project_root / "data/weather/open_meteo_bataan_2019_01_to_2026_06.csv",
    "contract": project_root / "notebooks/assessment/weather_feature_contract.json",
}
assert all(path.is_file() for path in sources.values())
def fingerprint(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()
source_hashes_before = {name: fingerprint(path) for name, path in sources.items()}
contract = json.loads(sources["contract"].read_text(encoding="utf-8"))
assert contract["alignment"]["historical_reconstruction_lag_hours"] == 1
""")
md("""
## 3. Select one event

The default is the first generated Weather / Storm event in July 2025, chosen for a worked lesson—not as a representative sample. `loc` selects its ID; `iloc[0]` retrieves the single matching row after we check uniqueness.
""")
code("""
selected_outage_id = "SYN-42-0300803004-20250701T1400"  # Change this for an exercise.
events = pd.read_csv(sources["events"], dtype={
    "outage_id": "string", "barangay_psgc": "string", "municipality_psgc": "string"})
matches = events.loc[events.outage_id.eq(selected_outage_id)]
assert len(matches) == 1, "Choose exactly one existing outage ID."
event = matches.iloc[0]
assert bool(event.is_synthetic)
onset = pd.to_datetime(event.start_time, format="mixed", utc=True).tz_convert(MANILA)
restoration = pd.to_datetime(event.end_time, format="mixed", utc=True).tz_convert(MANILA)
assert onset == onset.floor("h"), "This lesson uses hourly-aligned event starts."
assert restoration > onset
assert np.isclose((restoration - onset).total_seconds() / 60, event.duration_minutes)
display(pd.Series({"event": event.outage_id, "barangay": event.barangay,
    "municipality": event.municipality, "onset (Manila)": str(onset),
    "restoration (Manila)": str(restoration), "duration (minutes)": event.duration_minutes}, name="Selected event"))
""")
md("""
### Match the location using codes first

Read PSGC identifiers as text to preserve leading zeros. Check the event against its boundary record, then use the documented name alias to match the weather file; a municipal point represents weather shared by its barangays, not a sensor at the outage.
""")
code("""
boundary_json = json.loads(sources["boundaries"].read_text(encoding="utf-8"))
boundary_rows = pd.DataFrame([feature["properties"] for feature in boundary_json["features"]])
assert boundary_rows.barangay_psgc.is_unique
boundary_match = boundary_rows.loc[boundary_rows.barangay_psgc.eq(event.barangay_psgc)]
assert len(boundary_match) == 1
boundary = boundary_match.iloc[0]
for field in ["barangay", "municipality_psgc", "municipality"]:
    assert event[field] == boundary[field], f"Boundary mismatch: {field}"
weather_name_aliases = {"City of Balanga": "Balanga"}
weather_municipality = weather_name_aliases.get(event.municipality, event.municipality)
location_id = "municipality:" + event.municipality_psgc
print(f"{event.municipality} ({event.municipality_psgc}) → weather name: {weather_municipality}")
""")
md("""
## 4. Define the time window

The visible window runs from `onset − 24h` to `restoration + 12h`. We load another 24h of earlier history so rolling values at the left edge can be computed; only the requested window is plotted.

Restoration includes minutes/seconds. We retain the next full hourly record to cover the last overlapping rainfall interval, but never split or prorate its amount; an instantaneous wind sample beyond the exact end stays outside the plotted window.
""")
code("""
timeline_start = onset - pd.Timedelta(hours=24)
timeline_end = restoration + pd.Timedelta(hours=12)
read_start = timeline_start - pd.Timedelta(hours=24)
read_end = timeline_end.ceil("h")
expected_hours = pd.date_range(read_start, read_end, freq="h")
display(pd.Series({"visible start": str(timeline_start), "visible end": str(timeline_end),
    "loaded start (extra history)": str(read_start), "loaded end (hour support)": str(read_end)}, name="Manila times"))
""")
md("""
### Read a small slice without loading the whole weather file

`chunksize` reads the CSV in pieces; we keep only the selected municipality and time range. Localize the naive source clock using its recorded zone, then convert it to Manila—do not interpret that clock as UTC.
""")
code("""
weather_columns = ["municipality", "latitude", "longitude", "elevation_m",
    "timezone", "utc_offset_seconds", "time", "precipitation_mm",
    "wind_speed_10m_m_s", "wind_gusts_10m_m_s"]
weather_parts, location_parts = [], []
for chunk in pd.read_csv(sources["weather"], usecols=weather_columns, chunksize=100_000):
    local_rows = chunk.loc[chunk.municipality.eq(weather_municipality)].copy()
    if local_rows.empty:
        continue
    assert local_rows.timezone.eq("Asia/Singapore").all()
    assert local_rows.utc_offset_seconds.eq(28800).all()
    location_parts.append(local_rows[["municipality", "latitude", "longitude", "elevation_m"]].drop_duplicates())
    local_rows["timestamp"] = pd.to_datetime(local_rows.time, errors="raise").dt.tz_localize(
        "Asia/Singapore").dt.tz_convert(MANILA)
    weather_parts.append(local_rows.loc[local_rows.timestamp.between(read_start, read_end)])
assert weather_parts, "No matching weather municipality found."
weather_slice = pd.concat(weather_parts, ignore_index=True).sort_values("timestamp")
location_metadata = pd.concat(location_parts, ignore_index=True).drop_duplicates()
assert len(location_metadata) == 1, "Review changing or ambiguous weather coordinates."
display(location_metadata.reset_index(drop=True))
""")
md("""
### Validate hours, location and units

A gap is a missing timestamp; a null is a missing value at an existing timestamp. Reindex to an hourly grid and preserve both as nulls—missing rainfall is not zero rainfall.

The following checks cover this loaded event window. Unit labels are traced to the original export headers; numeric range checks alone cannot prove units or historical forecast availability.
""")
code("""
weather_values = ["precipitation_mm", "wind_speed_10m_m_s", "wind_gusts_10m_m_s"]
assert weather_slice.municipality.eq(weather_municipality).all()
assert not weather_slice.timestamp.duplicated().any(), "Duplicate municipality/hour keys."
assert weather_slice.timestamp.eq(weather_slice.timestamp.dt.floor("h")).all()
assert np.isfinite(weather_slice[weather_values].to_numpy(dtype=float)).all()
assert weather_slice[weather_values].ge(0).all().all(), "Negative rain or wind: review source."
missing_hours = expected_hours.difference(pd.DatetimeIndex(weather_slice.timestamp))
weather_hourly = weather_slice.set_index("timestamp")[weather_values].reindex(expected_hours)
weather_hourly.index.name = "valid_time"
weather_hourly["location_id"] = location_id
display(pd.Series({"expected hourly records": len(expected_hours), "observed records": len(weather_slice),
    "missing timestamps": len(missing_hours), "null weather cells": int(weather_hourly[weather_values].isna().sum().sum())}, name="Window checks"))
assert len(missing_hours) == 0, "Incomplete event window; investigate rather than zero-fill."
assert weather_hourly[weather_values].notna().all().all()
""")
code("""
visible_labels = weather_hourly.loc[timeline_start:timeline_end]
hour_support = weather_hourly.loc[timeline_start:read_end]
print(f"Visible hourly labels: {len(visible_labels)}; rainfall support labels: {len(hour_support)}")
preview = visible_labels[weather_values].copy()
preview.index = preview.index.strftime("%d %b %H:%M")
preview.columns = ["Rain (mm)", "Wind (m/s)", "Supplied gust (m/s)"]
display(pd.concat([preview.head(3), preview.tail(3)]))
""")
md("""
## 5. What do the weather timestamps mean?

Rainfall at `s` is an amount for the preceding hour `(s−1h, s]`; wind speed is an hourly scalar sample. Historical gust definitions depend on the source model, so we use the precise phrase **maximum supplied gust** rather than claim a measured continuous-time maximum.

The approved specification records this uncertainty: [historical definitions](https://open-meteo.com/en/docs/historical-weather-api) and [forecast definitions](https://open-meteo.com/en/docs). Matching column names alone does not establish matching temporal meaning.

### Four window formulas

| Feature ending at cutoff `c` | Calculation | Units |
|---|---|---|
| 6h rain | Add the six hourly amounts at `c−5h … c` | mm |
| 24h rain | Add the 24 hourly amounts at `c−23h … c` | mm |
| 6h mean wind | Add six scalar wind samples, divide by 6 | m/s |
| 6h maximum supplied gust | Take the largest of six supplied values | m/s |

Require every expected value. `rolling('6h', closed='right', min_periods=6)` uses labels in `(c−6h, c]`; on our checked hourly grid, that is exactly six samples.
""")
md("""
### Set the issue time and cutoff

For this worked check, the issue time equals onset: this tests ordering, **not** a 24–72h forecast. The approved exploratory lag gives `cutoff = onset − 1h`; real operational availability remains unknown because release times are absent.
""")
code("""
issue_time = onset
assumed_lag = pd.Timedelta(hours=contract["alignment"]["historical_reconstruction_lag_hours"])
cutoff = issue_time - assumed_lag
six_labels = pd.date_range(cutoff - pd.Timedelta(hours=5), cutoff, freq="h")
twenty_four_labels = pd.date_range(cutoff - pd.Timedelta(hours=23), cutoff, freq="h")
six_hour_values = weather_hourly.reindex(six_labels)[weather_values]
twenty_four_rain = weather_hourly.reindex(twenty_four_labels).precipitation_mm
assert six_hour_values.notna().all().all() and twenty_four_rain.notna().all()
print("Issue:", issue_time, "| cutoff:", cutoff)
print("6h precipitation interval:", cutoff - pd.Timedelta(hours=6), "to", cutoff)
print("24h precipitation interval:", cutoff - pd.Timedelta(hours=24), "to", cutoff)
""")
md("""
### Calculate within each weather location

`groupby('location_id')` prevents one location's values entering another's window. We calculate at weather-valid times, then relabel each cutoff `c` as the exploratory issue `c+1h`; this moves the label, not the observations.

The example contains one location and a small loaded window. Early values without enough history remain null.
""")
code("""
feature_parts = []
for group_location, local_weather in weather_hourly.groupby("location_id", sort=False):
    local_weather = local_weather.sort_index()
    rainfall = local_weather.precipitation_mm
    wind = local_weather.wind_speed_10m_m_s
    supplied_gust = local_weather.wind_gusts_10m_m_s
    local_features = pd.DataFrame({
        "rain_sum_6h_mm": rainfall.rolling("6h", closed="right", min_periods=6).sum(),
        "rain_sum_24h_mm": rainfall.rolling("24h", closed="right", min_periods=24).sum(),
        "wind_mean_6h_m_s": wind.rolling("6h", closed="right", min_periods=6).mean(),
        "gust_max_6h_m_s": supplied_gust.rolling("6h", closed="right", min_periods=6).max(),
    })
    local_features["location_id"] = group_location
    local_features.index = local_features.index + assumed_lag
    feature_parts.append(local_features)
weather_features = pd.concat(feature_parts).rename_axis("issue_time")
onset_features = weather_features.loc[issue_time].drop("location_id").astype(float)
display(onset_features.to_frame("Value at onset issue (assumed 1h lag)").round(4))
""")
md("""
## 6. Verify using actual hourly values

Read the six rows below. Add the rain column, average the wind column, and identify the largest gust; `math.fsum` independently adds the Python values rather than repeating the rolling calculation.
""")
code("""
manual_six_table = six_hour_values.copy()
manual_six_table.index = manual_six_table.index.strftime("%d %b %H:%M")
manual_six_table.columns = ["Rain (mm)", "Wind (m/s)", "Supplied gust (m/s)"]
display(manual_six_table)
rain_six_manual = math.fsum(six_hour_values.precipitation_mm.tolist())
wind_six_manual = math.fsum(six_hour_values.wind_speed_10m_m_s.tolist()) / 6
gust_six_manual = max(six_hour_values.wind_gusts_10m_m_s.tolist())
print(f"Rain sum = {rain_six_manual:.3f} mm")
print(f"Wind mean = {wind_six_manual:.4f} m/s; supplied gust maximum = {gust_six_manual:.3f} m/s")
""")
md("""
### Check the 24h rain total in four small blocks

Each row below lists six actual amounts. Add each block, then add the four subtotals; the final result must match the 24h rolling total.
""")
code("""
rain_blocks = []
for first_row in range(0, 24, 6):
    block = twenty_four_rain.iloc[first_row:first_row + 6]
    rain_blocks.append({"Local labels": f"{block.index[0]:%d %b %H:%M} → {block.index[-1]:%d %b %H:%M}",
        "Six amounts (mm)": block.tolist(), "Subtotal (mm)": math.fsum(block.tolist())})
manual_rain_table = pd.DataFrame(rain_blocks)
display(manual_rain_table)
rain_twenty_four_manual = math.fsum(manual_rain_table["Subtotal (mm)"].tolist())
manual_features = pd.Series({"rain_sum_6h_mm": rain_six_manual,
    "rain_sum_24h_mm": rain_twenty_four_manual, "wind_mean_6h_m_s": wind_six_manual,
    "gust_max_6h_m_s": gust_six_manual})
feature_comparison = pd.DataFrame({"Rolling": onset_features, "Hand-check": manual_features})
feature_comparison["Absolute difference"] = (feature_comparison.Rolling - feature_comparison["Hand-check"]).abs()
assert np.allclose(feature_comparison.Rolling, feature_comparison["Hand-check"], rtol=0, atol=1e-10)
display(feature_comparison.round(6))
""")
code("""
display(Markdown(f"**At onset, using the 1h assumed lag:** rain totals are "
    f"{onset_features.rain_sum_6h_mm:.2f} mm (6h) and {onset_features.rain_sum_24h_mm:.2f} mm (24h). "
    f"Mean wind is {onset_features.wind_mean_6h_m_s:.2f} m/s; maximum supplied gust is "
    f"{onset_features.gust_max_6h_m_s:.2f} m/s (6h). These describe one municipal weather window, not an outage cause."))
""")
md("""
## 7. Visualize the event timeline

Gold shading marks the exact synthetic outage interval; dashed marks onset and dotted marks restoration. Rain bars occupy their preceding hour; bars touching the visible edges retain the full recorded amount and are not prorated.

The rolling curves use issue-time labels with the assumed 1h lag. Curves and weather shown after onset are **retrospective context**, not inputs for a prediction made at onset.
""")
code("""
tick_times = list(pd.date_range(timeline_start, timeline_end, freq="6h"))
if (timeline_end - tick_times[-1]) < pd.Timedelta(hours=3):
    tick_times.pop()  # Keep the exact right endpoint readable.
tick_times.append(timeline_end)
def mark_outage(axis):
    axis.axvspan(onset, restoration, color=GOLD, alpha=0.2, label="Synthetic outage")
    axis.axvline(onset, color=GREY, linestyle="--", linewidth=1)
    axis.axvline(restoration, color=GREY, linestyle=":", linewidth=1)
    axis.set_xlim(timeline_start, timeline_end)
    axis.set_xticks(tick_times)
    axis.xaxis.set_major_formatter(mdates.DateFormatter("%d %b\\n%H:%M", tz=MANILA))
    axis.grid(axis="y", alpha=0.15)
    axis.set_ylim(bottom=0)
""")
code("""
fig, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=True, layout="constrained")
rain_left_edges = hour_support.index - pd.Timedelta(hours=1)
axes[0].bar(rain_left_edges, hour_support.precipitation_mm, width=1/24,
            align="edge", color=BLUE, edgecolor="white", label="Recorded hourly amount")
axes[0].set(title=f"Historical precipitation · {weather_municipality} · synthetic event", ylabel="Rain (mm / hourly interval)")
for feature, label, style, color in [("rain_sum_6h_mm", "6h rain", "-", BLUE), ("rain_sum_24h_mm", "24h rain", "--", GREY)]:
    axes[1].plot(weather_features.index, weather_features[feature], linestyle=style, color=color, label=label)
axes[1].set(title="Trailing rainfall totals · issue labels use assumed 1h lag", ylabel="Accumulated rain (mm)", xlabel="Asia/Manila (UTC+8)")
for axis in axes:
    mark_outage(axis)
    axis.legend(loc="upper left", bbox_to_anchor=(1.01, 1), frameon=False)
plt.show()
""")
code("""
fig, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=True, layout="constrained")
axes[0].plot(visible_labels.index, visible_labels.wind_speed_10m_m_s, color=BLUE, label="Wind sample")
axes[0].plot(visible_labels.index, visible_labels.wind_gusts_10m_m_s, color=GREY, linestyle="--", label="Supplied gust")
axes[0].set(title=f"Historical wind · {weather_municipality} · synthetic event", ylabel="Speed (m/s)")
axes[1].plot(weather_features.index, weather_features.wind_mean_6h_m_s, color=BLUE, label="6h mean wind")
axes[1].plot(weather_features.index, weather_features.gust_max_6h_m_s, color=GREY, linestyle="--", label="6h maximum supplied gust")
axes[1].set(title="Trailing wind features · issue labels use assumed 1h lag", ylabel="Speed (m/s)", xlabel="Asia/Manila (UTC+8)")
for axis in axes:
    mark_outage(axis)
    axis.legend(loc="upper left", bbox_to_anchor=(1.01, 1), frameon=False)
plt.show()
""")
code("""
largest_rain_label = visible_labels.precipitation_mm.idxmax()
largest_gust_label = visible_labels.wind_gusts_10m_m_s.idxmax()
display(Markdown(f"**Read the charts:** the largest displayed hourly rain amount is "
    f"{visible_labels.precipitation_mm.max():.2f} mm, labeled {largest_rain_label:%d %b %H:%M}. "
    f"The largest displayed supplied gust is {visible_labels.wind_gusts_10m_m_s.max():.2f} m/s "
    f"at {largest_gust_label:%d %b %H:%M}. These hourly-label summaries exclude the extra final support label; "
    "neither a peak nor its timing proves why the synthetic event occurred."))
""")
md("""
## 8. Retrospective view versus prediction inputs

| Use | Time rule | What we can claim |
|---|---|---|
| Event timeline | Before, during and after the event | Historical context only |
| Worked onset features | Required labels end at `onset−1h` | Time ordering checked; publication availability unknown |
| Future operational prediction | Inputs demonstrably available by issue; eligible forecast vintage for future weather | Requires availability/run metadata and later evaluation |

Restoration time, future realized weather and the generated cause cannot enter onset prediction features. This lesson fits no model and establishes no forecasting performance.

### Verify the boundary without invented weather

Recompute from only the actual values at or before cutoff. The result should agree even when all later records are excluded.
""")
code("""
past_only = weather_hourly.loc[:cutoff]
assert past_only.index.max() < issue_time
past_checks = pd.Series({
    "rain_sum_6h_mm": past_only.precipitation_mm.rolling("6h", closed="right", min_periods=6).sum().loc[cutoff],
    "rain_sum_24h_mm": past_only.precipitation_mm.rolling("24h", closed="right", min_periods=24).sum().loc[cutoff],
    "wind_mean_6h_m_s": past_only.wind_speed_10m_m_s.rolling("6h", closed="right", min_periods=6).mean().loc[cutoff],
    "gust_max_6h_m_s": past_only.wind_gusts_10m_m_s.rolling("6h", closed="right", min_periods=6).max().loc[cutoff],
})
assert np.allclose(past_checks, onset_features, rtol=0, atol=1e-10)
assert six_labels.max() == cutoff and twenty_four_labels.max() == cutoff
assert len(six_labels) == 6 and len(twenty_four_labels) == 24
assert {name: fingerprint(path) for name, path in sources.items()} == source_hashes_before
print("Checks passed: one location, complete hours, matching manual totals, past-only inputs, unchanged sources.")
""")
md("""
## 9. Try it yourself

1. Change the event ID using the small July 2025 list below, restart, and run all cells. Explain its weather-location match.
2. Add the six rain values by hand and verify the wind mean and gust maximum without looking at `onset_features`.
3. Use new variables `practice_issue = onset − 6h` and `practice_cutoff = practice_issue − assumed_lag`. Rebuild the hourly labels and compare with the onset features; keep the main lesson variables unchanged.
4. Remove one real hourly row in an in-memory copy below. Explain why an incomplete rain total must be null.

Do not edit the source CSVs. These are exercises using real values from the existing files.
""")
code("""
local_event_starts = pd.to_datetime(events.start_time, format="mixed", utc=True).dt.tz_convert(MANILA)
practice_candidates = events.loc[events.municipality_psgc.eq(event.municipality_psgc)
    & local_event_starts.between(pd.Timestamp("2025-07-01", tz=MANILA), pd.Timestamp("2025-07-31 23:00", tz=MANILA))]
display(practice_candidates[["outage_id", "barangay", "start_time", "duration_minutes"]].head(5))
# Exercise: select one of these IDs in section 3, then restart and run all cells.
""")
code("""
# Exercise: delete the middle hourly row from this in-memory copy only.
practice_rain = weather_hourly.precipitation_mm.copy()
hour_to_remove = six_labels[2]
# practice_rain = practice_rain.drop(hour_to_remove).reindex(weather_hourly.index)
# practice_total = practice_rain.rolling('6h', closed='right', min_periods=6).sum().loc[cutoff]
# print(practice_total)
# assert pd.isna(practice_total)

# Exercise: write the six rainfall additions and wind additions in your own cell.
# Extension: keep issue time and cutoff as separate variables when exploring earlier predictions.
""")
md("""
## 10. Summary and later FastAPI outputs

We matched one synthetic event to one municipal weather point, retained exact outage times, plotted its surrounding hours, and independently checked four trailing features. All window checks must pass before interpreting the values; results concern this selected event, not all outages.

Later FastAPI can expose three small outputs:

| Output | Useful fields |
|---|---|
| Event metadata | ID, synthetic flag, barangay/municipality PSGCs, onset, restoration, weather-point coordinates |
| Event weather timeline | Offset-aware timestamps, hourly rain/wind/supplied gust, units and interval meaning; explicit retrospective label |
| Feature snapshot | Issue time, cutoff, four values/units, sample counts, completeness and availability status |

The snapshot must say `retrospective_reconstruction_assumed_lag`; it is not an evaluated model input contract yet. Forecast compatibility, predictive usefulness and model selection remain later work; stop here for review.
""")
notebook = nbf.v4.new_notebook(cells=cells, metadata={
    "kernelspec": {"display_name": "Python (project .venv)", "language": "python", "name": "python3"},
    "language_info": {"name": "python", "version": "3.14.6"},
})
nbf.validate(notebook)
nbf.write(notebook, DESTINATION)
print(f"Created {DESTINATION} ({len(cells)} guided cells)")
