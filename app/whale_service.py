from datetime import datetime, timezone
from typing import List
import os

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


def fetch_whale_transfers_from_provider(
    min_usd_value: float = 500_000,
    limit: int = 20
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
                "maxCount": hex(limit)
            }
        ],
    }

    # Step 3: Make the request
    try:
        res = httpx.post(url, json=payload, timeout=10)
        res.raise_for_status()
    except httpx.HTTPError as e:
        print("Alchemy API error:", e)
        return []

    data = res.json()
    raw_transfers = data.get("result", {}).get("transfers", [])

    transfers: List[WhaleTransfer] = []

    # Step 4: Convert each transfer into your WhaleTransfer format
    for item in raw_transfers:
        amount = item.get("value")
        if not amount:
            continue

        amount_float = float(amount)

        # Whale threshold: only keep big transfers
        if amount_float < 100:  # 100 ETH ~= whale
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
            observed_at=datetime.now(timezone.utc)
        )

        transfers.append(wt)

    return transfers
