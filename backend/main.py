from pathlib import Path
import json
from typing import Literal
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
OUTAGES_DF = pd.read_csv(
    DATA_PATH,
    dtype={
        "municipality_psgc": "string",
        "barangay_psgc": "string",
    },
).drop_duplicates(subset=["outage_id"])

BOUNDARY_PATH = (
    DATA_PATH.parent.parent
    / "boundaries"
    / "bataan_barangays.geojson"
)

with BOUNDARY_PATH.open(encoding="utf-8") as file:
    boundaries = json.load(file)

MUNICIPALITY_PATH = (
    BOUNDARY_PATH.parent / "bataan_municipalities.geojson"
)

with MUNICIPALITY_PATH.open(encoding="utf-8") as file:
    municipality_boundaries = json.load(file)

BOUNDARY_PROPERTIES = [
    feature["properties"]
    for feature in boundaries["features"]
]

def filter_outages(
        year: int | None = None,
        municipality_psgc: str | None = None,
        barangay_psgc: str | None = None,
        cause_category: str | None = None,
):
    df = OUTAGES_DF

    if year is not None:
        df = df[df["year"] == year]

    if municipality_psgc:
        df = df[df["municipality_psgc"] == municipality_psgc]

    if barangay_psgc:
        df = df[df["barangay_psgc"] == barangay_psgc]

    if cause_category:
        df = df[df["cause_category"] == cause_category]

    return df

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

@app.get("/api/overview/trends")
def get_overview_trends(
    year: int | None = None,
    municipality_psgc: str | None = None,
    barangay_psgc: str | None = None,
    cause_category: str | None = None,
):
    granularity = "monthly"
    if OUTAGES_DF.empty:
        return {"granularity": granularity, "points": []}

    # Coverage is global, independent of filters. In the current dataset it
    # runs from January 2019 through June 2026, not through December 2026.
    coverage = OUTAGES_DF[["year", "month"]].drop_duplicates().sort_values(
        ["year", "month"]
    )
    first_year, first_month = map(int, coverage.iloc[0])
    last_year, last_month = map(int, coverage.iloc[-1])
    if year is None:
        periods = pd.period_range(
            start=f"{first_year}-{first_month:02d}",
            end=f"{last_year}-{last_month:02d}",
            freq="M",
        ).strftime("%Y-%m").tolist()

    else:
        if year < first_year or year > last_year:
            return {"granularity": granularity, "points": []}

        first = first_month if year == first_year else 1
        last = last_month if year == last_year else 12

        periods = [
            f"{year:04d}-{month:02d}"
            for month in range(first, last + 1)
        ]

    df = filter_outages(
        year=year,
        municipality_psgc=municipality_psgc,
        barangay_psgc=barangay_psgc,
        cause_category=cause_category,
    ).drop_duplicates(subset=["outage_id"])
    # Match KPI duration semantics: sum known event durations, and average
    # known durations only. These are event hours, not customer outage hours.
    durations = pd.to_numeric(df["duration_minutes"], errors="coerce")
    aggregated = (
        df.assign(
            duration_minutes=durations,
            period=(
                    df["year"].astype(str)
                    + "-"
                    + df["month"].astype(str).str.zfill(2)
            ),
        )
        .groupby("period")
        .agg(
            total_outages=("outage_id", "nunique"),
            total_minutes=("duration_minutes", "sum"),
            average_minutes=("duration_minutes", "mean"),
        )
        .reindex(periods)
    )

    points = []
    for period, row in aggregated.iterrows():
        average = row["average_minutes"]
        points.append({
            "period": str(period),
            "total_outages": (
                int(row["total_outages"]) if pd.notna(row["total_outages"]) else 0
            ),
            "total_duration_hours": (
                round(float(row["total_minutes"]) / 60, 2)
                if pd.notna(row["total_minutes"]) else 0.0
            ),
            "average_duration_minutes": (
                round(float(average), 2) if pd.notna(average) else None
            ),
        })
    return {"granularity": granularity, "points": points}

@app.get('/api/overview/kpis')
def get_overview_kpis(
        year: int | None = None,
        municipality_psgc: str | None = None,
        barangay_psgc: str | None = None,
        cause_category: str | None = None,
):
    df = filter_outages(
        year=year,
        municipality_psgc=municipality_psgc,
        barangay_psgc=barangay_psgc,
        cause_category=cause_category,
    )

    source_df = OUTAGES_DF

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

        if cause_category:
            previous = previous[
                previous["cause_category"] == cause_category
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

@app.get("/api/overview/causes")
def get_cause_distribution(
    year: int | None = None,
    municipality_psgc: str | None = None,
    barangay_psgc: str | None = None,
):
    df = filter_outages(
        year=year,
        municipality_psgc=municipality_psgc,
        barangay_psgc=barangay_psgc,
    )

    source_df = OUTAGES_DF

    if year is not None:
        df = df[df["year"] == year]

    if municipality_psgc:
        df = df[df["municipality_psgc"] == municipality_psgc]

    if barangay_psgc:
        df = df[df["barangay_psgc"] == barangay_psgc]

    causes = (
        df["cause_category"]
        .fillna("Other / Unknown")
        .value_counts()
    )

    total = int(causes.sum())

    return {
        "total_events": total,
        "causes": [
            {
                "name": name,
                "value": int(count),
                "percentage": round(count / total * 100, 2),
            }
            for name, count in causes.items()
        ],
    }


@app.get("/api/overview/geography")
def get_overview_geography(
    year: int | None = None,
    cause_category: str | None = None,
):
    df = filter_outages(
        year=year,
        cause_category=cause_category,
    ).copy()

    df["duration_minutes"] = pd.to_numeric(
        df["duration_minutes"],
        errors="coerce",
    )

    # Aggregate outage metrics by geographic code.
    def summarize(column):
        return df.groupby(column).agg(
            events=("outage_id", "nunique"),
            minutes=("duration_minutes", "sum"),
        )

    by_municipality = summarize("municipality_psgc")
    by_barangay = summarize("barangay_psgc")

    # Return zero values for areas without outages.
    def get_metrics(grouped, code):
        if code not in grouped.index:
            return {
                "total_outages": 0,
                "total_duration_hours": 0.0,
            }

        row = grouped.loc[code]

        return {
            "total_outages": int(row["events"]),
            "total_duration_hours": round(
                float(row["minutes"]) / 60, 2
            ),
        }

    # Unique municipality identifiers from the GeoJSON.
    municipalities = {
        p["municipality_psgc"]: p["municipality"]
        for p in BOUNDARY_PROPERTIES
    }

    municipality_results = [
        {
            "municipality_psgc": code,
            "municipality": name,
            **get_metrics(by_municipality, code),
        }
        for code, name in sorted(municipalities.items())
    ]

    barangay_results = [
        {
            "barangay_psgc": p["barangay_psgc"],
            "barangay": p["barangay"],
            "municipality_psgc": p["municipality_psgc"],
            "municipality": p["municipality"],
            **get_metrics(
                by_barangay,
                p["barangay_psgc"],
            ),
        }
        for p in BOUNDARY_PROPERTIES
    ]

    return {
        "municipalities": municipality_results,
        "barangays": barangay_results,
    }

@app.get("/api/overview/boundaries")
def get_overview_boundaries(
    level: Literal["municipality", "barangay"] = "barangay",
):
    if level == "municipality":
        return municipality_boundaries

    return boundaries