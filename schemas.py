"""
Database Schemas for API Gateway Chargeback Dashboard

Each Pydantic model represents a MongoDB collection.
Collection name is the lowercase class name.
"""
from typing import Optional, Literal
from pydantic import BaseModel, Field
from datetime import datetime

LifecycleStage = Literal["design", "develop", "test", "deploy", "deprecate", "retire"]
SubscriptionStatus = Literal["active", "paused", "canceled"]

class ApiService(BaseModel):
    name: str = Field(..., description="API display name")
    version: str = Field("v1", description="Semantic version or label")
    owner: Optional[str] = Field(None, description="Team or owner")
    lifecycle_stage: LifecycleStage = Field("deploy")
    rate_limit_per_min: Optional[int] = Field(None, ge=0)
    status: Literal["healthy", "degraded", "down"] = Field("healthy")

class Plan(BaseModel):
    name: str
    tier: Literal["free", "basic", "pro", "enterprise"] = "basic"
    monthly_price: float = Field(0, ge=0)
    included_calls: int = Field(10000, ge=0)
    overage_price_per_call: float = Field(0.0005, ge=0)

class Consumer(BaseModel):
    name: str
    email: str
    company: Optional[str] = None
    plan_id: Optional[str] = Field(None, description="Reference to plan _id as string")

class Subscription(BaseModel):
    consumer_id: str
    api_id: str
    plan_id: str
    start_date: Optional[datetime] = None
    status: SubscriptionStatus = "active"

class UsageEvent(BaseModel):
    api_id: str
    consumer_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    latency_ms: Optional[int] = Field(None, ge=0)
    status_code: int = Field(200, ge=100, le=599)
    bytes_in: Optional[int] = Field(0, ge=0)
    bytes_out: Optional[int] = Field(0, ge=0)

class Chargeback(BaseModel):
    period: str = Field(..., description="YYYY-MM")
    consumer_id: str
    api_id: str
    plan_id: str
    calls: int
    overage_calls: int
    amount: float
