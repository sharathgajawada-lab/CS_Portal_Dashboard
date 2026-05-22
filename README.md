# CS Portal Analytics Dashboard

A FastAPI-powered analytics dashboard for the Hear.com CS Portal metrics.

## Setup

1. Clone the repo
2. Install dependencies: `pip install -r requirements.txt`
3. Set environment variable: `export CMS_API_KEY=your-api-key`
4. Run locally: `uvicorn main:app --reload`
5. Open: `http://localhost:8000`

## Deploy to Render

1. Push to GitHub
2. Create new Web Service on render.com
3. Connect your GitHub repo
4. Add environment variable: `CMS_API_KEY=your-api-key`
5. Deploy!

## Features

- Live metrics from Hear.com CMS
- Custom date range picker
- KPI cards with WoW and period-over-period comparisons
- Day of week patterns
- Activity heatmap
- Smart anomaly alerts
- Clickable filters
