from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


class WhaleTransfer(BaseModel):
    tx_hash: str
    from_address: str
    to_address: str
    token_symbol: str
    token_address: Optional[str] = None
    amount: float
    usd_value: Optional[float] = None
    chain: str = "eth"
    block_number: Optional[int] = None
    timestamp: Optional[datetime] = None
    observed_at: Optional[datetime] = None


class WhaleTransferList(BaseModel):
    transfers: List[WhaleTransfer]
    count: int
    summary: Optional[str] = None

class AlertsSummary(BaseModel):
    summary: str
    transfer_count: int

class ChatRequest(BaseModel):
    question: str

class ChatResponse(BaseModel):
    answer: str
    transfer_count: int