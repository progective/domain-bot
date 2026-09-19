# -*- coding: utf-8 -*-
"""
ربات تلگرام فروش دامنه
نصب:  pip install "python-telegram-bot>=21"
اجرا: python domain_bot.py
"""
import asyncio
import html
import logging
import os
import re
import sqlite3
import time
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    ApplicationBuilder, CallbackQueryHandler, CommandHandler,
    ContextTypes, MessageHandler, filters,
)

# ====================== تنظیمات (فقط این بخش را ویرایش کن) ======================
# توکن اینجا گذاشته شده؛ پس این فایل را فقط روی مخزن Private گیت‌هاب بگذار.
# (اگر در Railway متغیر BOT_TOKEN بگذاری، آن مقدم است.)
BOT_TOKEN = os.environ.get("BOT_TOKEN") or "8607013123:AAEdO-cv5iX-SHoE5WjmG_dBO0KZPHG6Gz0"
ADMIN_ID = int(os.environ.get("ADMIN_ID") or 6286936011)   # آیدی عددی ادمین
PROXY = os.environ.get("PROXY", "")
USE_BUTTON_COLORS = True
_VOL = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "")     # Railway خودش وقتی Volume وصل باشد می‌گذارد
DB_FILE = os.environ.get("DB_FILE") or (os.path.join(_VOL, "domain_bot.db") if _VOL else "domain_bot.db")
ON_RAILWAY = bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_PROJECT_ID"))
PERSISTENT = (not ON_RAILWAY) or bool(_VOL)
PER_PAGE = 8
DEFAULT_WELCOME = "👋 به ربات فروش دامنه خوش آمدید.\nاز منوی زیر یکی از گزینه‌ها را انتخاب کنید."
# =================================================================================

HTML = ParseMode.HTML

try:
    os.makedirs(os.path.dirname(os.path.abspath(DB_FILE)), exist_ok=True)
    db = sqlite3.connect(DB_FILE, check_same_thread=False)
except Exception:  # مثلاً پوشه /data وجود ندارد
    DB_FILE = "domain_bot.db"
    PERSISTENT = not ON_RAILWAY
    db = sqlite3.connect(DB_FILE, check_same_thread=False)
db.row_factory = sqlite3.Row
db.executescript("""
CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY, name TEXT, username TEXT,
    balance INTEGER DEFAULT 0, banned INTEGER DEFAULT 0, joined INTEGER);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS extensions(
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, price INTEGER);
CREATE TABLE IF NOT EXISTS domains(
    id INTEGER PRIMARY KEY AUTOINCREMENT, ext_id INTEGER, name TEXT UNIQUE,
    price INTEGER, owner INTEGER, sold_at INTEGER, paid INTEGER,
    sni1 TEXT, sni2 TEXT, sni_status TEXT DEFAULT 'none');
CREATE TABLE IF NOT EXISTS topups(
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount INTEGER,
    status TEXT DEFAULT 'waiting', created INTEGER,
    r_type TEXT, r_file TEXT, r_text TEXT);
""")
db.commit()

# ============================== ابزارهای کمکی ==============================
_DIG = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def now():
    return int(time.time())


def ts(t):
    return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M") if t else "-"


def esc(s):
    return html.escape(str(s if s is not None else ""))


def fmt(n):
    return f"{int(n):,}"


def to_int(text):
    t = (text or "").translate(_DIG)
    t = t.replace(",", "").replace("٬", "").replace(" ", "")
    return int(t) if t.isdigit() else None


def fmt_card(c):
    c = re.sub(r"\D", "", c or "")
    return " ".join(c[i:i + 4] for i in range(0, len(c), 4))


def get_s(key, default=""):
    r = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def set_s(key, value):
    db.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
    db.commit()


def is_admin(uid):
    return ADMIN_ID != 0 and uid == ADMIN_ID


def is_banned(uid):
    r = db.execute("SELECT banned FROM users WHERE id=?", (uid,)).fetchone()
    return bool(r and r["banned"])


def get_balance(uid):
    r = db.execute("SELECT balance FROM users WHERE id=?", (uid,)).fetchone()
    return r["balance"] if r else 0


def get_user(uid):
    return db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def user_link(r):
    return f'<a href="tg://user?id={r["id"]}">{esc(r["name"] or "کاربر")}</a>'


def ensure_user(u):
    db.execute(
        "INSERT INTO users(id,name,username,joined) VALUES(?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET name=excluded.name, username=excluded.username",
        (u.id, (u.full_name or "")[:60], u.username or "", now()))
    db.commit()


def set_state(context, n, **kw):
    context.user_data["st"] = {"n": n, **kw}


def btn(text, data=None, url=None, style=None):
    kw = {}
    if style and USE_BUTTON_COLORS:
        kw["api_kwargs"] = {"style": style}   # primary / success / danger
    if url:
        return InlineKeyboardButton(text, url=url, **kw)
    return InlineKeyboardButton(text, callback_data=data, **kw)


def kb(rows):
    return InlineKeyboardMarkup(rows)


async def show(update, text, markup=None):
    """اگر از دکمه آمده باشد پیام را ویرایش می‌کند، وگرنه پیام جدید می‌فرستد."""
    q = update.callback_query
    if q:
        m = q.message
        try:
            if m and (m.photo or m.document):
                await q.edit_message_caption(caption=text, reply_markup=markup, parse_mode=HTML)
            else:
                await q.edit_message_text(text, reply_markup=markup, parse_mode=HTML)
            return
        except BadRequest as e:
            if "not modified" in str(e).lower():
                return
        except Exception:
            pass
        await m.reply_text(text, reply_markup=markup, parse_mode=HTML)
    else:
        await update.effective_message.reply_text(text, reply_markup=markup, parse_mode=HTML)


async def ask(update, context, state, text, back="home", **kw):
    set_state(context, state, **kw)
    await show(update, text, kb([[btn("❌ انصراف", f"cancel:{back}")]]))


# ============================== منوی اصلی ==============================
def home_text(uid):
    w = get_s("welcome", DEFAULT_WELCOME)
    return (f"{esc(w)}\n\n🆔 آیدی عددی: <code>{uid}</code>\n"
            f"💰 موجودی: <b>{fmt(get_balance(uid))}</b> تومان")


def home_kb(uid):
    rows = [
        [btn("🛒 خرید دامنه", "buy", style="success")],
        [btn("💳 شارژ حساب", "topup", style="primary")],
        [btn("🌐 دامنه‌های من", "mydom", style="primary")],
        [btn("🛟 پشتیبانی", "support", style="primary")],
    ]
    if is_admin(uid):
        rows.append([btn("🛠 مدیریت", "adm", style="danger")])
    return kb(rows)


async def send_home(update):
    uid = update.effective_user.id
    await show(update, home_text(uid), home_kb(uid))


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    ensure_user(u)
    context.user_data.clear()
    if ADMIN_ID == 0:
        await update.message.reply_text(
            "👋 سلام!\n\nآیدی عددی تلگرام شما:\n"
            f"<code>{u.id}</code>\n\n"
            "این عدد را در فایل domain_bot.py جلوی <code>ADMIN_ID =</code> بگذارید، "
            "فایل را ذخیره کنید و ربات را دوباره اجرا کنید تا منوی «مدیریت» برای شما فعال شود.",
            parse_mode=HTML)
        return
    if is_banned(u.id) and not is_admin(u.id):
        await update.message.reply_text("⛔️ حساب شما مسدود شده است.")
        return
    await send_home(update)


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"🆔 آیدی عددی شما: <code>{update.effective_user.id}</code>", parse_mode=HTML)


async def cmd_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_admin(update.effective_user.id):
        context.user_data.clear()
        await admin_home(update)


# ============================== خرید دامنه ==============================
async def buy_menu(update):
    uid = update.effective_user.id
    if get_s("shop_open", "1") != "1" and not is_admin(uid):
        await show(update, "🔴 فروشگاه موقتاً بسته است.", kb([[btn("🔙 بازگشت", "home")]]))
        return
    exts = db.execute(
        "SELECT e.id, e.name, e.price, "
        "(SELECT COUNT(*) FROM domains d WHERE d.ext_id=e.id AND d.owner IS NULL) AS stock "
        "FROM extensions e ORDER BY e.id").fetchall()
    if not exts:
        await show(update, "فعلاً پسوندی برای فروش ثبت نشده است.", kb([[btn("🔙 بازگشت", "home")]]))
        return
    rows = []
    for e in exts:
        tail = f"{e['stock']} عدد" if e["stock"] else "ناموجود"
        rows.append([btn(f"🌐 {e['name']}  |  از {fmt(e['price'])} تومان  |  {tail}", f"ext:{e['id']}:0")])
    rows.append([btn("🔙 بازگشت", "home")])
    await show(update, "🛒 <b>خرید دامنه</b>\n\nپسوند مورد نظر را انتخاب کنید:", kb(rows))


async def ext_view(update, ext_id, page=0):
    uid = update.effective_user.id
    e = db.execute("SELECT * FROM extensions WHERE id=?", (ext_id,)).fetchone()
    if not e:
        await buy_menu(update)
        return
    total = db.execute("SELECT COUNT(*) c FROM domains WHERE ext_id=? AND owner IS NULL", (ext_id,)).fetchone()["c"]
    doms = db.execute(
        "SELECT d.id, d.name, COALESCE(d.price, e.price) AS p FROM domains d "
        "JOIN extensions e ON e.id=d.ext_id WHERE d.ext_id=? AND d.owner IS NULL "
        "ORDER BY d.id LIMIT ? OFFSET ?", (ext_id, PER_PAGE, page * PER_PAGE)).fetchall()
    rows = [[btn(f"🌐 {d['name']} — {fmt(d['p'])} تومان", f"dom:{d['id']}")] for d in doms]
    nav = []
    if page > 0:
        nav.append(btn("◀️ قبلی", f"ext:{ext_id}:{page - 1}"))
    if (page + 1) * PER_PAGE < total:
        nav.append(btn("بعدی ▶️", f"ext:{ext_id}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([btn("🔙 بازگشت", "buy")])
    if total:
        text = (f"🌐 <b>دامنه‌های {esc(e['name'])}</b> ({total} عدد موجود)\n"
                f"💰 موجودی شما: <b>{fmt(get_balance(uid))}</b> تومان\n\nیک دامنه را انتخاب کنید:")
    else:
        text = f"😕 فعلاً دامنه‌ای با پسوند {esc(e['name'])} موجود نیست."
    await show(update, text, kb(rows))


async def dom_view(update, dom_id):
    uid = update.effective_user.id
    d = db.execute(
        "SELECT d.*, e.name AS ext, COALESCE(d.price, e.price) AS p FROM domains d "
        "JOIN extensions e ON e.id=d.ext_id WHERE d.id=?", (dom_id,)).fetchone()
    if not d or d["owner"] is not None:
        await show(update, "❌ این دامنه دیگر موجود نیست.", kb([[btn("🔙 بازگشت", "buy")]]))
        return
    bal = get_balance(uid)
    text = (f"🌐 دامنه: <b>{esc(d['name'])}</b>\n💰 قیمت: <b>{fmt(d['p'])}</b> تومان\n"
            f"👛 موجودی شما: <b>{fmt(bal)}</b> تومان\n")
    if bal >= d["p"]:
        rows = [[btn("✅ تایید و خرید", f"buyok:{dom_id}", style="success")],
                [btn("🔙 بازگشت", f"ext:{d['ext_id']}:0")]]
    else:
        text += f"\n⚠️ موجودی کافی نیست. کمبود: <b>{fmt(d['p'] - bal)}</b> تومان"
        rows = [[btn("💳 شارژ حساب", "topup", style="primary")],
                [btn("🔙 بازگشت", f"ext:{d['ext_id']}:0")]]
    await show(update, text, kb(rows))


async def buy_do(update, context, dom_id):
    uid = update.effective_user.id
    d = db.execute(
        "SELECT d.*, COALESCE(d.price, e.price) AS p FROM domains d "
        "JOIN extensions e ON e.id=d.ext_id WHERE d.id=?", (dom_id,)).fetchone()
    if not d or d["owner"] is not None:
        await show(update, "❌ این دامنه دیگر موجود نیست.", kb([[btn("🔙 بازگشت", "buy")]]))
        return
    if get_balance(uid) < d["p"]:
        await dom_view(update, dom_id)
        return
    # بدون await در این بخش، پس عملیات اتمیک است
    db.execute("UPDATE users SET balance=balance-? WHERE id=?", (d["p"], uid))
    db.execute("UPDATE domains SET owner=?, sold_at=?, paid=? WHERE id=?", (uid, now(), d["p"], dom_id))
    db.commit()
    await show(update,
               f"✅ <b>خرید با موفقیت انجام شد</b>\n\n🌐 دامنه: <b>{esc(d['name'])}</b>\n"
               f"💰 مبلغ: {fmt(d['p'])} تومان\n\nبرای ست کردن SNI روی دکمه زیر بزنید.",
               kb([[btn("⚙️ ست کردن SNI", f"sni:{dom_id}", style="success")],
                   [btn("🏠 منوی اصلی", "home")]]))
    try:
        await context.bot.send_message(
            ADMIN_ID,
            f"🛒 <b>فروش جدید</b>\n\n🌐 {esc(d['name'])}\n👤 {user_link(get_user(uid))} "
            f"(<code>{uid}</code>)\n💰 {fmt(d['p'])} تومان", parse_mode=HTML)
    except Exception:
        logging.exception("notify admin")


# ============================== شارژ حساب ==============================
async def topup_start(update, context):
    if not get_s("card_number") or not get_s("card_holder"):
        await show(update, "⚠️ شارژ حساب هنوز فعال نشده است. لطفاً با پشتیبانی تماس بگیرید.",
                   kb([[btn("🔙 بازگشت", "home")]]))
        return
    mn = int(get_s("min_charge", "10000"))
    await ask(update, context, "topup_amount",
              f"💳 <b>شارژ حساب</b>\n\nمبلغ مورد نظر را به <b>تومان</b> بفرستید (فقط عدد).\n"
              f"حداقل مبلغ: {fmt(mn)} تومان")


def topup_text(t, extra=""):
    u = get_user(t["user_id"])
    s = (f"💳 <b>درخواست شارژ #{t['id']}</b>\n\n👤 {user_link(u)}\n🆔 <code>{u['id']}</code>\n"
         f"💰 مبلغ: <b>{fmt(t['amount'])}</b> تومان\n🕒 {ts(t['created'])}")
    if t["r_text"]:
        s += f"\n\n📝 متن رسید:\n{esc(t['r_text'])}"
    return s + extra


async def send_topup_to_admin(context, tid):
    t = db.execute("SELECT * FROM topups WHERE id=?", (tid,)).fetchone()
    if not t:
        return
    text = topup_text(t)
    markup = kb([[btn("✅ تایید", f"tpok:{tid}", style="success"),
                  btn("❌ رد", f"tpno:{tid}", style="danger")]])
    try:
        if t["r_type"] == "photo":
            await context.bot.send_photo(ADMIN_ID, t["r_file"], caption=text, reply_markup=markup, parse_mode=HTML)
        elif t["r_type"] == "document":
            await context.bot.send_document(ADMIN_ID, t["r_file"], caption=text, reply_markup=markup, parse_mode=HTML)
        else:
            await context.bot.send_message(ADMIN_ID, text, reply_markup=markup, parse_mode=HTML)
    except Exception:
        logging.exception("send topup")


async def topup_decide(update, context, tid, ok):
    t = db.execute("SELECT * FROM topups WHERE id=?", (tid,)).fetchone()
    if not t or t["status"] != "pending":
        await show(update, "این درخواست قبلاً بررسی شده است.")
        return
    if ok:
        db.execute("UPDATE topups SET status='approved' WHERE id=?", (tid,))
        db.execute("UPDATE users SET balance=balance+? WHERE id=?", (t["amount"], t["user_id"]))
        db.commit()
        msg = (f"✅ رسید شما تایید شد و مبلغ <b>{fmt(t['amount'])}</b> تومان به حساب شما اضافه شد.\n"
               f"💰 موجودی فعلی: <b>{fmt(get_balance(t['user_id']))}</b> تومان")
        extra = "\n\n✅ <b>تایید شد</b>"
    else:
        db.execute("UPDATE topups SET status='rejected' WHERE id=?", (tid,))
        db.commit()
        msg = "❌ رسید شما تایید نشد. در صورت اشتباه با پشتیبانی تماس بگیرید."
        extra = "\n\n❌ <b>رد شد</b>"
    try:
        await context.bot.send_message(t["user_id"], msg, parse_mode=HTML)
    except Exception:
        pass
    await show(update, topup_text(t, extra))


# ============================== دامنه‌های من و SNI ==============================
SNI_ICON = {"none": "⚪️", "pending": "🟡", "done": "🟢"}


async def mydom_menu(update):
    uid = update.effective_user.id
    doms = db.execute("SELECT id,name,sni_status FROM domains WHERE owner=? ORDER BY sold_at DESC LIMIT 50", (uid,)).fetchall()
    if not doms:
        await show(update, "🌐 شما هنوز دامنه‌ای نخریده‌اید.",
                   kb([[btn("🛒 خرید دامنه", "buy", style="success")], [btn("🔙 بازگشت", "home")]]))
        return
    rows = [[btn(f"{SNI_ICON[d['sni_status']]} {d['name']}", f"mydomv:{d['id']}")] for d in doms]
    rows.append([btn("🔙 بازگشت", "home")])
    await show(update, "🌐 <b>دامنه‌های من</b>\n\n⚪️ SNI ثبت نشده  |  🟡 در انتظار تنظیم  |  🟢 تنظیم شده\n\nیک دامنه را انتخاب کنید:", kb(rows))


async def mydom_view(update, dom_id):
    uid = update.effective_user.id
    d = db.execute("SELECT * FROM domains WHERE id=? AND owner=?", (dom_id, uid)).fetchone()
    if not d:
        await mydom_menu(update)
        return
    st = {"none": "⚪️ ثبت نشده", "pending": "🟡 در انتظار تنظیم (حداکثر ۱ ساعت)", "done": "🟢 تنظیم شده"}[d["sni_status"]]
    text = (f"🌐 <b>{esc(d['name'])}</b>\n📅 تاریخ خرید: {ts(d['sold_at'])}\n⚙️ وضعیت SNI: {st}")
    if d["sni1"]:
        text += f"\n\nsni1: <code>{esc(d['sni1'])}</code>\nsni2: <code>{esc(d['sni2'])}</code>"
    label = "✏️ تغییر SNI" if d["sni1"] else "⚙️ ست کردن SNI"
    await show(update, text, kb([[btn(label, f"sni:{dom_id}", style="success")], [btn("🔙 بازگشت", "mydom")]]))


async def sni_start(update, context, dom_id):
    uid = update.effective_user.id
    d = db.execute("SELECT * FROM domains WHERE id=? AND owner=?", (dom_id, uid)).fetchone()
    if not d:
        await mydom_menu(update)
        return
    await ask(update, context, "sni1",
              f"⚙️ ست کردن SNI برای <b>{esc(d['name'])}</b>\n\nمقدار <b>sni1</b> را ارسال کنید:",
              back=f"mydomv:{dom_id}", id=dom_id)


def clean_sni(t):
    t = (t or "").strip().lower()
    return t if re.fullmatch(r"[a-z0-9._\-]{3,200}", t) else None


def sni_admin_text(d):
    u = get_user(d["owner"])
    return (f"⚙️ <b>درخواست ست SNI</b>\n\n🌐 دامنه: <b>{esc(d['name'])}</b>\n"
            f"👤 {user_link(u)} (<code>{u['id']}</code>)\n\n"
            f"sni1: <code>{esc(d['sni1'])}</code>\nsni2: <code>{esc(d['sni2'])}</code>")


async def sni_done(update, context, dom_id):
    d = db.execute("SELECT * FROM domains WHERE id=?", (dom_id,)).fetchone()
    if not d or d["sni_status"] != "pending":
        await show(update, "این درخواست قبلاً انجام شده یا وجود ندارد.", kb([[btn("🔙 پنل مدیریت", "adm")]]))
        return
    db.execute("UPDATE domains SET sni_status='done' WHERE id=?", (dom_id,))
    db.commit()
    try:
        await context.bot.send_message(
            d["owner"], f"✅ SNI دامنه <b>{esc(d['name'])}</b> تنظیم شد.", parse_mode=HTML)
    except Exception:
        pass
    await show(update, sni_admin_text(d) + "\n\n✅ <b>انجام شد</b>", kb([[btn("🔙 پنل مدیریت", "adm")]]))


# ============================== پشتیبانی ==============================
async def support(update):
    s = get_s("support")
    back = [btn("🔙 بازگشت", "home")]
    if not s:
        await show(update, "🛟 پشتیبانی هنوز تنظیم نشده است.", kb([back]))
    elif s.lstrip("-").isdigit():
        await show(update, f'🛟 برای ارتباط با پشتیبانی روی لینک زیر بزنید:\n\n<a href="tg://user?id={s}">💬 ارتباط با پشتیبانی</a>', kb([back]))
    else:
        await show(update, "🛟 برای ارتباط با پشتیبانی روی دکمه زیر بزنید:",
                   kb([[btn("💬 ارتباط با پشتیبانی", url=f"https://t.me/{s.lstrip('@')}")], back]))


# ============================== پنل مدیریت ==============================
def count(sql, args=()):
    return db.execute(sql, args).fetchone()[0]


async def admin_home(update):
    pt = count("SELECT COUNT(*) FROM topups WHERE status='pending'")
    ps = count("SELECT COUNT(*) FROM domains WHERE sni_status='pending'")
    shop = get_s("shop_open", "1") == "1"
    rows = [
        [btn("📊 آمار", "adm:stats", style="primary"), btn("🧾 آخرین فروش‌ها", "adm:sales", style="primary")],
        [btn("🏷 پسوندها و دامنه‌ها", "adm:exts", style="success")],
        [btn("👥 کاربران", "adm:users", style="primary"), btn("📢 پیام همگانی", "adm:bc", style="primary")],
        [btn(f"💳 رسیدهای شارژ ({pt})", "adm:tps", style="danger" if pt else None),
         btn(f"⚙️ درخواست SNI ({ps})", "adm:snis", style="danger" if ps else None)],
        [btn("💳 تنظیم کارت", "adm:card"), btn("🛟 تنظیم پشتیبانی", "adm:sup")],
        [btn("📝 متن خوش‌آمد", "adm:wel"), btn("💵 حداقل شارژ", "adm:min")],
        [btn("🟢 فروشگاه: باز" if shop else "🔴 فروشگاه: بسته", "adm:shop")],
        [btn("🔙 منوی اصلی", "home")],
    ]
    await show(update, "🛠 <b>پنل مدیریت</b>", kb(rows))


async def admin_stats(update):
    users = count("SELECT COUNT(*) FROM users")
    bal = count("SELECT COALESCE(SUM(balance),0) FROM users")
    total = count("SELECT COUNT(*) FROM domains")
    avail = count("SELECT COUNT(*) FROM domains WHERE owner IS NULL")
    rev = count("SELECT COALESCE(SUM(paid),0) FROM domains WHERE owner IS NOT NULL")
    charged = count("SELECT COALESCE(SUM(amount),0) FROM topups WHERE status='approved'")
    text = (f"📊 <b>آمار</b>\n\n👥 کاربران: {users}\n💰 مجموع موجودی کاربران: {fmt(bal)} تومان\n\n"
            f"🌐 کل دامنه‌ها: {total}\n🟢 موجود: {avail}\n🔴 فروخته‌شده: {total - avail}\n\n"
            f"💵 مجموع فروش: {fmt(rev)} تومان\n💳 مجموع شارژهای تاییدشده: {fmt(charged)} تومان")
    await show(update, text, kb([[btn("🔙 پنل مدیریت", "adm")]]))


async def admin_sales(update):
    rows = db.execute("SELECT name, paid, owner, sold_at FROM domains WHERE owner IS NOT NULL ORDER BY sold_at DESC LIMIT 20").fetchall()
    if not rows:
        text = "هنوز فروشی ثبت نشده است."
    else:
        text = "🧾 <b>۲۰ فروش آخر</b>\n\n" + "\n".join(
            f"🌐 {esc(r['name'])} — {fmt(r['paid'])} ت — <code>{r['owner']}</code> — {ts(r['sold_at'])}" for r in rows)
    await show(update, text, kb([[btn("🔙 پنل مدیریت", "adm")]]))


async def exts_menu(update):
    exts = db.execute(
        "SELECT e.id, e.name, e.price, "
        "(SELECT COUNT(*) FROM domains d WHERE d.ext_id=e.id AND d.owner IS NULL) AS stock "
        "FROM extensions e ORDER BY e.id").fetchall()
    rows = [[btn(f"🏷 {e['name']} | {fmt(e['price'])} ت | موجود: {e['stock']}", f"adm:ext:{e['id']}")] for e in exts]
    rows.append([btn("➕ افزودن پسوند جدید", "adm:extadd", style="success")])
    rows.append([btn("🔙 پنل مدیریت", "adm")])
    await show(update, "🏷 <b>پسوندها و دامنه‌ها</b>\n\nیک پسوند را برای مدیریت انتخاب کنید:", kb(rows))


async def ext_admin_view(update, ext_id):
    e = db.execute("SELECT * FROM extensions WHERE id=?", (ext_id,)).fetchone()
    if not e:
        await exts_menu(update)
        return
    av = count("SELECT COUNT(*) FROM domains WHERE ext_id=? AND owner IS NULL", (ext_id,))
    sold = count("SELECT COUNT(*) FROM domains WHERE ext_id=? AND owner IS NOT NULL", (ext_id,))
    text = (f"🏷 پسوند: <b>{esc(e['name'])}</b>\n💰 قیمت پیش‌فرض: {fmt(e['price'])} تومان\n"
            f"🟢 موجود: {av}\n🔴 فروخته‌شده: {sold}")
    rows = [
        [btn("➕ افزودن دامنه", f"adm:domadd:{ext_id}", style="success"),
         btn("📋 لیست دامنه‌ها", f"adm:dl:{ext_id}:0", style="primary")],
        [btn("✏️ تغییر قیمت", f"adm:extprice:{ext_id}"), btn("🗑 حذف پسوند", f"adm:extdel:{ext_id}", style="danger")],
        [btn("🔙 بازگشت", "adm:exts")],
    ]
    await show(update, text, kb(rows))


async def dom_list(update, ext_id, page):
    e = db.execute("SELECT * FROM extensions WHERE id=?", (ext_id,)).fetchone()
    if not e:
        await exts_menu(update)
        return
    total = count("SELECT COUNT(*) FROM domains WHERE ext_id=? AND owner IS NULL", (ext_id,))
    doms = db.execute("SELECT id,name,price FROM domains WHERE ext_id=? AND owner IS NULL ORDER BY id LIMIT ? OFFSET ?",
                      (ext_id, PER_PAGE, page * PER_PAGE)).fetchall()
    rows = [[btn(f"🗑 {d['name']}" + (f" ({fmt(d['price'])})" if d["price"] is not None else ""),
                 f"adm:ddel:{d['id']}:{ext_id}:{page}")] for d in doms]
    nav = []
    if page > 0:
        nav.append(btn("◀️ قبلی", f"adm:dl:{ext_id}:{page - 1}"))
    if (page + 1) * PER_PAGE < total:
        nav.append(btn("بعدی ▶️", f"adm:dl:{ext_id}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([btn("🔙 بازگشت", f"adm:ext:{ext_id}")])
    await show(update, f"📋 <b>دامنه‌های موجود {esc(e['name'])}</b> ({total})\nروی هر دامنه بزنید تا حذف شود.", kb(rows))


def add_domains(ext, text):
    added = dup = bad = 0
    for line in text.splitlines():
        p = line.split()
        if not p:
            continue
        name = p[0].lower()
        price = None
        if len(p) > 1:
            price = to_int(p[1])
            if price is None:
                bad += 1
                continue
        if not re.fullmatch(r"[a-z0-9.\-]+", name) or not name.endswith(ext["name"]) or name == ext["name"]:
            bad += 1
            continue
        try:
            db.execute("INSERT INTO domains(ext_id,name,price) VALUES(?,?,?)", (ext["id"], name, price))
            added += 1
        except sqlite3.IntegrityError:
            dup += 1
    db.commit()
    return added, dup, bad


async def users_menu(update):
    last = db.execute("SELECT id,name FROM users ORDER BY joined DESC LIMIT 10").fetchall()
    rows = [[btn("🔎 جستجو با آیدی عددی", "adm:ufind", style="primary")]]
    rows += [[btn(f"{(u['name'] or 'کاربر')[:20]} | {u['id']}", f"adm:u:{u['id']}")] for u in last]
    rows.append([btn("🔙 پنل مدیریت", "adm")])
    await show(update, f"👥 <b>کاربران</b> (کل: {count('SELECT COUNT(*) FROM users')})\n\nآخرین کاربران:", kb(rows))


async def user_view(update, uid):
    r = get_user(uid)
    if not r:
        await show(update, "❌ کاربری با این آیدی پیدا نشد (کاربر باید حداقل یک بار ربات را استارت کرده باشد).",
                   kb([[btn("🔙 بازگشت", "adm:users")]]))
        return
    nd = count("SELECT COUNT(*) FROM domains WHERE owner=?", (uid,))
    un = f"@{esc(r['username'])}" if r["username"] else "-"
    text = (f"👤 {user_link(r)}\n🆔 <code>{uid}</code>\n📛 یوزرنیم: {un}\n"
            f"💰 موجودی: <b>{fmt(r['balance'])}</b> تومان\n🌐 تعداد دامنه: {nd}\n"
            f"📅 عضویت: {ts(r['joined'])}\n🚦 وضعیت: {'⛔️ مسدود' if r['banned'] else '✅ فعال'}")
    rows = [
        [btn("➕ افزایش موجودی", f"adm:uadd:{uid}", style="success"), btn("➖ کاهش موجودی", f"adm:usub:{uid}", style="danger")],
        [btn("🌐 دامنه‌ها", f"adm:udom:{uid}"), btn("✉️ ارسال پیام", f"adm:umsg:{uid}")],
        [btn("✅ رفع مسدودی" if r["banned"] else "🚫 مسدود کردن", f"adm:uban:{uid}")],
        [btn("🔙 بازگشت", "adm:users")],
    ]
    await show(update, text, kb(rows))


async def admin_route(update, context, parts):
    sub = parts[1] if len(parts) > 1 else ""
    a = int(parts[2]) if len(parts) > 2 and parts[2].lstrip("-").isdigit() else None
    back = kb([[btn("🔙 پنل مدیریت", "adm")]])

    if sub == "":
        await admin_home(update)
    elif sub == "stats":
        await admin_stats(update)
    elif sub == "sales":
        await admin_sales(update)
    elif sub == "exts":
        await exts_menu(update)
    elif sub == "extadd":
        await ask(update, context, "a_ext_name", "🏷 نام پسوند جدید را بفرستید (مثلاً <code>.com</code>)", back="adm:exts")
    elif sub == "ext":
        await ext_admin_view(update, a)
    elif sub == "extprice":
        await ask(update, context, "a_ext_price", "💰 قیمت پیش‌فرض این پسوند را به تومان بفرستید:", back=f"adm:ext:{a}", id=a)
    elif sub == "extdel":
        await show(update, "⚠️ با حذف پسوند، دامنه‌های موجودِ آن هم حذف می‌شوند. مطمئنی؟",
                   kb([[btn("✅ بله، حذف کن", f"adm:extdelok:{a}", style="danger")], [btn("❌ خیر", f"adm:ext:{a}")]]))
    elif sub == "extdelok":
        if count("SELECT COUNT(*) FROM domains WHERE ext_id=? AND owner IS NOT NULL", (a,)):
            await show(update, "❌ این پسوند دامنه‌ی فروخته‌شده دارد و قابل حذف نیست.", kb([[btn("🔙 بازگشت", f"adm:ext:{a}")]]))
        else:
            db.execute("DELETE FROM domains WHERE ext_id=?", (a,))
            db.execute("DELETE FROM extensions WHERE id=?", (a,))
            db.commit()
            await exts_menu(update)
    elif sub == "domadd":
        e = db.execute("SELECT * FROM extensions WHERE id=?", (a,)).fetchone()
        await ask(update, context, "a_dom_add",
                  f"➕ دامنه‌های <b>{esc(e['name'])}</b> را بفرستید. هر دامنه در یک خط.\n\n"
                  "اگر قیمت مخصوص می‌خواهی، جلوی دامنه بنویس:\n<code>example.com 500000</code>\n"
                  "(بدون قیمت، قیمت پیش‌فرض پسوند اعمال می‌شود)", back=f"adm:ext:{a}", id=a)
    elif sub == "dl":
        await dom_list(update, a, int(parts[3]) if len(parts) > 3 else 0)
    elif sub == "ddel":
        d = db.execute("SELECT name FROM domains WHERE id=?", (a,)).fetchone()
        await show(update, f"🗑 دامنه <b>{esc(d['name']) if d else ''}</b> حذف شود؟",
                   kb([[btn("✅ حذف", f"adm:ddelok:{a}:{parts[3]}:{parts[4]}", style="danger")],
                       [btn("❌ خیر", f"adm:dl:{parts[3]}:{parts[4]}")]]))
    elif sub == "ddelok":
        db.execute("DELETE FROM domains WHERE id=? AND owner IS NULL", (a,))
        db.commit()
        await dom_list(update, int(parts[3]), int(parts[4]))
    elif sub == "users":
        await users_menu(update)
    elif sub == "ufind":
        await ask(update, context, "a_ufind", "🔎 آیدی عددی کاربر را بفرستید:", back="adm:users")
    elif sub == "u":
        await user_view(update, a)
    elif sub == "uadd":
        await ask(update, context, "a_uadd", "➕ مبلغ افزایش موجودی (تومان):", back=f"adm:u:{a}", id=a)
    elif sub == "usub":
        await ask(update, context, "a_usub", "➖ مبلغ کاهش موجودی (تومان):", back=f"adm:u:{a}", id=a)
    elif sub == "uban":
        db.execute("UPDATE users SET banned=1-banned WHERE id=?", (a,))
        db.commit()
        await user_view(update, a)
    elif sub == "udom":
        doms = db.execute("SELECT name, sni_status, sni1, sni2 FROM domains WHERE owner=?", (a,)).fetchall()
        text = "🌐 <b>دامنه‌های کاربر</b>\n\n" + ("\n".join(
            f"{SNI_ICON[d['sni_status']]} {esc(d['name'])}" + (f"  ({esc(d['sni1'])} , {esc(d['sni2'])})" if d["sni1"] else "")
            for d in doms) or "ندارد")
        await show(update, text, kb([[btn("🔙 بازگشت", f"adm:u:{a}")]]))
    elif sub == "umsg":
        await ask(update, context, "a_umsg", "✉️ پیام خود را بفرستید:", back=f"adm:u:{a}", id=a)
    elif sub == "bc":
        await ask(update, context, "a_bc", "📢 پیامی که می‌خواهی برای همه کاربران ارسال شود را بفرستید (متن، عکس و...):", back="adm")
    elif sub == "tps":
        ts_ = db.execute("SELECT * FROM topups WHERE status='pending' ORDER BY id LIMIT 30").fetchall()
        rows = [[btn(f"#{t['id']} | {fmt(t['amount'])} ت | {t['user_id']}", f"adm:tp:{t['id']}")] for t in ts_]
        rows.append([btn("🔙 پنل مدیریت", "adm")])
        await show(update, "💳 <b>رسیدهای در انتظار بررسی</b>" + ("" if ts_ else "\n\nموردی وجود ندارد."), kb(rows))
    elif sub == "tp":
        await send_topup_to_admin(context, a)
    elif sub == "snis":
        ds = db.execute("SELECT id,name FROM domains WHERE sni_status='pending' ORDER BY sold_at LIMIT 30").fetchall()
        rows = [[btn(f"🌐 {d['name']}", f"adm:sniv:{d['id']}")] for d in ds]
        rows.append([btn("🔙 پنل مدیریت", "adm")])
        await show(update, "⚙️ <b>درخواست‌های SNI در انتظار</b>" + ("" if ds else "\n\nموردی وجود ندارد."), kb(rows))
    elif sub == "sniv":
        d = db.execute("SELECT * FROM domains WHERE id=?", (a,)).fetchone()
        await show(update, sni_admin_text(d), kb([[btn("✅ انجام شد", f"snidone:{a}", style="success")],
                                                  [btn("🔙 بازگشت", "adm:snis")]]))
    elif sub == "card":
        cur = f"\n\nفعلی: <code>{fmt_card(get_s('card_number'))}</code> — {esc(get_s('card_holder'))}" if get_s("card_number") else ""
        await ask(update, context, "a_card_num", "💳 شماره کارت ۱۶ رقمی را بفرستید:" + cur, back="adm")
    elif sub == "sup":
        await ask(update, context, "a_support",
                  "🛟 آیدی عددی ادمین پشتیبانی (یا یوزرنیم مثل @name) را بفرستید.\nبرای پاک کردن، <code>-</code> بفرستید.\n\n"
                  f"فعلی: {esc(get_s('support') or 'ندارد')}", back="adm")
    elif sub == "wel":
        await ask(update, context, "a_welcome", "📝 متن خوش‌آمدگویی جدید را بفرستید:", back="adm")
    elif sub == "min":
        await ask(update, context, "a_min", f"💵 حداقل مبلغ شارژ (تومان) را بفرستید.\nفعلی: {fmt(get_s('min_charge', '10000'))}", back="adm")
    elif sub == "shop":
        set_s("shop_open", "0" if get_s("shop_open", "1") == "1" else "1")
        await admin_home(update)


async def run_broadcast(context, chat_id, msg_id):
    users = db.execute("SELECT id FROM users WHERE banned=0").fetchall()
    ok = fail = 0
    for r in users:
        try:
            await context.bot.copy_message(r["id"], chat_id, msg_id)
            ok += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)
    await context.bot.send_message(ADMIN_ID, f"📢 ارسال همگانی تمام شد.\n✅ موفق: {ok}\n❌ ناموفق: {fail}")


# ============================== مسیریابی دکمه‌ها ==============================
ADMIN_CMDS = {"adm", "tpok", "tpno", "snidone", "bcok"}


async def route(update, context, data):
    parts = data.split(":")
    cmd = parts[0]
    if cmd in ADMIN_CMDS and not is_admin(update.effective_user.id):
        return
    if cmd == "home":
        context.user_data.clear()
        await send_home(update)
    elif cmd == "cancel":
        context.user_data.clear()
        await route(update, context, ":".join(parts[1:]) or "home")
    elif cmd == "buy":
        await buy_menu(update)
    elif cmd == "ext":
        await ext_view(update, int(parts[1]), int(parts[2]) if len(parts) > 2 else 0)
    elif cmd == "dom":
        await dom_view(update, int(parts[1]))
    elif cmd == "buyok":
        await buy_do(update, context, int(parts[1]))
    elif cmd == "topup":
        await topup_start(update, context)
    elif cmd == "mydom":
        await mydom_menu(update)
    elif cmd == "mydomv":
        await mydom_view(update, int(parts[1]))
    elif cmd == "sni":
        await sni_start(update, context, int(parts[1]))
    elif cmd == "support":
        await support(update)
    elif cmd == "adm":
        await admin_route(update, context, parts)
    elif cmd == "tpok":
        await topup_decide(update, context, int(parts[1]), True)
    elif cmd == "tpno":
        await topup_decide(update, context, int(parts[1]), False)
    elif cmd == "snidone":
        await sni_done(update, context, int(parts[1]))
    elif cmd == "bcok":
        bc = context.user_data.pop("bc", None)
        if bc:
            context.application.create_task(run_broadcast(context, bc[0], bc[1]))
            await show(update, "📢 ارسال همگانی شروع شد. پس از پایان گزارش می‌دهم.", kb([[btn("🔙 پنل مدیریت", "adm")]]))


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    u = q.from_user
    ensure_user(u)
    if ADMIN_ID == 0:
        await q.answer("ابتدا ADMIN_ID را تنظیم کنید.", show_alert=True)
        return
    if is_banned(u.id) and not is_admin(u.id):
        await q.answer("⛔️ حساب شما مسدود است.", show_alert=True)
        return
    await q.answer()
    await route(update, context, q.data)


# ============================== دریافت پیام‌ها (بر اساس وضعیت) ==============================
async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    u = update.effective_user
    ensure_user(u)
    admin = is_admin(u.id)
    if ADMIN_ID == 0 or (is_banned(u.id) and not admin):
        return
    st = context.user_data.get("st")
    text = (msg.text or "").strip()
    if not st:
        await msg.reply_text(home_text(u.id), reply_markup=home_kb(u.id), parse_mode=HTML)
        return
    n = st["n"]
    home_back = kb([[btn("🏠 منوی اصلی", "home")]])
    adm_back = kb([[btn("🔙 پنل مدیریت", "adm")]])

    # ---------- شارژ ----------
    if n == "topup_amount":
        amt = to_int(text)
        mn = int(get_s("min_charge", "10000"))
        if amt is None or amt < mn:
            await msg.reply_text(f"❌ مبلغ نامعتبر است. یک عدد (تومان) و حداقل {fmt(mn)} بفرستید.")
            return
        cur = db.execute("INSERT INTO topups(user_id,amount,status,created) VALUES(?,?,'waiting',?)", (u.id, amt, now()))
        db.commit()
        set_state(context, "topup_receipt", id=cur.lastrowid)
        await msg.reply_text(
            "💳 <b>اطلاعات پرداخت</b>\n\n"
            f"شماره کارت:\n<code>{fmt_card(get_s('card_number'))}</code>\n"
            f"به نام: <b>{esc(get_s('card_holder'))}</b>\n"
            f"مبلغ: <b>{fmt(amt)}</b> تومان\n\n"
            "پس از واریز، <b>عکس رسید</b> (یا شماره پیگیری) را همین‌جا ارسال کنید.",
            parse_mode=HTML, reply_markup=kb([[btn("❌ انصراف", "cancel:home")]]))

    elif n == "topup_receipt":
        t = db.execute("SELECT * FROM topups WHERE id=?", (st["id"],)).fetchone()
        if not t or t["status"] != "waiting" or t["user_id"] != u.id:
            context.user_data.clear()
            await msg.reply_text("این درخواست منقضی شده است.", reply_markup=home_back)
            return
        rtype = rfile = rtext = None
        if msg.photo:
            rtype, rfile, rtext = "photo", msg.photo[-1].file_id, (msg.caption or "")[:500]
        elif msg.document:
            rtype, rfile, rtext = "document", msg.document.file_id, (msg.caption or "")[:500]
        elif text:
            rtype, rtext = "text", text[:500]
        else:
            await msg.reply_text("لطفاً عکس رسید یا متن شماره پیگیری را ارسال کنید.")
            return
        db.execute("UPDATE topups SET status='pending', r_type=?, r_file=?, r_text=? WHERE id=?", (rtype, rfile, rtext, t["id"]))
        db.commit()
        context.user_data.clear()
        await msg.reply_text("✅ رسید شما ثبت شد. پس از تایید مدیریت، موجودی شما شارژ می‌شود.", reply_markup=home_back)
        await send_topup_to_admin(context, t["id"])

    # ---------- SNI ----------
    elif n == "sni1":
        v = clean_sni(text)
        if not v:
            await msg.reply_text("❌ مقدار نامعتبر است. فقط حروف انگلیسی، عدد، نقطه و خط تیره (مثل ns1.example.com).")
            return
        set_state(context, "sni2", id=st["id"], v=v)
        await msg.reply_text("✅ sni1 ثبت شد.\n\nحالا مقدار <b>sni2</b> را ارسال کنید:", parse_mode=HTML,
                             reply_markup=kb([[btn("❌ انصراف", "cancel:home")]]))

    elif n == "sni2":
        v2 = clean_sni(text)
        if not v2:
            await msg.reply_text("❌ مقدار نامعتبر است. دوباره sni2 را بفرستید.")
            return
        db.execute("UPDATE domains SET sni1=?, sni2=?, sni_status='pending' WHERE id=? AND owner=?", (st["v"], v2, st["id"], u.id))
        db.commit()
        context.user_data.clear()
        d = db.execute("SELECT * FROM domains WHERE id=?", (st["id"],)).fetchone()
        await msg.reply_text("✅ درخواست شما ثبت شد و حداکثر تا <b>یک ساعت</b> دیگر تنظیم می‌شود.",
                             parse_mode=HTML, reply_markup=home_back)
        await context.bot.send_message(ADMIN_ID, sni_admin_text(d), parse_mode=HTML,
                                       reply_markup=kb([[btn("✅ انجام شد", f"snidone:{d['id']}", style="success")]]))

    # ---------- از اینجا به بعد فقط ادمین ----------
    elif not admin:
        context.user_data.clear()

    elif n == "a_ext_name":
        name = text.lower()
        if not name.startswith("."):
            name = "." + name
        if not re.fullmatch(r"\.[a-z0-9.\-]{1,20}", name):
            await msg.reply_text("❌ نام پسوند نامعتبر است. مثال: .com")
            return
        if db.execute("SELECT 1 FROM extensions WHERE name=?", (name,)).fetchone():
            await msg.reply_text("❌ این پسوند قبلاً ثبت شده است.")
            return
        set_state(context, "a_ext_price", name=name)
        await msg.reply_text(f"💰 قیمت پیش‌فرض پسوند <b>{esc(name)}</b> را به تومان بفرستید:", parse_mode=HTML,
                             reply_markup=kb([[btn("❌ انصراف", "cancel:adm:exts")]]))

    elif n == "a_ext_price":
        p = to_int(text)
        if p is None:
            await msg.reply_text("❌ فقط عدد (تومان) بفرستید.")
            return
        if "name" in st:
            db.execute("INSERT INTO extensions(name,price) VALUES(?,?)", (st["name"], p))
            db.commit()
            eid = db.execute("SELECT id FROM extensions WHERE name=?", (st["name"],)).fetchone()["id"]
        else:
            eid = st["id"]
            db.execute("UPDATE extensions SET price=? WHERE id=?", (p, eid))
            db.commit()
        context.user_data.clear()
        await ext_admin_view(update, eid)

    elif n == "a_dom_add":
        e = db.execute("SELECT * FROM extensions WHERE id=?", (st["id"],)).fetchone()
        added, dup, bad = add_domains(e, text)
        context.user_data.clear()
        await msg.reply_text(f"✅ اضافه شد: {added}\n⚠️ تکراری: {dup}\n❌ نامعتبر: {bad}", reply_markup=kb(
            [[btn("🔙 بازگشت", f"adm:ext:{e['id']}")]]))

    elif n == "a_ufind":
        uid = to_int(text)
        context.user_data.clear()
        if uid is None:
            await msg.reply_text("❌ آیدی نامعتبر است.", reply_markup=adm_back)
        else:
            await user_view(update, uid)

    elif n in ("a_uadd", "a_usub"):
        amt = to_int(text)
        if amt is None:
            await msg.reply_text("❌ فقط عدد (تومان) بفرستید.")
            return
        uid = st["id"]
        if n == "a_uadd":
            db.execute("UPDATE users SET balance=balance+? WHERE id=?", (amt, uid))
            note = f"➕ مبلغ {fmt(amt)} تومان توسط مدیریت به موجودی شما اضافه شد."
        else:
            db.execute("UPDATE users SET balance=MAX(balance-?,0) WHERE id=?", (amt, uid))
            note = f"➖ مبلغ {fmt(amt)} تومان توسط مدیریت از موجودی شما کسر شد."
        db.commit()
        context.user_data.clear()
        try:
            await context.bot.send_message(uid, note)
        except Exception:
            pass
        await user_view(update, uid)

    elif n == "a_umsg":
        uid = st["id"]
        context.user_data.clear()
        try:
            await context.bot.copy_message(uid, msg.chat_id, msg.message_id)
            await msg.reply_text("✅ ارسال شد.", reply_markup=kb([[btn("🔙 بازگشت", f"adm:u:{uid}")]]))
        except Exception:
            await msg.reply_text("❌ ارسال نشد (شاید کاربر ربات را بلاک کرده).", reply_markup=kb([[btn("🔙 بازگشت", f"adm:u:{uid}")]]))

    elif n == "a_bc":
        context.user_data.pop("st", None)
        context.user_data["bc"] = (msg.chat_id, msg.message_id)
        total = count("SELECT COUNT(*) FROM users WHERE banned=0")
        await msg.reply_text(f"پیام بالا برای <b>{total}</b> کاربر ارسال شود؟", parse_mode=HTML,
                             reply_markup=kb([[btn("✅ ارسال", "bcok", style="success"), btn("❌ انصراف", "cancel:adm", style="danger")]]))

    elif n == "a_card_num":
        c = re.sub(r"\D", "", text.translate(_DIG))
        if len(c) != 16:
            await msg.reply_text("❌ شماره کارت باید ۱۶ رقم باشد.")
            return
        set_state(context, "a_card_holder", num=c)
        await msg.reply_text("👤 نام صاحب کارت را بفرستید:", reply_markup=kb([[btn("❌ انصراف", "cancel:adm")]]))

    elif n == "a_card_holder":
        set_s("card_number", st["num"])
        set_s("card_holder", text[:60])
        context.user_data.clear()
        await msg.reply_text("✅ اطلاعات کارت ذخیره شد.", reply_markup=adm_back)

    elif n == "a_support":
        v = text.strip()
        if v == "-":
            set_s("support", "")
        else:
            v2 = v.translate(_DIG)
            if v2.lstrip("-").isdigit():
                v = v2
            elif not re.fullmatch(r"@?[A-Za-z0-9_]{4,32}", v):
                await msg.reply_text("❌ آیدی عددی یا یوزرنیم معتبر بفرستید.")
                return
            set_s("support", v)
        context.user_data.clear()
        await msg.reply_text("✅ ذخیره شد.", reply_markup=adm_back)

    elif n == "a_welcome":
        set_s("welcome", text[:1000])
        context.user_data.clear()
        await msg.reply_text("✅ متن خوش‌آمد ذخیره شد.", reply_markup=adm_back)

    elif n == "a_min":
        v = to_int(text)
        if v is None:
            await msg.reply_text("❌ فقط عدد بفرستید.")
            return
        set_s("min_charge", v)
        context.user_data.clear()
        await msg.reply_text("✅ ذخیره شد.", reply_markup=adm_back)


async def send_backup(bot, caption="💾 بکاپ دیتابیس"):
    tmp = "backup_tmp.db"
    dst = sqlite3.connect(tmp)
    db.backup(dst)
    dst.close()
    with open(tmp, "rb") as f:
        await bot.send_document(ADMIN_ID, f, filename=f"backup_{datetime.now():%Y%m%d_%H%M}.db", caption=caption)
    os.remove(tmp)


async def backup_loop(app):
    while True:
        await asyncio.sleep(12 * 3600)
        try:
            await send_backup(app.bot)
        except Exception:
            logging.exception("backup")


async def cmd_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_admin(update.effective_user.id):
        await send_backup(context.bot)


async def post_init(app):
    msg = "✅ ربات روشن شد."
    if not PERSISTENT:
        msg += ("\n\n⚠️ Volume در Railway وصل نیست؛ با هر دیپلوی یا ری‌استارت اطلاعات ربات پاک می‌شود. "
                "برای رفع: در Railway روی صفحه پروژه راست‌کلیک ← Volume ← Mount path را /data بگذار. "
                "تا آن موقع هر ۱۲ ساعت بکاپ دیتابیس برایت ارسال می‌شود.")
    try:
        await app.bot.send_message(ADMIN_ID, msg)
    except Exception:
        pass
    app.create_task(backup_loop(app))


async def on_error(update, context):
    logging.error("خطا:", exc_info=context.error)


def main():
    logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
    if BOT_TOKEN.startswith("PUT_"):
        print("❌ متغیر BOT_TOKEN تنظیم نشده است.")
        raise SystemExit(1)
    b = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init)
    if PROXY:
        b = b.proxy(PROXY).get_updates_proxy(PROXY)
    app = b.build()
    app.add_handler(CommandHandler("start", cmd_start, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("id", cmd_id, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("panel", cmd_panel, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("backup", cmd_backup, filters=filters.ChatType.PRIVATE))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, on_message))
    app.add_error_handler(on_error)
    print("✅ ربات روشن شد.")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
