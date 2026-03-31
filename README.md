# 📞 Twilio + Gemini Voice Agent
### Replaces VideoSDK — runs 100% locally

---

## Architecture

```
Caller dials your Twilio number
        ↓
    Twilio
        ↓  (HTTP POST)
/incoming-call  ← your server returns TwiML with WebSocket URL
        ↓
    Twilio opens WebSocket to /media-stream
        ↓
Your server bridges audio ↔ Gemini Realtime API
        ↓
Gemini responds with voice audio
        ↓
Audio sent back through Twilio → Caller hears AI voice
```

---

## Step 1 — Install Dependencies

```bash
pip install -r requirements.txt
```

---

## Step 2 — Set Up Environment Variables

```bash
cp .env.example .env
# Edit .env and fill in your keys
```

You need:
- `GOOGLE_API_KEY` → from https://aistudio.google.com/app/apikey
- `TWILIO_ACCOUNT_SID` + `TWILIO_AUTH_TOKEN` → from https://console.twilio.com
- `PUBLIC_URL` → your public server URL (see Step 3)

---

## Step 3 — Expose Your Local Server (ngrok)

Twilio needs a **public URL** to reach your server.

```bash
# Install ngrok: https://ngrok.com/download
ngrok http 8081
```

Copy the HTTPS URL (e.g. `https://abc123.ngrok-free.app`)
and set it as `PUBLIC_URL` in your `.env` file.

---

## Step 4 — Start the Server

```bash
python server.py
```

Server runs on `http://localhost:8081`

---

## Step 5 — Configure Twilio Webhook

1. Go to https://console.twilio.com
2. Click **Phone Numbers → Manage → Active Numbers**
3. Click your number
4. Under **Voice & Fax → A call comes in**, set:
   - **Webhook:** `https://your-ngrok-url.ngrok-free.app/incoming-call`
   - **HTTP Method:** `POST`
5. Click **Save**

---

## Step 6 — Test It!

Call your Twilio number. You should:
1. Hear "Please wait while we connect you..."
2. Be connected to Gemini AI voice (Leda)
3. Have a real-time voice conversation!

---

## Customization

### Change the AI Personality
Edit `SYSTEM_INSTRUCTION` in `server.py`:
```python
SYSTEM_INSTRUCTION = """You are a booking assistant for a dental clinic.
Ask for the patient's name, preferred date, and type of appointment."""
```

### Change the Voice
Edit the voice name in `GEMINI_SETUP`:
```python
"voice_name": "Leda"   # Options: Puck, Charon, Kore, Fenrir, Aoede, Leda, Orus, Zephyr
```

### Change the Gemini Model
```python
GEMINI_MODEL = "gemini-2.0-flash-live-001"
# or: gemini-2.5-flash-native-audio-preview-12-2025
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Twilio can't reach your server | Make sure ngrok is running and PUBLIC_URL is correct |
| No audio from Gemini | Check GOOGLE_API_KEY is valid |
| Call connects but silence | Check server logs for Gemini errors |
| 403 from Gemini | API key doesn't have Gemini Live access |

---

## Files

```
twilio_gemini_agent/
├── server.py          ← Main FastAPI server
├── requirements.txt   ← Python dependencies
├── .env.example       ← Environment variables template
└── README.md          ← This file
```
