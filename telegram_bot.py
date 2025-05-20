from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes

async def get_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print(f"Chat ID: {update.effective_chat.id}")

app = ApplicationBuilder().token("7808292765:AAENrrFMirdwvD3-gXLBuoUFqbp4BOyRfCo").build()
app.add_handler(MessageHandler(filters.ALL, get_chat_id))
app.run_polling()
