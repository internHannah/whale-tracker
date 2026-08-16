from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    DateTime,
    UniqueConstraint,
    Index,
)
from sqlalchemy.sql import func

from .db import Base


class WhaleTransferORM(Base):
    __tablename__ = "whale_transfers"
    __table_args__ = (
        UniqueConstraint(
            "tx_hash",
            "token_symbol",
            "from_address",
            "to_address",
            "amount",
            name="uq_transfer_identity",
        ),
        Index("ix_whale_transfers_block_number", "block_number"),
    )

    id = Column(Integer, primary_key=True, index=True)
    tx_hash = Column(String, index=True, nullable=False)
    from_address = Column(String, index=True, nullable=False)
    to_address = Column(String, index=True, nullable=False)
    token_symbol = Column(String, index=True, nullable=False)
    token_address = Column(String, nullable=True)
    amount = Column(Float, nullable=False)
    usd_value = Column(Float, nullable=True)
    chain = Column(String, default="eth")
    block_number = Column(Integer, nullable=True)
    timestamp = Column(DateTime(timezone=True), nullable=True)
    observed_at = Column(DateTime(timezone=True), server_default=func.now())
