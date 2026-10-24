"""Read-only, portable explorer for versioned building-informed infrastructure.

Consumes only a completed generated bundle. The original analytical dashboard
loaders and outage datasets are intentionally independent of this module.
"""
from __future__ import annotations

from collections import defaultdict, deque
from html import escape
import json
import math
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from utils.theme import get_color, style_plotly_figure

ROOT = Path(__file__).resolve().parents[1]
MAP_KEY = "building_topology_canvas"
LAYERS = ["Substations", "Feeder networks", "Distribution lines", "Electrical poles", "Transformers",
          "Building footprints", "Building classifications", "Barangay boundaries", "Municipal boundaries", "Observed infrastructure"]
CLASS_COLORS = {"unknown": "#AAB4BF", "residential": "#0072B2", "commercial": "#D55E00", "industrial": "#6F4DA0",
                "institutional": "#CC79A7", "mixed": "#564898", "ancillary": "#666666"}


def completed_runs(root=None):
    directory = Path(root or ROOT / "data/synthetic")
    return sorted([p for p in directory.glob("infrastructure_*")
                   if (p / "summary.json").is_file() and (p / "manifest.json").is_file()
                   and not (p / "RUN_INCOMPLETE").exists()], reverse=True)


def signature(directory):
    directory = Path(directory)
    return tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size)
                 for p in sorted([directory / "summary.json", directory / "validation.json", *directory.glob("view/*.parquet"),
                                  *directory.glob("tables/reported_*.csv")]))


@st.cache_resource(max_entries=2, show_spinner="Loading versioned infrastructure…")
def load_bundle(directory, stamp):
    """Immutable shared snapshot; callers must never edit its frames or tables."""
    path = Path(directory)
    if (path / "RUN_INCOMPLETE").exists():
        raise ValueError("This infrastructure run has not completed validation")
    layers = {p.stem: gpd.read_parquet(p) for p in (path / "view").glob("*.parquet")}
    tables = {p.stem: pd.read_csv(p, dtype={"barangay_psgc": str, "municipality_psgc": str,
              "matched_barangay_psgc": str, "municipality_context_psgc": str, "station_code_reported": str})
              for p in (path / "tables").glob("*.csv")}
    return layers, tables, json.loads((path / "summary.json").read_text(encoding="utf-8")), json.loads((path / "validation.json").read_text())


def geometry_paths(geometry):
    if geometry is None or geometry.is_empty:
        return
    if geometry.geom_type == "LineString":
        yield geometry
    elif geometry.geom_type == "Polygon":
        yield geometry.exterior
        yield from geometry.interiors
    elif hasattr(geometry, "geoms"):
        for part in geometry.geoms:
            yield from geometry_paths(part)


def limited(frame, maximum):
    return frame if len(frame) <= maximum else frame.iloc[np.linspace(0, len(frame)-1, maximum, dtype=int)]


def add_lines(figure, frame, name, color, width, kind=None, identity="edge_id", dash=False):
    x, y, custom = [], [], []
    for row in frame.to_dict("records"):
        for part in geometry_paths(row["geometry"]):
            px, py = part.xy
            x.extend([*px, None]); y.extend([*py, None])
            custom.extend([[kind, row.get(identity, ""), name]] * (len(px) + 1))
    if x:
        figure.add_trace(go.Scattermap(lon=x, lat=y, mode="lines", name=name, line={"color": color, "width": width},
            customdata=custom if kind else None,
            hovertemplate="%{customdata[2]}<br>%{customdata[1]}<extra></extra>" if kind else None,
            hoverinfo=None if kind else "skip", meta={"line_dash": [4, 3]} if dash else None))


def add_points(figure, frame, name, color, size, kind="node", identity="node_id"):
    if frame.empty:
        return
    points = frame.geometry.representative_point()
    figure.add_trace(go.Scattermap(lon=points.x.tolist(), lat=points.y.tolist(), mode="markers", name=name,
        marker={"size": size, "color": color},
        customdata=[[kind, str(row[identity]), name] for row in frame.to_dict("records")],
        hovertemplate="%{customdata[2]}<br>%{customdata[1]}<extra></extra>",
        meta={"zoom_scaled_marker": True} if name == "Modeled poles" else None))
    if name == "Observed substation geometry":
        figure.add_trace(go.Scattermap(lon=points.x.tolist(), lat=points.y.tolist(), mode="markers", name=name,
            marker={"size": size-6, "color": "white"}, showlegend=False,
            customdata=[[kind, str(row[identity]), name] for row in frame.to_dict("records")],
            hovertemplate="%{customdata[2]}<br>%{customdata[1]}<extra></extra>"))


def scope_layers(layers, variant="updated", municipality=None, station=None, feeder=None, tables=None):
    nodes, edges = layers[f"{variant}_nodes"], layers[f"{variant}_edges"]
    boundaries = layers["barangays"]
    if municipality:
        boundaries = boundaries.loc[boundaries.municipality_psgc.eq(municipality)]
        nodes = nodes.loc[nodes.municipality_psgc.eq(municipality)]
        edges = edges.loc[edges.municipality_psgc.eq(municipality)]
    if variant == "updated":
        if feeder:
            nodes = nodes.loc[nodes.reported_feeder_id.eq(feeder)]
            edges = edges.loc[edges.reported_feeder_id.eq(feeder)]
        elif station:
            nodes = nodes.loc[nodes.reported_substation_id.eq(station)]
            edges = edges.loc[edges.reported_substation_id.eq(station)]
    elif (station or feeder) and tables is not None:
        records = tables["reported_locations"]
        records = records.loc[records.reported_feeder_id.eq(feeder)] if feeder else records.loc[records.reported_substation_id.eq(station)]
        codes = set(records.matched_barangay_psgc.dropna())
        nodes = nodes.loc[nodes.barangay_psgc.isin(codes)]
        edges = edges.loc[edges.barangay_psgc.isin(codes)]
    return nodes, edges, boundaries


def trace_connections(nodes, edges, kind, identity, descendant_limit=2000):
    """Only active tree edges imply direction; open candidates stay undirected."""
    n = nodes.set_index("node_id")
    active = edges.loc[edges.is_tree_edge]
    parents = dict(zip(n.index, n.parent_node_id))
    children = defaultdict(list)
    edge_lookup = {}
    for row in active[["from_node_id", "to_node_id", "edge_id"]].itertuples(index=False):
        children[row.from_node_id].append(row.to_node_id)
        edge_lookup[(row.from_node_id, row.to_node_id)] = row.edge_id
    edge_ids, selected_nodes, upstream = set(), set(), []
    if kind == "edge":
        selected = edges.loc[edges.edge_id.eq(identity)]
        if selected.empty:
            return {"node_ids": set(), "edge_ids": set(), "upstream": [], "downstream": [], "truncated": False}
        row = selected.iloc[0]
        selected_nodes.update([row.from_node_id, row.to_node_id]); edge_ids.add(identity)
        if not bool(row.is_tree_edge):
            return {"node_ids": selected_nodes, "edge_ids": edge_ids, "upstream": [], "downstream": [],
                    "truncated": False, "direction_status": "No assigned direction: non-tree candidate"}
        start = row.to_node_id
    elif identity in n.index:
        start = identity
        selected_nodes.add(start)
    else:
        return {"node_ids": set(), "edge_ids": set(), "upstream": [], "downstream": [], "truncated": False}
    seen, current = set(), start
    while current in parents and pd.notna(parents[current]) and current not in seen:
        seen.add(current)
        parent = parents[current]
        upstream.append(parent); selected_nodes.add(parent)
        if (parent, current) in edge_lookup:
            edge_ids.add(edge_lookup[(parent, current)])
        current = parent
    queue, downstream = deque(children[start]), []
    while queue and len(downstream) < descendant_limit:
        child = queue.popleft()
        if child in downstream:
            continue
        downstream.append(child); selected_nodes.add(child)
        parent = parents.get(child)
        if (parent, child) in edge_lookup:
            edge_ids.add(edge_lookup[(parent, child)])
        queue.extend(children[child])
    return {"node_ids": selected_nodes, "edge_ids": edge_ids, "upstream": upstream,
            "downstream": downstream, "truncated": bool(queue), "direction_status": "Synthetic active tree only"}


def map_figure(layers, nodes, edges, boundaries, visibility, municipality, selection=None, connections=None, station=None):
    figure, notes = go.Figure(), []
    observed = layers["observed_infrastructure"]
    if municipality:
        # Substation polygons can lack source admin IDs; geometry is authoritative for this view.
        observed = observed.loc[observed.geometry.intersects(boundaries.geometry.union_all())]
    if "Barangay boundaries" in visibility:
        add_lines(figure, boundaries, "Barangay boundaries", get_color("boundary"), .6)
    if "Municipal boundaries" in visibility:
        municipalities = layers["municipalities"]
        if municipality:
            municipalities = municipalities.loc[municipalities.municipality_psgc.eq(municipality)]
        add_lines(figure, municipalities, "Municipal boundaries", get_color("municipality_boundary"), 2)
    if "Distribution lines" in visibility:
        sections = edges.loc[edges.geometry.geom_type.eq("LineString")]
        display = limited(sections, 6000)
        add_lines(figure, display, "Modeled road-aligned lines", get_color("modeled"), 1.1, "edge")
        if len(display) < len(sections):
            notes.append(f"Displaying {len(display):,} of {len(sections):,} line sections; focus a municipality/feeder for detail.")
    if "Feeder networks" in visibility and "reported_feeder_id" in edges:
        inferred = edges.loc[edges.reported_feeder_id.notna() & edges.geometry.geom_type.eq("LineString")]
        add_lines(figure, limited(inferred, 5000), "Inferred feeder association (not verified route)", get_color("source"), 2, "edge", dash=True)
    if "Electrical poles" in visibility:
        display = limited(nodes.loc[nodes.node_type.eq("pole")], 2500 if municipality else 700)
        add_points(figure, display, "Modeled poles", get_color("pole"), 5)
        notes.append(f"Poles rendered: {len(display):,}; choose a municipality/feeder to inspect a smaller network.")
    if "Transformers" in visibility:
        display = limited(nodes.loc[nodes.node_type.eq("transformer")], 2500)
        add_points(figure, display, "Modeled transformers", get_color("transformer"), 7)
        if len(display) < int(nodes.node_type.eq("transformer").sum()):
            notes.append("Transformer display is capped at 2,500; the full inventory remains available for focused inspection.")
    if "Substations" in visibility:
        add_points(figure, observed.loc[observed.asset_type.eq("substation")], "Observed substation geometry", get_color("observed"), 15,
                   kind="observed", identity="asset_id")
        if station:
            selected_station = layers["reported_substations_mapped"].loc[
                layers["reported_substations_mapped"].reported_substation_id.eq(station)]
            add_points(figure, selected_station, "Selected reported station · observed geometry", get_color("source"), 21,
                       kind="observed", identity="observed_asset_id")
    if "Observed infrastructure" in visibility:
        add_lines(figure, observed.loc[observed.geometry.geom_type.isin(["LineString", "Polygon", "MultiPolygon"])],
                  "Observed mapped infrastructure", get_color("observed"), 2.3, "observed", "asset_id")
        add_points(figure, limited(observed.loc[observed.geometry.geom_type.eq("Point")], 1500),
                   "Observed mapped point assets", get_color("observed"), 9, "observed", "asset_id")
    if any(name in visibility for name in ["Building footprints", "Building classifications"]):
        if not municipality:
            notes.append("Choose a municipality to render buildings. This avoids sending all 169k footprints to the browser.")
        else:
            buildings = layers["buildings_classified"].loc[layers["buildings_classified"].municipality_psgc.eq(municipality)]
            if selection:
                selected_frame = nodes.loc[nodes.node_id.eq(selection[1])] if selection[0] == "node" else edges.loc[edges.edge_id.eq(selection[1])]
                if len(selected_frame):
                    center = selected_frame.geometry.iloc[0].representative_point()
                    buildings = buildings.loc[buildings.geometry.intersects(center.buffer(.009))]
            display = limited(buildings, 1500)
            if "Building footprints" in visibility:
                add_lines(figure, display, "Observed OSM footprints", "#75818B", .7, "building", "building_id")
            if "Building classifications" in visibility:
                for label, group in display.groupby("classification"):
                    figure.add_trace(go.Choroplethmap(geojson=json.loads(group[["building_id", "geometry"]].to_json()),
                        locations=group.building_id.tolist(), featureidkey="properties.building_id", z=[0] * len(group),
                        zmin=0, zmax=1, colorscale=[[0, CLASS_COLORS[label]], [1, CLASS_COLORS[label]]], showscale=False, showlegend=True,
                        marker={"opacity": .75, "line": {"width": .5, "color": CLASS_COLORS[label]}}, name=f"Building use · {label}",
                        customdata=[["building", code, label] for code in group.building_id],
                        hovertemplate="%{customdata[1]}<br>Inferred use: %{customdata[2]}<extra></extra>"))
            notes.append(f"Building footprints rendered: {len(display):,} of {len(buildings):,} in the focused context. Unknown uses stay unknown.")
    if connections:
        highlight = layers.get("_active_edges", edges)
        highlight = highlight.loc[highlight.edge_id.isin(connections["edge_ids"]) & highlight.geometry.geom_type.eq("LineString")]
        add_lines(figure, limited(highlight, 4000), "Selected upstream / downstream tree", get_color("affected"), 4, "edge")
    if selection:
        kind, code = selection
        frame = {"node": nodes, "edge": edges, "observed": observed, "building": layers["buildings_classified"]}.get(kind)
        key = {"node": "node_id", "edge": "edge_id", "observed": "asset_id", "building": "building_id"}.get(kind)
        selected = frame.loc[frame[key].eq(code)] if frame is not None else nodes.iloc[:0]
        if len(selected):
            if kind == "edge":
                add_lines(figure, selected, "Selected section", get_color("primary"), 6, "edge")
            else:
                add_points(figure, selected, "Selected asset", get_color("primary"), 16, kind, key)
    bounds = boundaries.total_bounds
    if len(nodes) and len(nodes) < 2000:
        bounds = nodes.total_bounds
    west, south, east, north = bounds
    span = max(east - west, (north - south) * 1.4, .008)
    figure.update_layout(map={"style": "white-bg", "center": {"lon": (west+east)/2, "lat": (south+north)/2},
                              "zoom": max(7, min(15, math.log2(360 * 450 / (512 * span)) - .4))},
                          height=660, margin={"l": 0, "r": 0, "t": 0, "b": 75},
                          legend={"orientation": "h", "x": 0, "y": -.02, "font": {"size": 10}})
    style_plotly_figure(figure)
    return figure, notes, bounds


def topology_figure(nodes, edges, selection, connections):
    """Compact clickable parent/child view; table/map expose longer paths."""
    figure = go.Figure()
    if not connections or not connections["node_ids"]:
        return figure
    chosen = set(connections["upstream"][:8] + connections["downstream"][:30])
    if selection[0] == "node":
        chosen.add(selection[1])
    else:
        row = edges.loc[edges.edge_id.eq(selection[1])].iloc[0]
        chosen.update([row.from_node_id, row.to_node_id])
    selected = nodes.loc[nodes.node_id.isin(chosen)].copy()
    if "network_depth" not in selected:
        # Compact baseline bundles retain parent IDs, from which exact logical
        # depth can be recovered without inventing an electrical attribute.
        parents = dict(zip(nodes.node_id, nodes.parent_node_id))
        depths = {}
        for code in selected.node_id:
            chain, current, seen = [], code, set()
            while current in parents and current not in depths and current not in seen:
                seen.add(current); chain.append(current)
                current = parents[current]
            depth = depths.get(current, -1)
            for child in reversed(chain):
                depth += 1
                depths[child] = depth
        selected["network_depth"] = selected.node_id.map(depths)
    # Depth is real stored logical depth; horizontal packing is display-only.
    coordinates = {}
    for depth, group in selected.groupby("network_depth"):
        for position, code in enumerate(sorted(group.node_id)):
            coordinates[code] = (position - (len(group)-1)/2, -float(depth))
    for row in edges.loc[edges.is_tree_edge & edges.from_node_id.isin(chosen) & edges.to_node_id.isin(chosen)].itertuples():
        a, b = coordinates.get(row.from_node_id), coordinates.get(row.to_node_id)
        if a and b:
            figure.add_trace(go.Scatter(x=[a[0], b[0]], y=[a[1], b[1]], mode="lines", line={"color": get_color("muted"), "width": 1},
                                       hoverinfo="skip", showlegend=False))
    for kind, group in selected.groupby("node_type"):
        keep = group.loc[group.node_id.isin(coordinates)]
        figure.add_trace(go.Scatter(x=[coordinates[code][0] for code in keep.node_id], y=[coordinates[code][1] for code in keep.node_id],
            mode="markers", name=kind, marker={"size": 11}, customdata=keep.node_id.tolist(),
            hovertemplate="%{customdata}<extra></extra>"))
    figure.update_layout(height=320, margin={"l": 10, "r": 10, "t": 10, "b": 10}, xaxis={"visible": False},
                          yaxis={"visible": False}, clickmode="event+select")
    style_plotly_figure(figure)
    return figure


def render_explorer(directory=None):
    from utils import persistent_infrastructure_map as canvas
    st.title("Infrastructure Topology Explorer")
    st.caption("Inspect building-informed synthetic infrastructure, reported feeders, and preserved original topology.")
    runs = completed_runs() if directory is None else [Path(directory)]
    if not runs:
        st.info("No completed building-informed infrastructure version is available yet. Generate and validate a new version first.")
        return
    directory = runs[0]
    try:
        layers, tables, summary, validation = load_bundle(str(directory), signature(directory))
    except (OSError, ValueError, KeyError) as error:
        st.error(f"Infrastructure bundle could not be loaded: {error}")
        return
    layers = dict(layers)  # Per-render display references; cached frames stay read-only.
    st.caption(f"Version: {directory.name} · Validation: {'Passed' if validation['passed'] else 'Failed'}")
    st.info("Modeled network for inspection. Reported feeder locations and inferred associations are not verified "
            "PENELCO routes or service boundaries. Substation locations are shown only where observed geometry exists.")
    st.caption(f"{summary['building_count']:,} building footprints · {summary['transformers_moved']:,} transformers relocated · "
               f"{summary['building_proximity_gain']:,} additional footprints within 150 m · {summary['reported_feeders']} reported feeders")
    stations, feeders = tables["reported_substations"], tables["reported_feeders"]
    station_names = dict(zip(stations.reported_substation_id, stations.reported_name))
    municipalities = layers["municipalities"].sort_values("municipality")
    muni_names = dict(zip(municipalities.municipality_psgc, municipalities.municipality))
    columns = st.columns([1, 1, 1.6, 1])
    with columns[0]:
        variant = st.selectbox("Infrastructure version", ["updated", "baseline"], key="bie_version",
                              format_func=lambda value: "Building-informed update" if value == "updated" else "Original baseline")
    with columns[1]:
        municipality = st.selectbox("Municipality", [None, *muni_names], key="bie_municipality",
                                    format_func=lambda value: muni_names.get(value, "All municipalities"))
    with columns[2]:
        station = st.selectbox("Reported substation", [None, *station_names], key="bie_station",
                               format_func=lambda value: station_names.get(value, "All reported substations"))
    choices = feeders.loc[feeders.reported_substation_id.eq(station)] if station else feeders
    feeder_names = {row.reported_feeder_id: f"Feeder {row.feeder_number}" + (" · No load" if row.load_status_reported == "no_load" else "")
                    + (f" · {station_names[row.reported_substation_id]}" if not station else "") for row in choices.itertuples()}
    if st.session_state.get("bie_feeder") not in feeder_names:
        st.session_state.bie_feeder = None
    with columns[3]:
        feeder = st.selectbox("Reported feeder", [None, *feeder_names], key="bie_feeder",
                              format_func=lambda value: feeder_names.get(value, "All associated feeders"))
    if station:
        station_row = stations.loc[stations.reported_substation_id.eq(station)].iloc[0]
        capacity = f"{station_row.capacity_mva_reported:g} MVA reported" if pd.notna(station_row.capacity_mva_reported) else "Capacity not supplied"
        st.caption(f"{capacity} · Observed location match: {station_row.observed_match_status}. "
                   "An unmatched station is retained as a record without an invented map location.")
    if feeder and choices.set_index("reported_feeder_id").loc[feeder].load_status_reported == "no_load":
        st.warning("This feeder is reported as No load. No inferred asset assignment is generated for it.")
    with st.expander("Reported serviced locations and geographic matching", expanded=bool(feeder)):
        locations = tables["reported_locations"]
        if feeder:
            locations = locations.loc[locations.reported_feeder_id.eq(feeder)]
        elif station:
            locations = locations.loc[locations.reported_substation_id.eq(station)]
        st.dataframe(locations[["raw_location", "parent_location", "match_status", "matched_barangay_psgc",
                                "partial_coverage", "shared_barangay_multiple_feeders", "source_line"]],
                     hide_index=True, width="stretch", height=230)
        st.caption("Exact normalized name matches are contextualized by municipality where supported. Partial and shared descriptions "
                   "are preserved. A matched barangay is location evidence, not a feeder service boundary.")
    nodes, edges, boundaries = scope_layers(layers, variant, municipality, station, feeder, tables)
    all_nodes, all_edges = layers[f"{variant}_nodes"], layers[f"{variant}_edges"]
    layers["_active_edges"] = all_edges
    visibility = st.multiselect("Visible map layers", LAYERS,
        default=["Substations", "Feeder networks", "Distribution lines", "Transformers", "Municipal boundaries"], key="bie_layers")
    st.caption(f"Filtered model: {len(nodes):,} nodes · {len(edges):,} sections/attachments. "
               "Reported filters in baseline mode select matched geography for comparison; the baseline has no reported assignments.")
    if nodes.empty:
        st.info("No uniquely supported modeled assets match this scope. Reported records remain available above.")
    selection_controls = st.expander("Find or select a component", expanded=False)
    with selection_controls:
        picker, search = st.columns([1, 2])
    with picker:
        kind_label = st.selectbox("Component type", ["Transformer", "Pole", "Line section", "Observed infrastructure", "Building"], key="bie_kind")
    with search:
        query = st.text_input("Search component ID or name", key="bie_search", placeholder="Optional ID or name fragment")
    kind, field, candidates = {
        "Transformer": ("node", "node_id", nodes.loc[nodes.node_type.eq("transformer")]),
        "Pole": ("node", "node_id", nodes.loc[nodes.node_type.eq("pole")]),
        "Line section": ("edge", "edge_id", edges.loc[edges.geometry.geom_type.eq("LineString")]),
        "Observed infrastructure": ("observed", "asset_id", layers["observed_infrastructure"]),
        "Building": ("building", "building_id", layers["buildings_classified"]),
    }[kind_label]
    if kind in {"observed", "building"} and municipality:
        candidates = candidates.loc[candidates.geometry.intersects(boundaries.geometry.union_all())]
    if query:
        mask = candidates[field].str.contains(query, case=False, regex=False, na=False)
        if "name" in candidates:
            mask |= candidates.name.str.contains(query, case=False, regex=False, na=False)
        candidates = candidates.loc[mask]
    options = candidates[field].head(1000).tolist()
    selection = st.session_state.get("bie_selection")
    if selection and selection[0] == kind and selection[1] in set(candidates[field]) and selection[1] not in options:
        options.insert(0, selection[1])
    current_picker = selection[1] if selection and selection[0] == kind and selection[1] in options else None
    st.session_state.bie_asset_picker = current_picker

    def dropdown_pick():
        value = st.session_state.bie_asset_picker
        st.session_state.bie_selection = (kind, value) if value else None

    with selection_controls:
        st.selectbox("Select component", [None, *options], key="bie_asset_picker", on_change=dropdown_pick,
                      format_func=lambda value: value or "Choose a component or click the map")
        if len(candidates) > 1000:
            st.caption(f"First 1,000 of {len(candidates):,} selectable components shown; use the search or geographic filters to find any ID.")
    selection = st.session_state.get("bie_selection")
    valid_frame = {"node": all_nodes, "edge": all_edges, "observed": layers["observed_infrastructure"], "building": layers["buildings_classified"]}
    fields = {"node": "node_id", "edge": "edge_id", "observed": "asset_id", "building": "building_id"}
    if selection and selection[1] not in set(valid_frame[selection[0]][fields[selection[0]]]):
        selection = None
        st.session_state.bie_selection = None
    if selection and ((selection[0] == "node" and selection[1] not in set(nodes.node_id))
                      or (selection[0] == "edge" and selection[1] not in set(edges.edge_id))):
        selection = None
        st.session_state.bie_selection = None
    connections = trace_connections(all_nodes, all_edges, *selection) if selection and selection[0] in {"node", "edge"} else None
    map_col, details_col = st.columns([2.2, 1], gap="medium")
    with map_col:
        figure, notes, bounds = map_figure(layers, nodes, edges, boundaries, visibility, municipality, selection, connections, station)
        labels = []
        for row in municipalities.itertuples():
            point = row.geometry.representative_point()
            labels.append({"id": row.municipality_psgc, "name": row.municipality, "lon": point.x, "lat": point.y,
                           "kind": "municipality", "minZoom": 8})
        result = canvas.render(figure, f"{variant}:{municipality}:{station}:{feeder}", lambda: None,
            key=MAP_KEY, height=660, labels=labels, label_min_zoom=8, fit_bounds=bounds, fit_button_label="Fit filtered network")
        picked = getattr(result, "picked", None)
        if picked and isinstance(picked, dict):
            custom = picked.get("customdata", [])
            if len(custom) >= 2 and custom[0] in fields:
                candidate = (str(custom[0]), str(custom[1]))
                if candidate != selection and candidate[1] in set(valid_frame[candidate[0]][fields[candidate[0]]]):
                    st.session_state.bie_selection = candidate
                    if picked.get("view"):
                        st.session_state[MAP_KEY + "_view"] = picked["view"]
                    st.rerun()
        st.caption("Observed assets use the observed color; modeled assets use model colors. Dashed feeder overlays are inferred local "
                   "associations. Thick selected paths show the synthetic active tree; they do not verify station connectivity.")
        for note in notes:
            st.caption(note)
        st.caption("Footprints, roads and mapped observations: © OpenStreetMap contributors. "
                   "Building uses and feeder associations are inferred where stated.")
    with details_col.container(border=True):
        st.subheader("Component inspection", anchor=False)
        if selection:
            selected = valid_frame[selection[0]].loc[valid_frame[selection[0]][fields[selection[0]]].eq(selection[1])].iloc[0]
            st.code(selection[1], language=None, wrap_lines=True)
            show = ["asset_type", "node_type", "edge_type", "data_status", "name", "municipality", "barangay", "barangay_psgc",
                    "feeder_id", "reported_feeder_id", "reported_substation_id", "assignment_status", "reported_relationship_basis",
                    "classification", "classification_basis", "evidence_tags_json", "poi_ids_json", "siting_change",
                    "buildings_within_150m", "nearest_building_m", "parent_node_id", "source_node_id", "is_tree_edge", "edge_state", "length_m"]
            attributes = {key: str(selected[key]) for key in show if key in selected and pd.notna(selected[key])}
            if pd.notna(selected.get("reported_substation_id")):
                attributes["Reported substation name (local inference)"] = station_names.get(selected.reported_substation_id, selected.reported_substation_id)
            if pd.notna(selected.get("reported_feeder_id")):
                f = feeders.loc[feeders.reported_feeder_id.eq(selected.reported_feeder_id)]
                if len(f):
                    attributes["Reported feeder number (local inference)"] = str(f.feeder_number.iloc[0])
            if selection[0] == "observed":
                attributes["Data status"] = "Observed mapped infrastructure; electrical connections unverified"
            elif variant == "baseline" and selection[0] in {"node", "edge"}:
                attributes["Data status"] = "Original synthetic baseline"
            st.dataframe(pd.DataFrame(attributes.items(), columns=["Attribute", "Available value"]), hide_index=True, width="stretch", height=400)
            source_id = selected.get("source_node_id")
            if pd.notna(source_id):
                roots = all_nodes.loc[all_nodes.node_id.eq(source_id)]
                st.caption(f"Upstream logical source: {source_id}")
                if len(roots) and pd.notna(roots.iloc[0].get("anchor_asset_id")):
                    source = roots.iloc[0]
                    anchors = layers["observed_infrastructure"].loc[layers["observed_infrastructure"].asset_id.eq(source.anchor_asset_id)]
                    label = anchors.iloc[0].get("name") if len(anchors) else None
                    st.caption(f"Observed station reference: {label if pd.notna(label) else source.anchor_asset_id}.")
                    st.caption("This is the retained baseline geographic anchor, not a verified electrical connection to that station.")
            if connections:
                st.caption(connections.get("direction_status", ""))
                st.markdown(f"**{len(connections['upstream']):,} upstream nodes** · **{len(connections['downstream']):,} downstream nodes shown**")
                if connections["truncated"]:
                    st.caption("Downstream inspection is capped at 2,000 nodes. The full topology remains in the exported graph.")
                path_nodes = all_nodes.loc[all_nodes.node_id.isin(connections["node_ids"])]
                st.dataframe(path_nodes[["node_id", "node_type", "feeder_id", "parent_node_id"]], hide_index=True, height=200)
            if selection[0] == "building":
                proximity = tables["building_transformer_proximity"]
                st.dataframe(proximity.loc[proximity.building_id.eq(selection[1])], hide_index=True)
                st.caption("Nearest-transformer proximity is an inferred geographic relationship, not a verified service connection.")
        else:
            st.write("Click a component or use Select component to inspect its recorded attributes and connected tree.")
    if connections:
        st.subheader("Connected topology", anchor=False)
        st.caption("Compact view: up to eight upstream ancestors and thirty descendants. Hover for IDs; select a node to inspect it on the map. "
                   "Vertical position uses stored logical depth, not voltage or physical elevation.")
        event = st.plotly_chart(topology_figure(all_nodes, all_edges, selection, connections), key="bie_topology", theme=None,
                                on_select="rerun", selection_mode="points", config={"displayModeBar": False})
        points = event.selection.points
        if points and points[-1].get("customdata"):
            code = str(points[-1]["customdata"])
            if code in set(all_nodes.node_id) and ("node", code) != selection:
                st.session_state.bie_selection = ("node", code)
                st.rerun()
    with st.expander("Original versus updated · validation and portability"):
        comparison = pd.DataFrame([
            ["Transformers", summary["transformers_before"], summary["transformers_after"]],
            ["Unsupported transformer sites (no building within 150 m)", summary["unsupported_transformers_before"], summary["unsupported_transformers_after"]],
            ["Building footprints within 150 m of a modeled transformer", summary["baseline_buildings_within_150m"], summary["updated_buildings_within_150m"]],
            ["Median footprint-to-transformer distance (m)", round(summary["baseline_median_building_distance_m"], 2), round(summary["updated_median_building_distance_m"], 2)],
            ["Road-aligned corridors retained", summary["lines_retained"], summary["lines_retained"]],
            ["Pole inventory retained", summary["poles_retained"], summary["poles_retained"]]], columns=["Measure", "Original", "Updated"])
        st.dataframe(comparison, hide_index=True, width="stretch")
        st.dataframe(tables["transformer_changes"], hide_index=True, height=230)
        st.json(validation, expanded=False)
        st.caption("Select Original baseline above for the map comparison. Original routes/poles and all source outage facts remain preserved. "
                   "The generated portable viewer and visualization bundle are documented in the run's portability guide.")
