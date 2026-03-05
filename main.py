import os
import json
import logging
from datetime import datetime

import requests
from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import Response

from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse
from twilio.twiml.messaging_response import MessagingResponse

from contextlib import asynccontextmanager
from sqlmodel import Session, select

from openai import OpenAI

from db import engine, init_db, SmsRequest

# ---- LOGS (pour voir les erreurs dans Render -> Logs) ----
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app")

# ---- FastAPI lifecycle ----
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(lifespan=lifespan)

# ---- Env vars ----
TWILIO_ACCOUNT_SID = os.environ["TWILIO_ACCOUNT_SID"]
TWILIO_AUTH_TOKEN = os.environ["TWILIO_AUTH_TOKEN"]
TWILIO_NUMBER = os.environ["TWILIO_NUMBER"]

SHEETS_WEBHOOK_URL = os.environ.get("SHEETS_WEBHOOK_URL")
SHEETS_SECRET = os.environ.get("SHEETS_SECRET")

# (OpenAI lit OPENAI_API_KEY automatiquement si la variable existe)
openai_client = OpenAI()

twilio = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# ---- Google Sheets (Apps Script) ----
def send_to_google_sheet(from_number: str, raw_request: str, job_type="", address="", urgency=""):
    if not SHEETS_WEBHOOK_URL or not SHEETS_SECRET:
        logger.info("Sheets non configuré (SHEETS_WEBHOOK_URL ou SHEETS_SECRET manquant)")
        return

    payload = {
        "secret": SHEETS_SECRET,
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "from_number": from_number,
        "job_type": job_type,
        "address": address,
        "urgency": urgency,
        "raw_request": raw_request,
    }

    r = requests.post(SHEETS_WEBHOOK_URL, json=payload, timeout=5)
    logger.info("Sheets status=%s body=%s", r.status_code, r.text[:200])

# ---- IA : extraction (Structured Outputs) ----
def extract_fields_with_ai(raw_text: str) -> dict:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "job_type": {"type": "string"},
            "address": {"type": "string"},
            "urgency": {"type": "string", "enum": ["faible", "moyenne", "elevee"]},
        },
        "required": ["job_type", "address", "urgency"],
    }

    # Le format attendu côté API: text.format = {type: json_schema, name, schema}
    resp = openai_client.responses.create(
        model="gpt-4o-mini",
        input=[
            {
                "role": "system",
                "content": (
                    "Tu aides un plombier. Analyse le SMS et retourne un JSON strict.\n"
                    "job_type: fuite | wc | chauffe-eau | debouchage | autre ou estimée par toi\n"
                    "address: adresse complète si présente sinon \"\"\n"
                    "urgency: faible | moyenne | elevee (estimée par toi)\n"
                ),
            },
            {"role": "user", "content": raw_text},
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "plumber_request",
                "schema": schema,
            }
        },
    )

    # output_text doit contenir le JSON en texte
    return json.loads(resp.output_text)

def process_sms_after_reply(from_number: str, body: str):
    job_type, address, urgency = "autre", "", "moyenne"

    try:
        data = extract_fields_with_ai(body)
        job_type = data.get("job_type", job_type)
        address = data.get("address", address)
        urgency = data.get("urgency", urgency)
        logger.info("AI parsed: %s", data)
    except Exception as e:
        # IMPORTANT: on log l'erreur pour comprendre
        logger.exception("OpenAI ERROR: %s", e)

    try:
        send_to_google_sheet(from_number, body, job_type, address, urgency)
    except Exception as e:
        logger.exception("Sheets ERROR: %s", e)

# ---- Routes ----
@app.post("/voice")
async def voice(request: Request):
    form = await request.form()
    from_number = form.get("From")

    vr = VoiceResponse()
    vr.say("Bonjour. Désolé je n'ai pas pu vous répondre. Je vous envoie un SMS.")
    vr.hangup()

    twilio.messages.create(
        from_=TWILIO_NUMBER,
        to=from_number,
        body=(
            "Bonjour, je n’ai pas pu répondre.\n"
            "Pouvez-vous m’indiquer par SMS :\n"
            "1) le problème\n"
            "2) l’adresse complète\n"
            "Exemple : Fuite sous évier - 12 rue Victor Hugo, Paris"
        ),
    )

    return Response(content=str(vr), media_type="application/xml")

@app.post("/sms")
async def sms(request: Request, background_tasks: BackgroundTasks):
    form = await request.form()
    from_number = form.get("From")
    body = (form.get("Body") or "").strip()

    # Enregistre en base (rapide)
    with Session(engine) as session:
        session.add(SmsRequest(from_number=from_number, raw_request=body))
        session.commit()

    # Traitement après réponse Twilio
    background_tasks.add_task(process_sms_after_reply, from_number, body)

    resp = MessagingResponse()
    resp.message("Merci ! Message reçu. Je vous recontacte rapidement")
    return Response(content=str(resp), media_type="application/xml")

@app.get("/messages")
def get_messages():
    with Session(engine) as session:
        rows = session.exec(select(SmsRequest).order_by(SmsRequest.id.desc()).limit(50)).all()
        return [
            {
                "id": r.id,
                "from": r.from_number,
                "message": r.raw_request,
                "date": r.created_at,
            }
            for r in rows
        ]

# Test OpenAI direct (sans Twilio)
@app.get("/debug/openai")
def debug_openai():
    test = "Fuite sous évier cuisine, 12 rue Victor Hugo 75001 Paris"
    data = extract_fields_with_ai(test)
    return {"ok": True, "data": data}
