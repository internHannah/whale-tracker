AI Whale Watch

AI Whale Watch is a lightweight Ethereum whale-tracking dashboard that combines a FastAPI backend, a static HTML/JS frontend, and AI-generated research analysis. It displays recent large ETH transfers, provides wallet drill-down analytics, and can generate downloadable reports or data exports.

Project Structure

whale-tracker/
│
├── app/                     # FastAPI backend application
│   ├── api.py               # API route definitions (/alerts, /summary, /chat)
│   ├── db.py                # SQLite database connection and initialization
│   ├── models.py            # SQLAlchemy ORM models (Transfer table)
│   ├── schemas.py           # Pydantic schemas for API responses
│   ├── whale_service.py     # Fetches recent ETH transfers from provider
│   └── __init__.py
│
├── static/
│   └── index.html           # Frontend UI (table, analytics, report panel)
│
├── main.py                  # FastAPI entry point
├── whales.db                # SQLite database file
├── requirements.txt         # Python dependency list
├── .env                     # Environment variables (not committed)
└── .gitignore

Features
Whale Monitoring

Fetches 100–200 recent ETH transfers.

Table view with min-amount filter and pattern filter.

Pagination (10 rows per page).

Address links to Etherscan.

Clickable wallet addresses for drill-downs.

Wallet Drill-Down Analytics

Displays, for any selected wallet:

Inflow

Outflow

Net flow

Transfer count

Heuristic role classification (accumulator / distributor / balanced)

AI Analysis

AI-generated Market Snapshot summary.

Chat assistant for asking questions about current flows.

Snapshot report generator (ready to share with a PM or research team).

Downloads

Single dropdown with:

Download data (.csv)

Download AI report (.txt)

Print/Save as PDF

Backend Setup (FastAPI)
1. Clone the repository
git clone https://github.com/<your-username>/whale-tracker.git
cd whale-tracker

2. Create a virtual environment
python3 -m venv venv
source venv/bin/activate

3. Install dependencies
pip install -r requirements.txt

4. Create a .env file

The backend uses environment variables for API keys:

ETHERSCAN_API_KEY=your_key_here
OPENAI_API_KEY=your_key_here
DATABASE_URL=sqlite:///./whales.db

5. Run the FastAPI app
uvicorn main:app --reload

6. Access the API

API root: http://127.0.0.1:8000

Swagger docs: http://127.0.0.1:8000/docs

Frontend: http://127.0.0.1:8000/static/index.html

Frontend

The frontend is static and lives in /static/index.html.

If FastAPI is running, visit:

http://127.0.0.1:8000/static/index.html


If you want to serve it manually:

cd static
python3 -m http.server 8080


Note: Serving the static frontend alone will cause CORS failures unless the backend is available.

API Endpoints Overview
GET /alerts/latest

Fetches the most recent ETH transfers.
Query params:

limit (default 100)

min_amount

GET /alerts/summary

Returns the AI-generated Market Snapshot.

POST /alerts/chat

Request body:

{ "question": "Why are there repeated 100 ETH transfers?" }

More endpoints:

See /docs for full auto-generated documentation.

Database Schema

SQLite database created automatically on first run.

Table: transfers

Column	Type	Description
id	Integer	Primary key
amount	Float	Transfer amount in ETH
token_symbol	Text	Typically "ETH"
from_address	Text	Sender address
to_address	Text	Receiver address
block_number	Integer	Block number
observed_at	DateTime	Timestamp

Reset database:

rm whales.db

Deployment Guide
Recommended (Simple)

Use Render.com or Railway.app to deploy the FastAPI server.

Deploy main.py as a web service

Expose port 8000

Include your .env values in their environment variable panel

To deploy the frontend:

Option 1: Keep /static served by FastAPI (no CORS issues).
Option 2: Host static/index.html separately on:

Vercel

Netlify

GitHub Pages

(If separate, configure CORS accordingly.)

Testing

Fetch latest transfers:

curl "http://127.0.0.1:8000/alerts/latest?limit=20"


Ask a question:

curl -X POST http://127.0.0.1:8000/alerts/chat \
  -H "Content-Type: application/json" \
  -d '{"question": "Any patterns today?"}'