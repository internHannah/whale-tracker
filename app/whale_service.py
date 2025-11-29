from datetime import datetime, timezone
from typing import List, Optional
import os
import time
from typing import List, Optional
import httpx
from dotenv import load_dotenv

from .schemas import WhaleTransfer

# Load environment variables
load_dotenv()

ALCHEMY_API_KEY = os.getenv("ALCHEMY_API_KEY")


def _get_alchemy_url() -> str:
    if not ALCHEMY_API_KEY:
        raise RuntimeError("ALCHEMY_API_KEY is missing")
    return f"https://eth-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}"

# ---- Simple in-process cache for whale fetches ----

CACHE_TTL = 30  # seconds

_last_fetch_ts: float = 0.0
_last_fetch_data: List[WhaleTransfer] = []


def fetch_whales(limit: int = 200, min_amount: float = 100.0) -> List[WhaleTransfer]:
    """
    High-level function used by the API.
    - Uses a 30s cache so we don't spam Alchemy.
    - Lets the caller pick limit and min_amount.
    """
    global _last_fetch_ts, _last_fetch_data

    now = time.time()

    # 1) Return from cache if fresh
    if _last_fetch_data and (now - _last_fetch_ts) < CACHE_TTL:
        filtered = [t for t in _last_fetch_data if t.amount >= min_amount]
        return filtered[: min(limit, len(filtered))]

    # 2) Otherwise, hit Alchemy
    # safety cap so you don't accidentally pull too much
    limit = min(limit, 1000)

    fresh = fetch_whale_transfers_from_provider(limit=limit, min_amount=min_amount)

    # update cache with the raw results from provider
    _last_fetch_data = fresh
    _last_fetch_ts = now

    # just in case, filter + respect limit
    filtered = [t for t in fresh if t.amount >= min_amount]
    return filtered[: min(limit, len(filtered))]

def fetch_whale_transfers_from_provider(
    limit: int = 200,
    min_amount: float = 100.0,  # ETH threshold
) -> List[WhaleTransfer]:
    # Step 1: Build URL
    url = _get_alchemy_url()

    # Step 2: Build the request payload
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "alchemy_getAssetTransfers",
        "params": [
            {
                "fromBlock": "0x0",
                "toBlock": "latest",
                "category": ["external"],  # ETH transfers
                "withMetadata": True,
                "maxCount": hex(limit),
            }
        ],
    }

    try:
        res = httpx.post(url, json=payload, timeout=10)
        res.raise_for_status()
    except httpx.HTTPError as e:
        print("Alchemy API error:", e)
        return []

    data = res.json()
    raw_transfers = data.get("result", {}).get("transfers", [])

    transfers: List[WhaleTransfer] = []

    for item in raw_transfers:
        amount = item.get("value")
        if not amount:
            continue

        amount_float = float(amount)

        # use the parameter instead of hard-coded 100
        if amount_float < min_amount:
            continue

        block_hex = item.get("blockNum")
        block_num = int(block_hex, 16) if block_hex else None

        wt = WhaleTransfer(
            tx_hash=item.get("hash", ""),
            from_address=item.get("from", ""),
            to_address=item.get("to", ""),
            token_symbol=item.get("asset", "ETH"),
            token_address=None,
            amount=amount_float,
            usd_value=None,
            chain="eth",
            block_number=block_num,
            timestamp=None,
            observed_at=datetime.now(timezone.utc),
        )

        transfers.append(wt)

    return transfers
