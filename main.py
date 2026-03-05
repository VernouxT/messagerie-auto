import os
import requests
import json
from fastapi import BackgroundTasks
from openai import OpenAI
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

SHEETS_WEBHOOK_URL = os.environ.get("SHEETS_WEBHOOK_URL")
SHEETS_SECRET = os.environ.get("SHEETS_SECRET")

twilio = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
openai_client = OpenAI()

def extract_fields_with_ai(raw_text: str) -> dict:
    """
    Renvoie un dict avec:
    job_type: "fuite" | "wc" | "chauffe-eau" | "debouchage" | "autre"
    address: string (vide si non trouvé)
    urgency: "faible" | "moyenne" | "elevee"
    """
    schema = {
        "name": "plumber_request",
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "job_type": {
                    "type": "string",
                    "enum": ["fuite", "wc", "chauffe-eau", "debouchage", "autre"]
                },
                "address": {"type": "string"},
                "urgency": {"type": "string", "enum": ["faible", "moyenne", "elevee"]}
            },
            "required": ["job_type", "address", "urgency"]
        },
        "strict": True
    }

    response = openai_client.responses.create(
        model="gpt-4o-mini",
        input=[
            {
                "role": "system",
                "content": (
                    "Tu aides un plombier. À partir d'un SMS client, extrais:\n"
                    "- job_type (fuite/wc/chauffe-eau/debouchage/autre)\n"
                    "- address (adresse complète si présente, sinon chaîne vide)\n"
                    "- urgency (faible/moyenne/elevee) estimée par toi.\n"
                    "Réponds uniquement avec l'objet JSON demandé."
                )
            },
            {"role": "user", "content": raw_text}
        ],
        # Structured Outputs (JSON schema)
        text={
            "format": {
                "type": "json_schema",
                "json_schema": schema
            }
        }
    )

    # output_text contient le JSON (texte) dans ce mode
    return json.loads(response.output_text)

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

def process_sms_after_reply(from_number: str, body: str):
    # 1) IA (avec sécurité)
    job_type, address, urgency = "autre", "", "moyenne"
    try:
        data = extract_fields_with_ai(body)
        job_type = data.get("job_type", job_type)
        address = data.get("address", address)
        urgency = data.get("urgency", urgency)
    except Exception:
        # Si l'IA a un souci, on n'empêche pas le système de marcher
        pass

    # 2) Envoi au Google Sheet
    try:
        send_to_google_sheet(from_number, body, job_type, address, urgency)
    except Exception:
        pass



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
async def sms(request: Request, background_tasks: BackgroundTasks):
    form = await request.form()
    from_number = form.get("From")
    body = (form.get("Body") or "").strip()

    # 1) On enregistre en base (rapide)
    with Session(engine) as session:
        session.add(SmsRequest(from_number=from_number, raw_request=body))
        session.commit()

    # 2) On lance le traitement IA + Google Sheet APRES la réponse Twilio
    background_tasks.add_task(process_sms_after_reply, from_number, body)

    # 3) Réponse immédiate à Twilio
    resp = MessagingResponse()
    resp.message("Merci ! Message reçu. Je vous recontacte rapidement")
    return Response(content=str(resp), media_type="application/xml")



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
