import os
from collections import defaultdict, deque
import httpx
from fastapi import FastAPI, Request
from google import genai
from google.genai import types

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "fmn-secret-key").strip()

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is missing.")
if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY environment variable is missing.")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
client = genai.Client(api_key=GEMINI_API_KEY)

# Ajiye tarihin tattaunawa na karshe (sakonni 10) ga kowane chat_id
chat_histories: dict[int, deque[types.Content]] = defaultdict(lambda: deque(maxlen=10))

SYSTEM_INSTRUCTION = (
    "Sunanka FMN AI, mataimaki mai wayo, ladabi, da taimako. "
    "Ka amsa tambayoyin masu amfani da kyau, a bayyane, kuma da gaskiya. "
    "Idan mai amfani ya yi magana da Hausa, ka amsa da kyawawan Hausa; "
    "idan da Turanci ko wani harshe ne, ka bi harshen da ya yi amfani da shi."
)

app = FastAPI(title="FMN AI Telegram Bot")


async def send_telegram_message(http: httpx.AsyncClient, chat_id: int, text: str) -> None:
    # Telegram yana da iyaka na kimanin haruffa 4096 a sako daya
    chunk_size = 4000
    for i in range(0, len(text), chunk_size):
        part = text[i : i + chunk_size]
        await http.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": chat_id, "text": part},
            timeout=20.0,
        )


async def send_typing_action(http: httpx.AsyncClient, chat_id: int) -> None:
    await http.post(
        f"{TELEGRAM_API}/sendChatAction",
        json={"chat_id": chat_id, "action": "typing"},
        timeout=10.0,
    )


async def generate_fmn_reply(chat_id: int, user_text: str) -> str:
    history = list(chat_histories[chat_id])
    user_content = types.Content(
        role="user",
        parts=[types.Part.from_text(text=user_text)],
    )
    contents = history + [user_content]

    response = await client.aio.models.generate_content(
        model="gemini-2.5-flash",
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            temperature=0.7,
        ),
    )

    reply_text = (response.text or "").strip()
    if not reply_text:
        reply_text = "Yi hakuri, ban samu damar rubuta amsa yanzu ba. Sake gwadawa."

    chat_histories[chat_id].append(user_content)
    chat_histories[chat_id].append(
        types.Content(
            role="model",
            parts=[types.Part.from_text(text=reply_text)],
        )
    )
    return reply_text


@app.get("/")
async def health_check():
    return {"status": "ok", "bot": "FMN AI"}


@app.post("/webhook")
async def telegram_webhook(request: Request):
    secret_header = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if WEBHOOK_SECRET and secret_header != WEBHOOK_SECRET:
        return {"ok": False}

    update = await request.json()
    message = update.get("message") or update.get("edited_message")
    if not message:
        return {"ok": True}

    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text = message.get("text")

    if not chat_id or not text:
        return {"ok": True}

    text = text.strip()
    async with httpx.AsyncClient() as http:
        if text == "/start":
            chat_histories[chat_id].clear()
            await send_telegram_message(
                http,
                chat_id,
                "Assalamu alaikum! Maraba da zuwa FMN AI.\n\n"
                "Ni mataimakin AI ne wanda ke taimakawa wajen amsa tambayoyi, "
                "rubutu, karatu, coding, da bayani a harsuna daban-daban.\n\n"
                "Rubuta tambayarka ko abin da kake bukata yanzu!",
            )
            return {"ok": True}

        if text == "/clear":
            chat_histories[chat_id].clear()
            await send_telegram_message(
                http,
                chat_id,
                "An goge tarihin tattaunawarmu ta baya. Zamu iya farawa sabuwa!",
            )
            return {"ok": True}

        await send_typing_action(http, chat_id)
        try:
            reply = await generate_fmn_reply(chat_id, text)
        except Exception:
            reply = (
                "Akwai matsala ta dan lokaci wajen tuntubar AI. "
                "Don Allah ka sake tura sakonka bayan dan lokaci."
            )

        await send_telegram_message(http, chat_id, reply)

    return {"ok": True}
