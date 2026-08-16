from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


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


class TokenStat(BaseModel):
    token: str
    count: int
    volume: float
    largest: float


class AlertsStats(BaseModel):
    transfer_count: int
    unique_wallets: int
    by_token: List[TokenStat]
    token_filter: Optional[str] = None


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)


class ChatResponse(BaseModel):
    answer: str
    transfer_count: int
