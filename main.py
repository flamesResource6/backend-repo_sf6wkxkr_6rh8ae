import os
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from bson import ObjectId

from database import db, create_document, get_documents

app = FastAPI(title="API Gateway Chargeback Dashboard")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------- Helpers ---------

def oid(oid_str: str) -> ObjectId:
    try:
        return ObjectId(oid_str)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid ObjectId")


def serialize(doc: Dict[str, Any]) -> Dict[str, Any]:
    if not doc:
        return doc
    d = dict(doc)
    if "_id" in d:
        d["_id"] = str(d["_id"])
    return d


def month_bounds(period: str):
    # period format: YYYY-MM
    try:
        start = datetime.strptime(period + "-01", "%Y-%m-%d")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid period. Use YYYY-MM")
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1, day=1)
    else:
        end = start.replace(month=start.month + 1, day=1)
    return start, end


# --------- Models (request bodies) ---------
class ApiServiceIn(BaseModel):
    name: str
    version: str = "v1"
    owner: Optional[str] = None
    lifecycle_stage: str = "deploy"
    rate_limit_per_min: Optional[int] = None
    status: str = "healthy"


class PlanIn(BaseModel):
    name: str
    tier: str = "basic"
    monthly_price: float = 0
    included_calls: int = 10000
    overage_price_per_call: float = 0.0005


class ConsumerIn(BaseModel):
    name: str
    email: str
    company: Optional[str] = None
    plan_id: Optional[str] = None


class SubscriptionIn(BaseModel):
    consumer_id: str
    api_id: str
    plan_id: str
    start_date: Optional[datetime] = None
    status: str = "active"


class UsageEventIn(BaseModel):
    api_id: str
    consumer_id: str
    timestamp: Optional[datetime] = None
    latency_ms: Optional[int] = None
    status_code: int = 200
    bytes_in: Optional[int] = 0
    bytes_out: Optional[int] = 0


# --------- Root & Health ---------
@app.get("/")
def read_root():
    return {"message": "API Gateway Chargeback Dashboard Backend"}


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }

    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Configured"
            response["database_name"] = db.name
            response["connection_status"] = "Connected"
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"

    response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set"
    return response


# ---------- API Services ----------
@app.post("/apis")
def create_api(api: ApiServiceIn):
    api_id = create_document("apiservice", api.model_dump())
    return {"_id": api_id}


@app.get("/apis")
def list_apis():
    res = [serialize(x) for x in get_documents("apiservice")]
    return res


@app.put("/apis/{api_id}")
def update_api(api_id: str, api: ApiServiceIn):
    result = db["apiservice"].update_one({"_id": oid(api_id)}, {"$set": api.model_dump()})
    if result.matched_count == 0:
        raise HTTPException(404, "API not found")
    return {"updated": True}


# ---------- Plans ----------
@app.post("/plans")
def create_plan(plan: PlanIn):
    plan_id = create_document("plan", plan.model_dump())
    return {"_id": plan_id}


@app.get("/plans")
def list_plans():
    return [serialize(x) for x in get_documents("plan")]


@app.put("/plans/{plan_id}")
def update_plan(plan_id: str, plan: PlanIn):
    result = db["plan"].update_one({"_id": oid(plan_id)}, {"$set": plan.model_dump()})
    if result.matched_count == 0:
        raise HTTPException(404, "Plan not found")
    return {"updated": True}


# ---------- Consumers ----------
@app.post("/consumers")
def create_consumer(consumer: ConsumerIn):
    consumer_id = create_document("consumer", consumer.model_dump())
    return {"_id": consumer_id}


@app.get("/consumers")
def list_consumers():
    return [serialize(x) for x in get_documents("consumer")]


# ---------- Subscriptions ----------
@app.post("/subscriptions")
def create_subscription(sub: SubscriptionIn):
    data = sub.model_dump()
    if not data.get("start_date"):
        data["start_date"] = datetime.utcnow()
    sub_id = create_document("subscription", data)
    return {"_id": sub_id}


@app.get("/subscriptions")
def list_subscriptions(status: Optional[str] = None):
    filt = {"status": status} if status else {}
    return [serialize(x) for x in get_documents("subscription", filt)]


# ---------- Usage Ingestion ----------
@app.post("/usage")
def ingest_usage(evt: UsageEventIn):
    data = evt.model_dump()
    if not data.get("timestamp"):
        data["timestamp"] = datetime.utcnow()
    usage_id = create_document("usageevent", data)
    return {"_id": usage_id}


# ---------- Metrics ----------
@app.get("/metrics/overview")
def metrics_overview(period: Optional[str] = Query(None, description="YYYY-MM")):
    filt: Dict[str, Any] = {}
    if period:
        start, end = month_bounds(period)
        filt = {"timestamp": {"$gte": start, "$lt": end}}

    total_calls = db["usageevent"].count_documents(filt)

    # success rate and avg latency
    pipeline = [
        {"$match": filt},
        {"$group": {
            "_id": None,
            "avg_latency": {"$avg": "$latency_ms"},
            "success": {"$sum": {"$cond": [{"$lt": ["$status_code", 400]}, 1, 0]}},
            "total": {"$sum": 1}
        }}
    ]
    agg = list(db["usageevent"].aggregate(pipeline))
    avg_latency = agg[0]["avg_latency"] if agg else None
    success = agg[0]["success"] if agg else 0

    return {
        "total_calls": total_calls,
        "avg_latency_ms": avg_latency,
        "success_rate": (success / total_calls) if total_calls else None,
        "apis": db["apiservice"].count_documents({}),
        "consumers": db["consumer"].count_documents({}),
        "active_subscriptions": db["subscription"].count_documents({"status": "active"})
    }


@app.get("/metrics/api/{api_id}")
def metrics_by_api(api_id: str, period: Optional[str] = Query(None)):
    filt: Dict[str, Any] = {"api_id": api_id}
    if period:
        start, end = month_bounds(period)
        filt["timestamp"] = {"$gte": start, "$lt": end}

    total_calls = db["usageevent"].count_documents(filt)
    pipeline = [
        {"$match": filt},
        {"$group": {
            "_id": "$api_id",
            "avg_latency": {"$avg": "$latency_ms"},
            "success": {"$sum": {"$cond": [{"$lt": ["$status_code", 400]}, 1, 0]}},
            "total": {"$sum": 1}
        }}
    ]
    agg = list(db["usageevent"].aggregate(pipeline))
    avg_latency = agg[0]["avg_latency"] if agg else None
    success = agg[0]["success"] if agg else 0

    return {
        "api_id": api_id,
        "total_calls": total_calls,
        "avg_latency_ms": avg_latency,
        "success_rate": (success / total_calls) if total_calls else None,
    }


# ---------- Chargeback ----------
@app.get("/chargeback")
def chargeback_report(period: str = Query(..., description="YYYY-MM")):
    start, end = month_bounds(period)

    # Load reference data
    plans = {str(p["_id"]): p for p in db["plan"].find()}
    subs = list(db["subscription"].find({"status": "active", "start_date": {"$lte": end}}))

    report = []

    for s in subs:
        consumer_id = s["consumer_id"]
        api_id = s["api_id"]
        plan_id = s["plan_id"]
        plan = plans.get(plan_id)
        if not plan:
            plan = {"monthly_price": 0, "included_calls": 0, "overage_price_per_call": 0}

        usage_count = db["usageevent"].count_documents({
            "consumer_id": consumer_id,
            "api_id": api_id,
            "timestamp": {"$gte": start, "$lt": end}
        })
        included = int(plan.get("included_calls", 0))
        overage = max(0, usage_count - included)
        amount = float(plan.get("monthly_price", 0)) + overage * float(plan.get("overage_price_per_call", 0))

        report.append({
            "consumer_id": consumer_id,
            "api_id": api_id,
            "plan_id": plan_id,
            "period": period,
            "calls": usage_count,
            "overage_calls": overage,
            "amount": round(amount, 6)
        })

    return {"period": period, "items": report}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
