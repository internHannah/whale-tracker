from collections import defaultdict
import logging
import os
import threading
import time
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query
from openai import OpenAI

from .schemas import (
    WhaleTransferList,
    AlertsSummary,
    AlertsStats,
    TokenStat,
    ChatRequest,
    ChatResponse,
)
from . import whale_service


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/alerts", tags=["alerts"])
_openai_client: Optional[OpenAI] = None

SUMMARY_CACHE_TTL = 60  # seconds
_summary_cache_lock = threading.Lock()
_summary_cache: Dict[Tuple, Tuple[float, str, int]] = {}


def _get_openai() -> OpenAI:
    global _openai_client
    if not os.getenv("OPENAI_API_KEY"):
        raise HTTPException(
            status_code=503,
            detail="OPENAI_API_KEY is not configured.",
        )
    if _openai_client is None:
        _openai_client = OpenAI()
    return _openai_client


def _normalize_token(token: Optional[str]) -> Optional[str]:
    if not token:
        return None
    token_upper = token.strip().upper()
    if token_upper not in whale_service.TRACKED_ASSETS:
        allowed = ", ".join(sorted(whale_service.TRACKED_ASSETS))
        raise HTTPException(
            status_code=400,
            detail=f"Unknown token '{token}'. Use one of: {allowed}.",
        )
    return token_upper


def _load_transfers(
    *,
    limit: int,
    min_amount: float = 0.0,
    token: Optional[str] = None,
):
    return whale_service.fetch_whales(
        min_amount=min_amount,
        limit=limit,
        token=_normalize_token(token),
    )


def _summary_cache_get(key: Tuple) -> Optional[Tuple[str, int]]:
    now = time.time()
    with _summary_cache_lock:
        entry = _summary_cache.get(key)
        if not entry:
            return None
        ts, text, count = entry
        if now - ts > SUMMARY_CACHE_TTL:
            _summary_cache.pop(key, None)
            return None
        return text, count


def _summary_cache_set(key: Tuple, text: str, count: int) -> None:
    with _summary_cache_lock:
        _summary_cache[key] = (time.time(), text, count)
        if len(_summary_cache) > 32:
            oldest = min(_summary_cache.items(), key=lambda item: item[1][0])[0]
            _summary_cache.pop(oldest, None)


def _token_phrase(token: Optional[str]) -> str:
    if not token:
        return "ETH, USDC, USDT, and WBTC"
    return token.upper()


def _short_addr(addr: Optional[str]) -> str:
    if not addr:
        return ""
    if len(addr) < 10:
        return addr
    return f"{addr[:6]}...{addr[-4:]}"


def _aggregate_by_token(transfers) -> List[TokenStat]:
    by_token = defaultdict(lambda: {"count": 0, "volume": 0.0, "largest": 0.0})
    for t in transfers:
        tok = (t.token_symbol or "ETH").upper()
        amt = float(t.amount or 0)
        s = by_token[tok]
        s["count"] += 1
        s["volume"] += amt
        if amt > s["largest"]:
            s["largest"] = amt

    return [
        TokenStat(
            token=tok,
            count=s["count"],
            volume=round(s["volume"], 4),
            largest=round(s["largest"], 4),
        )
        for tok, s in sorted(
            by_token.items(), key=lambda kv: kv[1]["volume"], reverse=True
        )
    ]


def _unique_wallet_count(transfers) -> int:
    wallets = set()
    for t in transfers:
        if t.from_address:
            wallets.add(t.from_address.lower())
        if t.to_address:
            wallets.add(t.to_address.lower())
    return len(wallets)


def _format_agg_text(stats: List[TokenStat]) -> str:
    if not stats:
        return "No meaningful volume."
    return "\n".join(
        f"{s.token}: {s.count} transfers, total {s.volume:.2f} {s.token}, "
        f"largest {s.largest:.2f} {s.token}"
        for s in stats
    )


def _format_sample_text(transfers, max_rows: int = 20) -> str:
    lines = []
    for t in transfers[:max_rows]:
        lines.append(
            f"- {t.amount} {t.token_symbol} from {_short_addr(t.from_address)} "
            f"to {_short_addr(t.to_address)} (block {t.block_number})"
        )
    return "\n".join(lines)


def _ask_llm(
    *,
    system_msg: str,
    user_msg: str,
    temperature: float = 0.4,
) -> str:
    try:
        chat = _get_openai().chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            temperature=temperature,
        )
    except Exception:
        logger.exception("LLM request failed")
        raise HTTPException(
            status_code=502,
            detail="LLM request failed.",
        )

    content = chat.choices[0].message.content
    if not content:
        raise HTTPException(status_code=502, detail="LLM returned an empty response.")
    return content.strip()


@router.get("/latest", response_model=WhaleTransferList)
def latest_alerts(
    limit: int = Query(200, ge=1, le=1000),
    min_amount: float = Query(100.0, ge=0),
    token: Optional[str] = None,
):
    normalized = _normalize_token(token)
    transfers = _load_transfers(limit=limit, min_amount=min_amount, token=normalized)

    if not transfers:
        return WhaleTransferList(
            transfers=[],
            count=0,
            summary=f"No whale transfers found for token={normalized or 'ALL'}.",
        )

    return WhaleTransferList(
        transfers=transfers,
        count=len(transfers),
        summary=(
            f"Showing up to {len(transfers)} transfers"
            + (f" in {normalized}" if normalized else "")
            + f" (min_amount={min_amount}, limit={limit})."
        ),
    )


@router.get("/stats", response_model=AlertsStats)
def alerts_stats(
    limit: int = Query(200, ge=1, le=1000),
    min_amount: float = Query(0.0, ge=0),
    token: Optional[str] = None,
):
    """Structured per-token aggregates without calling an LLM."""
    normalized = _normalize_token(token)
    transfers = _load_transfers(limit=limit, min_amount=min_amount, token=normalized)

    return AlertsStats(
        transfer_count=len(transfers),
        unique_wallets=_unique_wallet_count(transfers),
        by_token=_aggregate_by_token(transfers),
        token_filter=normalized,
    )


@router.get("/summary", response_model=AlertsSummary)
def alerts_summary(
    limit: int = Query(20, ge=1, le=1000),
    min_amount: float = Query(0.0, ge=0),
    min_amount_eth: Optional[float] = Query(None, ge=0),
    token: Optional[str] = None,
):
    normalized = _normalize_token(token)
    amount = min_amount_eth if min_amount_eth is not None else min_amount
    cache_key = (limit, amount, normalized)

    cached = _summary_cache_get(cache_key)
    if cached:
        summary_text, transfer_count = cached
        return AlertsSummary(summary=summary_text, transfer_count=transfer_count)

    transfers = _load_transfers(limit=limit, min_amount=amount, token=normalized)

    if not transfers:
        return AlertsSummary(
            summary=f"No recent transfers found for {normalized or 'this slice'}.",
            transfer_count=0,
        )

    stats = _aggregate_by_token(transfers)
    snapshot_size = len(transfers)

    summary_text = _ask_llm(
        system_msg=(
            "You are an on-chain crypto analyst. "
            "Use the aggregated stats and sample transfers to describe patterns "
            "across tokens. If evidence is weak, say so."
        ),
        user_msg=(
            f"This snapshot contains {snapshot_size} {_token_phrase(normalized)} transfers "
            f"across {_unique_wallet_count(transfers)} unique wallets.\n\n"
            f"Aggregated stats by token:\n{_format_agg_text(stats)}\n\n"
            f"Sample transfers:\n{_format_sample_text(transfers)}\n\n"
            "In 3–5 sentences, summarize the main patterns, including how ETH vs "
            "stablecoins vs WBTC behave in this window."
        ),
        temperature=0.4,
    )
    _summary_cache_set(cache_key, summary_text, snapshot_size)

    return AlertsSummary(
        summary=summary_text,
        transfer_count=snapshot_size,
    )


@router.post("/chat", response_model=ChatResponse)
def alerts_chat(
    payload: ChatRequest,
    limit: int = Query(100, ge=20, le=1000),
    min_amount: float = Query(0.0, ge=0),
    token: Optional[str] = None,
):
    question = (payload.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question must not be empty.")

    normalized = _normalize_token(token)
    transfers = _load_transfers(limit=limit, min_amount=min_amount, token=normalized)

    if not transfers:
        return ChatResponse(
            answer=f"No transfers found for token={normalized or 'ALL'}, so I cannot analyze flows.",
            transfer_count=0,
        )

    stats = _aggregate_by_token(transfers)

    answer_text = _ask_llm(
        system_msg=(
            "You are an on-chain crypto analyst assisting a PM. "
            "Answer questions based strictly on the flows provided. "
            "If multiple token types exist, compare them. "
            "If something is uncertain or speculative, say so explicitly."
        ),
        user_msg=(
            f"Recent large {_token_phrase(normalized)} transfers "
            f"({len(transfers)} rows, {_unique_wallet_count(transfers)} unique wallets):\n\n"
            f"Aggregated stats by token:\n{_format_agg_text(stats)}\n\n"
            f"Sample transfers:\n{_format_sample_text(transfers)}\n\n"
            f"The user asks: {question}\n\n"
            "Answer in 3–6 sentences."
        ),
        temperature=0.5,
    )

    return ChatResponse(
        answer=answer_text,
        transfer_count=len(transfers),
    )
