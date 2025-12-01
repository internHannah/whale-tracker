from fastapi import APIRouter
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
    # Use same source as table so we see ETH + stables + WBTC
    transfers = whale_service.fetch_whales(
        min_amount=0.0,
        limit=limit,
    )

    transfers = _filter_by_token(transfers, token)

    if not transfers:
        return AlertsSummary(
            summary=f"No recent transfers found for {token or 'this slice'}.",
            transfer_count=0,
        )

    # ---------- 1) Build per-token stats ----------
    from collections import defaultdict

    token_counts = defaultdict(int)
    token_volumes = defaultdict(float)

    for t in transfers:
        sym = (t.token_symbol or "ETH").upper()
        token_counts[sym] += 1
        try:
            token_volumes[sym] += float(t.amount or 0)
        except (TypeError, ValueError):
            pass

    breakdown_lines = []
    for sym in sorted(token_counts.keys()):
        breakdown_lines.append(
            f"- {sym}: {token_counts[sym]} transfers, total volume ≈ {token_volumes[sym]:.4f} {sym}"
        )
    token_breakdown = "\n".join(breakdown_lines)

    # ---------- 2) Transfers list ----------
    lines = []
    for t in transfers:
        from_short = (t.from_address or "")[:6] + "..." + (t.from_address or "")[-4:]
        to_short = (t.to_address or "")[:6] + "..." + (t.to_address or "")[-4:]
        lines.append(
            f"- {t.amount} {t.token_symbol} from {from_short} to {to_short} (block {t.block_number})"
        )
    transfers_text = "\n".join(lines)

    tok_phrase = _token_phrase(token)

    # ---------- 3) Prompt that explicitly asks for cross-token analysis ----------
    system_msg = (
        "You are an on-chain crypto analyst. "
        "You summarize a snapshot of large token transfers for a PM or trader. "
        "Use the token breakdown and example transfers to reason carefully. "
        "If transfers include multiple token types (ETH, USDC, USDT, WBTC), "
        "compare them and state which dominates by volume and by count. "
        "Mention if flows look like exchange routing, OTC movement, "
        "accumulation, or benign internal transfers. "
        "If you don't have enough evidence, say so."
    )

    user_msg = (
        f"Here is a snapshot of recent large {tok_phrase} transfers on Ethereum.\n\n"
        f"Token breakdown (pre-computed):\n"
        f"{token_breakdown}\n\n"
        f"Example transfers:\n"
        f"{transfers_text}\n\n"
        "In 3–5 sentences, summarize the main patterns. "
        "Explicitly discuss how ETH vs stablecoins vs WBTC behave in this snapshot."
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
        transfer_count=len(transfers),
    )

# -----------------------------
# /alerts/chat
# -----------------------------
@router.post("/chat", response_model=ChatResponse)
def alerts_chat(
    payload: ChatRequest,
    limit: int = 20,
    token: Optional[str] = None,
):
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
