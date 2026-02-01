import json
import math
import sqlite3
import asyncio
import os
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.exceptions import TelegramBadRequest

# --- SOZLAMALAR ---
API_TOKEN = os.getenv("BOT_TOKEN")
GEOJSON_FILE = 'locations.json'
DB_FILE = 'taxi_queue.db'
ALLOWED_RADIUS = 500  # Metrda

if not API_TOKEN:
    raise ValueError("BOT_TOKEN topilmadi!")

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# --- BAZA BILAN ISHLASH (Xatoni tuzatuvchi qism) ---
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Jadvalni yaratish
    cursor.execute('''CREATE TABLE IF NOT EXISTS queue 
                      (user_id INTEGER PRIMARY KEY, name TEXT, station_name TEXT, 
                       lat REAL, lon REAL, joined_at TEXT, status TEXT DEFAULT 'online', 
                       msg_id INTEGER, is_active INTEGER DEFAULT 1, last_notified INTEGER DEFAULT 0)''')
    
    # Mavjud bazaga yangi ustunlarni qo'shish (agar ular yo'q bo'lsa)
    cursor.execute("PRAGMA table_info(queue)")
    columns = [column[1] for column in cursor.fetchall()]
    
    if 'last_notified' not in columns:
        cursor.execute("ALTER TABLE queue ADD COLUMN last_notified INTEGER DEFAULT 0")
    if 'is_active' not in columns:
        cursor.execute("ALTER TABLE queue ADD COLUMN is_active INTEGER DEFAULT 1")
    if 'msg_id' not in columns:
        cursor.execute("ALTER TABLE queue ADD COLUMN msg_id INTEGER")
        
    conn.commit()
    conn.close()

# --- GEOMATEMATIKA ---
def calculate_distance(lat1, lon1, lat2, lon2):
    R = 6371000 
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi, dlambda = math.radians(lat2-lat1), math.radians(lon2-lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2) * math.sin(dlambda/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

def find_closest_station(u_lat, u_lon):
    if not os.path.exists(GEOJSON_FILE): return "Noma'lum", 999999
    with open(GEOJSON_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
    closest_name, min_dist = "Noma'lum", float('inf')
    for feat in data.get('features', []):
        coords = feat.get('geometry', {}).get('coordinates')
        dist = calculate_distance(u_lat, u_lon, coords[1], coords[0])
        if dist < min_dist:
            min_dist, closest_name = dist, feat.get('properties', {}).get('name', "Bekat")
    return closest_name, min_dist

# --- JONLI STATUS MATNI ---
def get_live_status(user_id):
    conn = sqlite3.connect(DB_FILE); cursor = conn.cursor()
    cursor.execute("SELECT station_name, is_active, lat, lon FROM queue WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    if not res or res[1] == 0: 
        conn.close()
        return "📴 Oflayn. Navbatga turish uchun Live Location yuboring.", 0
    
    st_name, active, u_lat, u_lon = res
    _, dist = find_closest_station(u_lat, u_lon)
    
    cursor.execute("SELECT user_id, name, status FROM queue WHERE station_name = ? AND is_active = 1 ORDER BY joined_at ASC", (st_name,))
    drivers = cursor.fetchall()
    conn.close()

    total = len(drivers)
    my_pos = next((i for i, d in enumerate(drivers, 1) if d[0] == user_id), 0)
    
    dist_str = f"{int(dist)}m" if dist < 1000 else f"{round(dist/1000, 1)}km"
    status_icon = "⚠️ Uzoqlashdingiz!" if dist > ALLOWED_RADIUS else "✅ Bekatdasiz"

    driver_list = ""
    for i, (d_id, name, status) in enumerate(drivers, 1):
        icon = "✅" if status == "online" else "☕️"
        mark = "👉 " if d_id == user_id else ""
        driver_list += f"{mark}{i}. {name} {icon} {'(Siz)' if d_id == user_id else ''}\n"

    text = (f"📍 <b>{st_name}</b>\n"
            f"📏 Masofa: <b>{dist_str}</b> ({status_icon})\n"
            f"🔢 Navbatingiz: <b>{my_pos}/{total}</b>\n\n"
            f"{driver_list}\n"
            f"⌛️ <i>Yangilandi: {datetime.now().strftime('%H:%M:%S')}</i>")
    return text, my_pos

# --- REAL-TIME MONITORING LOOP ---
async def global_update_loop():
    while True:
        try:
            conn = sqlite3.connect(DB_FILE); cursor = conn.cursor()
            cursor.execute("SELECT user_id, msg_id, station_name, lat, lon, last_notified FROM queue WHERE is_active = 1")
            drivers = cursor.fetchall(); conn.close()

            for user_id, msg_id, st_name, lat, lon, last_notified in drivers:
                # Masofani tekshirish
                _, dist = find_closest_station(lat, lon)
                if dist > ALLOWED_RADIUS + 200:
                    try:
                        await bot.send_message(user_id, "❌ <b>Masofa juda uzoq!</b> Siz navbatdan chiqarildingiz.")
                        conn = sqlite3.connect(DB_FILE); cursor = conn.cursor()
                        cursor.execute("UPDATE queue SET is_active = 0 WHERE user_id = ?", (user_id,))
                        conn.commit(); conn.close()
                        continue
                    except: pass

                # Xabarni tahrirlash va Bildirishnoma
                try:
                    text, pos = get_live_status(user_id)
                    # 1-o'ringa chiqsa signal berish
                    if pos == 1 and last_notified == 0:
                        await bot.send_message(user_id, "🔔 <b>DIQQAT! Siz 1-o'ringa chiqdingiz!</b> Tayyor turing.", parse_mode="HTML")
                        conn = sqlite3.connect(DB_FILE); cursor = conn.cursor()
                        cursor.execute("UPDATE queue SET last_notified = 1 WHERE user_id = ?", (user_id,))
                        conn.commit(); conn.close()
                    elif pos != 1 and last_notified == 1:
                        conn = sqlite3.connect(DB_FILE); cursor = conn.cursor()
                        cursor.execute("UPDATE queue SET last_notified = 0 WHERE user_id = ?", (user_id,))
                        conn.commit(); conn.close()

                    if msg_id:
                        await bot.edit_message_text(chat_id=user_id, message_id=msg_id, text=text, parse_mode="HTML")
                except TelegramBadRequest: pass # Matn bir xil bo'lsa xato bermasligi uchun
                except Exception: continue
        except Exception as e:
            print(f"Loop xatosi: {e}")
        
        await asyncio.sleep(5)

# --- HANDLERLAR ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("👋 Navbatga turish uchun <b>Live Location</b> yuboring.", parse_mode="HTML")

@dp.message(F.location)
async def handle_location(message: types.Message):
    if not message.location.live_period:
        return await message.answer("⚠️ Iltimos, <b>Live Location</b> (Jonli lokatsiya) yuboring!")

    st_name, dist = find_closest_station(message.location.latitude, message.location.longitude)
    if dist > ALLOWED_RADIUS:
        return await message.answer(f"❌ Bekatdan uzoqdasiz ({int(dist)}m). Navbatga kirish uchun yaqinroq keling.")

    conn = sqlite3.connect(DB_FILE); cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO queue (user_id, name, station_name, lat, lon, joined_at, status, is_active, last_notified) VALUES (?, ?, ?, ?, ?, ?, 'online', 1, 0)",
                   (message.from_user.id, message.from_user.full_name, st_name, message.location.latitude, message.location.longitude, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit(); conn.close()

    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="☕️ Pauza"), KeyboardButton(text="📴 Offline")]], resize_keyboard=True)
    text, _ = get_live_status(message.from_user.id)
    sent_msg = await message.answer(text, reply_markup=kb, parse_mode="HTML")
    
    conn = sqlite3.connect(DB_FILE); cursor = conn.cursor()
    cursor.execute("UPDATE queue SET msg_id = ? WHERE user_id = ?", (sent_msg.message_id, message.from_user.id))
    conn.commit(); conn.close()

@dp.edited_message(F.location)
async def handle_edits(message: types.Message):
    st_name, _ = find_closest_station(message.location.latitude, message.location.longitude)
    conn = sqlite3.connect(DB_FILE); cursor = conn.cursor()
    cursor.execute("UPDATE queue SET lat = ?, lon = ?, station_name = ? WHERE user_id = ? AND is_active = 1", 
                   (message.location.latitude, message.location.longitude, st_name, message.from_user.id))
    conn.commit(); conn.close()

@dp.message(F.text == "📴 Offline")
async def cmd_offline(message: types.Message):
    conn = sqlite3.connect(DB_FILE); cursor = conn.cursor()
    cursor.execute("UPDATE queue SET is_active = 0 WHERE user_id = ?", (message.from_user.id,))
    conn.commit(); conn.close()
    await message.answer("👋 Navbatdan chiqdingiz.", reply_markup=ReplyKeyboardRemove())

@dp.message(F.text == "☕️ Pauza")
async def cmd_pause(message: types.Message):
    conn = sqlite3.connect(DB_FILE); cursor = conn.cursor()
    cursor.execute("UPDATE queue SET status = 'pauza' WHERE user_id = ?", (message.from_user.id,))
    conn.commit(); conn.close()
    await message.answer("☕️ Tanaffus.", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="▶️ Davom"), KeyboardButton(text="📴 Offline")]], resize_keyboard=True))

@dp.message(F.text == "▶️ Davom")
async def cmd_resume(message: types.Message):
    conn = sqlite3.connect(DB_FILE); cursor = conn.cursor()
    cursor.execute("UPDATE queue SET status = 'online' WHERE user_id = ?", (message.from_user.id,))
    conn.commit(); conn.close()
    await message.answer("🚀 Ishga qaytdingiz.", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="☕️ Pauza"), KeyboardButton(text="📴 Offline")]], resize_keyboard=True))

async def main():
    init_db()
    asyncio.create_task(global_update_loop())
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
