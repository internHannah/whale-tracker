# AI Whale Watch

AI Whale Watch is a lightweight Ethereum whale-tracking dashboard that combines a FastAPI backend, a static HTML/JS frontend, and AI-generated research analysis.  
It displays recent large ETH transfers, provides wallet drill-down analytics, and can generate downloadable reports or data exports.

---

## Project Structure

```
whale-tracker/
│
├── app/                          # FastAPI backend application
│   ├── api.py                    # API route definitions (/alerts, /summary, /chat)
│   ├── db.py                     # SQLite database connection + initialization
│   ├── models.py                 # SQLAlchemy ORM models (Transfer table)
│   ├── schemas.py                # Pydantic response schemas
│   ├── whale_service.py          # Fetches large ETH transfers (provider stub)
│   └── __init__.py
│
├── static/
│   └── index.html                # Frontend UI (table, drill-down, analytics, report)
│
├── main.py                       # FastAPI entry point
├── whales.db                     # SQLite database file
├── requirements.txt              # Python dependencies
├── .env                          # API keys + configuration (not committed)
└── .gitignore
```

---

## Features

### Whale Monitoring

- Fetches ~1000 recent ETH transfers.
- Table view with:
  - Min-amount filter
  - Pattern filter
  - Pagination (20 rows per page)
  - Scrollable bar
  - Auto-refresh 
- Address links to Etherscan.
- Clickable wallet addresses populate drill-down analytics.

### Wallet Drill-Down Analytics

For any selected wallet:

- Total inflow
- Total outflow
- Net flow
- Number of transfers
- Heuristic wallet role (accumulator, distributor, balanced)

### AI-Generated Summary & Research Notes

- `/alerts/summary` uses an LLM to summarize the snapshot.
- Research report panel:
  - Generates structured PM-style snapshot notes
  - Download as `.txt`
  - Download data as `.csv`
  - Print/save as PDF

### Floating Q&A Assistant

- Small, collapsible chat widget
- Users can ask:  
  “Why are these transfers happening?”  
  “Does this look like exchange activity?”

---

## Run Locally

### 1. Create environment

python3 -m venv venv
source venv/bin/activate

### 2. Install dependencies

pip install -r requirements.txt

### 3. Add environment variables

Create a `.env` file:
OPENAI_API_KEY=your_key_here

### 4. Start the server

python main.py

### 5. Open the dashboard

Visit:
http://localhost:8000

---

## API Endpoints

### `GET /alerts/latest`

Returns recent ETH transfers.

### `GET /alerts/summary`

Returns AI-generated market snapshot.

### `POST /alerts/chat`

LLM Q&A about current flows.

---

## Deployment Notes

This app can be deployed on:

- Vercel (static + serverless)
- Render (FastAPI backend)
- Railway
- EC2 / Lightsail
- Docker container

Static UI requires no build step.

---

## License

MIT License.
