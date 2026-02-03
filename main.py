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
    cid = message.chat.id
    if message.text=="🚖 Водитель":
        get_driver(cid)
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        kb.add("🟢 Выйти на линию","🔴 Уйти с линии","➕ Завершить поездку","📊 Конец смены")
        bot.send_message(cid,"Вы водитель\nКомиссия сервиса: 3%",reply_markup=kb)
    else:
        bot.send_message(cid,"📍 Отправьте свою геолокацию")
        # Пассажир выбирает способ оплаты позже

# ----------------- ВОДИТЕЛЬ ОНЛАЙН -----------------
@bot.message_handler(func=lambda m: m.text=="🟢 Выйти на линию")
def driver_online(message):
    cid = message.chat.id
    sql.execute("UPDATE drivers SET status='free' WHERE id=?", (cid,))
    db.commit()
    bot.send_message(cid,"📡 Вы вышли на линию\nОтправьте live геолокацию")

@bot.message_handler(func=lambda m: m.text=="🔴 Уйти с линии")
def driver_offline(message):
    cid = message.chat.id
    sql.execute("UPDATE drivers SET status='offline' WHERE id=?", (cid,))
    db.commit()
    bot.send_message(cid,"🔴 Вы ушли с линии")

# ----------------- ПРИЁМ GPS -----------------
@bot.message_handler(content_types=["location"])
def receive_location(message):
    cid = message.chat.id
    if message.location.live_period:
        update_driver_gps(cid,message.location.latitude,message.location.longitude)
    else:
        bot.send_message(cid,"📍 Отправьте live геопозицию (трансляцию)")

# ----------------- ЗАКАЗ -----------------
@bot.message_handler(func=lambda m: m.text=="📍 Заказать такси")
def order_taxi(message):
    cid = message.chat.id
    msg = bot.send_message(cid,"📍 Отправьте геолокацию для заказа")
    bot.register_next_step_handler(msg, handle_passenger_location)

def handle_passenger_location(message):
    if not message.location:
        bot.send_message(message.chat.id,"❌ Ошибка. Отправьте геолокацию")
        return
    lat, lon = message.location.latitude, message.location.longitude
    # поиск ближайшего водителя
    sql.execute("SELECT id, lat, lon FROM drivers WHERE status='free'")
    nearest = None
    min_dist = 999
    for d in sql.fetchall():
        driver_id, dlat, dlon = d
        if dlat is None: continue
        dist = distance_km(lat, lon, dlat, dlon)
        if dist < min_dist:
            min_dist = dist
            nearest = driver_id
    if not nearest:
        bot.send_message(message.chat.id,"❌ Нет свободных машин рядом")
        return
    # создаём заказ
    start_time = int(time.time())
    sql.execute("INSERT INTO orders (driver_id, passenger_id, price, start_time, end_time, payment) VALUES (?,?,?,?,?,?)",
                (nearest, message.chat.id, TRIP_PRICE, start_time, 0, "наличные"))
    sql.execute("UPDATE drivers SET status='busy' WHERE id=?", (nearest,))
    db.commit()
    bot.send_message(nearest,f"📢 Новый заказ! Клиент в {round(min_dist,2)} км\nЦена: {TRIP_PRICE} ₽")
    bot.send_message(message.chat.id,f"🚕 Машина найдена! Водитель в {round(min_dist,2)} км")

# ----------------- ЗАВЕРШЕНИЕ ПОЕЗДКИ -----------------
@bot.message_handler(func=lambda m: m.text=="➕ Завершить поездку")
def finish_trip(message):
    cid = message.chat.id
    sql.execute("SELECT id,start_time,price FROM orders WHERE driver_id=? AND end_time=0 ORDER BY id DESC", (cid,))
    order = sql.fetchone()
    if not order:
        bot.send_message(cid,"❌ Нет активных поездок")
        return
    order_id, start_time, price = order
    if time.time() - start_time < MIN_TRIP_TIME:
        bot.send_message(cid,"⏳ Слишком рано завершать поездку")
        return
    end_time = int(time.time())
    sql.execute("UPDATE orders SET end_time=? WHERE id=?", (end_time, order_id))
    sql.execute("UPDATE drivers SET status='free', trips=trips+1, earned=earned+?, commission=commission+? WHERE id=?",
                (price*(1-REAL_COMMISSION), price*REAL_COMMISSION, cid))
    db.commit()
    bot.send_message(cid,f"✅ Поездка завершена\nКомиссия: {SHOW_COMMISSION*100}%")
    # просим пассажира оценить
    kb = types.InlineKeyboardMarkup()
    for i in range(1,6):
        kb.add(types.InlineKeyboardButton("⭐"*i, callback_data=f"rate_{cid}_{i}"))
    bot.send_message(cid,"⭐ Оцените поездку", reply_markup=kb)

# ----------------- РЕЙТИНГ -----------------
@bot.callback_query_handler(func=lambda c: c.data.startswith("rate_"))
def rate_driver(call):
    _, driver_id, score = call.data.split("_")
    driver_id = int(driver_id)
    score = int(score)
    sql.execute("SELECT rating_sum, rating_count FROM drivers WHERE id=?", (driver_id,))
    rsum, rcount = sql.fetchone()
    rsum += score
    rcount += 1
    sql.execute("UPDATE drivers SET rating_sum=?, rating_count=? WHERE id=?",(rsum, rcount, driver_id))
    db.commit()
    avg = round(rsum/rcount,2)
    bot.edit_message_text(f"Спасибо! ⭐ Рейтинг водителя: {avg}", call.message.chat.id, call.message.message_id)

# ----------------- КОНЕЦ СМЕНЫ -----------------
@bot.message_handler(func=lambda m: m.text=="📊 Конец смены")
def end_shift(message):
    cid = message.chat.id
    sql.execute("SELECT trips, earned, commission FROM drivers WHERE id=?", (cid,))
    trips, earned, comm = sql.fetchone()
    bot.send_message(cid,
        f"📊 Итог смены:\nПоездок: {trips}\nДоход от пассажиров: {earned} ₽\n💼 К оплате сервису: {round(comm,2)} ₽")
    bot.

POKO1 | CLAY, [01.02.2026 22:59]
send_message(ADMIN_ID, f"🚨 Водитель {cid} закончил смену. Долг сервису: {round(comm,2)} ₽")
    sql.execute("UPDATE drivers SET trips=0, earned=0, commission=0 WHERE id=?", (cid,))
    db.commit()

# ----------------- АДМИН -----------------
@bot.message_handler(commands=["coder"])
def admin_panel(message):
    if message.chat.id != ADMIN_ID: return
    sql.execute("SELECT id,trips,earned,commission,rating_sum,rating_count FROM drivers")
    text = "📊 Статистика TaxiBistro:\n\n"
    for d in sql.fetchall():
        did,trips,earned,comm,rsum,rcount = d
        rating = round(rsum/rcount,2) if rcount else 0
        text += f"🚕 Водитель {did}\nПоездок: {trips}\nЗаработано: {earned} ₽\nКомиссия: {round(comm,2)} ₽\nРейтинг: {rating}\n\n"
    bot.send_message(message.chat.id,text)

print("TaxiBistro v7 запущен")

bot.infinity_polling()
