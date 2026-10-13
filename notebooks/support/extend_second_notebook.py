"""Extend lesson 02 in place with bounded retrospective weather context."""
from pathlib import Path
from textwrap import dedent
import nbformat as nbf

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "notebooks/02_weather_coverage_and_event_context.ipynb"
notebook = nbf.read(PATH, as_version=4)
marker = "### Other conditions overlapping the outage"
if any(marker in cell.source for cell in notebook.cells):
    raise RuntimeError("The extension already exists; inspect before editing again.")

def find_code(prefix):
    matches = [cell for cell in notebook.cells if cell.cell_type == "code" and cell.source.startswith(prefix)]
    assert len(matches) == 1, f"Ambiguous cell: {prefix}"
    return matches[0]

imports = find_code("from pathlib import Path")
imports.source = imports.source.replace("import pandas as pd", "import pandas as pd\nimport xarray as xr")
paths = find_code("root_candidates =")
paths.source = paths.source.replace('    "contract": project_root', '    "era5": project_root / "data/weather/era5_bataan_2019_01_to_2026_06.nc",\n    "contract": project_root')
loader = find_code("weather_columns =")
loader.source = loader.source.replace('    "wind_speed_10m_m_s", "wind_gusts_10m_m_s"]',
    '    "wind_speed_10m_m_s", "wind_gusts_10m_m_s", "temperature_2m_c",\n'
    '    "relative_humidity_2m_pct", "surface_pressure_hpa",\n'
    '    "soil_moisture_0_to_7cm_m3_m3", "soil_moisture_7_to_28cm_m3_m3"]')
validation = find_code("weather_values =")
validation.source = validation.source.replace('"wind_gusts_10m_m_s"]',
    '"wind_gusts_10m_m_s",\n    "temperature_2m_c", "relative_humidity_2m_pct", "surface_pressure_hpa",\n'
    '    "soil_moisture_0_to_7cm_m3_m3", "soil_moisture_7_to_28cm_m3_m3"]', 1)
validation.source = validation.source.replace('assert weather_slice[weather_values].ge(0).all().all(), "Negative rain or wind: review source."',
    'assert weather_slice[["precipitation_mm", "wind_speed_10m_m_s", "wind_gusts_10m_m_s"]].ge(0).all().all()\n'
    'assert weather_slice.relative_humidity_2m_pct.between(0, 100).all()\n'
    'assert weather_slice.surface_pressure_hpa.gt(0).all()\n'
    'soil_columns = ["soil_moisture_0_to_7cm_m3_m3", "soil_moisture_7_to_28cm_m3_m3"]\n'
    'assert weather_slice[soil_columns].ge(0).all().all() and weather_slice[soil_columns].le(1).all().all()')
preview = find_code("visible_labels =")
preview.source = preview.source.replace('preview = visible_labels[weather_values].copy()',
    'preview = visible_labels[["precipitation_mm", "wind_speed_10m_m_s", "wind_gusts_10m_m_s"]].copy()')
manual = find_code("manual_six_table =")
manual.source = manual.source.replace('manual_six_table = six_hour_values.copy()',
    'manual_six_table = six_hour_values[["precipitation_mm", "wind_speed_10m_m_s", "wind_gusts_10m_m_s"]].copy()')
for cell in notebook.cells:
    if cell.cell_type != "markdown":
        continue
    if cell.source.startswith("## 2. Dataset introduction"):
        cell.source = cell.source.replace('| `notebooks/assessment/weather_feature_contract.json`',
            '| `data/weather/era5_bataan_2019_01_to_2026_06.nc` | Atmospheric context at the event\'s recorded grid cell |\n| `notebooks/assessment/weather_feature_contract.json`')
    if cell.source.startswith("## 10. Summary"):
        cell.source = cell.source.replace('and independently checked four trailing features.',
            'checked four trailing features, and inspected other conditions before, during and after the outage.')
        cell.source = cell.source.replace('hourly rain/wind/supplied gust, units and interval meaning;',
            'rain/wind, temperature, humidity, pressure, soil moisture and ERA5 context; units, source location/grid and missingness;')
    if cell.source.startswith("## 9. Try it yourself"):
        cell.source = cell.source.replace('Do not edit the source CSVs.',
            '5. Compare during-outage humidity with the before-outage mean. Compare both soil depths and explain why neither confirms saturated soil or a tree failure.\n\nDo not edit the source CSVs.')

cells = []
def md(source):
    cells.append(nbf.v4.new_markdown_cell(dedent(source).strip()))
def code(source):
    source = dedent(source).strip()
    compile(source, "lesson02_extension", "exec")
    cells.append(nbf.v4.new_code_cell(source))

md("""
### Other conditions overlapping the outage

We also inspect temperature, relative humidity, surface pressure and two soil-moisture depths from the same municipal weather point. These describe conditions during the event; they do not show that heat, moisture or pressure caused it.

| Variable | Units | Read it as |
|---|---|---|
| Temperature / relative humidity | °C / % | Air temperature / humidity relative to saturation |
| Surface pressure | hPa | Pressure at the weather point, not sea-level pressure |
| Soil moisture 0–7 / 7–28 cm | m³/m³ | Modeled water volume per soil volume at two depths; not measured soil saturation |

The hourly samples inside `[onset, restoration)` overlap the outage. Unlike rainfall amounts, these are point-in-time samples; we do not allocate a full hourly value proportionally across the fractional restoration time.
""")
code("""
context_labels = {
    "temperature_2m_c": "Temperature (°C)",
    "relative_humidity_2m_pct": "Relative humidity (%)",
    "surface_pressure_hpa": "Surface pressure (hPa)",
    "soil_moisture_0_to_7cm_m3_m3": "Soil moisture 0–7 cm (m³/m³)",
    "soil_moisture_7_to_28cm_m3_m3": "Soil moisture 7–28 cm (m³/m³)",
}
municipal_context = visible_labels[list(context_labels)]
during_municipal = municipal_context.loc[(municipal_context.index >= onset)
    & (municipal_context.index < restoration)]
display(during_municipal.rename(columns=context_labels).round(3))
""")
md("""
### Add atmospheric context from ERA5

ERA5 supplies a coarse grid, separate from the municipal point. We use the grid coordinates already recorded with this event and check them against the NetCDF; its timestamps are UTC and must be converted to Manila.

| Variable | Units | Simple meaning |
|---|---|---|
| CAPE / CIN | J/kg | Energy available for convection / energy barrier to convection |
| Total column water vapour | kg/m² | Water vapour through the atmospheric column, not rainfall |
| Cloud-base height | m | Modeled height of cloud base above the surface |

CAPE alone does not confirm a thunderstorm or lightning strike. Keep CIN's source sign; no severe-weather thresholds are introduced. [ECMWF CAPE definition](https://codes.ecmwf.int/grib/param-db/59), [CIN definition](https://codes.ecmwf.int/grib/param-db/228001).
""")
code("""
era5_variables = ["cape", "cin", "tcwv", "cbh"]
expected_era5_units = {"cape": "J kg**-1", "cin": "J kg**-1", "tcwv": "kg m**-2", "cbh": "m"}
grid_latitude, grid_longitude = float(event.era5_latitude), float(event.era5_longitude)
utc_start = read_start.tz_convert("UTC").tz_localize(None).to_datetime64()
utc_end = read_end.tz_convert("UTC").tz_localize(None).to_datetime64()
with xr.open_dataset(sources["era5"], engine="h5netcdf") as era5:
    assert era5.attrs.get("Conventions") == "CF-1.7"
    assert era5.valid_time.encoding.get("units") == "seconds since 1970-01-01"
    assert grid_latitude in era5.latitude.values and grid_longitude in era5.longitude.values
    for name, unit in expected_era5_units.items():
        assert era5[name].attrs.get("units") == unit, f"Review changed units for {name}."
    grid_slice = era5[era5_variables].sel(latitude=grid_latitude, longitude=grid_longitude)
    era5_slice = grid_slice.sel(valid_time=slice(utc_start, utc_end)).load()
print(f"Recorded event grid: {grid_latitude:.2f}°N, {grid_longitude:.2f}°E; 0.25° spacing")
""")
code("""
era5_times = pd.DatetimeIndex(era5_slice.valid_time.values).tz_localize("UTC").tz_convert(MANILA)
assert era5_times.is_unique and era5_times.is_monotonic_increasing
era5_missing_hours = expected_hours.difference(era5_times)
era5_hourly = pd.DataFrame({name: era5_slice[name].values for name in era5_variables}, index=era5_times)
assert not np.isinf(era5_hourly.to_numpy(dtype=float)).any(), "Review infinite source values."
era5_hourly = era5_hourly.reindex(expected_hours)
era5_hourly.index.name = "valid_time"
era5_missing_values = era5_hourly.isna().sum()
display(era5_missing_values.to_frame("Missing values in loaded grid window"))
assert len(era5_missing_hours) == 0, "Review missing ERA5 timestamps."
# Preserve any source nulls; they must remain gaps, not invented clear skies or zero inhibition.
for name in ["cape", "tcwv", "cbh"]:
    assert era5_hourly[name].dropna().ge(0).all()
assert np.isclose(era5_hourly.loc[onset, "cape"], float(event.cape_j_kg), rtol=1e-6)
""")
md("""
### Compare before, during and after

These are simple averages and ranges of hourly samples—not time-weighted averages or significance tests. The three periods have different lengths; display their sample counts and preserve missing values.

Only nine additional context variables enter this table. Existing rainfall and wind calculations stay separate; source model differences also mean ERA5 and municipal values are not interchangeable.
""")
code("""
era5_labels = {"cape": "CAPE (J/kg)", "cin": "CIN (J/kg)",
    "tcwv": "Column water vapour (kg/m²)", "cbh": "Cloud-base height (m)"}
context_values = municipal_context.rename(columns=context_labels).join(
    era5_hourly.loc[timeline_start:timeline_end].rename(columns=era5_labels), validate="one_to_one")
phase_masks = {
    "Before": context_values.index < onset,
    "During": (context_values.index >= onset) & (context_values.index < restoration),
    "After": context_values.index >= restoration,
}
phase_samples = pd.Series({name: int(mask.sum()) for name, mask in phase_masks.items()}, name="Expected hourly samples")
assert phase_samples.sum() == len(context_values)
display(phase_samples.to_frame())
""")
code("""
context_summary = pd.DataFrame({name + " mean": context_values.loc[mask].mean()
    for name, mask in phase_masks.items()})
during_context = context_values.loc[phase_masks["During"]]
context_summary["During min"] = during_context.min()
context_summary["During max"] = during_context.max()
context_summary["Valid during / expected"] = during_context.count().astype(str) + " / " + str(phase_samples["During"])
display(context_summary.round(3))
print("Expected before/during/after samples:", phase_samples.tolist())
""")
md("""
### Plot the other overlapping conditions

The same shading marks this synthetic outage. Each panel uses its own units and a vertical scale focused on the observed range; compare changes within a variable, not heights across different panels.
""")
code("""
fig, axes = plt.subplots(2, 2, figsize=(12, 7), sharex=True, layout="constrained")
for axis, column in zip(axes.flat, list(context_labels)[:3]):
    axis.plot(municipal_context.index, municipal_context[column], color=BLUE)
    axis.set(title=context_labels[column], ylabel=context_labels[column].split("(")[-1].rstrip(")"))
soil_axis = axes.flat[3]
for column, label, style, color in [
    ("soil_moisture_0_to_7cm_m3_m3", "0–7 cm", "-", BLUE),
    ("soil_moisture_7_to_28cm_m3_m3", "7–28 cm", "--", GREY)]:
    soil_axis.plot(municipal_context.index, municipal_context[column], label=label, linestyle=style, color=color)
soil_axis.set(title="Soil moisture at two depths", ylabel="m³/m³")
soil_axis.legend(loc="best")
for axis in axes.flat:
    mark_outage(axis)
    axis.set_autoscaley_on(True)
    axis.relim()
    axis.autoscale_view(scalex=False, scaley=True)
    axis.set_xticks([timeline_start, onset, timeline_end])
fig.suptitle(f"Other municipal weather · {weather_municipality} · retrospective context")
plt.show()
""")
code("""
fig, axes = plt.subplots(2, 2, figsize=(12, 7), sharex=True, layout="constrained")
era5_visible = era5_hourly.loc[timeline_start:timeline_end]
for axis, column in zip(axes.flat, era5_variables):
    axis.plot(era5_visible.index, era5_visible[column], color=BLUE)
    axis.set(title=era5_labels[column], ylabel=era5_labels[column].split("(")[-1].rstrip(")"))
    mark_outage(axis)
    axis.set_autoscaley_on(True)
    axis.relim()
    axis.autoscale_view(scalex=False, scaley=True)
    axis.set_xticks([timeline_start, onset, timeline_end])
fig.suptitle(f"ERA5 grid {grid_latitude:.2f}°N, {grid_longitude:.2f}°E · retrospective context")
plt.show()
""")
code("""
display(Markdown(f"**During the selected outage:** temperature ranges from "
    f"{during_municipal.temperature_2m_c.min():.1f} to {during_municipal.temperature_2m_c.max():.1f} °C; "
    f"humidity from {during_municipal.relative_humidity_2m_pct.min():.0f} to "
    f"{during_municipal.relative_humidity_2m_pct.max():.0f}%. "
    f"CAPE ranges from {during_context['CAPE (J/kg)'].min():.1f} to "
    f"{during_context['CAPE (J/kg)'].max():.1f} J/kg across {phase_samples['During']} hourly labels. "
    "These are overlapping conditions; they do not identify an outage mechanism."))
""")
md("""
### Other outages may share the same weather

Check whether other events in this municipality overlap the selected outage. They share a municipal weather point, so counting each copy as independent weather evidence would overstate the information available.
""")
code("""
all_onsets = pd.to_datetime(events.start_time, format="mixed", utc=True).dt.tz_convert(MANILA)
all_restorations = pd.to_datetime(events.end_time, format="mixed", utc=True).dt.tz_convert(MANILA)
overlap_mask = events.municipality_psgc.eq(event.municipality_psgc) & (all_onsets < restoration) & (all_restorations > onset)
other_overlapping_events = events.loc[overlap_mask & events.outage_id.ne(selected_outage_id)]
print("Other overlapping synthetic events in the same municipality:", len(other_overlapping_events))
display(other_overlapping_events[["outage_id", "barangay", "start_time", "end_time"]].head(5))
print("No causal comparison or independent-sample test is performed.")
""")
insertion = next(i for i, c in enumerate(notebook.cells) if c.cell_type == "markdown" and c.source.startswith("## 8. Retrospective"))
notebook.cells[insertion:insertion] = cells
for cell in notebook.cells:
    if cell.cell_type == "code":
        compile(cell.source, cell.id, "exec")
nbf.validate(notebook)
nbf.write(notebook, PATH)
print(f"Extended lesson 02 with {len(cells)} concise cells.")
