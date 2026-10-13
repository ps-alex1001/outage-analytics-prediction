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
