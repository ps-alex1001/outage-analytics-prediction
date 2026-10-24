"""A source-first view of the complete inferred radial supply network."""
from collections import defaultdict
import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from utils.building_infrastructure_view import add_lines, add_points
from utils.theme import get_color, style_plotly_figure

ROOT = Path(__file__).resolve().parents[1]
MAP_KEY = 'power_connections_canvas'
LOCAL_RADIAL_VIEW_VERSION = 5


def completed_connections():
    return sorted([path for path in (ROOT / 'data/synthetic').glob('power_connections_*')
                   if (path / 'manifest.json').exists() and not (path / 'RUN_INCOMPLETE').exists()], reverse=True)


class ConnectionIndex:
    """Full active-tree reachability; rendering limits never limit impact counts."""
    def __init__(self, nodes, edges):
        self.nodes = nodes.set_index('node_id', drop=False)
        self.edges = edges.set_index('edge_id', drop=False)
        self.children = defaultdict(list)
        self.parent = {}
        self.parent_edge = {}
        for row in edges.loc[edges.is_tree_edge, ['edge_id','from_node_id','to_node_id']].itertuples(index=False):
            self.children[row.from_node_id].append(row.to_node_id)
            self.parent[row.to_node_id] = row.from_node_id
            self.parent_edge[row.to_node_id] = row.edge_id

    def reach(self, kind, identity):
        selected_edge = None
        if kind == 'edge':
            edge = self.edges.loc[identity]
            if not edge.is_tree_edge:
                raise ValueError('Non-tree connections have no modeled downstream direction')
            start, selected_edge = edge.to_node_id, identity
        else:
            start = identity
        descendants, stack = set(), [start]
        while stack:
            node = stack.pop()
            if node in descendants:
                continue
            descendants.add(node)
            stack.extend(self.children[node])
        affected_edges = {self.parent_edge[node] for node in descendants if node != start and node in self.parent_edge}
        if selected_edge:
            affected_edges.add(selected_edge)
        upstream_nodes, upstream_edges, cursor = [start], set(), start
        while cursor in self.parent:
            upstream_edges.add(self.parent_edge[cursor])
            cursor = self.parent[cursor]
            upstream_nodes.append(cursor)
        return dict(node_ids=descendants, edge_ids=affected_edges, upstream_nodes=upstream_nodes,
                    upstream_edges=upstream_edges, start=start)


@st.cache_resource(max_entries=2, show_spinner='Loading connected power network…')
def load_connections(directory, stamp):
    path = Path(directory)
    if (path / 'RUN_INCOMPLETE').exists():
        raise ValueError('Connection model is incomplete')
    layers = {name:gpd.read_parquet(path / (name + '.parquet')) for name in
              ['nodes','edges','sources','municipalities','barangays']}
    if (path / 'observed_substations.parquet').exists():
        layers['observed_substations'] = gpd.read_parquet(path / 'observed_substations.parquet')
    else:
        # Compatibility with earlier generated versions; no data is rewritten.
        parent = Path(json.loads((path / 'summary.json').read_text())['parent_run'])
        observed = gpd.read_parquet(parent / 'view/observed_infrastructure.parquet')
        layers['observed_substations'] = observed.loc[observed.asset_type.eq('substation')].copy()
    if (path/'reported_substations.csv').exists():
        layers['reported_substations'] = pd.read_csv(path/'reported_substations.csv',dtype={'municipality_psgc':'string'})
    return layers, pd.read_csv(path / 'branches.csv'), ConnectionIndex(layers['nodes'], layers['edges'])


def component_label(row):
    label = {'transformer':'Transformer','pole':'Pole','synthetic_source':'Feeder entry','substation':'Substation'}.get(
        row.get('node_type'), 'Line section')
    location = ' · '.join(str(row.get(col)) for col in ['barangay','municipality'] if pd.notna(row.get(col)))
    return f"{label}{' · ' + location if location else ''} · {str(row.get('node_id',row.get('edge_id'))) [-8:]}"


def figure_for(layers, source, branch, connections, selected, show_poles=False, show_barangays=False,
               show_other_networks=True, fit_all=False, inspected_station=None):
    nodes, edges = layers['nodes'], layers['edges']
    scope_nodes = nodes.loc[nodes.source_node_id.eq(source)]
    scope_edges = edges.loc[edges.source_node_id.eq(source) & edges.is_tree_edge]
    if branch:
        scope_nodes = scope_nodes.loc[scope_nodes.feeder_id.eq(branch) | scope_nodes.node_id.eq(source)]
        scope_edges = scope_edges.loc[scope_edges.feeder_id.eq(branch)]
    physical_types = ['distribution_line_section','inferred_road_junction','road_evidenced_extension','inferred_local_transfer']
    physical = scope_edges.loc[scope_edges.edge_type.isin(physical_types),['edge_id','geometry']]
    links = scope_edges.loc[scope_edges.edge_type.isin(['inferred_supply_connection','inferred_station_lead']),['edge_id','geometry']]
    affected = scope_edges.loc[scope_edges.edge_id.isin(connections['edge_ids'])]
    upstream = edges.loc[edges.edge_id.isin(connections['upstream_edges'])]
    figure = go.Figure()
    municipalities = layers['municipalities']
    if show_barangays:
        bounds = layers['barangays'].loc[layers['barangays'].municipality_psgc.isin(scope_nodes.municipality_psgc.dropna())]
        add_lines(figure,bounds,'Barangay borders','#B5BDCB',0.7)
    add_lines(figure,municipalities,'Municipality borders',get_color('municipality_boundary'),1.7)
    if show_other_networks:
        context = edges.loc[edges.is_tree_edge & edges.edge_type.isin(physical_types) & ~edges.source_node_id.eq(source)]
        add_lines(figure,context[['geometry']],'Other source networks · context','#CFD3DD',1)
    add_lines(figure,physical,'Remaining modeled network','#A6ADBD',2,kind='edge')
    # Upstream first: the affected subtree remains the visually dominant layer.
    add_lines(figure,upstream.loc[upstream.edge_type.isin(physical_types),['edge_id','geometry']],
              'Supply path to selection','#0072B2',4,kind='edge')
    add_lines(figure,affected.loc[affected.edge_type.isin(physical_types),['edge_id','geometry']],
              'Downstream of selection','#9C4AA3',4,kind='edge')
    # Draw these after the purple lines, with a white casing, so the relationship
    # across separated road clusters remains legible at province zoom levels.
    add_lines(figure,links,'Supply link casing','#FFFFFF',5)
    add_lines(figure,links,'Inferred source connection · route unknown','#0072B2',2.5,kind='edge',dash=True)
    entry_ids = set(links['edge_id'])
    entry_nodes = scope_edges.loc[scope_edges.edge_id.isin(entry_ids),'to_node_id']
    add_points(figure,scope_nodes.loc[scope_nodes.node_id.isin(entry_nodes)],
               'Feeder entry · local station departure','#0072B2',6)
    tx = scope_nodes.loc[scope_nodes.node_type.eq('transformer')]
    add_points(figure,tx.loc[~tx.node_id.isin(connections['node_ids'])],'Other transformers','#7F899C',5)
    add_points(figure,tx.loc[tx.node_id.isin(connections['node_ids'])],'Downstream transformers','#9C4AA3',7)
    if show_poles:
        # The user explicitly requests these details; no sampling removes connections.
        add_points(figure,scope_nodes.loc[scope_nodes.node_type.eq('pole')],'Modeled poles','#69788D',3)
    stations = layers.get('observed_substations', layers['sources'])
    # Name and status are available in hover; stations outside the current source
    # remain visible, without implying that their electrical relationship is known.
    station_points = stations.geometry.representative_point()
    active_sources = set(nodes.loc[nodes.node_type.eq('transformer'),'source_node_id'].dropna())
    source_ids = layers['sources'].loc[layers['sources'].node_id.isin(active_sources)].set_index('asset_id').node_id.to_dict()
    station_custom = []
    for row in stations.itertuples():
        label = row.name if pd.notna(row.name) else 'Unnamed mapped substation'
        role = row.substation if hasattr(row,'substation') and pd.notna(row.substation) else 'role unclassified'
        status = 'Inferred supply branches assigned' if row.asset_id in source_ids else 'Observed location · no modeled supply assignment'
        station_custom.append(['station',row.asset_id,label,role,status])
    figure.add_trace(go.Scattermap(lon=station_points.x.tolist(),lat=station_points.y.tolist(),mode='markers',
        name='All mapped substations',marker=dict(size=16,color='#0072B2',symbol='infra-square-0072B2'),
        customdata=station_custom,hovertemplate='%{customdata[2]}<br>%{customdata[3]}<br>%{customdata[4]}<extra></extra>'))
    add_points(figure,layers['sources'].loc[layers['sources'].node_id.eq(source)],'Selected substation','#0072B2',21)
    if inspected_station:
        add_points(figure,stations.loc[stations.asset_id.eq(inspected_station)],'Inspected station','#D55E00',13,
                   kind='station',identity='asset_id')
    if selected[0] == 'node':
        add_points(figure,nodes.loc[nodes.node_id.eq(selected[1])],'Selected component','#D55E00',13)
    else:
        add_lines(figure,edges.loc[edges.edge_id.eq(selected[1])],'Selected line','#D55E00',7,kind='edge')
    bounds = scope_nodes.total_bounds.tolist()
    if fit_all:
        bounds = stations.total_bounds.tolist()
    center = dict(lon=(bounds[0]+bounds[2])/2, lat=(bounds[1]+bounds[3])/2)
    figure.update_layout(map=dict(style='white-bg',center=center,zoom=10), height=650,
        margin=dict(l=0,r=0,t=0,b=0), showlegend=False, clickmode='event', uirevision=f'{source}:{branch}')
    style_plotly_figure(figure)
    return figure, bounds


def render_connections(directory=None):
    from utils import persistent_infrastructure_map as canvas
    if directory is None:
        runs = completed_connections()
        if not runs:
            st.info('Generate a connected model with scripts/connect_infrastructure_sources.py first.')
            return
        directory = runs[0]
    directory = Path(directory)
    stamp = (directory / 'manifest.json').stat().st_mtime_ns
    layers, branches, index = load_connections(str(directory), stamp)
    nodes, edges, sources = layers['nodes'], layers['edges'], layers['sources']
    metadata = json.loads((directory/'summary.json').read_text())
    local_model = metadata.get('method') == 'local_radial_road_forest'
    if st.session_state.get('pc_model_method') != metadata.get('method','legacy'):
        st.session_state.pc_model_method = metadata.get('method','legacy')
        st.session_state.pc_other_networks = not local_model
    st.write('Choose a substation and a modeled feeder, then click a line or transformer. Purple shows the downstream network of your selection.')
    if metadata.get('minimum_transformers_per_feeder'):
        st.caption(f'Active modeled feeders serve at least {metadata["minimum_transformers_per_feeder"]} transformers. '
            'Balanga reuses its formerly empty feeder after redistribution. Counts are not measured electrical loads.')
    source_counts = nodes.loc[nodes.node_type.eq('transformer')].groupby('source_node_id').size()
    source_options = source_counts.sort_values(ascending=False).index.tolist()
    if st.session_state.get('pc_source') not in source_options:
        st.session_state.pc_source = source_options[0]
    source_names = sources.set_index('node_id')['name'].to_dict()
    pending_source = st.session_state.pop('pc_pending_source',None)
    if pending_source in source_names:
        st.session_state.pc_source = pending_source
        st.session_state.pc_branch = None
    duplicates = sources['name'].duplicated(keep=False)
    for row in sources.loc[duplicates].itertuples():
        source_names[row.node_id] = f'{row.name} · OSM {row.asset_id.split("/")[-1]}'
    columns = st.columns([1.2,1.5,1])
    with columns[0]:
        source = st.selectbox('1. Substation',source_options, key='pc_source',
            format_func=lambda code:f'{source_names[code]} · {source_counts[code]:,} transformers')
    available = branches.loc[branches.source_node_id.eq(source)]
    feeder_counts = nodes.loc[nodes.node_type.eq('transformer')].groupby('feeder_id').size()
    branch_names = {}
    for number,row in enumerate(available.itertuples(),1):
        location = ', '.join(str(value) for value in [row.barangay,row.municipality] if pd.notna(value))
        display_number = int(row.display_feeder_number) if hasattr(row,'display_feeder_number') else number
        count = int(feeder_counts.get(row.branch_id,0))
        branch_names[row.branch_id] = f'Modeled feeder {display_number} · {count:,} transformers · {location}'
    if st.session_state.get('pc_branch') not in branch_names:
        st.session_state.pc_branch = None
    with columns[1]:
        branch = st.selectbox('2. Feeder',[None,*branch_names],key='pc_branch',
            format_func=lambda code:branch_names.get(code,'All modeled feeders'), placeholder='All modeled feeders')
    context = (str(directory),source,branch)
    if st.session_state.get('pc_context') != context:
        st.session_state.pc_context = context
        st.session_state.pc_fit_all = False
        st.session_state.pc_inspected_station = None
        root = available.loc[available.branch_id.eq(branch),'branch_root_id'].iloc[0] if branch else source
        st.session_state.pc_selection = ('node',root)
    with columns[2]:
        if st.button('Show whole feeder' if branch else 'Show whole substation',width='stretch'):
            root = available.loc[available.branch_id.eq(branch),'branch_root_id'].iloc[0] if branch else source
            st.session_state.pc_selection = ('node',root)
            st.session_state.pc_inspected_station = None
    scope = nodes.loc[nodes.source_node_id.eq(source)]
    if branch:
        scope = scope.loc[scope.feeder_id.eq(branch) | scope.node_id.eq(source)]
    with st.expander('Choose a component without clicking the map'):
        category = st.selectbox('Component type',['Transformer','Line section','Pole'],key='pc_kind')
        if category == 'Line section':
            choices = edges.loc[edges.source_node_id.eq(source) & edges.is_tree_edge & edges.edge_type.eq('distribution_line_section')]
            if branch:
                choices = choices.loc[choices.feeder_id.eq(branch)]
            field,kind = 'edge_id','edge'
        else:
            choices = scope.loc[scope.node_type.eq(category.lower())]
            field,kind = 'node_id','node'
        query = st.text_input('Find by barangay or component ID',key='pc_search')
        if query:
            choices = choices.loc[choices[field].str.contains(query,case=False,regex=False,na=False) |
                choices.barangay.fillna('').str.contains(query,case=False,regex=False)]
        options = choices[field].head(1000).tolist()
        component_labels = {row[field]:component_label(row) for _,row in choices.head(1000).iterrows()}
        def choose():
            value = st.session_state.pc_component
            if value:
                st.session_state.pc_selection = (kind,value)
        st.selectbox('3. Component',[None,*options],key='pc_component',on_change=choose,
            format_func=lambda code:component_labels.get(code,'Click map or choose a component'))
        st.caption('First 1,000 matching components shown. Search narrows the list; all network connections remain on the map.')
    selected = tuple(st.session_state.pc_selection)
    connections = index.reach(*selected)
    affected_nodes = index.nodes.loc[list(connections['node_ids'])]
    affected_edges = index.edges.loc[list(connections['edge_ids'])]
    transformer_count = int(affected_nodes.node_type.eq('transformer').sum())
    route_km = affected_edges.loc[affected_edges.is_length_bearing.fillna(False),'length_m'].sum()/1000
    barangays = affected_nodes.loc[affected_nodes.node_type.isin(['pole','transformer']),'barangay_psgc'].nunique()
    metrics = st.columns(4)
    metrics[0].metric('Downstream transformers',f'{transformer_count:,}')
    metrics[1].metric('Downstream modeled route',f'{route_km:,.2f} km')
    metrics[2].metric('Barangays with downstream assets',f'{barangays:,}')
    municipalities_reached = affected_nodes.loc[affected_nodes.node_type.isin(['pole','transformer']),'municipality_psgc'].nunique()
    metrics[3].metric('Municipalities reached',f'{municipalities_reached:,}')
    map_col, details = st.columns([3.2,1],gap='medium')
    with details:
        st.markdown('**Connection path**')
        st.write(source_names[source])
        if 'eligibility_basis' in sources:
            st.caption('Substation anchor: ' + str(sources.set_index('node_id').loc[source,'eligibility_basis']))
        selected_record = index.nodes.loc[selected[1]] if selected[0]=='node' else index.edges.loc[selected[1]]
        branch_id = selected_record.get('feeder_id')
        if pd.notna(branch_id):
            st.write('↓ ' + branch_names.get(branch_id,'Modeled feeder'))
        st.write('↓ ' + (component_label(selected_record) if selected[1] != source else 'All modeled feeders from this substation'))
        st.write(f'↓ **{transformer_count:,} transformers downstream**')
        st.caption('Blue squares = mapped substations. Blue dashed lines = inferred source links; blue dots mark their branch entry points. '
            'Purple = downstream network. Orange = selected component. Gray = remaining network.')
        show_other_networks = st.checkbox('Show other substations’ networks',value=not local_model,key='pc_other_networks')
        if st.button('Fit all substations',width='stretch'):
            st.session_state.pc_fit_all = True
        show_poles = st.checkbox('Show individual poles',key='pc_poles')
        show_barangays = st.checkbox('Show barangay borders',key='pc_barangays')
        stations = layers['observed_substations']
        inspected_id = st.session_state.get('pc_inspected_station')
        if inspected_id:
            station = stations.loc[stations.asset_id.eq(inspected_id)].iloc[0]
            label = station['name'] if pd.notna(station['name']) else 'Unnamed mapped substation'
            st.info(f'{label}: mapped location retained. No modeled feeder is assigned to this station. '
                'The highlighted network still belongs to the substation selected above.')
        st.caption('Station locations are mapped observations. Feeder routes and short station departures are synthetic; official PENELCO feeder IDs are not assigned here.')
        if metadata.get('unresolved_transformers',0):
            st.caption(f'{metadata["unresolved_transformers"]:,} transformers province-wide remain in unresolved local road sections. '
                'They are retained in the context layer; no distant station link is forced.')
    with map_col:
        fit_all = st.session_state.get('pc_fit_all',False)
        figure,bounds = figure_for(layers,source,branch,connections,selected,show_poles,show_barangays,
            show_other_networks,fit_all,st.session_state.get('pc_inspected_station'))
        municipality_labels = []
        for row in layers['municipalities'].itertuples():
            point = row.geometry.representative_point()
            municipality_labels.append(dict(id=str(row.municipality_psgc),name=row.municipality,lon=point.x,lat=point.y,kind='municipality',minZoom=8))
        for row in layers['observed_substations'].itertuples():
            # The selected name is already shown in the controls/inspection panel;
            # keep its map marker uncovered so the feeder origin stays visible.
            if pd.notna(row.name):
                point = row.geometry.representative_point()
                selected_station = row.asset_id in set(sources.loc[sources.node_id.eq(source),'asset_id'])
                # Keep an existing label in the update list so its cached DOM node
                # is hidden when switching from a neighboring station to this one.
                municipality_labels.append(dict(id=row.asset_id,name=row.name,lon=point.x,lat=point.y,kind='station',
                                                minZoom=99 if selected_station else 11.5))
        result = canvas.render(figure,f'{directory.name}:{source}:{branch}:{fit_all}',lambda:None,key=MAP_KEY,
            height=650,labels=municipality_labels,label_min_zoom=8,fit_bounds=bounds,fit_button_label='Fit substation / feeder')
        picked = getattr(result,'picked',None)
        if isinstance(picked,dict):
            custom = picked.get('customdata',[])
            if len(custom)>=2 and custom[0] == 'station':
                station_id = str(custom[1])
                linked = sources.loc[sources.asset_id.eq(station_id),'node_id']
                if len(linked) and linked.iloc[0] in source_options:
                    new_source = linked.iloc[0]
                    if source != new_source:
                        st.session_state.pc_pending_source = new_source
                        st.rerun()
                    if selected != ('node',new_source):
                        st.session_state.pc_selection = ('node',new_source)
                        st.rerun()
                elif station_id in set(layers['observed_substations'].asset_id) and station_id != st.session_state.get('pc_inspected_station'):
                    st.session_state.pc_inspected_station = station_id
                    st.rerun()
            if len(custom)>=2 and custom[0] in {'node','edge'}:
                candidate = (str(custom[0]),str(custom[1]))
                frame = index.nodes if candidate[0]=='node' else index.edges
                if candidate != selected and candidate[1] in frame.index:
                    st.session_state.pc_selection = candidate
                    if picked.get('view'):
                        st.session_state[MAP_KEY+'_view'] = picked['view']
                    st.rerun()
        st.caption(f'All {len(layers["observed_substations"]):,} mapped substation records are retained on the map. '
            f'The substation selector lists {len(source_options)} locations with modeled transformer connections. Click a blue square to inspect it. '
            'Use Fit all substations to see locations outside the selected network. Map data: © OpenStreetMap contributors.')
    st.caption('This is network topology (electrical connections), rather than terrain elevation. '
        'The highlighted extent is a radial disconnection preview, not a recorded outage or confirmed service area. '
        'Barangay counts refer to assets present, not entire barangays losing power. Backup supply and switching are not modeled.')
    if 'reported_substations' in layers:
        with st.expander('Reported substations from your listing'):
            reported = layers['reported_substations']
            st.write(f'All {len(reported)} reported substation headings are retained. Unmatched or ambiguous names '
                'do not receive invented coordinates. Multiple headings can refer to one mapped location.')
            st.dataframe(reported[['reported_name','capacity_mva_reported','observed_match_status','observed_asset_id']],
                         hide_index=True,width='stretch')
            st.caption('The active model uses geographically located anchors and modeled feeder groups; these are distinct '
                'from the reported station and feeder inventory. Feeders explicitly reported No load are excluded from the active reported catalog.')
    with st.expander('Connection assumptions and validation'):
        if local_model:
            st.write('Feeders grow from nearby mapped substation candidates over existing road sections, using a strong municipal-boundary crossing penalty. '
                'Station departures are at most 250 m. New road-junction links are at most 40 m and require existing-road support. '
                'Sections without a supported local connection remain unresolved. Named stations with incomplete role tags may be used as explicitly inferred anchors. '
                'These modeled feeder numbers are local display labels, not official PENELCO feeder numbers.')
            if metadata.get('feeder_policy'):
                st.write(metadata['feeder_policy'])
            st.json(json.loads((directory/'validation.json').read_text()),expanded=False)
        else:
            st.write('Every original branch is connected to a mapped distribution substation. Existing geographic station anchors are retained; '
            'remaining branches use the nearest mapped distribution substation. This assumption supplies missing connectivity without inventing station coordinates. '
            'Reported feeder labels remain local evidence; these modeled branches are not official PENELCO feeders. '
            'Non-tree candidate connections remain available in the evidence view and are excluded from the radial preview.')
        st.dataframe(available[['source_name','municipality','barangay','connection_basis','gap_distance_m']],hide_index=True)
        st.json(json.loads((directory/'validation.json').read_text()),expanded=False)
        st.code(selected[1],language=None)
