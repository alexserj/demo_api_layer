
from fastapi import FastAPI, HTTPException
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


class PaymentRequest(BaseModel):
    from_account: str
    to_account: str
    amount: float
    currency: str


class PaymentStatus(BaseModel):
    payment_id: str
    status: str
    settlement_time: str = None


@app.post("/api/payments", response_model=PaymentStatus)
def initiate_payment(req: PaymentRequest):
    payment_id = cbs_adapter.initiate_payment(req)
    return PaymentStatus(payment_id=payment_id, status="pending")


@app.get("/api/payments/{payment_id}/status", response_model=PaymentStatus)
def check_status(payment_id: str):
    payment = cbs_adapter.get_status(payment_id)
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    return PaymentStatus(
        payment_id=payment_id,
        status=payment["status"],
        settlement_time=payment["settlement_time"]
    )


@app.post("/api/payments/{payment_id}/settle", response_model=PaymentStatus)
def instant_settle(payment_id: str):
    payment = cbs_adapter.settle_payment(payment_id)
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    return PaymentStatus(
        payment_id=payment_id,
        status=payment["status"],
        settlement_time=payment["settlement_time"]
    )


# To run: uvicorn api_layer_demo:app --reload
# The LegacyCBSAdapter simulates CBS integration. Replace its methods with real CBS API calls for production use.