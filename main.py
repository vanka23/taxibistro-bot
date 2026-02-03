import telebot
import math
import sqlite3
import threading
import time
import os
from telebot import types
from datetime import datetime

TOKEN = "8253782171:AAFib-Jsk7Bz-lGPNhlt0mANqNywuBF3vFo"
ADMIN_ID = 6119485226

TRIP_PRICE = 165
MIN_TRIP_TIME = 180  # 3 минуты
REAL_COMMISSION = 0.06
SHOW_COMMISSION = 0.03
GPS_TIMEOUT = 300  # 5 минут

bot = telebot.TeleBot(TOKEN)

# ----------------- БАЗА -----------------
db = sqlite3.connect("taxi.db", check_same_thread=False)
sql = db.cursor()

sql.execute("""
CREATE TABLE IF NOT EXISTS drivers (
    id INTEGER PRIMARY KEY,
    trips INTEGER,
    earned REAL,
    commission REAL,
    status TEXT,
    lat REAL,
    lon REAL,
    last_gps INTEGER,
    rating_sum INTEGER,
    rating_count INTEGER
)
""")
sql.execute("""
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    driver_id INTEGER,
    passenger_id INTEGER,
    price REAL,
    start_time INTEGER,
    end_time INTEGER,
    payment TEXT
)
""")
db.commit()

# ----------------- ФУНКЦИИ -----------------
def distance_km(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) \
        * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

def get_driver(user_id):
    sql.execute("SELECT * FROM drivers WHERE id=?", (user_id,))
    d = sql.fetchone()
    if not d:
        sql.execute("INSERT INTO drivers VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (user_id, 0, 0, 0, "offline", None, None, 0, 0, 0))
        db.commit()
        sql.execute("SELECT * FROM drivers WHERE id=?", (user_id,))
        d = sql.fetchone()
    return d

def update_driver_gps(driver_id, lat, lon):
    sql.execute("UPDATE drivers SET lat=?, lon=?, last_gps=? WHERE id=?",
                (lat, lon, int(time.time()), driver_id))
    db.commit()

def check_gps_timeout():
    now = int(time.time())
    sql.execute("SELECT id, last_gps, status FROM drivers WHERE status='free'")
    for d in sql.fetchall():
        driver_id, last_gps, status = d
        if last_gps and now - last_gps > GPS_TIMEOUT:
            sql.execute("UPDATE drivers SET status='offline' WHERE id=?", (driver_id,))
            bot.send_message(driver_id, "⚠️ Сняты с линии (GPS не обновлялся 5 минут)")
    db.commit()
    threading.Timer(60, check_gps_timeout).start()

check_gps_timeout()

# ----------------- START -----------------
@bot.message_handler(commands=["start"])
def start(message):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("🚖 Водитель","🧍 Пассажир")
    bot.send_message(message.chat.id,"Выберите роль:",reply_markup=kb)

# ----------------- РОЛЬ -----------------
@bot.message_handler(func=lambda m: m.text in ["🚖 Водитель","🧍 Пассажир"])
def role(message):
    cid = message.chat

