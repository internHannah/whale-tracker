from datetime import datetime, timezone
from typing import List, Optional
import os
import time
import httpx
from dotenv import load_dotenv

from .schemas import WhaleTransfer

load_dotenv()
ALCHEMY_API_KEY = os.getenv("ALCHEMY_API_KEY")

def _get_alchemy_url() -> str:
    if not ALCHEMY_API_KEY:
        raise RuntimeError("ALCHEMY_API_KEY is missing")
    return f"https://eth-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}"

CACHE_TTL = 30  # seconds
_last_fetch_ts: float = 0.0
_last_fetch_data: List[WhaleTransfer] = []


def fetch_whales(limit: int = 400, min_amount: float = 100.0) -> List[WhaleTransfer]:
    """
    High-level function used by the API.
    - Uses a 30s cache so we don't spam Alchemy.
    - Lets the caller pick limit and min_amount (in *token units*).
    """
    global _last_fetch_ts, _last_fetch_data

    now = time.time()

    if _last_fetch_data and (now - _last_fetch_ts) < CACHE_TTL:
        filtered = [t for t in _last_fetch_data if t.amount >= min_amount]
        return filtered[: min(limit, len(filtered))]

    limit = min(limit, 1000)

    fresh = fetch_whale_transfers_from_provider(limit=limit, min_amount=min_amount)

    _last_fetch_data = fresh
    _last_fetch_ts = now

    filtered = [t for t in fresh if t.amount >= min_amount]
    return filtered[: min(limit, len(filtered))]


def fetch_whale_transfers_from_provider(
    limit: int = 200,
    min_amount: float = 100.0,
) -> List[WhaleTransfer]:
    """
    Fetch large transfers for:
      - native ETH (external)
      - ERC-20 tokens (erc20)
    Then keep only ETH, USDC, USDT, WBTC and apply min_amount.
    """
    url = _get_alchemy_url()

    def _call_alchemy(categories: list[str]) -> list[dict]:
        max_rows_per_category = 500 
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "alchemy_getAssetTransfers",
            "params": [
                {
                    "fromBlock": "0x0",
                    "toBlock": "latest",
                    "category": categories,
                    "withMetadata": True,
                    "excludeZeroValue": True,
                    "maxCount": hex(max_rows_per_category),
                    "order": "desc",
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
        return data.get("result", {}).get("transfers", [])

    # 🔹 separate calls
    raw_eth   = _call_alchemy(["external"])
    raw_erc20 = _call_alchemy(["erc20"])

    raw_transfers = raw_eth + raw_erc20

    TRACKED_ASSETS = {"ETH", "USDC", "USDT", "WBTC"}
    transfers: List[WhaleTransfer] = []

    for item in raw_transfers:
        amount = item.get("value")
        if amount is None:
            continue
        try:
            amount_float = float(amount)
        except (TypeError, ValueError):
            continue

        if amount_float < min_amount:
            continue

        raw_contract = item.get("rawContract") or {}
        token_addr = raw_contract.get("address")  
        raw_symbol = item.get("asset")

        if token_addr is None:
            asset_symbol = "ETH"
        else:
            asset_symbol = (raw_symbol or "UNKNOWN").upper()

        if asset_symbol not in TRACKED_ASSETS:
            continue

        block_hex = item.get("blockNum")
        block_num = int(block_hex, 16) if block_hex else 0

        wt = WhaleTransfer(
            tx_hash=item.get("hash", ""),
            from_address=item.get("from", ""),
            to_address=item.get("to", ""),
            token_symbol=asset_symbol,
            token_address=token_addr,
            amount=amount_float,
            usd_value=None,
            chain="eth",
            block_number=block_num,
            timestamp=None,
            observed_at=datetime.now(timezone.utc),
        )
        transfers.append(wt)

    transfers.sort(key=lambda t: t.block_number or 0, reverse=True)
    return transfers[:limit]  
