from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set
import os
import time
import httpx
from dotenv import load_dotenv

from .schemas import WhaleTransfer


load_dotenv()
ALCHEMY_API_KEY = os.getenv("ALCHEMY_API_KEY")

TRACKED_ASSETS = frozenset({"ETH", "USDC", "USDT", "WBTC"})
CACHE_TTL = 30  # seconds
PROVIDER_LIMIT = 1000
MAX_ROWS_PER_CATEGORY = 500

_last_fetch_ts: float = 0.0
_last_fetch_data: List[WhaleTransfer] = []


def _get_alchemy_url() -> str:
    if not ALCHEMY_API_KEY:
        raise RuntimeError("ALCHEMY_API_KEY is missing")
    return f"https://eth-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}"


def _parse_timestamp(item: Dict) -> Optional[datetime]:
    metadata = item.get("metadata") or {}
    raw = metadata.get("blockTimestamp")
    if not raw:
        return None
    try:
        # Alchemy returns e.g. "2024-01-15T12:34:56.000Z"
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def fetch_whales(limit: int = 400, min_amount: float = 100.0) -> List[WhaleTransfer]:
    """
    High-level function used by the API.
    - Uses a 30s cache so we don't spam Alchemy.
    - Cache stores an unfiltered snapshot; callers filter by min_amount/limit.
    """
    global _last_fetch_ts, _last_fetch_data

    now = time.time()
    limit = max(1, min(limit, PROVIDER_LIMIT))

    if not _last_fetch_data or (now - _last_fetch_ts) >= CACHE_TTL:
        _last_fetch_data = fetch_whale_transfers_from_provider(
            limit=PROVIDER_LIMIT,
            min_amount=0.0,
        )
        _last_fetch_ts = now

    filtered = [t for t in _last_fetch_data if t.amount >= min_amount]
    return filtered[:limit]


def fetch_whale_transfers_from_provider(
    limit: int = 200,
    min_amount: float = 100.0,
) -> List[WhaleTransfer]:
    """
    Fetch large transfers for native ETH and ERC-20 tokens,
    keep only TRACKED_ASSETS, then apply min_amount.
    """
    url = _get_alchemy_url()

    def _call_alchemy(categories: List[str]) -> List[dict]:
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
                    "maxCount": hex(MAX_ROWS_PER_CATEGORY),
                    "order": "desc",
                }
            ],
        }
        try:
            res = httpx.post(url, json=payload, timeout=15.0)
            res.raise_for_status()
            data = res.json()
        except httpx.HTTPError as e:
            print("Alchemy API error:", e)
            return []

        if "error" in data:
            print("Alchemy RPC error:", data["error"])
            return []

        return data.get("result", {}).get("transfers", []) or []

    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_eth = pool.submit(_call_alchemy, ["external"])
        fut_erc20 = pool.submit(_call_alchemy, ["erc20"])
        raw_transfers = fut_eth.result() + fut_erc20.result()

    transfers: List[WhaleTransfer] = []
    seen_keys: Set[str] = set()

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

        tx_hash = item.get("hash") or ""
        from_addr = item.get("from") or ""
        to_addr = item.get("to") or ""
        dedupe_key = f"{tx_hash}|{asset_symbol}|{from_addr}|{to_addr}|{amount_float}"
        if not tx_hash or dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)

        block_hex = item.get("blockNum")
        try:
            block_num = int(block_hex, 16) if block_hex else None
        except (TypeError, ValueError):
            block_num = None

        transfers.append(
            WhaleTransfer(
                tx_hash=tx_hash,
                from_address=from_addr,
                to_address=to_addr,
                token_symbol=asset_symbol,
                token_address=token_addr,
                amount=amount_float,
                usd_value=None,
                chain="eth",
                block_number=block_num,
                timestamp=_parse_timestamp(item),
                observed_at=datetime.now(timezone.utc),
            )
        )

    transfers.sort(key=lambda t: t.block_number or 0, reverse=True)
    return transfers[: max(1, min(limit, PROVIDER_LIMIT))]
