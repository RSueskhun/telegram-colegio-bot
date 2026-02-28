from fastapi import FastAPI, Request
import os
import requests
import uvicorn
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
import base64
from email.mime.text import MIMEText

app = FastAPI()

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GMAIL_USER = os.environ.get("GMAIL_USER")

@app.post("/")
async def telegram_webhook(request: Request):
    data = await request.json()

    if "message" in data:
        chat_id = data["message"]["chat"]["id"]
        text = data["message"].get("text", "")

        if text == "/start":
            send_message(chat_id, "Bot funcionando correctamente 🚀")
        elif text == "/enviar":
            send_message(chat_id, "Servidor funcionando. Siguiente paso: integrar Sheets.")
    
    return {"status": "ok"}


def send_message(chat_id, text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={
        "chat_id": chat_id,
        "text": text
    })


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)