from pathlib import Path
import streamlit as st
from utils.theme import apply_app_theme
from utils.power_connections_view import render_connections

st.set_page_config(page_title="Substation and feeder explorer", layout="wide")
apply_app_theme()
st.title("Substation and feeder explorer")
render_connections(Path(__file__).resolve().parent / "dataset")
