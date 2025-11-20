from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from datetime import timedelta, datetime
from pydantic import BaseModel
from typing import Optional
from uuid import uuid4

# Model classes
class PaymentRequest(BaseModel):
    from_account: str
    to_account: str
    amount: float
    currency: str  # Source currency
    target_currency: Optional[str] = None  # Optional: convert to this currency

class PaymentStatus(BaseModel):
    payment_id: str
    status: str
    settlement_time: Optional[str] = None
    amount: Optional[float] = None
    currency: Optional[str] = None
    fx_rate: Optional[float] = None
    converted_amount: Optional[float] = None
    target_currency: Optional[str] = None

# Adapter pattern for legacy CBS integration
class LegacyCBSAdapter:
    def __init__(self):
        self.payments = {}

    def initiate_payment(self, req):
        payment_id = str(uuid4())
        # Simulate legacy CBS logic (replace with real CBS API call)
        self.payments[payment_id] = {
            "request": req.dict(),
            "status": "pending",
            "settlement_time": None
        }
        return payment_id

    def get_status(self, payment_id):
        payment = self.payments.get(payment_id)
        if not payment:
            return None
        return payment

    def settle_payment(self, payment_id):
        payment = self.payments.get(payment_id)
        if not payment:
            return None
        payment["status"] = "settled"
        payment["settlement_time"] = datetime.utcnow().isoformat()
        return payment


app = FastAPI()

# Use the adapter for CBS integration
cbs_adapter = LegacyCBSAdapter()

# Webhook registry (in-memory for demo)
webhooks = {}

# Audit log (in-memory for demo)
audit_log = []

# Metrics (in-memory for demo)
metrics = {
    "total_requests": 0,
    "successful_payments": 0,
    "rate_limit_hits": 0,
    "fraud_blocks": 0,
}

def log_action(user, action, details):
    audit_log.append({
        "timestamp": datetime.utcnow().isoformat(),
        "user": user,
        "action": action,
        "details": details
    })

# JWT config
SECRET_KEY = "demo_secret_key_change_me"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/token")

def create_access_token(data: dict, expires_delta: timedelta = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def get_current_user(token: str = Depends(oauth2_scheme)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        return username
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid credentials")

class WebhookRegistration(BaseModel):
    payment_id: str
    url: str


# Simple FX rates table (for demo)
FX_RATES = {
    ("USD", "EUR"): 0.92,
    ("EUR", "USD"): 1.09,
    ("USD", "GBP"): 0.80,
    ("GBP", "USD"): 1.25,
    ("EUR", "GBP"): 0.87,
    ("GBP", "EUR"): 1.15,
}

def get_fx_rate(src, tgt):
    if src == tgt:
        return 1.0
    return FX_RATES.get((src, tgt), None)

@app.post("/api/payments", response_model=PaymentStatus)
def initiate_payment(req: PaymentRequest, user: str = Depends(get_current_user)):
    import time
    metrics["total_requests"] += 1
    # --- Rate limiting ---
    RATE_LIMIT = 10  # max requests per minute per user
    WINDOW = 60  # seconds
    if not hasattr(initiate_payment, "user_requests"):
        initiate_payment.user_requests = {}
    now = time.time()
    user_reqs = initiate_payment.user_requests.setdefault(user, [])
    # Remove requests older than WINDOW
    user_reqs = [t for t in user_reqs if now - t < WINDOW]
    if len(user_reqs) >= RATE_LIMIT:
        metrics["rate_limit_hits"] += 1
        log_action(user, "rate_limit_exceeded", {"count": len(user_reqs)})
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again later.")
    user_reqs.append(now)
    initiate_payment.user_requests[user] = user_reqs

    # --- Fraud detection ---
    FRAUD_AMOUNT = 10000.0  # flag payments over this amount
    SUSPICIOUS_ACCOUNTS = {"FAKE123", "TEST999"}
    fraud_flags = []
    if req.amount > FRAUD_AMOUNT:
        fraud_flags.append("high_amount")
    if req.to_account in SUSPICIOUS_ACCOUNTS:
        fraud_flags.append("suspicious_account")
    if fraud_flags:
        metrics["fraud_blocks"] += 1
        log_action(user, "fraud_detected", {"flags": fraud_flags, **req.dict()})
        raise HTTPException(status_code=403, detail=f"Fraud detected: {', '.join(fraud_flags)}")

    # --- FX conversion ---
    fx_rate = None
    converted_amount = None
    target_currency = req.target_currency or req.currency
    if req.target_currency and req.target_currency != req.currency:
        fx_rate = get_fx_rate(req.currency, req.target_currency)
        if fx_rate is None:
            log_action(user, "initiate_payment_failed", {"reason": "FX rate not found", **req.dict()})
            raise HTTPException(status_code=400, detail="FX rate not available for requested currency pair")
        converted_amount = round(req.amount * fx_rate, 2)
    else:
        converted_amount = req.amount
    payment_id = cbs_adapter.initiate_payment(req)
    metrics["successful_payments"] += 1
    log_action(user, "initiate_payment", {"payment_id": payment_id, **req.dict(), "fx_rate": fx_rate, "converted_amount": converted_amount, "target_currency": target_currency})
    return PaymentStatus(
        payment_id=payment_id,
        status="pending",
        amount=req.amount,
        currency=req.currency,
        fx_rate=fx_rate,
        converted_amount=converted_amount,
        target_currency=target_currency
    )
# Token endpoint for demo (single user: demo/demo)
@app.get("/api/metrics")
def get_metrics():
    return metrics

@app.get("/api/payments/{payment_id}/status", response_model=PaymentStatus)

def check_status(payment_id: str, user: str = Depends(get_current_user)):
    payment = cbs_adapter.get_status(payment_id)
    if not payment:
        log_action(user, "check_status_failed", {"payment_id": payment_id})
        raise HTTPException(status_code=404, detail="Payment not found")
    req = payment["request"]
    fx_rate = None
    converted_amount = None
    target_currency = req.get("target_currency") or req["currency"]
    if req.get("target_currency") and req["currency"] != req["target_currency"]:
        fx_rate = get_fx_rate(req["currency"], req["target_currency"])
        converted_amount = round(req["amount"] * fx_rate, 2) if fx_rate else None
    else:
        converted_amount = req["amount"]
    log_action(user, "check_status", {"payment_id": payment_id, "status": payment["status"]})
    return PaymentStatus(
        payment_id=payment_id,
        status=payment["status"],
        settlement_time=payment["settlement_time"],
        amount=req["amount"],
        currency=req["currency"],
        fx_rate=fx_rate,
        converted_amount=converted_amount,
        target_currency=target_currency
    )

def send_webhook(payment_id, status, settlement_time):
    import requests
    url = webhooks.get(payment_id)
    if url:
        try:
            requests.post(url, json={
                "payment_id": payment_id,
                "status": status,
                "settlement_time": settlement_time
            })
        except Exception:
            pass  # Ignore errors for demo

@app.post("/api/payments/{payment_id}/settle", response_model=PaymentStatus)


def instant_settle(payment_id: str, background_tasks: BackgroundTasks, user: str = Depends(get_current_user)):
    payment = cbs_adapter.get_status(payment_id)
    if not payment:
        log_action(user, "instant_settle_failed", {"payment_id": payment_id})
        raise HTTPException(status_code=404, detail="Payment not found")
    req = payment["request"]
    fx_rate = None
    converted_amount = None
    target_currency = req.get("target_currency") or req["currency"]
    if req.get("target_currency") and req["currency"] != req["target_currency"]:
        fx_rate = get_fx_rate(req["currency"], req["target_currency"])
        converted_amount = round(req["amount"] * fx_rate, 2) if fx_rate else None
    else:
        converted_amount = req["amount"]
    # Simulate async settlement
    def async_settle():
        settled = cbs_adapter.settle_payment(payment_id)
        if settled:
            log_action(user, "instant_settle", {"payment_id": payment_id, "status": settled["status"], "settlement_time": settled["settlement_time"], "fx_rate": fx_rate, "converted_amount": converted_amount, "target_currency": target_currency})
            send_webhook(payment_id, settled["status"], settled["settlement_time"])
    background_tasks.add_task(async_settle)
    log_action(user, "instant_settle_requested", {"payment_id": payment_id})
    return PaymentStatus(
        payment_id=payment_id,
        status="settling",
        settlement_time=None,
        amount=req["amount"],
        currency=req["currency"],
        fx_rate=fx_rate,
        converted_amount=converted_amount,
        target_currency=target_currency
    )

@app.post("/api/webhooks/register")

def register_webhook(reg: WebhookRegistration, user: str = Depends(get_current_user)):
    webhooks[reg.payment_id] = reg.url
    log_action(user, "register_webhook", {"payment_id": reg.payment_id, "url": reg.url})
    return {"result": "webhook registered"}

# Token endpoint for demo (single user: demo/demo)
@app.post("/api/token")
def login(form_data: OAuth2PasswordRequestForm = Depends()):
    if form_data.username == "demo" and form_data.password == "demo":
        access_token = create_access_token(
            data={"sub": form_data.username},
            expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        )
        log_action(form_data.username, "login_success", {})
        return {"access_token": access_token, "token_type": "bearer"}
    else:
        log_action(form_data.username, "login_failed", {})
        raise HTTPException(status_code=401, detail="Incorrect username or password")


# To run: uvicorn api_layer_demo:app --reload
# The LegacyCBSAdapter simulates CBS integration. Replace its methods with real CBS API calls for production use.
# Webhook endpoint: POST /api/webhooks/register {"payment_id": ..., "url": ...}
# Settlement is now asynchronous; webhook is called on status change.
# Audit log is stored in-memory for demo. For production, use a secure, immutable log store.