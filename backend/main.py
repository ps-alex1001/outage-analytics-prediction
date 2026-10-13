from pathlib import Path

import pandas as pd
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

@app.get('/')
def read_root():
    return {'Hello' : 'World!'}

DATA_PATH = (Path(__file__).parent.parent / 'data' / 'outages' /
             'synthetic_outage_events.csv')

@app.get('/api/outages/summary')
def get_outage_summary(year: int | None = None):
    df = pd.read_csv(DATA_PATH)
    if year is not None:
        df = df[df['year'] == year]
    return {
        'total_outages': len(df),
        'selected_year': year,
        'data_type' : 'synthetic',
    }

@app.get('/api/overview/kpis')
def get_overview_kpis(
        year: int | None = None,
        municipality_psgc: str | None = None,
        barangay_psgc: str | None = None,
):
    df = pd.read_csv(DATA_PATH, dtype={'municipality_psgc': 'string', 'barangay_psgc': 'string'})
    df = df.drop_duplicates(subset=['outage_id'])
    source_df = df

    # Apply filters
    if year is not None:
        df = df[df["year"] == year]

    if municipality_psgc:
        df = df[df["municipality_psgc"] == municipality_psgc]

    if barangay_psgc:
        df = df[df["barangay_psgc"] == barangay_psgc]

    # Main KPIs
    total_outages = len(df)
    durations = pd.to_numeric(df["duration_minutes"], errors="coerce")

    total_hours = durations.sum() / 60
    average_minutes = durations.mean()

    def percentage_change(current, previous):
        if pd.isna(current) or pd.isna(previous) or previous == 0:
            return None

        return round((current - previous) / previous * 100, 2)

    comparison = None

    if year is not None and year > 2019:
        previous = source_df[source_df["year"] == year - 1]

        # Compare matching six-month periods for 2026
        if year == 2026:
            previous = previous[previous["month"].between(1, 6)]

        if municipality_psgc:
            previous = previous[
                previous["municipality_psgc"] == municipality_psgc
                ]

        if barangay_psgc:
            previous = previous[
                previous["barangay_psgc"] == barangay_psgc
                ]

        previous_durations = pd.to_numeric(
            previous["duration_minutes"], errors="coerce"
        )

        comparison = {
            "period": "Jan–Jun 2025" if year == 2026 else str(year - 1),
            "outages_change_pct": percentage_change(
                total_outages, len(previous)
            ),
            "duration_change_pct": percentage_change(
                total_hours, previous_durations.sum() / 60
            ),
            "average_change_pct": percentage_change(
                average_minutes, previous_durations.mean()
            ),
        }

    # Identify the leading location in the filtered scope
    def top_location(columns):
        if df.empty:
            return None

        ranking = (df.groupby(columns)
            .size()
            .sort_values(ascending=False))

        if ranking.empty:
            return None

        keys = ranking.index[0]
        subset = df

        for col, value in zip(columns, keys):
            subset = subset[subset[col] == value]

        causes = subset["cause_category"].dropna().mode()

        return {
            "name": subset.iloc[0][columns[-1]],
            "municipality": subset.iloc[0]["municipality"],
            "events": len(subset),
            "percentage": round(
                len(subset) / total_outages * 100, 2
            ),
            "common_cause": (
                causes.iloc[0] if not causes.empty else None
            ),
        }

    return {
        "total_outages": total_outages,
        "total_duration_hours": round(float(total_hours), 2),
        "average_duration_minutes": (
            round(float(average_minutes), 2)
            if pd.notna(average_minutes) else None
        ),
        "top_municipality": top_location(
            ["municipality_psgc", "municipality"]
        ),
        "top_barangay": top_location(
            ["barangay_psgc", "barangay"]
        ),
        "filters": {
            "year": year,
            "municipality_psgc": municipality_psgc,
            "barangay_psgc": barangay_psgc,
        },
        "comparison": comparison,
    }