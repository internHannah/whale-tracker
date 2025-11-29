from fastapi import APIRouter
from openai import OpenAI
from .schemas import WhaleTransferList, AlertsSummary, ChatRequest, ChatResponse
from . import whale_service  # service layer



client = OpenAI()  
router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("/latest", response_model=WhaleTransferList)
def latest_alerts(limit: int = 200,  min_amount: float = 100.0):
    """
    Return whale transfers from the service layer.
    Right now the service still returns fake data,
    but later it will call a real provider.
    """

    transfers = whale_service.fetch_whales(
        min_amount=min_amount,
        limit=limit,
    )

    if not transfers:
        return WhaleTransferList(
            transfers=[],
            count=0,
            summary="No whale transfers found (or provider returned nothing).",
        )

    return WhaleTransferList(
        transfers=transfers,
        count=len(transfers),
        summary=f"Showing up to {len(transfers)} large ETH transfers (currently filtering by amount >= 100 ETH).",
    )

@router.get("/summary", response_model=AlertsSummary)
def alerts_summary(limit: int = 20, min_amount_eth: float = 100.0):
    """
    Use an LLM to summarize the recent whale transfers.
    """

    # 1. Get recent whale transfers from the same service you already use
    transfers = whale_service.fetch_whale_transfers_from_provider(
        min_amount=0,   # we are not really using USD filter right now
        limit=limit,
    )

    # 2. If nothing came back, return a simple message
    if not transfers:
        return AlertsSummary(
            summary="No recent large ETH transfers were found, so there is nothing to analyze right now.",
            transfer_count=0,
        )

    # 3. Build a compact text description of the transfers for the model
    lines = []
    for t in transfers:
        # shorten the addresses so the prompt is not huge
        from_short = t.from_address[:6] + "..." + t.from_address[-4:]
        to_short = t.to_address[:6] + "..." + t.to_address[-4:]
        line = f"- {t.amount} {t.token_symbol} from {from_short} to {to_short} (block {t.block_number})"
        lines.append(line)

    transfers_text = "\n".join(lines)

    # 4. Create a prompt for the model
    system_msg = (
        "You are an on-chain crypto analyst helping a user understand a whale-monitoring dashboard. "
        "The dashboard has: (1) a table of large ETH transfers, (2) a Flow Intelligence panel with metrics "
        "like total volume, largest transfer, concentration and top wallets, (3) a Wallet Drill-down panel "
        "showing inflow/outflow/net flow for a selected wallet, and (4) an auto-generated Research Note. "
        "You look at large Ethereum transfers and answer questions about possible explanations. "
        "If the user asks how to use the dashboard, explain clearly what each part does and how a PM or trader could use it. "
        'Be precise, avoid overconfidence, and mention uncertainty when you don\'t know.'
    )

    user_msg = (
        "Here are recent large ETH transfers on Ethereum:\n\n"
        f"{transfers_text}\n\n"
        "In 3–5 sentences, explain what might be going on. "
        "Mention whether this looks like internal movements, exchange inflows/outflows, OTC trades, "
        "or accumulation by a large wallet. If you are not sure, say so."
    )

    # 5. Call the OpenAI chat model
    chat = client.chat.completions.create(
        model="gpt-4.1-mini",  # you can swap to another model if you want
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.4,
    )

    summary_text = chat.choices[0].message.content.strip()

    # 6. Return the summary and how many transfers we analyzed
    return AlertsSummary(
        summary=summary_text,
        transfer_count=len(transfers),
    )

@router.post("/chat", response_model=ChatResponse)
def alerts_chat(payload: ChatRequest, limit: int = 20):
    """
    Let the user ask a question about the current whale flows.
    We send the latest transfers + their question to the LLM.
    """

    # 1. Get the same whale transfers we use for summary
    transfers = whale_service.fetch_whale_transfers_from_provider(
        min_amount=0,
        limit=limit,
    )

    if not transfers:
        return ChatResponse(
            answer="Right now I don't see any large ETH transfers, so there isn't enough data to answer that question.",
            transfer_count=0,
        )

    # 2. Build compact text for the model
    lines = []
    for t in transfers:
        from_short = (t.from_address or "")[:6] + "..." + (t.from_address or "")[-4:] if t.from_address else ""
        to_short = (t.to_address or "")[:6] + "..." + (t.to_address or "")[-4:] if t.to_address else ""
        line = f"- {t.amount} {t.token_symbol} from {from_short} to {to_short} (block {t.block_number})"
        lines.append(line)

    transfers_text = "\n".join(lines)

    # 3. Use their question + flows in the prompt
    system_msg = (
        "You are an on-chain crypto analyst. "
        "You look at large Ethereum transfers and answer questions about possible explanations. "
        "Be precise, avoid overconfidence, and mention uncertainty when you don't know."
    )

    user_msg = (
        "Here are recent large ETH transfers:\n\n"
        f"{transfers_text}\n\n"
        f"The user asks: {payload.question}\n\n"
        "Answer in 3–6 sentences. Base your answer strictly on the flows above and common on-chain patterns. "
        "If something is speculative, say that it is only a possibility."
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

