import os
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

    resp = MessagingResponse()
    resp.message("Merci ! Message reçu. Je vous recontacte rapidement")

    return Response(content=str(resp), media_type="application/xml")
