from collections import defaultdict
from fastapi import APIRouter, Query
from typing import List, Optional
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


client = OpenAI()
router = APIRouter(prefix="/alerts", tags=["alerts"])


# -----------------------------
# Helpers
# -----------------------------
def _filter_by_token(transfers, token: Optional[str]):
    if not token:
        return transfers
    token_upper = token.upper()
    return [t for t in transfers if (t.token_symbol or "").upper() == token_upper]


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
        for tok, s in sorted(by_token.items(), key=lambda kv: kv[1]["volume"], reverse=True)
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


# -----------------------------
# /alerts/latest
# -----------------------------
@router.get("/latest", response_model=WhaleTransferList)
def latest_alerts(
    limit: int = 200,
    min_amount: float = 100.0,
    token: Optional[str] = None,
):
    transfers = whale_service.fetch_whales(
        min_amount=min_amount,
        limit=limit,
    )

    transfers = _filter_by_token(transfers, token)

    if not transfers:
        return WhaleTransferList(
            transfers=[],
            count=0,
            summary=f"No whale transfers found for token={token or 'ALL'}.",
        )

    return WhaleTransferList(
        transfers=transfers,
        count=len(transfers),
        summary=(
            f"Showing up to {len(transfers)} transfers"
            + (f" in {token.upper()}" if token else "")
            + f" (min_amount={min_amount}, limit={limit})."
        ),
    )


# -----------------------------
# /alerts/stats
# -----------------------------
@router.get("/stats", response_model=AlertsStats)
def alerts_stats(
    limit: int = Query(200, ge=1, le=1000),
    min_amount: float = 0.0,
    token: Optional[str] = None,
):
    """Structured per-token aggregates without calling an LLM."""
    transfers = whale_service.fetch_whales(
        min_amount=min_amount,
        limit=limit,
    )
    transfers = _filter_by_token(transfers, token)

    return AlertsStats(
        transfer_count=len(transfers),
        unique_wallets=_unique_wallet_count(transfers),
        by_token=_aggregate_by_token(transfers),
        token_filter=token.upper() if token else None,
    )


# -----------------------------
# /alerts/summary
# -----------------------------
@router.get("/summary", response_model=AlertsSummary)
def alerts_summary(
    limit: int = 20,
    min_amount_eth: float = 0.0,
    token: Optional[str] = None,
):
    limit = max(1, min(limit, 1000))

    transfers = whale_service.fetch_whales(
        min_amount=min_amount_eth,
        limit=limit,
    )
    transfers = _filter_by_token(transfers, token)

    if not transfers:
        return AlertsSummary(
            summary=f"No recent transfers found for {token or 'this slice'}.",
            transfer_count=0,
        )

    stats = _aggregate_by_token(transfers)
    agg_text = _format_agg_text(stats)
    sample_text = _format_sample_text(transfers)
    tok_phrase = _token_phrase(token)
    snapshot_size = len(transfers)

    system_msg = (
        "You are an on-chain crypto analyst. "
        "Use the aggregated stats and sample transfers to describe patterns "
        "across tokens. If evidence is weak, say so."
    )

    user_msg = (
        f"This snapshot contains {snapshot_size} {tok_phrase} transfers "
        f"across {_unique_wallet_count(transfers)} unique wallets.\n\n"
        f"Aggregated stats by token:\n{agg_text}\n\n"
        f"Sample transfers:\n{sample_text}\n\n"
        "In 3–5 sentences, summarize the main patterns, including how ETH vs "
        "stablecoins vs WBTC behave in this window."
    )

    chat = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.4,
    )

    summary_text = chat.choices[0].message.content.strip()

    return AlertsSummary(
        summary=summary_text,
        transfer_count=snapshot_size,
    )


# -----------------------------
# /alerts/chat
# -----------------------------
@router.post("/chat", response_model=ChatResponse)
def alerts_chat(
    payload: ChatRequest,
    limit: int = Query(100, ge=20, le=1000),
    token: Optional[str] = None,
):
    limit = min(limit, 1000)

    transfers = whale_service.fetch_whales(
        min_amount=0.0,
        limit=limit,
    )

    transfers = _filter_by_token(transfers, token)

    if not transfers:
        return ChatResponse(
            answer=f"No transfers found for token={token or 'ALL'}, so I cannot analyze flows.",
            transfer_count=0,
        )

    stats = _aggregate_by_token(transfers)
    agg_text = _format_agg_text(stats)
    sample_text = _format_sample_text(transfers)
    tok_phrase = _token_phrase(token)

    system_msg = (
        "You are an on-chain crypto analyst assisting a PM. "
        "Answer questions based strictly on the flows provided. "
        "If multiple token types exist, compare them. "
        "If something is uncertain or speculative, say so explicitly."
    )

    user_msg = (
        f"Recent large {tok_phrase} transfers "
        f"({len(transfers)} rows, {_unique_wallet_count(transfers)} unique wallets):\n\n"
        f"Aggregated stats by token:\n{agg_text}\n\n"
        f"Sample transfers:\n{sample_text}\n\n"
        f"The user asks: {payload.question}\n\n"
        "Answer in 3–6 sentences."
    )

    chat = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.5,
    )

    answer_text = chat.choices[0].message.content.strip()

    return ChatResponse(
        answer=answer_text,
        transfer_count=len(transfers),
    )
