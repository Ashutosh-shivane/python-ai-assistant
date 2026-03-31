"""
Twilio + Gemini Voice Agent — Inbound + Outbound
=================================================

Key fixes vs previous version:
  1. Model: gemini-2.5-flash-native-audio-preview-12-2025  (matches your working code)
  2. Audio: Twilio sends mulaw 8kHz → we upsample to PCM 16kHz for Gemini
             Gemini responds PCM 24kHz → we downsample to mulaw 8kHz for Twilio
  3. WS URL: uses correct v1beta endpoint for Gemini API key (not Vertex)

Audio format contract:
  Twilio  → server : base64(mulaw, 8kHz, mono)
  server  → Gemini : base64(PCM s16le, 16kHz, mono)   ← Gemini requires this
  Gemini  → server : base64(PCM s16le, 24kHz, mono)
  server  → Twilio : base64(mulaw, 8kHz, mono)         ← Twilio requires this

Requirements:
    pip install -r requirements.txt
"""

import asyncio
import audioop          # stdlib — mulaw <-> pcm, resample
import base64
import json
import os
import logging
from datetime import datetime
from urllib.parse import quote

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, Response, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import websockets
from twilio.rest import Client as TwilioClient

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Twilio Gemini Voice Agent")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

# ── Config ────────────────────────────────────────────────────────────────────
GOOGLE_API_KEY      = os.getenv("GOOGLE_API_KEY")
TWILIO_ACCOUNT_SID  = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN   = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")
PUBLIC_URL          = os.getenv("PUBLIC_URL", "").rstrip("/")

# ✅ Exact model string from your working VideoSDK code
GEMINI_MODEL = "gemini-2.5-flash-native-audio-preview-12-2025"

# Gemini Live API WebSocket endpoint (Gemini API key auth — not Vertex)
GEMINI_WS_URL = (
    "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta"
    f".GenerativeService.BidiGenerateContent?key={GOOGLE_API_KEY}"
)

SYSTEM_INSTRUCTION = (
"You are Ashutosh's personal AI voice assistant on a phone call. "
"You represent Ashutosh Dhanaji Shivane, a Java and Spring Boot focused MCA graduate from Pune, India. "
"Here is everything about him: "

"CONTACT: Email ashutoshshivane4@gmail.com, Phone +91 77699 80880, "
"LinkedIn linkedin.com/in/ashutosh-shivane, GitHub github.com/Ashutosh-shivane. "

"SKILLS: Core Java, Python, JavaScript, HTML5, CSS, Spring Boot, REST APIs, Flask, React, jQuery, MySQL, "
"Git, Postman, Firebase, Gemini API. "

"PROJECTS: "
"1. Event Management Platform (Aug 2025–Present) — Full-stack app using Spring Boot and React with JWT authentication, "
"RESTful APIs for event creation and volunteer registration, MySQL database. "
"2. Sociosphere Social Media App (Sep–Dec 2024) — Features include user authentication, profile management, "
"post creation, direct messaging, group chats, video calling, Firebase ML Kit and Gemini API for auto captions and chatbot. "
"3. E-commerce Website for Sidhigiri Math (Feb–May 2024) — Flask REST APIs, MySQL, authentication, "
"product listing, ticket booking, responsive HTML/CSS frontend. "

"WORK EXPERIENCE: Software Developer Trainee at Real Time Application Center Kolhapur (Sep 2022–Aug 2023) — "
"Worked on core banking modules supporting 1000+ accounts, optimized MySQL queries, PHP/MySQL server-side logic. "

"EDUCATION: MCA from D.Y. Patil Institute Pune (2023–2025) GPA 8.16/10. "
"BSc Computer Science from Vivekanand College Kolhapur (2019–2022) 86.60 percent. "

"ACHIEVEMENTS: Event Coordinator at College Technical Fest. "
"Certificates in Java, React, and Data Structures and Algorithms. "

"Answer any questions about Ashutosh confidently and naturally. "
"Keep responses concise and conversational. Never use markdown or bullet points. "
"If asked something you don't know about him, say you don't have that information."

)

GEMINI_SETUP = {
    "setup": {
        "model": f"models/{GEMINI_MODEL}",
        "generation_config": {
            "response_modalities": ["AUDIO"],
            "speech_config": {
                "voice_config": {
                    "prebuilt_voice_config": {"voice_name": "Leda"}
                }
            }
        },
        "system_instruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]}
    }
}

twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
call_log: list[dict] = []


# ── Audio conversion helpers ──────────────────────────────────────────────────

def mulaw8k_to_pcm16k(mulaw_b64: str) -> str:
    """
    Twilio → Gemini
    base64 mulaw 8kHz  →  base64 PCM s16le 16kHz
    """
    raw_mulaw = base64.b64decode(mulaw_b64)
    pcm_8k    = audioop.ulaw2lin(raw_mulaw, 2)          # mulaw → PCM 16-bit 8kHz
    pcm_16k, _ = audioop.ratecv(pcm_8k, 2, 1, 8000, 16000, None)  # 8kHz → 16kHz
    return base64.b64encode(pcm_16k).decode()


def pcm24k_to_mulaw8k(pcm_b64: str) -> str:
    """
    Gemini → Twilio
    base64 PCM s16le 24kHz  →  base64 mulaw 8kHz
    """
    raw_pcm   = base64.b64decode(pcm_b64)
    pcm_8k, _ = audioop.ratecv(raw_pcm, 2, 1, 24000, 8000, None)  # 24kHz → 8kHz
    mulaw     = audioop.lin2ulaw(pcm_8k, 2)             # PCM → mulaw
    return base64.b64encode(mulaw).decode()


# ── Dashboard ──────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def dashboard():
    with open("dashboard.html", "r") as f:
        return HTMLResponse(content=f.read())


# ── Make outbound call ────────────────────────────────────────────────────────
@app.post("/make-call")
async def make_call(request: Request):
    body      = await request.json()
    to_number = body.get("to", "").strip()
    custom_msg = body.get("message", "").strip()
    to_number="+91"+to_number

    if not to_number:
        return JSONResponse({"error": "Missing 'to' phone number"}, status_code=400)

    try:
        webhook_url = f"{PUBLIC_URL}/incoming-call"
        if custom_msg:
            webhook_url += f"?message={quote(custom_msg)}"

        call = twilio_client.calls.create(
            to=to_number,
            from_=TWILIO_PHONE_NUMBER,
            url=webhook_url,
            method="POST",
            status_callback=f"{PUBLIC_URL}/call-status",
            status_callback_method="POST",
            status_callback_event=["initiated", "ringing", "answered", "completed"],
        )

        call_log.append({
            "sid":        call.sid,
            "to":         to_number,
            "from":       TWILIO_PHONE_NUMBER,
            "direction":  "outbound",
            "status":     "initiated",
            "started_at": datetime.now().isoformat(),
            "duration":   None,
        })
        logger.info(f"Outbound call {call.sid} → {to_number}")
        return JSONResponse({"success": True, "call_sid": call.sid, "to": to_number})

    except Exception as e:
        logger.error(f"Call error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Twilio webhook (inbound + outbound) ───────────────────────────────────────
@app.post("/incoming-call")
async def incoming_call(request: Request):
    params     = dict(request.query_params)
    custom_msg = params.get("message", "")

    ws_url = (
        PUBLIC_URL
        .replace("https://", "wss://")
        .replace("http://", "ws://")
        + "/media-stream"
    )
    logger.info(f">>> WebSocket URL sent to Twilio: {ws_url}")

    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="{ws_url}" />
    </Connect>
</Response>"""

    try:
        form = await request.form()
        if form.get("Direction") == "inbound":
            call_log.append({
                "sid":        form.get("CallSid", ""),
                "to":         form.get("To", ""),
                "from":       form.get("From", ""),
                "direction":  "inbound",
                "status":     "in-progress",
                "started_at": datetime.now().isoformat(),
                "duration":   None,
            })
    except Exception:
        pass

    return Response(content=twiml, media_type="application/xml")


# ── Status callback ───────────────────────────────────────────────────────────
@app.post("/call-status")
async def call_status(request: Request):
    form     = await request.form()
    sid      = form.get("CallSid")
    status   = form.get("CallStatus")
    duration = form.get("CallDuration")
    for entry in call_log:
        if entry["sid"] == sid:
            entry["status"] = status
            if duration:
                entry["duration"] = duration
            break
    logger.info(f"{sid}: {status}")
    return Response(status_code=204)


# ── Call log ──────────────────────────────────────────────────────────────────
@app.get("/calls")
async def get_calls():
    return JSONResponse(list(reversed(call_log)))


# ── Health ────────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "model": GEMINI_MODEL, "public_url": PUBLIC_URL}


# ── WebSocket audio bridge ────────────────────────────────────────────────────
@app.websocket("/media-stream")
async def media_stream(twilio_ws: WebSocket):
    await twilio_ws.accept()
    logger.info("Twilio media stream connected")
    stream_sid = None

    try:
        async with websockets.connect(GEMINI_WS_URL) as gemini_ws:
            logger.info(f"Connected to Gemini: {GEMINI_MODEL}")

            # Send setup
            await gemini_ws.send(json.dumps(GEMINI_SETUP))
            setup_ack = await gemini_ws.recv()
            logger.info(f"Gemini setup ack: {setup_ack[:120]}")

            # Trigger opening greeting
            # ✅ New
            await _say(gemini_ws,
                       "Greet the caller warmly. Introduce yourself as Ashutosh's AI assistant. Say you can answer questions about Ashutosh's skills, projects, experience, and background. Ask how you can help.")
            async def from_twilio():
                nonlocal stream_sid
                try:
                    async for raw in twilio_ws.iter_text():
                        data  = json.loads(raw)
                        event = data.get("event")

                        if event == "start":
                            stream_sid = data["start"]["streamSid"]
                            logger.info(f"Stream SID: {stream_sid}")

                        elif event == "media":
                            # Convert mulaw 8kHz → PCM 16kHz for Gemini
                            pcm_16k_b64 = mulaw8k_to_pcm16k(data["media"]["payload"])
                            await gemini_ws.send(json.dumps({
                                "realtime_input": {
                                    "media_chunks": [{
                                        "mime_type": "audio/pcm;rate=16000",
                                        "data":      pcm_16k_b64
                                    }]
                                }
                            }))

                        elif event == "stop":
                            logger.info("Twilio stream stopped")
                            break

                except WebSocketDisconnect:
                    logger.info("Twilio disconnected")

            async def from_gemini():
                try:
                    async for msg in gemini_ws:
                        response = json.loads(msg)
                        parts = (
                            response
                            .get("serverContent", {})
                            .get("modelTurn", {})
                            .get("parts", [])
                        )
                        for part in parts:
                            if "inlineData" in part and stream_sid:
                                # Convert Gemini PCM 24kHz → mulaw 8kHz for Twilio
                                mulaw_b64 = pcm24k_to_mulaw8k(part["inlineData"]["data"])
                                await twilio_ws.send_text(json.dumps({
                                    "event":     "media",
                                    "streamSid": stream_sid,
                                    "media":     {"payload": mulaw_b64}
                                }))
                except Exception as e:
                    logger.error(f"Gemini receive error: {e}")

            await asyncio.gather(from_twilio(), from_gemini())

    except Exception as e:
        logger.error(f"Media stream error: {e}")

    logger.info("Session ended")


async def _say(ws, text: str):
    """Send a text prompt to Gemini to trigger a spoken response."""
    await ws.send(json.dumps({
        "client_content": {
            "turns":         [{"role": "user", "parts": [{"text": text}]}],
            "turn_complete": True
        }
    }))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=int(os.getenv("PORT", 8081)))