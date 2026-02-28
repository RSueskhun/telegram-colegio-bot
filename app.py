import os
import json
import requests
from fastapi import FastAPI, Request

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

app = FastAPI()

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
SHEET_ID = os.environ.get("SHEET_ID", "")
SHEETS_SA_JSON = os.environ.get("SHEETS_SA_JSON", "")

DIRECTORIO_TAB = os.environ.get("DIRECTORIO_TAB", "DIRECTORIO")
PLANTILLAS_TAB = os.environ.get("PLANTILLAS_TAB", "PLANTILLAS")

STATE = {}  # key: user_id -> dict


@app.get("/")
def health_check():
    return {"status": "server running"}


# ---------- Google Sheets client ----------
def sheets_client():
    if not SHEETS_SA_JSON:
        raise ValueError("SHEETS_SA_JSON no configurado")
    if not SHEET_ID:
        raise ValueError("SHEET_ID no configurado")

    try:
        sa_info = json.loads(SHEETS_SA_JSON)
    except Exception as e:
        raise ValueError(f"Error parseando SHEETS_SA_JSON: {e}")

    creds = Credentials.from_service_account_info(
        sa_info,
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    return build("sheets", "v4", credentials=creds).spreadsheets()


def read_range(tab_name: str, a1: str):
    svc = sheets_client()
    rng = f"{tab_name}!{a1}"
    resp = svc.values().get(spreadsheetId=SHEET_ID, range=rng).execute()
    return resp.get("values", [])


def read_table(tab_name: str):
    rows = read_range(tab_name, "A:Z")
    if not rows:
        return [], []
    headers = [h.strip() for h in rows[0]]
    data = rows[1:]
    return headers, data


# ---------- Data helpers ----------
def load_directorio():
    headers, rows = read_table(DIRECTORIO_TAB)
    idx = {h: i for i, h in enumerate(headers)}
    required = ["CURSO", "ESTUDIANTE", "PADRE_NOMBRE", "PADRE_EMAIL", "MADRE_NOMBRE", "MADRE_EMAIL"]
    for r in required:
        if r not in idx:
            raise ValueError(f"Falta columna en {DIRECTORIO_TAB}: {r}")

    out = []
    for row in rows:
        def get(col):
            i = idx[col]
            if i < len(row) and isinstance(row[i], str):
                return row[i].strip()
            return str(row[i]).strip() if i < len(row) else ""
        out.append({
            "CURSO": get("CURSO"),
            "ESTUDIANTE": get("ESTUDIANTE"),
            "PADRE_NOMBRE": get("PADRE_NOMBRE"),
            "PADRE_EMAIL": get("PADRE_EMAIL"),
            "MADRE_NOMBRE": get("MADRE_NOMBRE"),
            "MADRE_EMAIL": get("MADRE_EMAIL"),
        })
    return out


def load_plantillas():
    headers, rows = read_table(PLANTILLAS_TAB)
    idx = {h: i for i, h in enumerate(headers)}
    required = ["PLANTILLA_ID", "ASUNTO", "CUERPO"]
    for r in required:
        if r not in idx:
            raise ValueError(f"Falta columna en {PLANTILLAS_TAB}: {r}")

    out = []
    for row in rows:
        def get(col):
            i = idx[col]
            if i < len(row) and isinstance(row[i], str):
                return row[i].strip()
            return str(row[i]).strip() if i < len(row) else ""
        pid = get("PLANTILLA_ID")
        if pid:
            out.append({
                "PLANTILLA_ID": pid,
                "ASUNTO": get("ASUNTO"),
                "CUERPO": get("CUERPO"),
            })
    return out


def render_vars(text: str, data: dict) -> str:
    out = (text or "").replace("\\n", "\n")
    for k, v in data.items():
        out = out.replace("{{" + k + "}}", v or "")
        out = out.replace("{{ " + k + " }}", v or "")
    return out


# ---------- Telegram helpers ----------
def tg(method: str, payload: dict):
    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_TOKEN no configurado")
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"
    r = requests.post(url, json=payload, timeout=15)
    return r.json()


def send_message(chat_id: int, text: str, keyboard=None):
    payload = {"chat_id": chat_id, "text": text}
    if keyboard:
        payload["reply_markup"] = keyboard
    tg("sendMessage", payload)


def answer_callback(callback_id: str):
    tg("answerCallbackQuery", {"callback_query_id": callback_id})


def inline_keyboard(buttons, cols=2):
    rows = []
    row = []
    for b in buttons:
        row.append({"text": b["text"], "callback_data": b["data"]})
        if len(row) >= cols:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return {"inline_keyboard": rows}


# ---------- Bot flow ----------
def start_flow(chat_id: int, user_id: int):
    directorio = load_directorio()
    cursos = sorted({r["CURSO"] for r in directorio if r["CURSO"]})
    if not cursos:
        send_message(chat_id, "No encontré cursos en DIRECTORIO.")
        return

    STATE[user_id] = {"step": "WAIT_COURSE"}
    send_message(
        chat_id,
        "Selecciona el curso:",
        inline_keyboard([{"text": c, "data": f"C|{c}"} for c in cursos], cols=2),
    )


def pick_course(chat_id: int, user_id: int, curso: str):
    directorio = load_directorio()
    estudiantes = [r["ESTUDIANTE"] for r in directorio if r["CURSO"] == curso and r["ESTUDIANTE"]]
    if not estudiantes:
        send_message(chat_id, f"No encontré estudiantes para {curso}.")
        return

    STATE[user_id] = {"step": "WAIT_STUDENT", "curso": curso}
    send_message(
        chat_id,
        f"Curso: {curso}\nSelecciona estudiante:",
        inline_keyboard([{"text": e, "data": f"S|{curso}|{e}"} for e in estudiantes], cols=1),
    )


def pick_student(chat_id: int, user_id: int, curso: str, estudiante: str):
    directorio = load_directorio()
    row = next((r for r in directorio if r["CURSO"] == curso and r["ESTUDIANTE"] == estudiante), None)
    if not row:
        send_message(chat_id, "No encontré ese estudiante.")
        return

    plantillas = load_plantillas()
    if not plantillas:
        send_message(chat_id, "No hay plantillas en PLANTILLAS.")
        return

    STATE[user_id] = {
        "step": "WAIT_TEMPLATE",
        "curso": curso,
        "estudiante": estudiante,
        "padre_nombre": row["PADRE_NOMBRE"],
        "padre_email": row["PADRE_EMAIL"],
        "madre_nombre": row["MADRE_NOMBRE"],
        "madre_email": row["MADRE_EMAIL"],
    }

    send_message(
        chat_id,
        f"Estudiante: {estudiante}\nPadre: {row['PADRE_EMAIL'] or '(sin correo)'}\nMadre: {row['MADRE_EMAIL'] or '(sin correo)'}\n\nSelecciona plantilla:",
        inline_keyboard([{"text": p["PLANTILLA_ID"], "data": f"T|{p['PLANTILLA_ID']}"} for p in plantillas], cols=2),
    )


def pick_template(chat_id: int, user_id: int, plantilla_id: str):
    st = STATE.get(user_id, {})
    plantillas = load_plantillas()
    tpl = next((p for p in plantillas if p["PLANTILLA_ID"] == plantilla_id), None)
    if not tpl:
        send_message(chat_id, "No encontré esa plantilla.")
        return

    data = {
        "CURSO": st.get("curso", ""),
        "ESTUDIANTE": st.get("estudiante", ""),
        "PADRE": st.get("padre_nombre", ""),
        "MADRE": st.get("madre_nombre", ""),
    }

    asunto = render_vars(tpl["ASUNTO"], data)
    cuerpo = render_vars(tpl["CUERPO"], data)

    st.update({
        "step": "PREVIEW",
        "plantilla_id": plantilla_id,
        "asunto": asunto,
        "cuerpo": cuerpo,
    })
    STATE[user_id] = st

    destinos = ", ".join([e for e in [st.get("padre_email", ""), st.get("madre_email", "")] if e]) or "(sin destinatarios)"
    preview = (
        "📌 Vista previa\n\n"
        f"Curso: {st.get('curso')}\n"
        f"Estudiante: {st.get('estudiante')}\n"
        f"Para: {destinos}\n"
        f"Plantilla: {plantilla_id}\n\n"
        f"Asunto:\n{asunto}\n\n"
        f"Cuerpo:\n{cuerpo}\n"
    )

    send_message(
        chat_id,
        preview,
        inline_keyboard([
            {"text": "✏️ Editar asunto", "data": "EA"},
            {"text": "✏️ Editar cuerpo", "data": "EC"},
            {"text": "✅ (Luego) Enviar", "data": "SEND"},
            {"text": "❌ Cancelar", "data": "CANCEL"},
        ], cols=1),
    )


# ---------- Webhook ----------
@app.post("/")
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
    except Exception:
        return {"status": "ok"}

    if "callback_query" in data:
        cq = data["callback_query"]
        callback_id = cq["id"]
        user_id = cq["from"]["id"]
        chat_id = cq["message"]["chat"]["id"]
        payload = cq.get("data", "")

        answer_callback(callback_id)

        try:
            if payload.startswith("C|"):
                pick_course(chat_id, user_id, payload.split("|", 1)[1])
            elif payload.startswith("S|"):
                _, curso, estudiante = payload.split("|", 2)
                pick_student(chat_id, user_id, curso, estudiante)
            elif payload.startswith("T|"):
                pick_template(chat_id, user_id, payload.split("|", 1)[1])
            elif payload == "EA":
                st = STATE.get(user_id, {})
                st["step"] = "WAIT_ASUNTO"
                STATE[user_id] = st
                send_message(chat_id, "Escribe el asunto final:")
            elif payload == "EC":
                st = STATE.get(user_id, {})
                st["step"] = "WAIT_CUERPO"
                STATE[user_id] = st
                send_message(chat_id, "Pega/escribe el cuerpo final del correo:")
            elif payload == "CANCEL":
                STATE.pop(user_id, None)
                send_message(chat_id, "Cancelado. Usa /enviar para empezar de nuevo.")
            elif payload == "SEND":
                send_message(chat_id, "Perfecto. El flujo está listo ✅\nSiguiente paso: conectar el envío con tu Gmail institucional.")
        except Exception as e:
            send_message(chat_id, f"Error interno: {e}")

        return {"status": "ok"}

    if "message" in data:
        msg = data["message"]
        user_id = msg["from"]["id"]
        chat_id = msg["chat"]["id"]
        text = (msg.get("text") or "").strip()

        if text == "/start":
            send_message(chat_id, "Bot funcionando correctamente 🚀\nUsa /enviar para comenzar.")
            return {"status": "ok"}

        if text == "/enviar":
            try:
                start_flow(chat_id, user_id)
            except Exception as e:
                send_message(chat_id, f"Error leyendo Google Sheets: {e}")
            return {"status": "ok"}

        st = STATE.get(user_id, {})
        if st.get("step") == "WAIT_ASUNTO":
            st["asunto"] = text
            st["step"] = "PREVIEW"
            STATE[user_id] = st
            send_message(chat_id, "Listo (edición de asunto pendiente de ajustar). Usa /enviar por ahora.")
            return {"status": "ok"}

        if st.get("step") == "WAIT_CUERPO":
            st["cuerpo"] = text
            st["step"] = "PREVIEW"
            STATE[user_id] = st
            send_message(chat_id, "Listo (edición de cuerpo pendiente de ajustar). Usa /enviar por ahora.")
            return {"status": "ok"}

        send_message(chat_id, "Usa /enviar para iniciar.")
        return {"status": "ok"}

    return {"status": "ok"}