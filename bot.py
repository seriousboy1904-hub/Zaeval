import asyncio
import sqlite3
import json
import os
import math
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.exceptions import TelegramBadRequest

API_TOKEN = 'SIZNING_TOKEN'  # tokenni shu yerga yoz
GEOJSON_FILE = 'locations.json'
DB_FILE = 'taxi_queue.db'

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# --- DB init ---
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS queue 
                      (user_id INTEGER PRIMARY KEY, name TEXT, station_name TEXT, 
                       lat REAL, lon REAL, joined_at TEXT, status TEXT DEFAULT 'online', 
                       msg_id INTEGER, is_active INTEGER DEFAULT 1)''')
    conn.commit()
    conn.close()

# --- Location update ---
def update_location_db(user_id, name, station_name, lat, lon, force_active=False):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT is_active FROM queue WHERE user_id=?", (user_id,))
    row = cursor.fetchone()

    if row and row[0] == 0 and not force_active:
        conn.close()
        return False

    if row:
        cursor.execute("UPDATE queue SET lat=?, lon=?, station_name=?, is_active=1 WHERE user_id=?",
                       (lat, lon, station_name, user_id))
    else:
        cursor.execute("INSERT INTO queue (user_id,name,station_name,lat,lon,joined_at,status,is_active) VALUES (?,?,?,?,?,?, 'online',1)",
                       (user_id,name,station_name,lat,lon,datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()
    return True

# --- Distance / closest station ---
def calculate_distance(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi, dlambda = math.radians(lat2-lat1), math.radians(lon2-lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

def find_closest(u_lat, u_lon):
    if not os.path.exists(GEOJSON_FILE): return "Noma'lum", 0
    with open(GEOJSON_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
    closest, min_dist = None, float('inf')
    for feat in data.get('features', []):
        coords = feat.get('geometry', {}).get('coordinates')
        name = feat.get('properties', {}).get('name', "Bekat")
        dist = calculate_distance(u_lat, u_lon, coords[1], coords[0])
        if dist < min_dist:
            min_dist, closest = dist, name
    return closest, min_dist

# --- Live queue text ---
def get_live_queue_text(user_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT station_name, is_active FROM queue WHERE user_id=?", (user_id,))
    res = cursor.fetchone()
    if not res or res[1] == 0:
        conn.close()
        return "📴 Siz hozir oflaynsiz. Navbatga turish uchun Live Location yuboring."

    st_name = res[0]
    cursor.execute("SELECT user_id,name,status FROM queue WHERE station_name=? AND is_active=1 ORDER BY joined_at ASC", (st_name,))
    drivers = cursor.fetchall()
    conn.close()

    total = len(drivers)
    my_pos = next((i for i, d in enumerate(drivers, 1) if d[0]==user_id), 0)

    driver_list = ""
    for i, (d_id,name,status) in enumerate(drivers,1):
        icon = "✅" if status=="online" else "☕️"
        mark = "👉 " if d_id==user_id else ""
        driver_list += f"{mark}{i}. {name} {icon}\n"

    return f"📍 <b>Bekat: {st_name}</b>\n🔢 <b>Navbatingiz: {my_pos}/{total}</b>\n\n📋 <b>Ro'yxat:</b>\n{driver_list}\n⌛️ <i>Yangilandi: {datetime.now().strftime('%H:%M:%S')}</i>"

# --- Auto-refresh ---
async def global_refresh():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id,msg_id FROM queue WHERE is_active=1 AND msg_id IS NOT NULL")
    active_drivers = cursor.fetchall()
    conn.close()

    for user_id,msg_id in active_drivers:
        try:
            await bot.edit_message_text(chat_id=user_id,message_id=msg_id,
                                        text=get_live_queue_text(user_id),
                                        parse_mode="HTML")
        except TelegramBadRequest:
            pass
        except Exception:
            continue

async def auto_loop():
    while True:
        await global_refresh()
        await asyncio.sleep(5)

# --- Handlers ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    guide_text = (
        "👋 <b>Assalomu alaykum!</b>\n\n"
        "Navbatga turish uchun <b>Live Location</b> yuboring."
    )
    await message.answer(guide_text, parse_mode="HTML", reply_markup=ReplyKeyboardRemove())

@dp.message(F.location)
async def handle_location(message: types.Message):
    if message.location.live_period is None:
        await message.answer("⚠️ Iltimos, Live Location yuboring!")
        return
    st_name,_ = find_closest(message.location.latitude,message.location.longitude)
    update_location_db(message.from_user.id,message.from_user.full_name,st_name,
                       message.location.latitude,message.location.longitude,force_active=True)
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton("☕️ Pauza"),KeyboardButton("📴 Offline")]],resize_keyboard=True)
    sent_msg = await message.answer(get_live_queue_text(message.from_user.id),reply_markup=kb,parse_mode="HTML")
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE queue SET msg_id=? WHERE user_id=?",(sent_msg.message_id,message.from_user.id))
    conn.commit()
    conn.close()
    await global_refresh()

# --- Main ---
async def main():
    init_db()
    asyncio.create_task(auto_loop())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())