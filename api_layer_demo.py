from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from datetime import timedelta
from pydantic import BaseModel
from uuid import uuid4
from datetime import datetime

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

class PaymentRequest(BaseModel):
    from_account: str
    to_account: str
    amount: float
    currency: str


class PaymentStatus(BaseModel):
    payment_id: str
    status: str
    settlement_time: str = None

class WebhookRegistration(BaseModel):
    payment_id: str
    url: str


@app.post("/api/payments", response_model=PaymentStatus)
def initiate_payment(req: PaymentRequest, user: str = Depends(get_current_user)):
    payment_id = cbs_adapter.initiate_payment(req)
    return PaymentStatus(payment_id=payment_id, status="pending")


@app.get("/api/payments/{payment_id}/status", response_model=PaymentStatus)
def check_status(payment_id: str, user: str = Depends(get_current_user)):
    payment = cbs_adapter.get_status(payment_id)
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    return PaymentStatus(
        payment_id=payment_id,
        status=payment["status"],
        settlement_time=payment["settlement_time"]
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
        raise HTTPException(status_code=404, detail="Payment not found")
    # Simulate async settlement
    def async_settle():
        settled = cbs_adapter.settle_payment(payment_id)
        send_webhook(payment_id, settled["status"], settled["settlement_time"])
    background_tasks.add_task(async_settle)
    return PaymentStatus(
        payment_id=payment_id,
        status="settling",
        settlement_time=None
    )

@app.post("/api/webhooks/register")
def register_webhook(reg: WebhookRegistration, user: str = Depends(get_current_user)):
    webhooks[reg.payment_id] = reg.url
    return {"result": "webhook registered"}

# Token endpoint for demo (single user: demo/demo)
@app.post("/api/token")
def login(form_data: OAuth2PasswordRequestForm = Depends()):
    if form_data.username == "demo" and form_data.password == "demo":
        access_token = create_access_token(
            data={"sub": form_data.username},
            expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        )
        return {"access_token": access_token, "token_type": "bearer"}
    else:
        raise HTTPException(status_code=401, detail="Incorrect username or password")


# To run: uvicorn api_layer_demo:app --reload
# The LegacyCBSAdapter simulates CBS integration. Replace its methods with real CBS API calls for production use.
# Webhook endpoint: POST /api/webhooks/register {"payment_id": ..., "url": ...}
# Settlement is now asynchronous; webhook is called on status change.