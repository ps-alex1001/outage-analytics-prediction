"""Persistent Plotly/MapLibre canvas using Streamlit's public Components v2 API.

Native st.plotly_chart hashes the figure into its widget identity in Streamlit
1.64 and exposes selection, but not relayout. A stable component keeps the same
canvas while highlights change and records the viewport separately from data.
The Plotly bundle comes from the installed Python package, never a remote CDN.
"""
import hashlib
import json

from plotly.offline import get_plotlyjs
import plotly.io as pio
import streamlit as st

from utils.charts import MAP_CONFIG
from utils.theme import get_color, get_map_styles, get_theme

KEY = "infra_persistent_map"

JS = r"""
const instances = new WeakMap();
function installLineStyles(plot) {
  const map = plot._fullLayout?.map?._subplot?.map;
  if (!map) return;
  const applied = {};
  for (const trace of plot.data) {
    if (!trace.meta?.line_dash) continue;
    const layerId = 'plotly-trace-layer-' + trace.uid + '-line';
    if (!map.getLayer(layerId)) continue;
    // Scattermap lacks a dash property; use MapLibre's existing line layer.
    // This changes paint only, retaining every coordinate and picking target.
    map.setPaintProperty(layerId, 'line-dasharray', trace.meta.line_dash);
    applied[trace.name] = trace.meta.line_dash;
  }
  plot.dataset.lineStyles = JSON.stringify(applied);
}
function installMarkerZoomScaling(plot) {
  const map = plot._fullLayout?.map?._subplot?.map;
  if (!map) return;
  for (const trace of plot.data) {
    if (!trace.meta?.zoom_scaled_marker) continue;
    const layerId = 'plotly-trace-layer-' + trace.uid + '-circle';
    if (!map.getLayer(layerId)) continue;
    // Native MapLibre interpolation shrinks poles at wider views without
    // Plotly redraws or Streamlit reruns. Values are radii, not diameters.
    map.setPaintProperty(layerId, 'circle-radius',
      ['interpolate', ['linear'], ['zoom'], 11, 0.5, 13, 1, 15, 2.5, 16, 3]);
  }
}
async function installSymbols(plot, casing) {
  // Scattermap accepts sprite image IDs; our infrastructure shapes are local.
  // Register local, actual shape images using MapLibre's public addImage API.
  const map = plot._fullLayout?.map?._subplot?.map;
  if (!map) throw new Error('The Plotly MapLibre bridge is unavailable');
  const symbols = [...new Set(plot.data.filter(t => t.type === 'scattermap')
    .map(t => t.marker?.symbol).filter(s => typeof s === 'string' && s.startsWith('infra-')))];
  for (const name of symbols) {
    if (map.hasImage(name)) continue;
    const [, shape, color] = name.split('-');
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = 32;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#' + color; ctx.strokeStyle = casing; ctx.lineWidth = 3;
    ctx.beginPath();
    if (shape === 'square') ctx.rect(5, 5, 22, 22);
    else if (shape === 'diamond') {
      ctx.moveTo(16, 2); ctx.lineTo(30, 16); ctx.lineTo(16, 30); ctx.lineTo(2, 16);
    } else if (shape === 'cross' || shape === 'x') {
      if (shape === 'x') {
        ctx.translate(16,16); ctx.rotate(Math.PI/4); ctx.scale(0.85,0.85); ctx.translate(-16,-16);
      }
      const points = [[11,2],[21,2],[21,11],[30,11],[30,21],[21,21],[21,30],
                      [11,30],[11,21],[2,21],[2,11],[11,11]];
      points.forEach(([x,y],i) => i ? ctx.lineTo(x,y) : ctx.moveTo(x,y));
    } else throw new Error('Unsupported infrastructure shape: ' + shape);
    ctx.closePath(); ctx.fill(); ctx.stroke();
    map.addImage(name, ctx.getImageData(0,0,32,32), {pixelRatio: 3.2});
  }
  plot.dataset.symbols = JSON.stringify(symbols);
}
function installBarangayLabels(state, labels, minZoom, labelStyle) {
  const {plot} = state;
  const map = plot._fullLayout?.map?._subplot?.map;
  if (!map) return;
  // Local HTML labels need no remote glyph service on the blank basemap.
  // They pass pointer events through to existing infrastructure/boundary picks.
  if (!state.labelLayer?.isConnected) {
    const layer = document.createElement('div');
    layer.setAttribute('aria-label', 'Barangay names');
    Object.assign(layer.style, {position: 'absolute', pointerEvents: 'none', overflow: 'hidden', zIndex: '5'});
    plot.appendChild(layer);
    state.labelLayer = layer;
    state.labelNodes = new Map();
    state.labelMeasure = document.createElement('canvas').getContext('2d');
    state.labelMeasure.font = '600 12px system-ui';
  }
  state.labelItems = labels.map(item => {
    let node = state.labelNodes.get(item.id);
    if (!node) {
      node = document.createElement('span');
      node.dataset.barangayId = item.id;
      Object.assign(node.style, {position: 'absolute', transform: 'translate(-50%,-50%)',
        pointerEvents: 'none', whiteSpace: 'nowrap', font: '600 12px system-ui',
        padding: '1px 2px', lineHeight: '16px',
        textShadow: '1px 1px 2px white,-1px -1px 2px white,1px -1px 2px white,-1px 1px 2px white'});
      state.labelLayer.appendChild(node);
      state.labelNodes.set(item.id, node);
    }
    node.dataset.geographicKind = item.kind || 'barangay';
    node.style.fontSize = item.kind === 'municipality' ? '14px' : '12px';
    node.style.color = labelStyle.color;
    node.style.background = labelStyle.background;
    node.textContent = item.name;
    if (item.subtitle) {
      const subtitle = document.createElement('span');
      subtitle.textContent = item.subtitle;
      Object.assign(subtitle.style, {display: 'block', font: '400 11px system-ui', textAlign: 'center'});
      node.appendChild(subtitle);
    }
    state.labelMeasure.font = '600 ' + node.style.fontSize + ' system-ui';
    const nameWidth = state.labelMeasure.measureText(item.name).width;
    state.labelMeasure.font = '400 11px system-ui';
    const subtitleWidth = item.subtitle ? state.labelMeasure.measureText(item.subtitle).width : 0;
    return {...item, node, width: Math.max(nameWidth, subtitleWidth) + 8, height: item.subtitle ? 36 : 20};
  });
  state.labelMinZoom = minZoom;
  if (state.labelMap !== map) {
    if (state.labelMap && state.updateLabels) {
      state.labelMap.off('move', state.updateLabels); state.labelMap.off('resize', state.updateLabels);
    }
    state.labelMap = map;
    state.updateLabels = () => {
      const mapRect = map.getContainer().getBoundingClientRect(), plotRect = plot.getBoundingClientRect();
      Object.assign(state.labelLayer.style, {left: (mapRect.left-plotRect.left)+'px',
        top: (mapRect.top-plotRect.top)+'px', width: mapRect.width+'px', height: mapRect.height+'px'});
      const zoom = map.getZoom();
      const visible = zoom >= state.labelMinZoom;
      state.labelLayer.style.display = visible ? 'block' : 'none';
      const boxes = [];
      let count = 0;
      for (const item of state.labelItems) {
        item.node.style.display = 'none';
        if (!visible || zoom < (item.minZoom ?? state.labelMinZoom) || zoom >= (item.maxZoom ?? Infinity)) continue;
        const p = map.project([item.lon, item.lat]);
        const box = {left: p.x-item.width/2, right: p.x+item.width/2, top: p.y-item.height/2, bottom: p.y+item.height/2};
        if (box.right < 0 || box.left > mapRect.width || box.bottom < 0 || box.top > mapRect.height) continue;
        // Deterministic screen-space collision suppression, like ordinary map
        // labels. No infrastructure or service geometry is sampled/discarded.
        if (boxes.some(b => box.left < b.right && box.right > b.left && box.top < b.bottom && box.bottom > b.top)) continue;
        boxes.push(box);
        item.node.style.left = p.x+'px'; item.node.style.top = p.y+'px'; item.node.style.display = 'block';
        count++;
      }
      plot.dataset.barangayLabelCount = String(count);
      plot.dataset.barangayLabelMinZoom = String(state.labelMinZoom);
    };
    map.on('move', state.updateLabels);
    map.on('resize', state.updateLabels);
  }
  state.updateLabels();
}
export default function(component) {
  const {parentElement, data} = component;
  let state = instances.get(parentElement);
  if (!state) {
    // A development hot reload replaces this module's WeakMap but can keep the
    // component host. Retire its prior canvas instead of appending a second map.
    for (const old of parentElement.querySelectorAll('.js-plotly-plot')) {
      Plotly.purge(old); old.remove();
    }
    const plot = document.createElement('div');
    plot.setAttribute('aria-label', 'Infrastructure map');
    plot.style.height = (data.height || 720) + 'px';
    plot.style.position = 'relative';
    parentElement.appendChild(plot);
    state = {plot, queue: Promise.resolve(), ready: false, legend: {}, view: null};
    instances.set(parentElement, state);
  }
  state.component = component;
  const {plot} = state;
  plot.style.height = (data.height || 720) + 'px';
  const rememberView = () => {
    const map = plot._fullLayout?.map?._subplot?.map;
    if (!map) return null;
    const center = map.getCenter();
    // Plotly.layout may retain the incoming fit values after react while
    // uirevision keeps a different live camera. Always read MapLibre itself.
    state.view = {center: {lon: center.lng, lat: center.lat}, zoom: map.getZoom(),
                  bearing: map.getBearing(), pitch: map.getPitch()};
    // DOM diagnostics describe the rendered map, useful for accessible QC.
    plot.dataset.viewport = JSON.stringify(state.view);
    plot.dataset.legend = JSON.stringify(state.legend);
    const snapshot = {revision: state.revision, viewport: state.view, legend: state.legend};
    // Relayout must remain entirely local: setStateValue triggers a Streamlit
    // rerun, making every drag grey out/refresh the map. Commit on picks only.
    try { sessionStorage.setItem(data.storageKey, JSON.stringify(snapshot)); } catch (_) {}
    return snapshot;
  };
  // Serialize React updates; rapid clicks cannot finish drawing out of order.
  state.queue = state.queue.then(async () => {
    if (!parentElement.isConnected) return;
    const fit = state.revision !== data.revision;
    if (state.ready && !fit) rememberView();
    const figure = JSON.parse(data.figure);
    state.fitView = {center: {...figure.layout.map.center}, zoom: figure.layout.map.zoom,
                     bearing: 0, pitch: 0};
    // Keep callback identity stable: a new config function makes Plotly.react
    // internally fall back to newPlot, which would recreate the MapLibre map.
    const config = state.config ||= {...data.config, modeBarButtons: [[
      'zoomInMap', 'zoomOutMap', ...(!data.fitButtonLabel ? [{
        name: 'Reset view',
        icon: {width: 24, height: 24, path: 'M3 11L12 3L21 11H18V21H14V15H10V21H6V11Z'},
        // Plotly's built-in reset remembers the initial province view forever.
        // Resolve the current municipality fit at click time instead.
        click: () => Plotly.relayout(plot, {
          'map.center': state.fitView.center, 'map.zoom': state.fitView.zoom,
          'map.bearing': 0, 'map.pitch': 0})
      }] : [])
    ]]};
    let saved = data.saved;
    try {
      const local = JSON.parse(sessionStorage.getItem(data.storageKey));
      if (local?.revision === data.revision) saved = local;
    } catch (_) {}
    if (!state.ready && saved?.revision === data.revision) {
      state.view = saved.viewport;
      state.legend = {...saved.legend};
    }
    if (fit && state.ready) state.view = null;
    const applyFit = fit && !state.view; // A remount can restore a saved camera.
    state.revision = data.revision;
    if (state.view) Object.assign(figure.layout.map, state.view);
    figure.layout.uirevision = data.revision;
    figure.layout.map.uirevision = data.revision;
    figure.layout.legend ||= {};
    figure.layout.legend.uirevision = 'infrastructure-legend';
    const pickingChanged = state.pickingEnabled !== data.pickingEnabled;
    state.pickingEnabled = data.pickingEnabled;
    // View-only maps still support hover, drag and zoom. They must not emit
    // component triggers, even when a trace retains useful hover customdata.
    figure.layout.clickmode = state.pickingEnabled ? 'event' : 'none';
    for (const trace of figure.data) {
      if (trace.name?.startsWith('Selected')) {
        trace.visible = true;
        trace.showlegend = false; // A selected pair always stays visible.
      } else if (trace.uid in state.legend) {
        trace.visible = state.legend[trace.uid];
      }
    }
    const changed = state.figure !== data.figure || fit || pickingChanged;
    if (!state.ready) {
      await Plotly.newPlot(plot, figure.data, figure.layout, config);
      await installSymbols(plot, data.mapStyles.selected.casing);
      state.ready = true;
      plot.dataset.mountId = crypto.randomUUID();
      // Plotly reapplies trace paint during zoom/legend updates; restore the
      // camera expression after each draw without issuing another redraw.
      plot.on('plotly_afterplot', () => { installMarkerZoomScaling(plot); installLineStyles(plot); });
      plot.on('plotly_click', event => {
        if (!state.pickingEnabled) return;
        const point = event.points?.find(p => p.customdata);
        if (!point) return;
        // Commit the view with the click, even if a pan event is in flight.
        const view = rememberView();
        state.component.setTriggerValue('picked', {customdata: point.customdata, view});
      });
      plot.on('plotly_relayout', event => {
        if (Object.keys(event).some(k => k.startsWith('map.'))) rememberView();
      });
      plot.on('plotly_restyle', event => {
        if ('visible' in event[0]) {
          for (const trace of plot.data) {
            if (!trace.name?.startsWith('Selected')) state.legend[trace.uid] = trace.visible ?? true;
          }
          rememberView();
        }
      });
      const observer = new ResizeObserver(() => Plotly.Plots.resize(plot));
      observer.observe(parentElement);
      state.observer = observer;
    } else if (changed) {
      await Plotly.react(plot, figure.data, figure.layout, config);
      await installSymbols(plot, data.mapStyles.selected.casing);
    }
    if (data.fitBounds) {
      // Spatial maps fit the real canvas dimensions rather than a fixed zoom.
      const map = plot._fullLayout?.map?._subplot?.map;
      const camera = map?.cameraForBounds(data.fitBounds, {padding: 20, maxZoom: 15});
      if (camera) state.fitView = {center: {lon: camera.center.lng, lat: camera.center.lat},
                                  zoom: camera.zoom, bearing: 0, pitch: 0};
    }
    if (applyFit) {
      // Explicit navigation must apply the new fit even when Plotly retains an
      // earlier GUI camera during a trace/subplot update. Never fit on a pick.
      await Plotly.relayout(plot, {
        'map.center': state.fitView.center, 'map.zoom': state.fitView.zoom,
        'map.bearing': 0, 'map.pitch': 0});
    }
    if (data.fitButtonLabel) {
      if (!state.fitButton?.isConnected) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'infra-map-fit';
        button.onpointerdown = event => event.stopPropagation();
        button.onclick = event => {
          event.stopPropagation();
          // Explicit fitting stays local, like pan/zoom: no backend trigger.
          Plotly.relayout(plot, {'map.center': state.fitView.center,
            'map.zoom': state.fitView.zoom, 'map.bearing': 0, 'map.pitch': 0});
        };
        plot.appendChild(button);
        state.fitButton = button;
      }
      state.fitButton.textContent = data.fitButtonLabel;
      state.fitButton.setAttribute('aria-label', data.fitButtonLabel);
      Object.assign(state.fitButton.style, {position: 'absolute', top: '10px', left: '10px', zIndex: '6',
        padding: '6px 10px', borderRadius: '6px', font: '500 12px system-ui', cursor: 'pointer',
        background: data.controlStyle.background, color: data.controlStyle.color,
        border: '1px solid ' + data.controlStyle.border, boxShadow: '0 1px 4px rgba(0,0,0,.12)'});
    } else {
      state.fitButton?.remove();
    }
    installMarkerZoomScaling(plot);
    installLineStyles(plot);
    installBarangayLabels(state, data.labels || [], data.labelMinZoom ?? 13.5, data.mapStyles.labels);
    state.figure = data.figure;
    plot.dataset.selection = JSON.stringify(data.selection);
    plot.dataset.pickingEnabled = String(state.pickingEnabled);
    plot.dataset.revision = data.revision;
    plot.dataset.serverRender = String(data.serverRender);
    plot.dataset.themeMode = data.themeMode;
    if (changed) plot.dataset.drawCount = String(Number(plot.dataset.drawCount || 0) + 1);
    rememberView();
  }).catch(error => {
    console.error('Infrastructure map:', error);
    plot.setAttribute('aria-label', 'Infrastructure map error: ' + error.message);
  });
  // v2 may clean up a renderer before updating data; only purge on real removal.
  return () => queueMicrotask(() => {
    if (!parentElement.isConnected) {
      state.observer?.disconnect();
      if (state.ready) Plotly.purge(plot);
      instances.delete(parentElement);
    }
  });
}
"""

_MAP = st.components.v2.component(
    "persistent_infrastructure_map", js=get_plotlyjs() + "\n" + JS,
    # Plotly and MapLibre insert their own required CSS in the document head.
    isolate_styles=False,
)


def remember_pick_view(picked):
    """Persist a click's browser viewport without backend events during panning."""
    value = picked.get("view")
    if value:
        st.session_state["infra_map_view"] = value


def render(figure, municipality, on_pick, selection=None, labels=None, label_min_zoom=13.5,
           *, key=KEY, height=720, display_names=None, fit_bounds=None, fit_button_label=None):
    """Only navigation/Fit changes revision; on_pick=None makes clicks local too."""
    document = json.loads(pio.to_json(figure))
    occurrences = {}
    for trace in document["data"]:
        group = (trace.get("name"), trace.get("type"), trace.get("mode"))
        count = occurrences.get(group, 0)
        occurrences[group] = count + 1
        trace["uid"] = hashlib.sha256(repr((group, count)).encode()).hexdigest()[:20]
        # Display copy must not rename identities used by saved layer state.
        for original, label in (display_names or {}).items():
            if trace.get("name") == original:
                trace["name"] = label
            if isinstance(trace.get("hovertemplate"), str):
                trace["hovertemplate"] = trace["hovertemplate"].replace(original, label)
    # Keep existing impact-map state intact; other maps have isolated state.
    fit_key = "infra_fit_revision" if key == KEY else key + "_fit_revision"
    count_key = "infra_map_render_count" if key == KEY else key + "_render_count"
    storage_key = "infra_map_storage_id" if key == KEY else key + "_storage_id"
    view_key = "infra_map_view" if key == KEY else key + "_view"
    revision = f"{municipality}:{st.session_state.get(fit_key, 0)}"
    st.session_state[count_key] = st.session_state.get(count_key, 0) + 1
    # A per-Streamlit-session key prevents view leakage between browser sessions.
    if storage_key not in st.session_state:
        import uuid
        st.session_state[storage_key] = uuid.uuid4().hex
    return _MAP(
        key=key, height=height, width="stretch",
        data={"figure": json.dumps(document, separators=(",", ":")),
              "config": MAP_CONFIG, "revision": revision,
              "saved": st.session_state.get(view_key), "height": height,
              "selection": selection,
              "pickingEnabled": on_pick is not None,
              "fitButtonLabel": fit_button_label,
              "controlStyle": {"background": get_color("surface"), "color": get_color("primary"),
                               "border": get_color("control_border")},
              "labels": labels or [], "labelMinZoom": label_min_zoom,
              "mapStyles": get_map_styles(), "themeMode": get_theme().name,
              "fitBounds": ([[float(fit_bounds[0]), float(fit_bounds[1])],
                             [float(fit_bounds[2]), float(fit_bounds[3])]] if fit_bounds is not None else None),
              "serverRender": st.session_state[count_key],
              "storageKey": "infra-map:" + st.session_state[storage_key]},
        on_picked_change=on_pick,
    )
