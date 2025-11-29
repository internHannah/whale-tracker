from sqlalchemy import Column, Integer, String, Float, DateTime
from sqlalchemy.sql import func
from .db import Base

class WhaleTransferORM(Base):
    __tablename__ = "whale_transfers"

    id = Column(Integer, primary_key=True, index=True)
    tx_hash = Column(String, unique=True, index=True, nullable=False)
    from_address = Column(String, index=True, nullable=False)
    to_address = Column(String, index=True, nullable=False)
    token_symbol = Column(String, index=True, nullable=False)
    token_address = Column(String, nullable=True)
    amount = Column(Float, nullable=False)
    usd_value = Column(Float, nullable=True)
    chain = Column(String, default="eth")
    block_number = Column(Integer, nullable=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    observed_at = Column(DateTime(timezone=True), server_default=func.now())
