from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set
import logging
import os
import threading
import time

import httpx
from dotenv import load_dotenv

from .schemas import WhaleTransfer


load_dotenv()
logger = logging.getLogger(__name__)

ALCHEMY_API_KEY = os.getenv("ALCHEMY_API_KEY")

TRACKED_ASSETS = frozenset({"ETH", "USDC", "USDT", "WBTC"})
CACHE_TTL = 30  # seconds
PROVIDER_LIMIT = 1000
MAX_ROWS_PER_CATEGORY = 500

_cache_lock = threading.Lock()
_last_fetch_ts: float = 0.0
_last_fetch_data: List[WhaleTransfer] = []
_last_source: str = "empty"


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
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def cache_status() -> dict:
    with _cache_lock:
        age = None
        if _last_fetch_ts:
            age = round(time.time() - _last_fetch_ts, 1)
        return {
            "cache_size": len(_last_fetch_data),
            "cache_age_seconds": age,
            "cache_source": _last_source,
            "cache_ttl_seconds": CACHE_TTL,
        }


def _orm_to_schema(row) -> WhaleTransfer:
    return WhaleTransfer(
        tx_hash=row.tx_hash,
        from_address=row.from_address,
        to_address=row.to_address,
        token_symbol=row.token_symbol,
        token_address=row.token_address,
        amount=row.amount,
        usd_value=row.usd_value,
        chain=row.chain or "eth",
        block_number=row.block_number,
        timestamp=row.timestamp,
        observed_at=row.observed_at,
    )


def save_transfers(transfers: List[WhaleTransfer]) -> int:
    """Upsert transfers into SQLite. Returns number of newly inserted rows."""
    if not transfers:
        return 0

    from .db import SessionLocal
    from .models import WhaleTransferORM

    inserted = 0
    db = SessionLocal()
    try:
        for t in transfers:
            exists = (
                db.query(WhaleTransferORM.id)
                .filter_by(
                    tx_hash=t.tx_hash,
                    token_symbol=t.token_symbol,
                    from_address=t.from_address,
                    to_address=t.to_address,
                    amount=t.amount,
                )
                .first()
            )
            if exists:
                continue
            db.add(
                WhaleTransferORM(
                    tx_hash=t.tx_hash,
                    from_address=t.from_address,
                    to_address=t.to_address,
                    token_symbol=t.token_symbol,
                    token_address=t.token_address,
                    amount=t.amount,
                    usd_value=t.usd_value,
                    chain=t.chain,
                    block_number=t.block_number,
                    timestamp=t.timestamp,
                    observed_at=t.observed_at or datetime.now(timezone.utc),
                )
            )
            inserted += 1
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Failed to persist whale transfers")
        return 0
    finally:
        db.close()
    return inserted


def load_transfers_from_db(limit: int = PROVIDER_LIMIT) -> List[WhaleTransfer]:
    from .db import SessionLocal
    from .models import WhaleTransferORM

    db = SessionLocal()
    try:
        rows = (
            db.query(WhaleTransferORM)
            .order_by(WhaleTransferORM.block_number.desc().nullslast())
            .limit(max(1, min(limit, PROVIDER_LIMIT)))
            .all()
        )
        return [_orm_to_schema(row) for row in rows]
    except Exception:
        logger.exception("Failed to load whale transfers from DB")
        return []
    finally:
        db.close()


def fetch_whales(limit: int = 400, min_amount: float = 100.0) -> List[WhaleTransfer]:
    """
    High-level function used by the API.
    - Thread-safe 30s cache of an unfiltered snapshot.
    - Persists successful Alchemy pulls; falls back to SQLite when needed.
    """
    global _last_fetch_ts, _last_fetch_data, _last_source

    now = time.time()
    limit = max(1, min(limit, PROVIDER_LIMIT))

    with _cache_lock:
        if not _last_fetch_data or (now - _last_fetch_ts) >= CACHE_TTL:
            fresh: List[WhaleTransfer] = []
            try:
                fresh = fetch_whale_transfers_from_provider(
                    limit=PROVIDER_LIMIT,
                    min_amount=0.0,
                )
            except Exception:
                logger.exception("Alchemy provider fetch failed")

            if fresh:
                _last_fetch_data = fresh
                _last_fetch_ts = now
                _last_source = "alchemy"
                saved = save_transfers(fresh)
                if saved:
                    logger.info("Persisted %s new whale transfers", saved)
            elif not _last_fetch_data:
                db_rows = load_transfers_from_db(PROVIDER_LIMIT)
                if db_rows:
                    _last_fetch_data = db_rows
                    _last_fetch_ts = now
                    _last_source = "sqlite"
                    logger.warning("Serving whale transfers from SQLite fallback")
            else:
                # Keep serving stale cache; refresh timestamp to avoid hammering Alchemy.
                _last_fetch_ts = now
                _last_source = "stale-cache"
                logger.warning("Alchemy returned no rows; continuing with stale cache")

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
            logger.warning("Alchemy API error: %s", e)
            return []

        if "error" in data:
            logger.warning("Alchemy RPC error: %s", data["error"])
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
