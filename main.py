import os
import requests
from datetime import datetime
from fastapi import FastAPI, Request
from fastapi.responses import Response
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse
from twilio.twiml.messaging_response import MessagingResponse
from contextlib import asynccontextmanager
from sqlmodel import Session

from db import engine, init_db, SmsRequest

from contextlib import asynccontextmanager
from db import init_db

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(lifespan=lifespan)

# Env vars
TWILIO_ACCOUNT_SID = os.environ["TWILIO_ACCOUNT_SID"]
TWILIO_AUTH_TOKEN  = os.environ["TWILIO_AUTH_TOKEN"]
TWILIO_NUMBER      = os.environ["TWILIO_NUMBER"]  # ex: +33...

twilio = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

SHEETS_WEBHOOK_URL = os.environ.get("SHEETS_WEBHOOK_URL")
SHEETS_SECRET = os.environ.get("SHEETS_SECRET")

def send_to_google_sheet(from_number: str, raw_request: str, job_type="", address="", urgency=""):
    if not SHEETS_WEBHOOK_URL or not SHEETS_SECRET:
        return  # si pas configuré, on n'envoie pas

    payload = {
        "secret": SHEETS_SECRET,
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "from_number": from_number,
        "job_type": job_type,
        "address": address,
        "urgency": urgency,
        "raw_request": raw_request,
    }

    # timeout court pour éviter de bloquer Twilio trop longtemps
    requests.post(SHEETS_WEBHOOK_URL, json=payload, timeout=5)

@app.post("/voice")
async def voice(request: Request):
    form = await request.form()
    from_number = form.get("From")

    # Répondre à l'appel (TwiML)
    vr = VoiceResponse()
    vr.say("Bonjour. Désolé je n'ai pas pu vous répondre. Je vous envoie un SMS.")
    vr.hangup()

    # Envoyer SMS au client
    twilio.messages.create(
        from_=TWILIO_NUMBER,
        to=from_number,
        body="Bonjour, plombier {Nom}.Je n’ai pas pu répondre à votre appel.Pouvez-vous m’indiquer par SMS en quelques mots votre besoin (ex : fuite, chauffe-eau, WC) ?Je vous rappelle rapidement pour les détails."
    )

    return Response(content=str(vr), media_type="application/xml")


@app.post("/sms")
async def sms(request: Request):
    form = await request.form()
    from_number = form.get("From")
    body = (form.get("Body") or "").strip()

    # On enregistre chaque SMS en base
    with Session(engine) as session:
        session.add(SmsRequest(from_number=from_number, raw_request=body))
        session.commit()

    #Envoi vers GoogleSheets
    send_to_google_sheet(from_number, body)

    resp = MessagingResponse()
    resp.message("Merci ! Message reçu. Je vous recontacte rapidement")

    return Response(content=str(resp), media_type="application/xml")

from sqlmodel import select

@app.get("/messages")
def get_messages():
    with Session(engine) as session:
        rows = session.exec(select(SmsRequest).order_by(SmsRequest.id.desc())).all()
        return [
            {
                "id": r.id,
                "from": r.from_number,
                "message": r.raw_request,
                "date": r.created_at
            }
            for r in rows
        ]
