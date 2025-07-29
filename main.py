# ===============  ВАШ БОТ + KEEP-ALIVE  ===============
import os
import logging
import datetime
from pathlib import Path

from docx import Document
from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    ContextTypes, CallbackQueryHandler
)
from telegram.helpers import escape

# -------------------------------------------------
# KEEP-ALIVE (Flask) — компактно внутри main.py
# -------------------------------------------------
from flask import Flask
import threading

_flask_app = Flask(__name__)

@_flask_app.route("/")
def _ping():
    return "ok", 200

def keep_alive():
    threading.Thread(
        target=lambda: _flask_app.run(host="0.0.0.0", port=8080),
        daemon=True
    ).start()

# -------------------------------------------------
# Переменные окружения
# -------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))

if not BOT_TOKEN or not ADMIN_ID:
    raise RuntimeError("Укажите BOT_TOKEN и ADMIN_ID в Secrets")

# -------------------------------------------------
# База данных
# -------------------------------------------------
DB_URL = "sqlite:///users.db"
engine = create_engine(DB_URL, echo=False, pool_pre_ping=True)
Base = declarative_base()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

class UserRecord(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, unique=True, nullable=False)
    username = Column(String)
    status = Column(String, default="pending")
    requested_at = Column(DateTime, default=datetime.datetime.utcnow)

Base.metadata.create_all(bind=engine)

# -------------------------------------------------
# Логгирование
# -------------------------------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# -------------------------------------------------
# data.docx
# -------------------------------------------------
DATA_FILE = Path("data.docx")

def create_sample_docx(path: Path):
    doc = Document()
    doc.add_paragraph("Ключевое слово: пример")
    doc.add_paragraph("Описание: Это тестовое описание.")
    doc.save(path)
    logger.info("Создан data.docx")

def load_data(file_path: Path) -> dict:
    if not file_path.exists():
        create_sample_docx(file_path)

    try:
        doc = Document(file_path)
    except Exception as e:
        logger.exception("Ошибка чтения docx: %s", e)
        return {}

    data, current_keyword = {}, None
    for para in doc.paragraphs:
        text = para.text.strip()
        if text.startswith("Ключевое слово:"):
            current_keyword = text.replace("Ключевое слово:", "").strip().lower()
        elif text.startswith("Описание:") and current_keyword:
            description = text.replace("Описание:", "").strip()
            data[current_keyword] = description
            current_keyword = None
    return data

DATA = load_data(DATA_FILE)
logger.info("Загружено %d записей", len(DATA))

# -------------------------------------------------
# Утилиты
# -------------------------------------------------
def is_approved(user_id: int) -> bool:
    with SessionLocal() as session:
        user = session.query(UserRecord).filter_by(user_id=user_id).first()
        return bool(user and user.status == "approved")

# -------------------------------------------------
# Хэндлеры
# -------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    with SessionLocal() as session:
        record = session.query(UserRecord).filter_by(user_id=user.id).first()
        if record and record.status == "approved":
            await update.message.reply_text(
                "✅ Добро пожаловать!\n\nОтправьте любое слово для поиска. "
                "В кладовке можно найти экстремистов, террористов и других лиц, связанных с экстремизмом и терроризмом, "
                "в том числе запрещённых проповедников и организаций. "
                "Также можно найти запрещённые НС, СЯ и ЯВ. "
                "Поиск осуществляется как по одному-двум словам, так и по его части; "
                "выдаётся 7 наиболее подходящих совпадений, удачи тебе в поисках."
            )
            return
        if record and record.status == "blocked":
            await update.message.reply_text("❌ Вам отказано в доступе.")
            return

        if not record:
            session.add(
                UserRecord(user_id=user.id, username=user.username or "N/A")
            )
            session.commit()

            keyboard = [[InlineKeyboardButton("Одобрить", callback_data=f"approve_{user.id}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"📬 Новая заявка:\n"
                     f"ID: {user.id}\n"
                     f"Имя: {user.full_name}\n"
                     f"Username: @{user.username or '—'}",
                reply_markup=reply_markup
            )

        await update.message.reply_text("📨 Ваша заявка отправлена администратору.")

async def approve_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return

    user_id = int(query.data.split("_")[1])
    with SessionLocal() as session:
        record = session.query(UserRecord).filter_by(user_id=user_id).first()
        if not record:
            await query.edit_message_text("❌ Пользователь не найден")
            return
        record.status = "approved"
        session.commit()

        await query.edit_message_text("✅ Пользователь одобрен")
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text="✅ Ваша заявка одобрена! Нажмите /start, чтобы начать."
            )
        except Exception as e:
            logger.warning(f"Не удалось уведомить пользователя {user_id}: {e}")

async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    with SessionLocal() as session:
        records = session.query(UserRecord).all()
        if not records:
            await update.message.reply_text("📝 Нет зарегистрированных пользователей.")
            return

        lines, keyboard = [], []
        for r in records:
            status = "✅" if r.status == "approved" else ("❌" if r.status == "blocked" else "⏳")
            lines.append(f"{status} <b>{r.user_id}</b> — {escape(r.username or 'N/A')}")
            keyboard.append([
                InlineKeyboardButton(
                    f"{'Заблокать' if r.status == 'approved' else 'Одобрить'} {r.user_id}",
                    callback_data=f"toggle_{r.user_id}"
                )
            ])

        await update.message.reply_text(
            "📋 Список пользователей:\n\n" + "\n".join(lines),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def toggle_user_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return

    user_id = int(query.data.split("_")[1])
    with SessionLocal() as session:
        record = session.query(UserRecord).filter_by(user_id=user_id).first()
        if not record:
            await query.edit_message_text("❌ Пользователь не найден.")
            return

        old_status = record.status
        if old_status == "approved":
            record.status = "blocked"
            status_text = "🔒 Пользователь заблокирован"
            notify_text = "❌ Ваш доступ отозван."
        else:
            record.status = "approved"
            status_text = "✅ Пользователь одобрен"
            notify_text = "✅ Ваша заявка одобрена!"

        session.commit()
        await query.edit_message_text(status_text)
        try:
            await context.bot.send_message(chat_id=user_id, text=notify_text)
        except Exception as e:
            logger.warning(f"Не удалось уведомить пользователя {user_id}: {e}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not is_approved(update.effective_user.id):
            await update.message.reply_text("❌ У вас нет доступа.")
            return

        query = update.message.text.strip().lower()
        if not query:
            await update.message.reply_text("🔍 Пустой запрос.")
            return

        matches = [
            (k, v) for k, v in DATA.items()
            if query in k or k in query
        ][:7]

        if not matches:
            await update.message.reply_text("🔍 Ничего не найдено.")
            return

        lines = [f"<b>{escape(k.capitalize())}</b>\n{escape(v)}" for k, v in matches]
        text = "\n\n".join(lines)
        if len(matches) == 7:
            text += "\n\n<i>Показано первые 7 совпадений</i>"
        await update.message.reply_text(text, parse_mode="HTML")

    except Exception as e:
        logger.exception("Ошибка в handle_message: %s", e)
        await update.message.reply_text("⚠️ Произошла ошибка, попробуйте позже.")

async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤷‍♂️ Неизвестная команда. Нажмите /start")

# -------------------------------------------------
# Запуск
# -------------------------------------------------
def main():
    keep_alive()  # запускаем Flask-пингер
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("users", users_command))
    application.add_handler(CallbackQueryHandler(approve_callback, pattern="^approve_"))
    application.add_handler(CallbackQueryHandler(toggle_user_status, pattern="^toggle_"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.COMMAND, unknown))

    logger.info("✅ Бот запущен на Replit")
    application.run_polling()

if __name__ == "__main__":
    main()