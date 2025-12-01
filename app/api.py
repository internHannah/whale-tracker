from fastapi import APIRouter, Query
from typing import Optional
from openai import OpenAI

from .schemas import WhaleTransferList, AlertsSummary, ChatRequest, ChatResponse
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

    from collections import defaultdict

    by_token = defaultdict(lambda: {"count": 0, "volume": 0.0, "largest": 0.0})
    for t in transfers:
        tok = (t.token_symbol or "ETH").upper()
        amt = float(t.amount or 0)
        s = by_token[tok]
        s["count"] += 1
        s["volume"] += amt
        if amt > s["largest"]:
            s["largest"] = amt

    agg_lines = []
    for tok, s in by_token.items():
        agg_lines.append(
            f"{tok}: {s['count']} transfers, total {s['volume']:.2f} {tok}, "
            f"largest {s['largest']:.2f} {tok}"
        )
    agg_text = "\n".join(agg_lines) or "No meaningful volume."

    sample = transfers[: min(20, len(transfers))]
    sample_lines = []
    for t in sample:
        from_short = (t.from_address or "")[:6] + "..." + (t.from_address or "")[-4:]
        to_short = (
            (t.to_address or "")[:6] + "..." + (t.to_address or "")[-4:]
            if t.to_address
            else ""
        )
        sample_lines.append(
            f"- {t.amount} {t.token_symbol} from {from_short} to {to_short} (block {t.block_number})"
        )
    sample_text = "\n".join(sample_lines)

    tok_phrase = _token_phrase(token)
    snapshot_size = len(transfers)

    system_msg = (
        "You are an on-chain crypto analyst. "
        "Use the aggregated stats and sample transfers to describe patterns "
        "across tokens. If evidence is weak, say so."
    )

    user_msg = (
        f"This snapshot contains {snapshot_size} {tok_phrase} transfers.\n\n"
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

    lines = []
    for t in transfers:
        from_short = (t.from_address or "")[:6] + "..." + (t.from_address or "")[-4:]
        to_short = (t.to_address or "")[:6] + "..." + (t.to_address or "")[-4:] if t.to_address else ""
        lines.append(
            f"- {t.amount} {t.token_symbol} from {from_short} to {to_short} (block {t.block_number})"
        )
    transfers_text = "\n".join(lines)

    from collections import defaultdict

    by_token = defaultdict(lambda: {"count": 0, "volume": 0.0, "largest": 0.0})
    for t in transfers:
        tok = (t.token_symbol or "ETH").upper()
        amt = float(t.amount or 0)
        s = by_token[tok]
        s["count"] += 1
        s["volume"] += amt
        if amt > s["largest"]:
            s["largest"] = amt

    agg_lines = []
    for tok, s in by_token.items():
        agg_lines.append(
            f"{tok}: {s['count']} transfers, total {s['volume']:.2f} {tok}, "
            f"largest {s['largest']:.2f} {tok}"
        )
    agg_text = "\n".join(agg_lines) or "No meaningful volume."

    sample = transfers[: min(20, len(transfers))]
    sample_lines = []
    for t in sample:
        from_short = (t.from_address or "")[:6] + "..." + (t.from_address or "")[-4:]
        to_short = (
            (t.to_address or "")[:6] + "..." + (t.to_address or "")[-4:]
            if t.to_address
            else ""
        )
        sample_lines.append(
            f"- {t.amount} {t.token_symbol} from {from_short} to {to_short} (block {t.block_number})"
        )
    sample_text = "\n".join(sample_lines)

    tok_phrase = _token_phrase(token)

    system_msg = (
        "You are an on-chain crypto analyst assisting a PM. "
        "Answer questions based strictly on the flows provided. "
        "If multiple token types exist, compare them. "
        "If something is uncertain or speculative, say so explicitly."
    )

    user_msg = (
        f"Here are recent large {tok_phrase} transfers:\n\n"
        f"{transfers_text}\n\n"
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
