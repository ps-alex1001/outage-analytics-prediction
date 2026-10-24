"""Plotly figures for the Overview, rendered entirely inside Streamlit."""

from html import escape
import math

import geopandas as gpd
import pandas as pd
import plotly.graph_objects as go
from shapely.ops import polylabel
from utils.theme import MAP_BASE_STYLE, get_color, get_sequential_scale, style_plotly_figure
PLOT_CONFIG = {"displayModeBar": False, "scrollZoom": False, "responsive": True}
MAP_CONFIG = {
    "displayModeBar": True, "displaylogo": False, "scrollZoom": True,
    "responsive": True,
    "modeBarButtons": [["zoomInMap", "zoomOutMap", "resetViewMap"]],
}


def municipality_geography(barangays: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Derive municipality outlines from the supplied barangays, in memory."""
    return barangays[["municipality_psgc", "municipality", "geometry"]].dissolve(
        by="municipality_psgc", as_index=False, aggfunc="first"
    )


def municipality_label_points(municipalities: gpd.GeoDataFrame) -> gpd.GeoSeries:
    """Place text in the widest interior of each municipality's main polygon."""
    projected = municipalities.to_crs(32651)  # Bataan's UTM zone, distances in metres.
    points = []
    for geometry in projected.geometry:
        polygon = max(geometry.geoms, key=lambda part: part.area) if geometry.geom_type == "MultiPolygon" else geometry
        points.append(polylabel(polygon, tolerance=100))
    return gpd.GeoSeries(points, crs=projected.crs).to_crs(4326)


def barangay_choropleth(aggregated: gpd.GeoDataFrame, geojson: dict, *, metric="outage_count",
                       metric_label="Outage count", focus=False, interactive=False) -> go.Figure:
    west, south, east, north = aggregated.total_bounds
    hover = [[escape(row.barangay), escape(row.municipality), int(row.outage_count), float(row.total_outage_hours),
              "No events" if pd.isna(getattr(row, metric)) else
              (f"{getattr(row, metric):,.0f}" if metric == "outage_count" else f"{getattr(row, metric):,.2f} h"),
              row.barangay_psgc]
             for row in aggregated.itertuples(index=False)]
    hovertemplate = ("<b>%{customdata[0]}</b>, %{customdata[1]}"
                     f"<br>{escape(metric_label)}: %{{customdata[4]}}"
                     "<br>Outage count: %{customdata[2]:,}"
                     "<br>Total outage hours: %{customdata[3]:,.1f}<extra></extra>")
    values = [None if pd.isna(value) else float(value) for value in aggregated[metric]]
    maximum = max([value for value in values if value is not None] or [0])
    figure = go.Figure(go.Choroplethmap(
        geojson=geojson, featureidkey="properties.barangay_psgc",
        locations=aggregated.barangay_psgc.tolist(), z=values,
        zmin=0, zmax=maximum or 1,
        # Sequential light-to-dark blue encodes magnitude through luminance,
        # without requiring red/green discrimination.
        colorscale=get_sequential_scale(),
        marker={"line": {"color": get_color("surface"), "width": 0.65}, "opacity": 0.96},
        customdata=hover,
        hovertemplate=hovertemplate if metric != "outage_count" else ("<b>%{customdata[0]}</b>, %{customdata[1]}"
                       "<br>Outage count: %{customdata[2]:,}"
                       "<br>Total outage hours: %{customdata[3]:,.1f}<extra></extra>"),
        colorbar={"title": {"text": metric_label + (" (h)" if metric != "outage_count" else ""), "side": "top"},
                  "orientation": "h", "x": 0.5, "xanchor": "center", "y": -0.02,
                  "len": 0.85, "thickness": 10, "outlinewidth": 0,
                  "tickformat": ",d" if metric == "outage_count" else ",.1f"},
        unselected={"marker": {"opacity": 1}},
    ))
    missing = aggregated[metric].isna().tolist()
    if any(missing):
        figure.add_trace(go.Choroplethmap(
            geojson=geojson, featureidkey="properties.barangay_psgc",
            locations=aggregated.loc[aggregated[metric].isna(), "barangay_psgc"].tolist(),
            z=[0] * sum(missing), zmin=0, zmax=1,
            colorscale=[[0, get_color("missing")], [1, get_color("missing")]], showscale=False,
            customdata=[item for item, absent in zip(hover, missing) if absent], hovertemplate=hovertemplate,
            marker={"line": {"color": get_color("surface"), "width": 0.65}},
            unselected={"marker": {"opacity": 1}}, name="No events",
        ))
    municipalities = municipality_geography(aggregated)
    longitude, latitude = [], []
    for geometry in municipalities.geometry:
        polygons = list(geometry.geoms) if geometry.geom_type == "MultiPolygon" else [geometry]
        for polygon in polygons:
            for ring in [polygon.exterior, *polygon.interiors]:
                x, y = ring.xy
                longitude.extend([*x, None])
                latitude.extend([*y, None])
    # A white casing keeps the dark municipality boundaries legible over both
    # pale and dark count fills. Thin white lines still identify barangays.
    for color, width in [(get_color("surface"), 3.8), (get_color("municipality_boundary"), 1.8)]:
        figure.add_trace(go.Scattermap(
            lon=longitude, lat=latitude, mode="lines", line={"color": color, "width": width},
            hoverinfo="skip", showlegend=False, name="Municipality boundaries",
        ))
    anchors = municipality_label_points(municipalities)
    figure.add_trace(go.Scattermap(
        lon=anchors.x.tolist(), lat=anchors.y.tolist(), mode="text",
        text=municipalities.municipality.str.replace("City of Balanga", "Balanga City", regex=False).tolist(),
        textfont={"size": 11, "color": get_color("text")}, textposition="middle center",
        hoverinfo="skip", showlegend=False, name="Municipality names",
    ))
    figure.update_layout(
        # The shared light basemap supplies geographic context beneath polygons.
        # Explicit camera also works with Streamlit's bundled Plotly.js versions
        # that do not yet implement layout.map.fitbounds.
        map={"style": MAP_BASE_STYLE, "center": {"lon": (west + east) / 2,
                                               "lat": (south + north) / 2}, "zoom": 9},
        height=555, margin={"l": 0, "r": 0, "t": 8, "b": 30},
        uirevision="barangays:" + "|".join(aggregated.municipality_psgc.unique()),
    )
    if focus:
        # Conservative Web Mercator fit for a narrow desktop map column.
        def mercator(latitude):
            return math.log(math.tan(math.pi / 4 + math.radians(latitude) / 2))
        horizontal = max((east - west) / 360, 1e-8)
        vertical = max((mercator(north) - mercator(south)) / (2 * math.pi), 1e-8)
        zoom = min(math.log2(380 / (512 * horizontal)), math.log2(410 / (512 * vertical))) - 0.2
        figure.update_layout(map_zoom=max(0, min(14, zoom)))
    if interactive:
        figure.update_layout(clickmode="event+select", uirevision="|".join(aggregated.municipality_psgc.unique()))
    return style_plotly_figure(figure)


def monthly_bars(monthly: pd.DataFrame, metric: str, year: int) -> go.Figure:
    hours = metric == "total_outage_hours"
    label = "Outage hours" if hours else "Outage events"
    figure = go.Figure(go.Bar(
        x=monthly.month_label.tolist(), y=monthly[metric].tolist(),
        marker_color=get_color("selected" if hours else "primary"), marker_line_width=0,
        marker_pattern_shape="/" if hours else "",
        hovertemplate=f"<b>%{{x}} {year}</b><br>{label}: %{{y:,.1f}}<extra></extra>" if hours
        else f"<b>%{{x}} {year}</b><br>{label}: %{{y:,d}}<extra></extra>",
    ))
    figure.update_layout(
        height=230, margin={"l": 55, "r": 8, "t": 8, "b": 35},
        bargap=0.32,
        xaxis={"type": "category", "categoryorder": "array", "categoryarray": monthly.month_label.tolist(),
               "tickangle": 0, "fixedrange": True, "showgrid": False, "automargin": True},
        yaxis={"rangemode": "tozero", "fixedrange": True,
               "zeroline": False, "title": {"text": "Hours" if hours else "Events", "standoff": 10},
               "tickformat": ",.0f", "automargin": True},
        showlegend=False,
    )
    return style_plotly_figure(figure)
