from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple
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
_http_lock = threading.Lock()
_last_fetch_ts: float = 0.0
_last_fetch_data: List[WhaleTransfer] = []
_last_source: str = "empty"
_refreshing: bool = False
_http_client: Optional[httpx.Client] = None


def _get_alchemy_url() -> str:
    if not ALCHEMY_API_KEY:
        raise RuntimeError("ALCHEMY_API_KEY is missing")
    return f"https://eth-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}"


def _get_http_client() -> httpx.Client:
    global _http_client
    if _http_client is None:
        with _http_lock:
            if _http_client is None:
                _http_client = httpx.Client(timeout=15.0)
    return _http_client


def close() -> None:
    global _http_client
    with _http_lock:
        if _http_client is not None:
            _http_client.close()
            _http_client = None


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
            "refreshing": _refreshing,
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


def _identity(t: WhaleTransfer) -> Tuple:
    return (t.tx_hash, t.token_symbol, t.from_address, t.to_address, t.amount)


def save_transfers(transfers: List[WhaleTransfer]) -> int:
    """Insert new transfers into SQLite. Returns number of newly inserted rows."""
    if not transfers:
        return 0

    from .db import SessionLocal
    from .models import WhaleTransferORM

    tx_hashes = list({t.tx_hash for t in transfers if t.tx_hash})
    db = SessionLocal()
    try:
        existing_rows = (
            db.query(
                WhaleTransferORM.tx_hash,
                WhaleTransferORM.token_symbol,
                WhaleTransferORM.from_address,
                WhaleTransferORM.to_address,
                WhaleTransferORM.amount,
            )
            .filter(WhaleTransferORM.tx_hash.in_(tx_hashes))
            .all()
        )
        existing_keys = set(existing_rows)

        inserted = 0
        for t in transfers:
            key = _identity(t)
            if key in existing_keys:
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
            existing_keys.add(key)
            inserted += 1

        if inserted:
            db.commit()
        else:
            db.rollback()
        return inserted
    except Exception:
        db.rollback()
        logger.exception("Failed to persist whale transfers")
        return 0
    finally:
        db.close()


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


def _slice_transfers(
    data: List[WhaleTransfer],
    *,
    min_amount: float,
    token: Optional[str],
    limit: int,
) -> List[WhaleTransfer]:
    token_upper = token.upper() if token else None
    out: List[WhaleTransfer] = []
    for t in data:
        if t.amount < min_amount:
            continue
        if token_upper and (t.token_symbol or "").upper() != token_upper:
            continue
        out.append(t)
        if len(out) >= limit:
            break
    return out


def fetch_whales(
    limit: int = 400,
    min_amount: float = 100.0,
    token: Optional[str] = None,
) -> List[WhaleTransfer]:
    """
    High-level function used by the API.
    - Thread-safe 30s cache of an unfiltered snapshot.
    - Network/DB work happens outside the lock so requests are not blocked.
    - Token filter is applied before the limit slice.
    """
    global _last_fetch_ts, _last_fetch_data, _last_source, _refreshing

    now = time.time()
    limit = max(1, min(limit, PROVIDER_LIMIT))

    with _cache_lock:
        cached = list(_last_fetch_data)
        stale = (not cached) or (now - _last_fetch_ts >= CACHE_TTL)
        if stale and not _refreshing:
            _refreshing = True
            should_refresh = True
        else:
            should_refresh = False

    if not should_refresh:
        return _slice_transfers(
            cached, min_amount=min_amount, token=token, limit=limit
        )

    fresh: List[WhaleTransfer] = []
    source = "stale-cache"
    try:
        try:
            fresh = fetch_whale_transfers_from_provider(
                limit=PROVIDER_LIMIT,
                min_amount=0.0,
            )
        except Exception:
            logger.exception("Alchemy provider fetch failed")

        if fresh:
            source = "alchemy"
            saved = save_transfers(fresh)
            if saved:
                logger.info("Persisted %s new whale transfers", saved)
        elif not cached:
            db_rows = load_transfers_from_db(PROVIDER_LIMIT)
            if db_rows:
                fresh = db_rows
                source = "sqlite"
                logger.warning("Serving whale transfers from SQLite fallback")
        else:
            logger.warning("Alchemy returned no rows; continuing with stale cache")
    finally:
        with _cache_lock:
            if fresh:
                _last_fetch_data = fresh
                _last_source = source
            elif cached:
                _last_source = "stale-cache"
            _last_fetch_ts = time.time()
            _refreshing = False
            snapshot = list(_last_fetch_data)

    return _slice_transfers(
        snapshot, min_amount=min_amount, token=token, limit=limit
    )


def fetch_whale_transfers_from_provider(
    limit: int = 200,
    min_amount: float = 100.0,
) -> List[WhaleTransfer]:
    """
    Fetch large transfers for native ETH and ERC-20 tokens,
    keep only TRACKED_ASSETS, then apply min_amount.
    """
    url = _get_alchemy_url()
    client = _get_http_client()

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
            res = client.post(url, json=payload)
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
