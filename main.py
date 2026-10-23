import os
import asyncio
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import uvicorn
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo,
    ReplyKeyboardMarkup,
    KeyboardButton,
    WebAppInfo as KBWebApp,
)
from bms_client import JBDReader

load_dotenv()

BMS_MAC_ADDRESS = os.getenv("BMS_MAC_ADDRESS", "AA:BB:CC:11:22:33")
BOT_TOKEN       = os.getenv("BOT_TOKEN", "")
WEBAPP_URL      = os.getenv("WEBAPP_URL", "")
POLL_INTERVAL   = int(os.getenv("POLL_INTERVAL", "3"))

bms = JBDReader(BMS_MAC_ADDRESS)
app = FastAPI()
bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher()


# ─── Фоновый опрос BMS ──────────────────────────────────────
async def bms_poller():
    while True:
        await bms.update()
        await asyncio.sleep(POLL_INTERVAL)


# ─── REST API ────────────────────────────────────────────────
@app.get("/api/bms")
async def get_bms_data():
    return bms.data


@app.get("/api/health")
async def health():
    return {"ok": True, "status": bms.data.get("status")}


@app.get("/")
async def root():
    return FileResponse("static/index.html")


app.mount("/static", StaticFiles(directory="static"), name="static")


# ─── Telegram Bot ────────────────────────────────────────────
def _status_emoji(direction: str) -> str:
    return {"charging": "⚡", "discharging": "🔋", "idle": "⏸"}.get(direction, "❓")


def _build_status_text(d: dict) -> str:
    emo = _status_emoji(d["direction"])
    errors_txt = ""
    if d["has_errors"]:
        lines = [f"  {e['icon']} {e['name']}" for e in d["errors"]]
        errors_txt = "\n🚨 *ОШИБКИ:*\n" + "\n".join(lines)

    cells_txt = ""
    if d["cells"]:
        cells_lines = [f"  #{i+1}: `{c:.3f} В`" for i, c in enumerate(d["cells"])]
        cells_txt = "\n🔗 *Ячейки:*\n" + "\n".join(cells_lines)
        cells_txt += f"\n  Δ разбаланс: `{d['cell_delta_mv']} мВ`"

    temps_txt = ""
    if d["temps"]:
        temps_lines = [f"  Датчик {i+1}: `{t}°C`" for i, t in enumerate(d["temps"])]
        temps_txt = "\n🌡 *Температуры:*\n" + "\n".join(temps_lines)

    return (
        f"{emo} *LiFePO4 — Полный статус*\n\n"
        f"📊 Заряд: `{d['soc']}%`\n"
        f"🔋 Напряжение: `{d['voltage']} В`\n"
        f"⚡ Ток: `{d['current']} А`\n"
        f"💡 Мощность: `{d['power']} Вт`\n"
        f"🔄 Циклы: `{d['cycles']}`\n"
        f"📦 Ёмкость: `{d['capacity_remain']} / {d['capacity_total']} Ач`\n"
        f"⏱ Прогноз: `{d['time_remaining_str']}`\n"
        f"🏥 Здоровье (SoH): `{d['soh_estimate']}%`\n"
        f"🔌 MOS заряд: `{'✅' if d['charge_mosfet'] else '❌'}`"
        f"  |  MOS разряд: `{'✅' if d['discharge_mosfet'] else '❌'}`\n"
        f"📅 Дата пр-ва: `{d['production_date']}`\n"
        f"🖥 Прошивка: `v{d['sw_version']}` | `{d['hw_version']}`\n"
        f"⏳ Аптайм: `{d['uptime_str']}`"
        f"{temps_txt}{cells_txt}{errors_txt}"
    )


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Дашборд", web_app=KBWebApp(url=WEBAPP_URL))],
            [KeyboardButton(text="📋 Статус"), KeyboardButton(text="🔋 Ячейки")],
            [KeyboardButton(text="🌡 Температура"), KeyboardButton(text="🚨 Ошибки")],
        ],
        resize_keyboard=True,
    )
    await message.answer(
        "👋 *Добро пожаловать в BMS Monitor!*\n\n"
        "Используйте кнопки ниже для быстрого доступа,\n"
        "или нажмите *📊 Дашборд* для полного мониторинга.",
        reply_markup=kb,
        parse_mode="Markdown",
    )


@dp.message(lambda m: m.text == "📋 Статус")
async def cmd_status(message: types.Message):
    d = bms.data
    text = _build_status_text(d)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Открыть дашборд", web_app=WebAppInfo(url=WEBAPP_URL))]
    ])
    await message.answer(text, reply_markup=kb, parse_mode="Markdown")


@dp.message(lambda m: m.text == "🔋 Ячейки")
async def cmd_cells(message: types.Message):
    d = bms.data
    if not d["cells"]:
        await message.answer("Данные ячеек пока не получены...")
        return
    lines = [f"#{i+1}:  `{c:.3f} В`  {'🟢' if abs(c - d['cell_avg']) < 0.005 else '🟡' if abs(c - d['cell_avg']) < 0.015 else '🔴'}"
             for i, c in enumerate(d["cells"])]
    text = (
        f"🔗 *Напряжения ячеек*\n\n"
        + "\n".join(lines) +
        f"\n\nМин: `{d['cell_min']:.3f} В`  |  Макс: `{d['cell_max']:.3f} В`\n"
        f"Средн: `{d['cell_avg']:.3f} В`  |  Δ: `{d['cell_delta_mv']} мВ`"
    )
    await message.answer(text, parse_mode="Markdown")


@dp.message(lambda m: m.text == "🌡 Температура")
async def cmd_temp(message: types.Message):
    d = bms.data
    if not d["temps"]:
        await message.answer("Датчики температуры пока не отвечают...")
        return
    lines = [f"Датчик {i+1}: `{t}°C`  {'🔥' if t > 45 else '✅' if t > 5 else '❄️'}"
             for i, t in enumerate(d["temps"])]
    text = (
        "🌡 *Температуры BMS*\n\n" +
        "\n".join(lines) +
        f"\n\nМакс: `{d['temp_max']}°C`  |  Мин: `{d['temp_min']}°C`  |  Средн: `{d['temp_avg']}°C`"
    )
    await message.answer(text, parse_mode="Markdown")


@dp.message(lambda m: m.text == "🚨 Ошибки")
async def cmd_errors(message: types.Message):
    d = bms.data
    if not d["has_errors"]:
        await message.answer("✅ *Ошибок нет!* Все системы в норме.", parse_mode="Markdown")
        return
    lines = [f"{e['icon']} *{e['name']}*  ⚠️ `{e['severity']}`" for e in d["errors"]]
    text = "🚨 *Активные ошибки / защиты:*\n\n" + "\n".join(lines)
    await message.answer(text, parse_mode="Markdown")


# ─── Запуск ──────────────────────────────────────────────────
async def main():
    asyncio.create_task(bms_poller())
    asyncio.create_task(dp.start_polling(bot))

    config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())
