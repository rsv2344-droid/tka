from flask import Flask, request, redirect, session, render_template_string, send_from_directory, Response, jsonify
import os
from werkzeug.utils import secure_filename
import secrets
from datetime import datetime, timedelta
from PIL import Image, ImageDraw, ImageFont
import io
import textwrap
import psycopg2
from psycopg2.extras import RealDictCursor
import requests as http_requests
import cloudinary
import cloudinary.uploader
import hashlib
import re
import smtplib
import random
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))

# ============================================================
# ADMIN CONFIG - Encrypted access
# ============================================================
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASS_HASH = os.environ.get("ADMIN_PASS_HASH", hashlib.sha256("admin123".encode()).hexdigest())

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def verify_admin_password(password):
    return hashlib.sha256(password.encode()).hexdigest() == ADMIN_PASS_HASH

# ============================================================
# CLOUDINARY CONFIG
# ============================================================
cloudinary.config(
    cloud_name = os.environ.get("CLOUDINARY_CLOUD_NAME", ""),
    api_key    = os.environ.get("CLOUDINARY_API_KEY", ""),
    api_secret = os.environ.get("CLOUDINARY_API_SECRET", ""),
    secure     = True
)

# ============================================================
# NEON POSTGRESQL CONFIG
# ============================================================
DATABASE_URL = os.environ.get("DATABASE_URL", "")

# SMTP CONFIG (for OTP email)
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
SMTP_FROM = os.environ.get("SMTP_FROM", SMTP_USER)

# FONT PATHS
FONT_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD    = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_MONO    = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"

# ============================================================
# POSTGRESQL DB FUNCTIONS
# ============================================================
def db():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    return conn

def q(sql, args=(), one=False):
    pg_sql = sql.replace("?", "%s")
    pg_sql = pg_sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
    if "INSERT OR IGNORE" in sql:
        pg_sql = pg_sql.replace("INSERT OR IGNORE INTO", "INSERT INTO")
        if "VALUES(%s,%s)" in pg_sql:
            pg_sql = pg_sql.replace("VALUES(%s,%s)", "VALUES(%s,%s) ON CONFLICT DO NOTHING")

    conn = db()
    try:
        cur = conn.cursor()
        cur.execute(pg_sql, args if args else None)
        if pg_sql.strip().upper().startswith("SELECT") or "RETURNING" in pg_sql.upper():
            rows = cur.fetchall()
            conn.commit()
            return (rows[0] if rows else None) if one else rows
        else:
            conn.commit()
            return None
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

def q_many(sql, args_list):
    pg_sql = sql.replace("?", "%s")
    conn = db()
    try:
        cur = conn.cursor()
        cur.executemany(pg_sql, args_list)
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

# ============================================================
# SCHEMA SETUP
# ============================================================
def init_db():
    conn = db()
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS categories(
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        image TEXT,
        parent_id INTEGER,
        sort_order INTEGER DEFAULT 0,
        card_size TEXT DEFAULT 'medium'
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS products(
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        price REAL NOT NULL,
        image TEXT,
        category_id INTEGER,
        sort_order INTEGER DEFAULT 0,
        card_size TEXT DEFAULT 'medium',
        FOREIGN KEY(category_id) REFERENCES categories(id)
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS design(
        id SERIAL PRIMARY KEY,
        background TEXT,
        overlay TEXT,
        animation TEXT
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS orders(
        id SERIAL PRIMARY KEY,
        created_at TEXT,
        phone TEXT,
        address TEXT,
        details TEXT,
        items TEXT,
        total REAL,
        latitude TEXT,
        longitude TEXT,
        email TEXT
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS settings(
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS customers(
        id SERIAL PRIMARY KEY,
        email TEXT UNIQUE NOT NULL,
        name TEXT,
        phone TEXT,
        address TEXT,
        created_at TEXT
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS otp_codes(
        id SERIAL PRIMARY KEY,
        email TEXT NOT NULL,
        code TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        expires_at TIMESTAMP NOT NULL,
        used BOOLEAN DEFAULT FALSE
    )""")
    conn.commit()
    conn.close()

init_db()

DEFAULT_SETTINGS = {
    "checkout_title":             "أكمل طلبك",
    "checkout_phone_label":       "رقم الهاتف *",
    "checkout_phone_placeholder": "مثال: 0912345678",
    "checkout_address_label":     "عنوان التوصيل *",
    "checkout_address_placeholder": "الشارع، المبنى، المنطقة...",
    "checkout_notes_label":       "ملاحظات إضافية (اختياري)",
    "checkout_notes_placeholder": "أي تعليمات خاصة...",
    "checkout_confirm_btn":       "تأكيد الطلب",
    "checkout_back_btn":          "العودة للسلة",
    "store_name":                 "متجري",
    "cart_btn_label":             "السلة",
    "order_confirmed_title":      "تم تأكيد الطلب!",
    "order_confirmed_msg":        "شكراً لك، استلمنا طلبك.",
    "order_confirmed_redirect":   "جاري إعادة التوجيه إلى المتجر...",
    "order_confirmed_back_btn":   "العودة للمتجر",
    "lang":                       "ar",
    "otp_subject":                "رمز التحقق من متجري",
    "otp_body":                   "رمز التحقق الخاص بك هو: {code}\nصالح لمدة 5 دقائق.",
    "enter_email":                "البريد الإلكتروني",
    "enter_otp":                  "أدخل رمز التحقق",
    "verify_btn":                 "تحقق",
    "otp_sent":                   "تم إرسال رمز التحقق إلى بريدك الإلكتروني.",
    "otp_invalid":                "رمز التحقق غير صحيح أو منتهي الصلاحية.",
    "otp_expired":                "انتهت صلاحية الرمز، حاول مرة أخرى.",
    "resend_otp":                 "إعادة إرسال الرمز",
}

for k, v in DEFAULT_SETTINGS.items():
    conn = db()
    cur = conn.cursor()
    cur.execute("INSERT INTO settings(key, value) VALUES(%s,%s) ON CONFLICT DO NOTHING", (k, v))
    conn.commit()
    conn.close()

def get_setting(key):
    row = q("SELECT value FROM settings WHERE key=%s", (key,), one=True)
    return row["value"] if row else DEFAULT_SETTINGS.get(key, "")

def get_all_settings():
    rows = q("SELECT key, value FROM settings")
    return {r["key"]: r["value"] for r in rows}

for migration in [
    "ALTER TABLE categories ADD COLUMN IF NOT EXISTS parent_id INTEGER",
    "ALTER TABLE categories ADD COLUMN IF NOT EXISTS sort_order INTEGER DEFAULT 0",
    "ALTER TABLE categories ADD COLUMN IF NOT EXISTS card_size TEXT DEFAULT 'medium'",
    "ALTER TABLE products ADD COLUMN IF NOT EXISTS sort_order INTEGER DEFAULT 0",
    "ALTER TABLE products ADD COLUMN IF NOT EXISTS card_size TEXT DEFAULT 'medium'",
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS latitude TEXT",
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS longitude TEXT",
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS email TEXT",
]:
    try:
        q(migration)
    except Exception:
        pass

# ============================================================
# CLOUDINARY STORAGE
# ============================================================
def save(file):
    if file and file.filename:
        if not allowed_file(file.filename):
            return None
        try:
            file_bytes = file.read()
            result = cloudinary.uploader.upload(
                io.BytesIO(file_bytes),
                folder="store_uploads",
                resource_type="image"
            )
            return result["public_id"]
        except Exception as e:
            print(f"Cloudinary upload error: {e}")
            return None
    return None

def image_url(public_id):
    if not public_id:
        return ""
    cloud_name = os.environ.get("CLOUDINARY_CLOUD_NAME", "")
    return f"https://res.cloudinary.com/{cloud_name}/image/upload/{public_id}"

def require_admin():
    return session.get("admin") is True

SIZE_HEIGHT = {
    "small":  "100px",
    "medium": "140px",
    "large":  "200px",
}

# ============================================================
# OTP FUNCTIONS
# ============================================================
def generate_otp():
    return str(random.randint(100000, 999999))

def send_otp_email(email, otp):
    if not SMTP_USER or not SMTP_PASS:
        print("SMTP not configured, skipping email send.")
        return False
    subject = get_setting("otp_subject") or "رمز التحقق من متجري"
    body = get_setting("otp_body").format(code=otp) if "{" in get_setting("otp_body") else f"رمز التحقق الخاص بك هو: {otp}\nصالح لمدة 5 دقائق."
    msg = MIMEMultipart()
    msg["From"] = SMTP_FROM
    msg["To"] = email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))
    try:
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        print(f"Error sending email: {e}")
        return False

def store_otp(email, otp):
    expires_at = datetime.now() + timedelta(minutes=5)
    q("INSERT INTO otp_codes(email, code, expires_at) VALUES(%s,%s,%s)", (email, otp, expires_at))

def verify_otp(email, otp):
    row = q("SELECT id FROM otp_codes WHERE email=%s AND code=%s AND used=FALSE AND expires_at > NOW() ORDER BY id DESC LIMIT 1", (email, otp), one=True)
    if row:
        q("UPDATE otp_codes SET used=TRUE WHERE id=%s", (row["id"],))
        return True
    return False

# ============================================================
# ORDER PNG GENERATOR
# ============================================================
def generate_order_png(order):
    WHITE      = (255, 255, 255)
    BLACK      = (20,  20,  20)
    DARK_GRAY  = (60,  60,  60)
    MED_GRAY   = (120, 120, 120)
    LIGHT_GRAY = (240, 240, 240)
    ACCENT     = (30,  30,  30)
    DIVIDER    = (210, 210, 210)

    try:
        f_title  = ImageFont.truetype(FONT_BOLD,    28)
        f_header = ImageFont.truetype(FONT_BOLD,    16)
        f_label  = ImageFont.truetype(FONT_BOLD,    14)
        f_value  = ImageFont.truetype(FONT_REGULAR, 14)
        f_mono   = ImageFont.truetype(FONT_MONO,    13)
        f_small  = ImageFont.truetype(FONT_REGULAR, 12)
        f_total  = ImageFont.truetype(FONT_BOLD,    20)
        f_id     = ImageFont.truetype(FONT_BOLD,    36)
    except:
        f_title = f_header = f_label = f_value = f_mono = f_small = f_total = f_id = ImageFont.load_default()

    W = 640
    PAD = 36

    items_text = order.get("items", "") or ""
    item_lines = [l.strip() for l in items_text.strip().split("\n") if l.strip()]
    wrapped_items = []
    for line in item_lines:
        wrapped = textwrap.wrap(line, width=62)
        wrapped_items.extend(wrapped if wrapped else [line])

    notes = (order.get("details", "") or "").strip()
    note_lines = textwrap.wrap(notes, width=62) if notes else []

    lat = (order.get("latitude") or "").strip()
    lon = (order.get("longitude") or "").strip()
    has_location = bool(lat and lon)

    H = 80 + 70 + 20 + 50 + 10 + 1 + 16 + 3*30
    if notes:
        H += 30 + (len(note_lines) - 1) * 18
    if has_location:
        H += 30
    H += 16 + 1 + 16 + 24 + 10 + len(wrapped_items) * 20 + 10 + 1 + 16 + 36 + 36

    img = Image.new("RGB", (W, H), WHITE)
    d   = ImageDraw.Draw(img)

    d.rectangle([0, 0, W, 70], fill=ACCENT)
    store_name = get_setting("store_name") or "My Store"
    d.text((PAD, 20), store_name, font=f_title, fill=WHITE)
    d.text((W - PAD, 20), "ORDER RECEIPT", font=f_small, fill=(180,180,180), anchor="ra")

    y = 90
    oid = str(order.get("id", ""))
    d.text((PAD, y), f"#{oid}", font=f_id, fill=BLACK)
    date_str = str(order.get("created_at", ""))
    d.text((W - PAD, y + 8), date_str, font=f_small, fill=MED_GRAY, anchor="ra")
    y += 58

    d.line([(PAD, y), (W - PAD, y)], fill=DIVIDER, width=1)
    y += 16

    def info_row(label, value, ypos):
        d.text((PAD, ypos), label + ":", font=f_label, fill=MED_GRAY)
        d.text((PAD + 120, ypos), value or "-", font=f_value, fill=DARK_GRAY)
        return ypos + 30

    y = info_row("Phone",   order.get("phone", ""),   y)
    y = info_row("Address", order.get("address", ""), y)

    if notes:
        d.text((PAD, y), "Notes:", font=f_label, fill=MED_GRAY)
        for i, nl in enumerate(note_lines):
            d.text((PAD + 120, y + i * 18), nl, font=f_value, fill=DARK_GRAY)
        y += 30 + (len(note_lines) - 1) * 18

    if has_location:
        loc_val = f"{lat}, {lon}"
        y = info_row("Location", loc_val, y)

    y += 10
    d.line([(PAD, y), (W - PAD, y)], fill=DIVIDER, width=1)
    y += 16

    d.text((PAD, y), "ITEMS", font=f_header, fill=BLACK)
    y += 30

    d.rectangle([PAD, y, W - PAD, y + len(wrapped_items) * 20 + 12], fill=LIGHT_GRAY, outline=DIVIDER)
    y += 8
    for line in wrapped_items:
        d.text((PAD + 12, y), line, font=f_mono, fill=DARK_GRAY)
        y += 20
    y += 6

    d.line([(PAD, y), (W - PAD, y)], fill=DIVIDER, width=1)
    y += 16

    total_val = order.get("total", 0)
    d.text((PAD, y), "TOTAL", font=f_header, fill=MED_GRAY)
    d.text((W - PAD, y - 2), f"{float(total_val):.0f}", font=f_total, fill=BLACK, anchor="ra")

    y = H - 22
    d.text((W // 2, y), "Thank you for your order!", font=f_small, fill=MED_GRAY, anchor="ma")

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf

# ============================================================
# TRANSLATIONS
# ============================================================
TRANSLATIONS = {
    "ar": {
        "categories": "الأقسام",
        "products": "المنتجات",
        "cart": "السلة",
        "checkout": "إتمام الطلب",
        "add_to_cart": "أضف للسلة",
        "back": "رجوع",
        "empty_cart": "السلة فارغة",
        "browse": "تصفح المنتجات",
        "total": "المجموع",
        "phone": "رقم الهاتف",
        "address": "العنوان",
        "notes": "ملاحظات",
        "confirm": "تأكيد الطلب",
        "order_confirmed": "تم تأكيد الطلب!",
        "thank_you": "شكراً لك، استلمنا طلبك.",
        "redirecting": "جاري إعادة التوجيه...",
        "back_to_store": "العودة للمتجر",
        "login": "تسجيل الدخول",
        "register": "إنشاء حساب",
        "logout": "تسجيل الخروج",
        "my_orders": "طلباتي",
        "welcome": "مرحباً",
        "guest": "زائر",
        "qty": "الكمية",
        "remove": "حذف",
        "continue_shopping": "مواصلة التسوق",
        "customer_login": "تسجيل دخول العميل",
        "customer_register": "إنشاء حساب عميل",
        "name": "الاسم",
        "submit": "إرسال",
        "have_account": "لديك حساب؟",
        "no_account": "ليس لديك حساب؟",
        "create_one": "أنشئ واحداً",
        "login_here": "سجل دخول هنا",
        "orders": "الطلبات",
        "date": "التاريخ",
        "status": "الحالة",
        "pending": "قيد الانتظار",
        "completed": "مكتمل",
        "search": "بحث",
        "no_products": "لا توجد منتجات",
        "no_categories": "لا توجد أقسام",
        "price": "السعر",
        "each": "للواحد",
        "subtotal": "المجموع الفرعي",
        "delete": "حذف",
        "edit": "تعديل",
        "save": "حفظ",
        "cancel": "إلغاء",
        "add": "إضافة",
        "category": "القسم",
        "product": "المنتج",
        "image": "الصورة",
        "optional": "اختياري",
        "required": "مطلوب",
        "success": "تم بنجاح",
        "error": "خطأ",
        "loading": "جاري التحميل...",
        "location": "الموقع",
        "detecting": "جاري تحديد الموقع...",
        "detected": "تم تحديد الموقع",
        "not_available": "الموقع غير متاح",
        "not_supported": "المتصفح لا يدعم تحديد الموقع",
        "no_orders": "لا توجد طلبات",
        "order_details": "تفاصيل الطلب",
        "enter_email": "البريد الإلكتروني",
        "enter_otp": "أدخل رمز التحقق",
        "verify_btn": "تحقق",
        "otp_sent": "تم إرسال رمز التحقق إلى بريدك الإلكتروني.",
        "otp_invalid": "رمز التحقق غير صحيح أو منتهي الصلاحية.",
        "otp_expired": "انتهت صلاحية الرمز، حاول مرة أخرى.",
        "resend_otp": "إعادة إرسال الرمز",
        "login_required": "يجب تسجيل الدخول لإتمام الطلب",
    },
    "en": {
        "categories": "Categories",
        "products": "Products",
        "cart": "Cart",
        "checkout": "Checkout",
        "add_to_cart": "Add to Cart",
        "back": "Back",
        "empty_cart": "Your cart is empty",
        "browse": "Browse Products",
        "total": "Total",
        "phone": "Phone Number",
        "address": "Address",
        "notes": "Notes",
        "confirm": "Confirm Order",
        "order_confirmed": "Order Confirmed!",
        "thank_you": "Thank you, we received your order.",
        "redirecting": "Redirecting to store...",
        "back_to_store": "Back to Store",
        "login": "Login",
        "register": "Register",
        "logout": "Logout",
        "my_orders": "My Orders",
        "welcome": "Welcome",
        "guest": "Guest",
        "qty": "Qty",
        "remove": "Remove",
        "continue_shopping": "Continue Shopping",
        "customer_login": "Customer Login",
        "customer_register": "Customer Registration",
        "name": "Name",
        "submit": "Submit",
        "have_account": "Have an account?",
        "no_account": "Don't have an account?",
        "create_one": "Create one",
        "login_here": "Login here",
        "orders": "Orders",
        "date": "Date",
        "status": "Status",
        "pending": "Pending",
        "completed": "Completed",
        "search": "Search",
        "no_products": "No products",
        "no_categories": "No categories",
        "price": "Price",
        "each": "each",
        "subtotal": "Subtotal",
        "delete": "Delete",
        "edit": "Edit",
        "save": "Save",
        "cancel": "Cancel",
        "add": "Add",
        "category": "Category",
        "product": "Product",
        "image": "Image",
        "optional": "Optional",
        "required": "Required",
        "success": "Success",
        "error": "Error",
        "loading": "Loading...",
        "location": "Location",
        "detecting": "Detecting location...",
        "detected": "Location detected",
        "not_available": "Location not available",
        "not_supported": "Geolocation not supported",
        "no_orders": "No orders yet",
        "order_details": "Order Details",
        "enter_email": "Email",
        "enter_otp": "Enter verification code",
        "verify_btn": "Verify",
        "otp_sent": "Verification code sent to your email.",
        "otp_invalid": "Invalid or expired verification code.",
        "otp_expired": "Code expired, please try again.",
        "resend_otp": "Resend code",
        "login_required": "Login required to place order",
    }
}

def t(key):
    lang = get_setting("lang") or "ar"
    return TRANSLATIONS.get(lang, TRANSLATIONS["ar"]).get(key, key)

def get_dir():
    lang = get_setting("lang") or "ar"
    return "rtl" if lang == "ar" else "ltr"

# ============================================================
# MOBILE STORE FRONTEND - MODERN DESIGN
# ============================================================
MOBILE_BASE = """<!DOCTYPE html>
<html lang="{{lang}}" dir="{{dir}}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=Tajawal:wght@400;500;700;800&display=swap" rel="stylesheet">
<style>
:root {
  --primary: #1a1a2e;
  --primary-light: #16213e;
  --accent: #4361ee;
  --accent-hover: #3451c7;
  --success: #10b981;
  --danger: #ef4444;
  --warning: #f59e0b;
  --bg: #f8fafc;
  --card: #ffffff;
  --text: #1e293b;
  --text-muted: #64748b;
  --border: #e2e8f0;
  --shadow-sm: 0 1px 2px 0 rgb(0 0 0 / 0.05);
  --shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1);
  --shadow-lg: 0 10px 15px -3px rgb(0 0 0 / 0.1), 0 4px 6px -4px rgb(0 0 0 / 0.1);
  --radius: 16px;
  --radius-sm: 12px;
  --radius-xs: 8px;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: {{font_family}};
  background: var(--bg);
  min-height: 100vh;
  padding-bottom: 100px;
  color: var(--text);
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}
.store-nav {
  background: rgba(255,255,255,0.95);
  padding: 14px 18px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  box-shadow: var(--shadow-sm);
  position: sticky;
  top: 0;
  z-index: 100;
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
}
.store-nav .store-name {
  font-size: 20px;
  font-weight: 800;
  color: var(--primary);
  text-decoration: none;
  letter-spacing: -0.5px;
}
.nav-cart-btn {
  background: var(--primary);
  color: #fff;
  border: none;
  border-radius: 50px;
  padding: 10px 18px;
  font-size: 14px;
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 8px;
  text-decoration: none;
  transition: all 0.2s ease;
  box-shadow: var(--shadow);
}
.nav-cart-btn:hover { background: var(--primary-light); transform: translateY(-1px); }
.nav-cart-btn .badge {
  background: var(--danger);
  color: #fff;
  border-radius: 50px;
  padding: 2px 8px;
  font-size: 11px;
  font-weight: 700;
}
.products-grid {
  display: grid;
  gap: 14px;
  padding: 16px;
  grid-template-columns: repeat(2, 1fr);
}
.product-card {
  background: var(--card);
  border-radius: var(--radius);
  overflow: hidden;
  box-shadow: var(--shadow-sm);
  display: flex;
  flex-direction: column;
  text-decoration: none;
  color: inherit;
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
  border: 1px solid var(--border);
}
.product-card:hover {
  transform: translateY(-4px);
  box-shadow: var(--shadow-lg);
  border-color: var(--accent);
}
.product-card img {
  width: 100%;
  object-fit: cover;
  transition: transform 0.3s ease;
}
.product-card:hover img { transform: scale(1.05); }
.product-card .no-img {
  width: 100%;
  background: linear-gradient(135deg, #f1f5f9 0%, #e2e8f0 100%);
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-muted);
  font-size: 13px;
  font-weight: 500;
}
.product-card .card-info {
  padding: 12px 12px 14px;
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.product-card .card-name {
  font-size: 14px;
  font-weight: 700;
  color: var(--text);
  line-height: 1.3;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.product-card .card-price {
  font-size: 15px;
  font-weight: 800;
  color: var(--accent);
}
.qty-controls {
  display: flex;
  align-items: center;
  background: #f1f5f9;
  border-radius: 50px;
  overflow: hidden;
  width: 100%;
  border: 1px solid var(--border);
}
.qty-controls form { flex: 1; }
.qty-controls button {
  width: 100%;
  background: none;
  border: none;
  font-size: 18px;
  font-weight: 700;
  padding: 8px 0;
  color: var(--primary);
  cursor: pointer;
  transition: background 0.15s;
}
.qty-controls button:hover { background: #e2e8f0; }
.qty-controls .qty-num {
  font-size: 14px;
  font-weight: 700;
  color: var(--text);
  min-width: 32px;
  text-align: center;
}
.add-btn {
  width: 100%;
  background: var(--primary);
  color: #fff;
  border: none;
  border-radius: 50px;
  padding: 10px 0;
  font-size: 13px;
  font-weight: 700;
  cursor: pointer;
  text-align: center;
  text-decoration: none;
  display: block;
  transition: all 0.2s ease;
  box-shadow: var(--shadow-sm);
}
.add-btn:hover { background: var(--primary-light); transform: translateY(-1px); }
.back-btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  background: var(--card);
  color: var(--text);
  font-size: 14px;
  font-weight: 600;
  text-decoration: none;
  padding: 10px 18px;
  border-radius: var(--radius-sm);
  box-shadow: var(--shadow-sm);
  margin: 12px 16px 8px;
  border: 1px solid var(--border);
  transition: all 0.2s ease;
}
.back-btn:hover { box-shadow: var(--shadow); transform: translateX(-2px); }
.section-title {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 16px;
  font-weight: 800;
  color: var(--text);
  background: var(--card);
  padding: 12px 20px;
  border-radius: var(--radius-sm);
  box-shadow: var(--shadow-sm);
  margin: 12px 16px 8px;
  border: 1px solid var(--border);
}
.cart-item {
  background: var(--card);
  border-radius: var(--radius);
  padding: 16px;
  margin: 0 16px 12px;
  display: flex;
  align-items: center;
  gap: 14px;
  box-shadow: var(--shadow-sm);
  border: 1px solid var(--border);
  transition: all 0.2s ease;
}
.cart-item:hover { box-shadow: var(--shadow); }
.cart-item img {
  width: 64px;
  height: 64px;
  border-radius: var(--radius-xs);
  object-fit: cover;
  flex-shrink: 0;
}
.cart-item .no-img-sm {
  width: 64px;
  height: 64px;
  border-radius: var(--radius-xs);
  background: linear-gradient(135deg, #f1f5f9 0%, #e2e8f0 100%);
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-muted);
  font-size: 20px;
}
.cart-item .item-details { flex: 1; min-width: 0; }
.cart-item .item-name {
  font-size: 15px;
  font-weight: 700;
  color: var(--text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.cart-item .item-price {
  font-size: 13px;
  color: var(--text-muted);
  margin-top: 3px;
  font-weight: 500;
}
.cart-item .item-subtotal {
  font-size: 15px;
  font-weight: 800;
  color: var(--accent);
}
.qty-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 10px;
}
.qty-row form button {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  border: 1.5px solid var(--border);
  background: var(--card);
  font-size: 16px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  color: var(--text);
  transition: all 0.15s;
}
.qty-row form button:hover { background: var(--bg); border-color: var(--accent); }
.qty-row .qty-num {
  font-size: 15px;
  font-weight: 700;
  min-width: 24px;
  text-align: center;
  color: var(--text);
}
.delete-btn {
  background: none;
  border: none;
  color: var(--danger);
  font-size: 20px;
  cursor: pointer;
  padding: 6px;
  margin-left: auto;
  border-radius: 50%;
  transition: all 0.15s;
  width: 36px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.delete-btn:hover { background: #fef2f2; }
.cart-footer {
  position: fixed;
  bottom: 0;
  left: 0;
  right: 0;
  background: var(--card);
  padding: 16px 18px;
  box-shadow: 0 -4px 20px rgba(0,0,0,0.08);
  z-index: 200;
  border-top: 1px solid var(--border);
}
.cart-footer .total-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}
.cart-footer .total-label { font-size: 16px; color: var(--text-muted); font-weight: 500; }
.cart-footer .total-val { font-size: 24px; font-weight: 800; color: var(--primary); }
.checkout-btn {
  display: block;
  width: 100%;
  background: var(--primary);
  color: #fff;
  border: none;
  border-radius: 50px;
  padding: 16px;
  font-size: 17px;
  font-weight: 700;
  text-align: center;
  text-decoration: none;
  cursor: pointer;
  transition: all 0.2s ease;
  box-shadow: var(--shadow);
}
.checkout-btn:hover { background: var(--primary-light); transform: translateY(-2px); }
.lang-switcher {
  position: fixed;
  bottom: 90px;
  {{lang_pos}}: 16px;
  z-index: 150;
  background: var(--card);
  border-radius: 50px;
  padding: 8px 16px;
  box-shadow: var(--shadow);
  border: 1px solid var(--border);
  font-size: 13px;
  font-weight: 700;
  color: var(--text);
  text-decoration: none;
  display: flex;
  align-items: center;
  gap: 6px;
  transition: all 0.2s ease;
}
.lang-switcher:hover { transform: scale(1.05); box-shadow: var(--shadow-lg); }
.customer-menu {
  position: fixed;
  bottom: 140px;
  {{lang_pos}}: 16px;
  z-index: 150;
  background: var(--card);
  border-radius: var(--radius-sm);
  padding: 8px;
  box-shadow: var(--shadow);
  border: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 140px;
}
.customer-menu a {
  padding: 8px 12px;
  border-radius: var(--radius-xs);
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
  text-decoration: none;
  display: flex;
  align-items: center;
  gap: 8px;
  transition: all 0.15s;
}
.customer-menu a:hover { background: var(--bg); color: var(--accent); }
.empty-state {
  text-align: center;
  padding: 80px 24px;
}
.empty-state-icon {
  font-size: 64px;
  margin-bottom: 16px;
  opacity: 0.3;
}
.empty-state-text {
  font-size: 18px;
  font-weight: 700;
  color: var(--text-muted);
  margin-bottom: 8px;
}
.empty-state-sub {
  font-size: 14px;
  color: var(--text-muted);
  margin-bottom: 24px;
}
{BG_STYLE}
@keyframes fadeIn {
  from { opacity: 0; transform: translateY(10px); }
  to { opacity: 1; transform: translateY(0); }
}
.fade-in { animation: fadeIn 0.4s ease-out; }
@keyframes slideUp {
  from { opacity: 0; transform: translateY(20px); }
  to { opacity: 1; transform: translateY(0); }
}
.slide-up { animation: slideUp 0.5s ease-out; }
</style>
</head>
<body>
"""

def get_cart_summary():
    ids = session.get("cart", [])
    counts = {}
    for i in ids:
        counts[i] = counts.get(i, 0) + 1
    total = 0.0
    qty = sum(counts.values())
    for prod_id, cnt in counts.items():
        p = q("SELECT price FROM products WHERE id=%s", (prod_id,), one=True)
        if p:
            total += float(p["price"]) * cnt
    return qty, total

def get_mobile_base():
    d = q("SELECT * FROM design ORDER BY id DESC LIMIT 1", one=True)
    bg_style = ""
    if d and d["background"]:
        bg_url = image_url(d["background"])
        bg_style = f"body::before {{ content: ''; position: fixed; top: 0; left: 0; right: 0; bottom: 0; width: 100%; height: 100%; background-image: url('{bg_url}'); background-size: cover; background-position: center; background-repeat: no-repeat; z-index: -1; opacity: 0.15; }}"
    lang = get_setting("lang") or "ar"
    dir_ = "rtl" if lang == "ar" else "ltr"
    font_family = "'Tajawal', 'Inter', sans-serif" if lang == "ar" else "'Inter', 'Tajawal', sans-serif"
    lang_pos = "left" if lang == "ar" else "right"
    return MOBILE_BASE.replace("{BG_STYLE}", bg_style).replace("{{lang}}", lang).replace("{{dir}}", dir_).replace("{{font_family}}", font_family).replace("{{lang_pos}}", lang_pos)

def get_navbar(cart_qty=0, cart_total=0):
    store_name = get_setting("store_name")
    cart_label = get_setting("cart_btn_label")
    badge = f'<span class="badge">{cart_qty}</span>' if cart_qty > 0 else ""
    total_str = f" &middot; {int(cart_total)}" if cart_qty > 0 else ""
    return f'''
    <nav class="store-nav">
        <a href="/" class="store-name">{store_name}</a>
        <a href="/cart" class="nav-cart-btn">{cart_label}{total_str} {badge}</a>
    </nav>'''

def get_customer_menu():
    customer = session.get("customer")
    if customer:
        return f'''
        <div class="customer-menu">
            <a href="/customer/orders"><span>&#128203;</span> {t("my_orders")}</a>
            <a href="/customer/logout"><span>&#128682;</span> {t("logout")}</a>
        </div>'''
    return f'''
    <div class="customer-menu">
        <a href="/customer/login"><span>&#128100;</span> {t("login")}</a>
        <a href="/customer/register"><span>&#128221;</span> {t("register")}</a>
    </div>'''

def render_grid(items, href_fn, action_fn, size_field="card_size"):
    if not items:
        return ""
    html = '<div class="products-grid fade-in">'
    for row in items:
        item = dict(row)
        size = item.get(size_field, "medium") or "medium"
        size = size if size in SIZE_HEIGHT else "medium"
        cols = {"small": "span 1", "medium": "span 1", "large": "span 2"}.get(size, "span 1")
        height = SIZE_HEIGHT.get(size, "140px")
        href = href_fn(item) if href_fn else None
        action = action_fn(item) if action_fn else ""
        item_image = item.get("image")
        img_src = image_url(item_image) if item_image else ""
        img_html = f'<img src="{img_src}" style="width:100%;height:{height};object-fit:cover;" alt="" loading="lazy">' if item_image else f'<div class="no-img" style="height:{height};"><span>&#128247;</span></div>'
        name = item.get("name", "")
        item_price = item.get("price")
        price_html = f'<div class="card-price">{int(item_price)}</div>' if item_price is not None else ""

        if href:
            inner = f'<a href="{href}" class="product-card" style="grid-column:{cols};">{img_html}<div class="card-info"><div class="card-name">{name}</div></div></a>'
        else:
            inner = f'<div class="product-card" style="grid-column:{cols};">{img_html}<div class="card-info"><div class="card-name">{name}</div>{price_html}{action}</div></div>'
        html += inner
    html += "</div>"
    return html

@app.route("/")
def home():
    cats = q("SELECT * FROM categories WHERE parent_id IS NULL ORDER BY sort_order ASC, id ASC")
    base = get_mobile_base()
    qty, total = get_cart_summary()
    navbar = get_navbar(qty, total)
    lang_switch = f'<a href="/switch_lang" class="lang-switcher">&#127760; {"EN" if get_setting("lang") == "ar" else "AR"}</a>'
    customer_menu = get_customer_menu()
    if cats:
        cats_html = render_grid(cats, href_fn=lambda c: f"/category/{c['id']}", action_fn=None)
    else:
        cats_html = f'<div class="empty-state"><div class="empty-state-icon">&#128193;</div><div class="empty-state-text">{t("no_categories")}</div></div>'
    return base + navbar + lang_switch + customer_menu + f'<div class="slide-up"><p class="section-title">&#128193; {t("categories")}</p>{cats_html}</div><script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script></body></html>'

@app.route("/switch_lang")
def switch_lang():
    current = get_setting("lang") or "ar"
    new_lang = "en" if current == "ar" else "ar"
    conn = db()
    cur = conn.cursor()
    cur.execute("INSERT INTO settings(key, value) VALUES(%s,%s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", ("lang", new_lang))
    conn.commit()
    conn.close()
    return redirect("/")

@app.route("/category/<int:id>")
def category(id):
    cat = q("SELECT * FROM categories WHERE id=%s", (id,), one=True)
    if not cat:
        return f"<h3>{t('error')}</h3><a href='/'>{t('back')}</a>", 404
    subcats = q("SELECT * FROM categories WHERE parent_id=%s ORDER BY sort_order ASC, id ASC", (id,))
    prods = q("SELECT * FROM products WHERE category_id=%s ORDER BY sort_order ASC, id ASC", (id,))
    base = get_mobile_base()
    qty, total = get_cart_summary()
    navbar = get_navbar(qty, total)
    cart_counts = {}
    for i in session.get("cart", []):
        cart_counts[i] = cart_counts.get(i, 0) + 1
    back_url = f"/category/{cat['parent_id']}" if cat["parent_id"] else "/"
    subcats_html = render_grid(subcats, href_fn=lambda c: f"/category/{c['id']}", action_fn=None) if subcats else ""

    def prod_action(p):
        cnt = cart_counts.get(p["id"], 0)
        pid = p["id"]
        if cnt > 0:
            return f'<div class="qty-controls"><form method="post" action="/cart/remove/{pid}?next=/category/{id}"><button>-</button></form><span class="qty-num">{cnt}</span><form method="post" action="/cart/add_one/{pid}?next=/category/{id}"><button>+</button></form></div>'
        return f'<a href="/add/{pid}" class="add-btn">{t("add_to_cart")}</a>'

    prods_html = render_grid(prods, href_fn=None, action_fn=prod_action) if prods else (
        f'<div class="empty-state"><div class="empty-state-icon">&#128230;</div><div class="empty-state-text">{t("no_products")}</div></div>' if not subcats else ""
    )
    sections = subcats_html + prods_html
    lang_switch = f'<a href="/switch_lang" class="lang-switcher">&#127760; {"EN" if get_setting("lang") == "ar" else "AR"}</a>'
    return base + navbar + lang_switch + f'<a href="{back_url}" class="back-btn">&#8592; {t("back")}</a><div class="slide-up"><p class="section-title">&#128193; {cat["name"]}</p>{sections}</div><script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script></body></html>'

@app.route("/add/<int:id>")
def add(id):
    product = q("SELECT id, category_id FROM products WHERE id=%s", (id,), one=True)
    if not product:
        return redirect("/")
    cart = session.get("cart", [])
    cart.append(id)
    session["cart"] = cart
    return redirect(f"/category/{product['category_id']}")

@app.route("/cart")
def cart():
    ids = session.get("cart", [])
    counts = {}
    for i in ids:
        counts[i] = counts.get(i, 0) + 1
    items = []
    total = 0.0
    for prod_id, cnt in counts.items():
        p = q("SELECT * FROM products WHERE id=%s", (prod_id,), one=True)
        if p:
            subtotal = float(p["price"]) * cnt
            items.append({"id": prod_id, "name": p["name"], "price": p["price"], "image": p["image"], "qty": cnt, "subtotal": subtotal})
            total += subtotal
    base = get_mobile_base()
    qty_total, _ = get_cart_summary()
    navbar = get_navbar(qty_total, total)
    lang_switch = f'<a href="/switch_lang" class="lang-switcher">&#127760; {"EN" if get_setting("lang") == "ar" else "AR"}</a>'
    if not items:
        return base + navbar + lang_switch + f'<div class="empty-state"><div class="empty-state-icon">&#128722;</div><div class="empty-state-text">{t("empty_cart")}</div><div class="empty-state-sub">{t("browse")}</div><a href="/" class="checkout-btn" style="display:inline-block;width:auto;padding:14px 36px;margin-top:8px;">{t("browse")}</a></div><script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script></body></html>'
    items_html = ""
    for i in items:
        img_src = image_url(i["image"]) if i["image"] else ""
        img = f'<img src="{img_src}" alt="" loading="lazy">' if i["image"] else '<div class="no-img-sm"><span>&#128247;</span></div>'
        items_html += f'<div class="cart-item">{img}<div class="item-details"><div class="item-name">{i["name"]}</div><div class="item-price">{int(i["price"])} {t("each")}</div><div class="qty-row"><form method="post" action="/cart/remove/{i["id"]}"><button>-</button></form><span class="qty-num">{i["qty"]}</span><form method="post" action="/cart/add_one/{i["id"]}"><button>+</button></form><span class="item-subtotal">{int(i["subtotal"])}</span><form method="post" action="/cart/delete/{i["id"]}" style="margin-left:auto"><button class="delete-btn">&#128465;</button></form></div></div></div>'
    return base + navbar + lang_switch + f'<a href="/" class="back-btn">&#8592; {t("continue_shopping")}</a><div class="slide-up"><p class="section-title">&#128722; {t("cart")}</p><div style="margin-top:8px;">{items_html}</div></div><div class="cart-footer"><div class="total-row"><span class="total-label">{t("total")}</span><span class="total-val">{int(total)}</span></div><a href="/checkout" class="checkout-btn">{t("checkout")} &rarr;</a></div><script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script></body></html>'

@app.route("/cart/remove/<int:prod_id>", methods=["POST"])
def cart_remove(prod_id):
    cart = session.get("cart", [])
    if prod_id in cart:
        cart.remove(prod_id)
    session["cart"] = cart
    return redirect(request.args.get("next", "/cart"))

@app.route("/cart/add_one/<int:prod_id>", methods=["POST"])
def cart_add_one(prod_id):
    if q("SELECT id FROM products WHERE id=%s", (prod_id,), one=True):
        cart = session.get("cart", [])
        cart.append(prod_id)
        session["cart"] = cart
    return redirect(request.args.get("next", "/cart"))

@app.route("/cart/delete/<int:prod_id>", methods=["POST"])
def cart_delete(prod_id):
    session["cart"] = [i for i in session.get("cart", []) if i != prod_id]
    return redirect("/cart")

# ============================================================
# CUSTOMER AUTH (EMAIL + OTP)
# ============================================================
@app.route("/customer/login", methods=["GET", "POST"])
def customer_login():
    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        if not email:
            error = t("error")
        else:
            customer = q("SELECT * FROM customers WHERE email=%s", (email,), one=True)
            if customer:
                otp = generate_otp()
                store_otp(email, otp)
                if send_otp_email(email, otp):
                    session["login_email"] = email
                    return redirect("/customer/verify_otp?action=login")
                else:
                    error = "فشل إرسال البريد الإلكتروني، تأكد من الإعدادات."
            else:
                error = "البريد الإلكتروني غير مسجل، يرجى إنشاء حساب."
    base = get_mobile_base()
    return base + f'''
    <div style="min-height:100vh;padding:24px;display:flex;flex-direction:column;justify-content:center;align-items:center;">
        <div style="width:100%;max-width:380px;">
            <div style="text-align:center;margin-bottom:32px;">
                <div style="font-size:48px;margin-bottom:12px;">&#128100;</div>
                <h2 style="font-size:22px;font-weight:800;color:var(--primary);">{t("customer_login")}</h2>
            </div>
            {"<div class='alert alert-danger' style='border-radius:12px;margin-bottom:16px;'>" + error + "</div>" if error else ""}
            <form method="post" style="display:flex;flex-direction:column;gap:16px;">
                <div>
                    <label style="font-size:13px;font-weight:700;color:var(--text-muted);display:block;margin-bottom:6px;">{t("enter_email")} *</label>
                    <input type="email" name="email" required style="width:100%;padding:14px 16px;border:2px solid var(--border);border-radius:var(--radius-sm);font-size:15px;outline:none;transition:border-color 0.2s;" placeholder="{t("enter_email")}">
                </div>
                <button type="submit" style="width:100%;background:var(--primary);color:#fff;border:none;border-radius:50px;padding:16px;font-size:16px;font-weight:700;cursor:pointer;transition:all 0.2s;box-shadow:var(--shadow);">{t("login")}</button>
            </form>
            <div style="text-align:center;margin-top:20px;font-size:14px;color:var(--text-muted);">
                {t("no_account")} <a href="/customer/register" style="color:var(--accent);font-weight:700;text-decoration:none;">{t("create_one")}</a>
            </div>
            <div style="text-align:center;margin-top:12px;">
                <a href="/" style="color:var(--text-muted);font-size:13px;text-decoration:none;">{t("back_to_store")}</a>
            </div>
        </div>
    </div>
    </body></html>'''

@app.route("/customer/register", methods=["GET", "POST"])
def customer_register():
    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip()
        address = request.form.get("address", "").strip()
        if not email or not name:
            error = "البريد والاسم مطلوبان"
        else:
            existing = q("SELECT id FROM customers WHERE email=%s", (email,), one=True)
            if existing:
                error = "البريد الإلكتروني مسجل بالفعل"
            else:
                # Save temporary data and send OTP
                session["reg_data"] = {"email": email, "name": name, "phone": phone, "address": address}
                otp = generate_otp()
                store_otp(email, otp)
                if send_otp_email(email, otp):
                    session["login_email"] = email
                    return redirect("/customer/verify_otp?action=register")
                else:
                    error = "فشل إرسال البريد الإلكتروني، تأكد من الإعدادات."
    base = get_mobile_base()
    return base + f'''
    <div style="min-height:100vh;padding:24px;display:flex;flex-direction:column;justify-content:center;align-items:center;">
        <div style="width:100%;max-width:380px;">
            <div style="text-align:center;margin-bottom:32px;">
                <div style="font-size:48px;margin-bottom:12px;">&#128221;</div>
                <h2 style="font-size:22px;font-weight:800;color:var(--primary);">{t("customer_register")}</h2>
            </div>
            {"<div class='alert alert-danger' style='border-radius:12px;margin-bottom:16px;'>" + error + "</div>" if error else ""}
            <form method="post" style="display:flex;flex-direction:column;gap:16px;">
                <div>
                    <label style="font-size:13px;font-weight:700;color:var(--text-muted);display:block;margin-bottom:6px;">{t("enter_email")} *</label>
                    <input type="email" name="email" required style="width:100%;padding:14px 16px;border:2px solid var(--border);border-radius:var(--radius-sm);font-size:15px;outline:none;" placeholder="{t("enter_email")}">
                </div>
                <div>
                    <label style="font-size:13px;font-weight:700;color:var(--text-muted);display:block;margin-bottom:6px;">{t("name")} *</label>
                    <input type="text" name="name" required style="width:100%;padding:14px 16px;border:2px solid var(--border);border-radius:var(--radius-sm);font-size:15px;outline:none;" placeholder="{t("name")}">
                </div>
                <div>
                    <label style="font-size:13px;font-weight:700;color:var(--text-muted);display:block;margin-bottom:6px;">{t("phone")} (اختياري)</label>
                    <input type="tel" name="phone" style="width:100%;padding:14px 16px;border:2px solid var(--border);border-radius:var(--radius-sm);font-size:15px;outline:none;" placeholder="{t("phone")}">
                </div>
                <div>
                    <label style="font-size:13px;font-weight:700;color:var(--text-muted);display:block;margin-bottom:6px;">{t("address")} (اختياري)</label>
                    <input type="text" name="address" style="width:100%;padding:14px 16px;border:2px solid var(--border);border-radius:var(--radius-sm);font-size:15px;outline:none;" placeholder="{t("address")}">
                </div>
                <button type="submit" style="width:100%;background:var(--primary);color:#fff;border:none;border-radius:50px;padding:16px;font-size:16px;font-weight:700;cursor:pointer;transition:all 0.2s;box-shadow:var(--shadow);">{t("register")}</button>
            </form>
            <div style="text-align:center;margin-top:20px;font-size:14px;color:var(--text-muted);">
                {t("have_account")} <a href="/customer/login" style="color:var(--accent);font-weight:700;text-decoration:none;">{t("login_here")}</a>
            </div>
            <div style="text-align:center;margin-top:12px;">
                <a href="/" style="color:var(--text-muted);font-size:13px;text-decoration:none;">{t("back_to_store")}</a>
            </div>
        </div>
    </div>
    </body></html>'''

@app.route("/customer/verify_otp", methods=["GET", "POST"])
def verify_otp():
    error = None
    action = request.args.get("action", "login")
    email = session.get("login_email")
    if not email:
        return redirect("/customer/login")
    if request.method == "POST":
        otp = request.form.get("otp", "").strip()
        if verify_otp(email, otp):
            # Complete registration or login
            if action == "register":
                reg_data = session.get("reg_data", {})
                if reg_data:
                    q("INSERT INTO customers(email, name, phone, address, created_at) VALUES(%s,%s,%s,%s,%s)",
                      (email, reg_data.get("name"), reg_data.get("phone"), reg_data.get("address"), datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                    session.pop("reg_data", None)
            customer = q("SELECT * FROM customers WHERE email=%s", (email,), one=True)
            if customer:
                session["customer"] = dict(customer)
                session.pop("login_email", None)
                # Redirect to checkout if there is a pending checkout
                next_url = session.pop("checkout_redirect", "/")
                return redirect(next_url)
            else:
                error = "حدث خطأ، حاول مرة أخرى."
        else:
            error = t("otp_invalid")
    base = get_mobile_base()
    return base + f'''
    <div style="min-height:100vh;padding:24px;display:flex;flex-direction:column;justify-content:center;align-items:center;">
        <div style="width:100%;max-width:380px;">
            <div style="text-align:center;margin-bottom:32px;">
                <div style="font-size:48px;margin-bottom:12px;">&#128274;</div>
                <h2 style="font-size:22px;font-weight:800;color:var(--primary);">{t("verify_btn")}</h2>
                <p style="color:var(--text-muted);font-size:14px;margin-top:8px;">{t("otp_sent")}</p>
            </div>
            {"<div class='alert alert-danger' style='border-radius:12px;margin-bottom:16px;'>" + error + "</div>" if error else ""}
            <form method="post" style="display:flex;flex-direction:column;gap:16px;">
                <div>
                    <label style="font-size:13px;font-weight:700;color:var(--text-muted);display:block;margin-bottom:6px;">{t("enter_otp")} *</label>
                    <input type="text" name="otp" required style="width:100%;padding:14px 16px;border:2px solid var(--border);border-radius:var(--radius-sm);font-size:15px;outline:none;transition:border-color 0.2s;" placeholder="123456">
                </div>
                <button type="submit" style="width:100%;background:var(--primary);color:#fff;border:none;border-radius:50px;padding:16px;font-size:16px;font-weight:700;cursor:pointer;transition:all 0.2s;box-shadow:var(--shadow);">{t("verify_btn")}</button>
            </form>
            <div style="text-align:center;margin-top:16px;">
                <a href="/customer/resend_otp?action={action}" style="color:var(--accent);font-weight:600;text-decoration:none;font-size:14px;">{t("resend_otp")}</a>
            </div>
            <div style="text-align:center;margin-top:12px;">
                <a href="/" style="color:var(--text-muted);font-size:13px;text-decoration:none;">{t("back_to_store")}</a>
            </div>
        </div>
    </div>
    </body></html>'''

@app.route("/customer/resend_otp")
def resend_otp():
    email = session.get("login_email")
    action = request.args.get("action", "login")
    if email:
        otp = generate_otp()
        store_otp(email, otp)
        send_otp_email(email, otp)
    return redirect(f"/customer/verify_otp?action={action}")

@app.route("/customer/logout")
def customer_logout():
    session.pop("customer", None)
    return redirect("/")

@app.route("/customer/orders")
def customer_orders():
    customer = session.get("customer")
    if not customer:
        return redirect("/customer/login")
    orders = q("SELECT * FROM orders WHERE email=%s ORDER BY id DESC", (customer.get("email"),))
    base = get_mobile_base()
    qty, total = get_cart_summary()
    navbar = get_navbar(qty, total)
    lang_switch = f'<a href="/switch_lang" class="lang-switcher">&#127760; {"EN" if get_setting("lang") == "ar" else "AR"}</a>'

    orders_html = ""
    if orders:
        for o in orders:
            lat = o.get("latitude") or ""
            lon = o.get("longitude") or ""
            map_link = f'<a href="https://www.google.com/maps?q={lat},{lon}" target="_blank" style="color:var(--accent);font-size:12px;font-weight:600;text-decoration:none;">&#128205; Map</a>' if lat and lon else ""
            orders_html += f'''
            <div class="cart-item" style="flex-direction:column;align-items:flex-start;gap:8px;">
                <div style="display:flex;justify-content:space-between;width:100%;align-items:center;">
                    <span style="font-size:14px;font-weight:800;color:var(--primary);">#{o["id"]}</span>
                    <span style="font-size:12px;color:var(--text-muted);">{o["created_at"]}</span>
                </div>
                <div style="font-size:13px;color:var(--text);white-space:pre-line;width:100%;line-height:1.6;">{o["items"]}</div>
                <div style="display:flex;justify-content:space-between;width:100%;align-items:center;margin-top:4px;">
                    <span style="font-size:16px;font-weight:800;color:var(--accent);">{int(o["total"])}</span>
                    {map_link}
                </div>
            </div>'''
    else:
        orders_html = f'<div class="empty-state"><div class="empty-state-icon">&#128203;</div><div class="empty-state-text">{t("no_orders")}</div></div>'

    return base + navbar + lang_switch + f'<a href="/" class="back-btn">&#8592; {t("back_to_store")}</a><div class="slide-up"><p class="section-title">&#128203; {t("my_orders")}</p>{orders_html}</div><script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script></body></html>'

# ============================================================
# CHECKOUT (requires login)
# ============================================================
@app.route("/checkout", methods=["GET", "POST"])
def checkout():
    # Require login
    customer = session.get("customer")
    if not customer:
        session["checkout_redirect"] = request.url
        return redirect("/customer/login")
    s = get_all_settings()
    if request.method == "POST":
        phone   = request.form.get("phone", "").strip()
        address = request.form.get("address", "").strip()
        details = request.form.get("details", "").strip()
        lat     = request.form.get("lat", "").strip()
        lon     = request.form.get("lon", "").strip()
        if not phone or not address:
            return redirect("/checkout")
        ids = session.get("cart", [])
        counts = {}
        for i in ids:
            counts[i] = counts.get(i, 0) + 1
        lines = []
        total = 0.0
        for prod_id, cnt in counts.items():
            p = q("SELECT * FROM products WHERE id=%s", (prod_id,), one=True)
            if p:
                subtotal = float(p["price"]) * cnt
                total += subtotal
                cat = q("SELECT name FROM categories WHERE id=%s", (p["category_id"],), one=True)
                cat_name = cat["name"] if cat else "-"
                lines.append(f"{p['name']} ({cat_name}) x {cnt} = {subtotal:.0f}")
        q("INSERT INTO orders(created_at, phone, address, details, items, total, latitude, longitude, email) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
          (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), phone, address, details,
           "\n".join(lines), total, lat or None, lon or None, customer.get("email")))
        session["cart"] = []
        return redirect("/order_confirmed")

    if not session.get("cart"):
        return redirect("/cart")

    base = get_mobile_base()
    default_phone = customer.get("phone", "") if customer else ""
    default_address = customer.get("address", "") if customer else ""

    geo_script = """
<script>
(function(){
  var latField = document.getElementById('lat');
  var lonField = document.getElementById('lon');
  var geoStatus = document.getElementById('geo-status');
  function setStatus(msg, color){ if(geoStatus){ geoStatus.textContent = msg; geoStatus.style.color = color; } }
  if(navigator.geolocation){
    setStatus('""" + t("detecting") + """', '#888');
    navigator.geolocation.getCurrentPosition(
      function(pos){ latField.value = pos.coords.latitude.toFixed(7); lonField.value = pos.coords.longitude.toFixed(7); setStatus('""" + t("detected") + """ \u2713', '#10b981'); },
      function(err){ setStatus('""" + t("not_available") + """', '#ef4444'); },
      { enableHighAccuracy: true, timeout: 8000, maximumAge: 0 }
    );
  } else { setStatus('""" + t("not_supported") + """', '#ef4444'); }
})();
</script>
"""

    geo_ui = f"""
<div style="display:flex;align-items:center;gap:10px;background:#f8fafc;border:2px solid var(--border);border-radius:var(--radius-sm);padding:12px 16px;margin-top:6px;">
  <span style="font-size:22px;">&#128205;</span>
  <span id="geo-status" style="font-size:13px;color:var(--text-muted);font-weight:600;">{t("detecting")}</span>
</div>
<input type="hidden" name="lat" id="lat" value="">
<input type="hidden" name="lon" id="lon" value="">
"""

    return base + f"""
<div style="min-height:100vh;padding:24px;max-width:480px;margin:0 auto;">
  <a href="/cart" class="back-btn" style="margin:0 0 24px;">&#8592; {s.get('checkout_back_btn',t('back'))}</a>
  <h2 style="font-size:22px;font-weight:800;margin-bottom:24px;color:var(--primary);">{s.get('checkout_title',t('checkout'))}</h2>
  <form method="post" style="display:flex;flex-direction:column;gap:16px;">
    <div>
      <label style="font-size:13px;font-weight:700;color:var(--text-muted);display:block;margin-bottom:6px;">{s.get('checkout_phone_label',t('phone'))} *</label>
      <input type="tel" name="phone" required value="{default_phone}" style="width:100%;padding:14px 16px;border:2px solid var(--border);border-radius:var(--radius-sm);font-size:15px;outline:none;transition:border-color 0.2s;" placeholder="{s.get('checkout_phone_placeholder','')}">
    </div>
    <div>
      <label style="font-size:13px;font-weight:700;color:var(--text-muted);display:block;margin-bottom:6px;">{s.get('checkout_address_label',t('address'))} *</label>
      <input type="text" name="address" required value="{default_address}" style="width:100%;padding:14px 16px;border:2px solid var(--border);border-radius:var(--radius-sm);font-size:15px;outline:none;transition:border-color 0.2s;" placeholder="{s.get('checkout_address_placeholder','')}">
    </div>
    <div>
      <label style="font-size:13px;font-weight:700;color:var(--text-muted);display:block;margin-bottom:6px;">{s.get('checkout_notes_label',t('notes'))}</label>
      <textarea name="details" rows="3" style="width:100%;padding:14px 16px;border:2px solid var(--border);border-radius:var(--radius-sm);font-size:15px;outline:none;resize:none;transition:border-color 0.2s;" placeholder="{s.get('checkout_notes_placeholder','')}"></textarea>
    </div>
    <div>
      <label style="font-size:13px;font-weight:700;color:var(--text-muted);display:block;margin-bottom:6px;">&#128205; {t('location')}</label>
      {geo_ui}
    </div>
    <button type="submit" style="width:100%;background:var(--primary);color:#fff;border:none;border-radius:50px;padding:16px;font-size:17px;font-weight:700;margin-top:6px;cursor:pointer;transition:all 0.2s;box-shadow:var(--shadow);">{s.get('checkout_confirm_btn',t('confirm'))}</button>
  </form>
</div>
{geo_script}
</body></html>"""

@app.route("/order_confirmed")
def order_confirmed():
    s = get_all_settings()
    title     = s.get('order_confirmed_title',    t('order_confirmed'))
    msg       = s.get('order_confirmed_msg',      t('thank_you'))
    redir_msg = s.get('order_confirmed_redirect', t('redirecting'))
    back_btn  = s.get('order_confirmed_back_btn', t('back_to_store'))
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"><meta http-equiv="refresh" content="4;url=/"><style>body{{font-family:Arial,sans-serif;background:#f8fafc;}}</style></head><body><div style="min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px;"><div style="text-align:center;"><div style="font-size:70px;">&#9989;</div><h2 style="font-size:22px;font-weight:700;margin-top:16px;">{title}</h2><p style="color:#555;font-size:15px;">{msg}</p><p style="color:#999;font-size:13px;margin-top:6px;">{redir_msg}</p><a href="/" style="display:inline-block;margin-top:20px;background:var(--primary);color:#fff;border-radius:50px;padding:13px 32px;font-size:15px;font-weight:600;text-decoration:none;">{back_btn}</a></div></div></body></html>"""

# ============================================================
# ADMIN LOGIN - ENCRYPTED
# ============================================================
@app.route("/admin", methods=["GET", "POST"])
def admin():
    error = None
    if request.method == "POST":
        u = request.form.get("u", "").strip()
        p = request.form.get("p", "").strip()
        if u == ADMIN_USER and verify_admin_password(p):
            session["admin"] = True
            return redirect("/dashboard")
        error = "Invalid username or password."
    return render_template_string("""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Admin Login</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    body { font-family: Arial, sans-serif; background: #f0f2f5; min-height: 100vh; display: flex; align-items: center; justify-content: center; }
    .login-card { background: #fff; border-radius: 18px; box-shadow: 0 4px 24px rgba(0,0,0,0.10); padding: 40px 32px; width: 100%; max-width: 380px; }
    .login-card h3 { font-size: 22px; font-weight: 700; margin-bottom: 24px; text-align: center; }
    .form-control { border-radius: 10px; padding: 12px 14px; font-size: 15px; border: 1.5px solid #e0e0e0; }
    .form-control:focus { border-color: #111; box-shadow: none; }
    .btn-login { background: #111; color: #fff; border: none; border-radius: 50px; padding: 13px; font-size: 16px; font-weight: 700; width: 100%; margin-top: 8px; }
    .btn-login:hover { background: #333; color: #fff; }
  </style>
</head>
<body>
  <div class="login-card">
    <h3>&#128274; Admin Login</h3>
    {% if error %}<div class="alert alert-danger py-2">{{ error }}</div>{% endif %}
    <form method="post">
      <input name="u" class="form-control mb-3" placeholder="Username" required autocomplete="username">
      <input name="p" type="password" class="form-control mb-3" placeholder="Password" required autocomplete="current-password">
      <button class="btn-login">Login</button>
    </form>
  </div>
</body>
</html>""", error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/admin")

# ============================================================
# API: Reorder (AJAX)
# ============================================================
@app.route("/api/reorder_cats", methods=["POST"])
def api_reorder_cats():
    if not require_admin():
        return {"ok": False}, 403
    data = request.get_json()
    for item in data.get("order", []):
        q("UPDATE categories SET sort_order=%s WHERE id=%s", (item["order"], item["id"]))
    return {"ok": True}

@app.route("/api/reorder_prods", methods=["POST"])
def api_reorder_prods():
    if not require_admin():
        return {"ok": False}, 403
    data = request.get_json()
    for item in data.get("order", []):
        q("UPDATE products SET sort_order=%s WHERE id=%s", (item["order"], item["id"]))
    return {"ok": True}

@app.route("/api/set_size", methods=["POST"])
def api_set_size():
    if not require_admin():
        return {"ok": False}, 403
    data = request.get_json()
    kind = data.get("kind")
    id_  = data.get("id")
    size = data.get("size")
    if size not in ("small", "medium", "large"):
        return {"ok": False, "error": "invalid size"}, 400
    if kind == "cat":
        q("UPDATE categories SET card_size=%s WHERE id=%s", (size, id_))
    elif kind == "prod":
        q("UPDATE products SET card_size=%s WHERE id=%s", (size, id_))
    else:
        return {"ok": False}, 400
    return {"ok": True}

# ============================================================
# SETTINGS PANEL
# ============================================================
SETTINGS_FIELDS = [
    {
        "section": "المتجر العام",
        "fields": [
            {"key": "store_name",     "label": "اسم المتجر",    "ref": "store_name"},
            {"key": "cart_btn_label", "label": "نص زر السلة",   "ref": "cart_btn_label"},
            {"key": "lang",           "label": "اللغة (ar/en)", "ref": "lang"},
        ]
    },
    {
        "section": "صفحة الدفع (Checkout)",
        "fields": [
            {"key": "checkout_title",               "label": "عنوان الصفحة",               "ref": "checkout_title"},
            {"key": "checkout_back_btn",            "label": "زر الرجوع",                  "ref": "checkout_back_btn"},
            {"key": "checkout_phone_label",         "label": "تسمية حقل رقم الهاتف",      "ref": "checkout_phone_label"},
            {"key": "checkout_phone_placeholder",   "label": "نص توضيحي لحقل الهاتف",     "ref": "checkout_phone_placeholder"},
            {"key": "checkout_address_label",       "label": "تسمية حقل العنوان",         "ref": "checkout_address_label"},
            {"key": "checkout_address_placeholder", "label": "نص توضيحي لحقل العنوان",    "ref": "checkout_address_placeholder"},
            {"key": "checkout_notes_label",         "label": "تسمية حقل الملاحظات",       "ref": "checkout_notes_label"},
            {"key": "checkout_notes_placeholder",   "label": "نص توضيحي لحقل الملاحظات", "ref": "checkout_notes_placeholder"},
            {"key": "checkout_confirm_btn",         "label": "نص زر تأكيد الطلب",         "ref": "checkout_confirm_btn"},
        ]
    },
    {
        "section": "صفحة تأكيد الطلب",
        "fields": [
            {"key": "order_confirmed_title",    "label": "عنوان الصفحة",           "ref": "order_confirmed_title"},
            {"key": "order_confirmed_msg",      "label": "رسالة التأكيد",          "ref": "order_confirmed_msg"},
            {"key": "order_confirmed_redirect", "label": "رسالة إعادة التوجيه",    "ref": "order_confirmed_redirect"},
            {"key": "order_confirmed_back_btn", "label": "نص زر العودة للمتجر",    "ref": "order_confirmed_back_btn"},
        ]
    },
    {
        "section": "OTP (البريد الإلكتروني)",
        "fields": [
            {"key": "otp_subject", "label": "موضوع البريد", "ref": "otp_subject"},
            {"key": "otp_body",    "label": "نص البريد (استخدم {code} للرمز)", "ref": "otp_body"},
        ]
    }
]

SETTINGS_TEMPLATE = """<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>إعدادات النصوص</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.0/font/bootstrap-icons.css">
  <style>
    :root { --accent: #4361ee; --accent-dark: #3451c7; --bg: #f0f2f5; --card: #fff; --border: #e8e8e8; --text: #1a1a2e; --muted: #6c757d; }
    * { box-sizing: border-box; }
    body { font-family: Arial, sans-serif; background: var(--bg); color: var(--text); margin: 0; padding: 0; }
    .topbar { background: #111; padding: 14px 20px; display: flex; align-items: center; justify-content: space-between; position: sticky; top: 0; z-index: 100; box-shadow: 0 2px 8px rgba(0,0,0,0.15); }
    .topbar h1 { color: #fff; font-size: 17px; font-weight: 700; margin: 0; }
    .topbar a { color: #fff; text-decoration: none; background: rgba(255,255,255,0.15); border-radius: 8px; padding: 7px 14px; font-size: 13px; font-weight: 600; }
    .page-wrap { max-width: 780px; margin: 0 auto; padding: 24px 16px 60px; }
    .section-card { background: var(--card); border-radius: 16px; box-shadow: 0 2px 12px rgba(0,0,0,0.07); margin-bottom: 22px; overflow: hidden; }
    .section-header { background: linear-gradient(135deg, #4361ee 0%, #3a0ca3 100%); color: #fff; padding: 14px 20px; font-size: 14px; font-weight: 700; }
    .field-row { display: grid; grid-template-columns: 1fr 2fr; border-bottom: 1px solid var(--border); align-items: stretch; }
    .field-row:last-child { border-bottom: none; }
    .field-meta { padding: 14px 18px; border-left: 1px solid var(--border); background: #fafbff; display: flex; flex-direction: column; justify-content: center; gap: 4px; }
    .field-meta .field-label { font-size: 13px; font-weight: 700; }
    .field-meta .field-ref { font-size: 11px; color: var(--muted); font-family: monospace; background: #f0f0f8; padding: 2px 6px; border-radius: 4px; display: inline-block; }
    .field-input { padding: 12px 16px; display: flex; align-items: center; }
    .field-input input { width: 100%; border: 1.5px solid var(--border); border-radius: 10px; padding: 10px 13px; font-size: 14px; outline: none; direction: auto; }
    .field-input input:focus { border-color: var(--accent); box-shadow: 0 0 0 3px rgba(67,97,238,0.12); }
    .save-bar { position: fixed; bottom: 0; left: 0; right: 0; background: #fff; border-top: 1px solid var(--border); padding: 14px 20px; display: flex; align-items: center; justify-content: space-between; z-index: 200; }
    .save-btn { background: var(--accent); color: #fff; border: none; border-radius: 50px; padding: 11px 30px; font-size: 15px; font-weight: 700; cursor: pointer; }
    @media (max-width: 600px) { .field-row { grid-template-columns: 1fr; } .field-meta { border-left: none; border-bottom: 1px solid var(--border); } }
  </style>
</head>
<body>
<div class="topbar">
  <h1>&#9881;&#65039; إعدادات النصوص</h1>
  <a href="/dashboard">&#8594; العودة للوحة</a>
</div>
<div class="page-wrap">
  {% if saved %}<div class="alert alert-success mb-3" style="border-radius:12px;">تم حفظ الإعدادات بنجاح!</div>{% endif %}
  <form method="post">
    {% for section in sections %}
    <div class="section-card">
      <div class="section-header">{{ section.section }}</div>
      {% for f in section.fields %}
      <div class="field-row">
        <div class="field-meta"><span class="field-label">{{ f.label }}</span><span class="field-ref">{{ f.ref }}</span></div>
        <div class="field-input"><input type="text" name="{{ f.key }}" value="{{ values.get(f.key, '') }}" placeholder="{{ f.label }}"></div>
      </div>
      {% endfor %}
    </div>
    {% endfor %}
    <div class="save-bar">
      <span style="font-size:13px;color:#888;">التغييرات تنعكس فوراً على المتجر</span>
      <button type="submit" class="save-btn">&#10003; حفظ الإعدادات</button>
    </div>
  </form>
</div>
</body></html>
"""

@app.route("/settings", methods=["GET", "POST"])
def settings_panel():
    if not require_admin():
        return redirect("/admin")
    saved = False
    if request.method == "POST":
        for section in SETTINGS_FIELDS:
            for f in section["fields"]:
                val = request.form.get(f["key"], "").strip()
                if val:
                    conn = db()
                    cur = conn.cursor()
                    cur.execute(
                        "INSERT INTO settings(key, value) VALUES(%s,%s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
                        (f["key"], val)
                    )
                    conn.commit()
                    conn.close()
        saved = True
    current_vals = get_all_settings()
    return render_template_string(SETTINGS_TEMPLATE, sections=SETTINGS_FIELDS, values=current_vals, saved=saved)

# ============================================================
# DASHBOARD
# ============================================================
DASHBOARD_TEMPLATE = """<!DOCTYPE html>
<html lang="en" dir="ltr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Admin Dashboard</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.0/font/bootstrap-icons.css">
  <script src="https://cdn.jsdelivr.net/npm/sortablejs@1.15.0/Sortable.min.js"></script>
  <style>
    :root { --sidebar-w: 220px; --accent: #111; --accent2: #4361ee; --success: #2ecc71; --danger: #e53935; --bg: #f0f2f5; --card-bg: #fff; --border: #e8e8e8; --text: #1a1a2e; --muted: #6c757d; }
    * { box-sizing: border-box; }
    body { font-family: Arial, sans-serif; background: var(--bg); color: var(--text); margin: 0; }
    .topnav { position: fixed; top: 0; left: 0; right: 0; height: 56px; background: var(--accent); display: flex; align-items: center; justify-content: space-between; padding: 0 16px; z-index: 1000; gap: 10px; }
    .topnav .brand { color: #fff; font-size: 17px; font-weight: 700; white-space: nowrap; }
    .topnav .topnav-actions { display: flex; align-items: center; gap: 8px; }
    .topnav .btn-topnav { background: rgba(255,255,255,0.12); color: #fff; border: none; border-radius: 8px; padding: 6px 12px; font-size: 13px; font-weight: 600; text-decoration: none; cursor: pointer; white-space: nowrap; }
    .topnav .btn-topnav:hover { background: rgba(255,255,255,0.22); color: #fff; }
    .topnav .btn-topnav.danger { background: rgba(229,57,53,0.7); }
    .hamburger { display: none; background: none; border: none; color: #fff; font-size: 22px; cursor: pointer; padding: 4px; }
    .sidebar { position: fixed; top: 56px; left: 0; width: var(--sidebar-w); height: calc(100vh - 56px); background: #fff; border-right: 1px solid var(--border); overflow-y: auto; z-index: 900; transition: transform 0.3s; }
    .sidebar .nav-item { display: block; padding: 12px 18px; font-size: 14px; font-weight: 600; color: var(--text); text-decoration: none; border-left: 3px solid transparent; cursor: pointer; background: none; border-top: none; border-bottom: none; border-right: none; width: 100%; text-align: left; }
    .sidebar .nav-item:hover, .sidebar .nav-item.active { background: #f0f4ff; color: var(--accent2); border-left-color: var(--accent2); }
    .sidebar .nav-section { padding: 10px 18px 4px; font-size: 11px; font-weight: 700; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; }
    .main-content { margin-top: 56px; margin-left: var(--sidebar-w); padding: 20px; min-height: calc(100vh - 56px); }
    .stat-card { background: var(--card-bg); border-radius: 14px; padding: 18px 20px; box-shadow: 0 2px 10px rgba(0,0,0,0.06); }
    .stat-card .stat-num { font-size: 28px; font-weight: 700; }
    .stat-card .stat-label { font-size: 13px; color: var(--muted); margin-top: 2px; }
    .panel { background: var(--card-bg); border-radius: 14px; box-shadow: 0 2px 10px rgba(0,0,0,0.06); overflow: hidden; margin-bottom: 20px; }
    .panel-header { padding: 14px 20px; border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; }
    .panel-header h5 { margin: 0; font-size: 15px; font-weight: 700; }
    .panel-body { padding: 16px 20px; }
    .drag-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 12px; }
    .drag-card { background: #f8f9fc; border: 1.5px solid var(--border); border-radius: 12px; overflow: hidden; cursor: grab; transition: box-shadow 0.2s; user-select: none; position: relative; }
    .drag-card:active { cursor: grabbing; }
    .drag-card.sortable-chosen { box-shadow: 0 8px 24px rgba(67,97,238,0.18); border-color: var(--accent2); }
    .drag-card.sortable-ghost { opacity: 0.4; }
    .drag-card img { width: 100%; height: 90px; object-fit: cover; display: block; }
    .drag-card .no-img-admin { width: 100%; height: 90px; background: #e8eaf0; display: flex; align-items: center; justify-content: center; color: #aaa; font-size: 12px; }
    .drag-card .dc-body { padding: 8px 10px 10px; }
    .drag-card .dc-name { font-size: 13px; font-weight: 700; margin-bottom: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .drag-card .dc-price { font-size: 12px; color: var(--muted); margin-bottom: 6px; }
    .drag-card .dc-actions { display: flex; gap: 4px; flex-wrap: wrap; }
    .drag-card .dc-actions .btn { padding: 3px 8px; font-size: 11px; border-radius: 6px; }
    .drag-card .drag-handle { position: absolute; top: 4px; right: 4px; background: rgba(0,0,0,0.45); color: #fff; border-radius: 6px; padding: 2px 5px; font-size: 12px; cursor: grab; line-height: 1; }
    .size-btns { display: flex; gap: 3px; margin-top: 4px; }
    .size-btns button { flex: 1; padding: 2px 0; font-size: 10px; border-radius: 5px; border: 1.5px solid #ddd; background: #fff; cursor: pointer; font-weight: 600; }
    .size-btns button.active { background: var(--accent2); color: #fff; border-color: var(--accent2); }
    .orders-table { width: 100%; border-collapse: collapse; font-size: 13px; }
    .orders-table th { background: var(--accent); color: #fff; padding: 10px 12px; font-weight: 600; text-align: left; }
    .orders-table td { padding: 10px 12px; border-bottom: 1px solid var(--border); vertical-align: top; }
    .orders-table tr:hover td { background: #f8f9fc; }
    .breadcrumb-bar { display: flex; align-items: center; gap: 6px; font-size: 13px; color: var(--muted); margin-bottom: 14px; flex-wrap: wrap; }
    .breadcrumb-bar a { color: var(--accent2); text-decoration: none; font-weight: 600; }
    .modal-content { border-radius: 16px; }
    .form-control, .form-select { border-radius: 10px; border: 1.5px solid #e0e0e0; padding: 10px 13px; font-size: 14px; }
    .form-control:focus, .form-select:focus { border-color: var(--accent2); box-shadow: 0 0 0 3px rgba(67,97,238,0.12); }
    .btn-primary { background: var(--accent2); border-color: var(--accent2); }
    .btn-primary:hover { background: #3451c7; border-color: #3451c7; }
    .tab-custom { display: flex; gap: 4px; background: #f0f2f5; border-radius: 12px; padding: 4px; margin-bottom: 18px; }
    .tab-custom button { flex: 1; padding: 9px 14px; border: none; border-radius: 9px; font-size: 13px; font-weight: 600; background: none; color: var(--muted); cursor: pointer; }
    .tab-custom button.active { background: #fff; color: var(--text); box-shadow: 0 2px 6px rgba(0,0,0,0.08); }
    .map-link { display:inline-flex;align-items:center;gap:4px;background:#e8f5e9;color:#2e7d32;border-radius:6px;padding:3px 8px;font-size:11px;font-weight:700;text-decoration:none; }
    @media (max-width: 768px) { .sidebar { transform: translateX(-100%); } .sidebar.open { transform: translateX(0); } .main-content { margin-left: 0; padding: 14px; } .hamburger { display: block; } .drag-grid { grid-template-columns: repeat(auto-fill, minmax(130px, 1fr)); } .orders-table { font-size: 12px; } }
    @media (max-width: 480px) { .drag-grid { grid-template-columns: repeat(2, 1fr); } }
    .sidebar-overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.35); z-index: 850; }
    .sidebar-overlay.show { display: block; }
    #save-toast { position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%); background: #222; color: #fff; padding: 10px 22px; border-radius: 30px; font-size: 13px; font-weight: 600; opacity: 0; transition: opacity 0.3s; z-index: 9999; pointer-events: none; }
    #save-toast.show { opacity: 1; }
  </style>
</head>
<body>
<nav class="topnav">
  <div style="display:flex;align-items:center;gap:10px;">
    <button class="hamburger" onclick="toggleSidebar()"><i class="bi bi-list"></i></button>
    <span class="brand">&#128722; Admin Dashboard</span>
  </div>
  <div class="topnav-actions">
    <a href="/settings" class="btn-topnav"><i class="bi bi-translate"></i> <span>Texts</span></a>
    <a href="/design" class="btn-topnav"><i class="bi bi-palette"></i> <span>Design</span></a>
    <a href="/admin/database" class="btn-topnav"><i class="bi bi-database"></i> <span>Database</span></a>
    <a href="/" class="btn-topnav" target="_blank"><i class="bi bi-shop"></i> <span>Store</span></a>
    <a href="/logout" class="btn-topnav danger"><i class="bi bi-box-arrow-right"></i> <span>Logout</span></a>
  </div>
</nav>
<div class="sidebar-overlay" id="sidebar-overlay" onclick="toggleSidebar()"></div>
<div class="sidebar" id="sidebar">
  <div class="nav-section">Current Section</div>
  {% if selected_cat %}
    <a href="/dashboard" class="nav-item"><i class="bi bi-house"></i> Home</a>
    {% if selected_cat.parent_id %}
      <a href="/dashboard?cat={{ selected_cat.parent_id }}" class="nav-item"><i class="bi bi-arrow-left"></i> Go Up</a>
    {% else %}
      <a href="/dashboard" class="nav-item"><i class="bi bi-arrow-left"></i> Back to Categories</a>
    {% endif %}
    <div class="nav-section">Inside: {{ selected_cat.name }}</div>
    <div class="nav-item active"><i class="bi bi-grid"></i> Sub-categories & Products</div>
  {% else %}
    <div class="nav-item active"><i class="bi bi-grid"></i> Main Categories</div>
  {% endif %}
  <div class="nav-section">Orders</div>
  <a href="/dashboard" class="nav-item" onclick="showTab('orders'); return false;"><i class="bi bi-receipt"></i> Orders ({{ orders|length }})</a>
  <div class="nav-section">Settings</div>
  <a href="/settings" class="nav-item"><i class="bi bi-translate"></i> Texts & Labels</a>
</div>
<div class="main-content">
  <div class="row g-3 mb-4">
    <div class="col-6 col-md-3"><div class="stat-card"><div class="stat-num">{{ orders|length }}</div><div class="stat-label">&#128230; Orders</div></div></div>
    <div class="col-6 col-md-3"><div class="stat-card"><div class="stat-num">{{ all_cats|length }}</div><div class="stat-label">&#128193; Categories</div></div></div>
    <div class="col-6 col-md-3"><div class="stat-card"><div class="stat-num">{{ total_prods }}</div><div class="stat-label">&#128717; Products</div></div></div>
    <div class="col-6 col-md-3"><div class="stat-card"><div class="stat-num">{{ "%.0f"|format(total_revenue) }}</div><div class="stat-label">&#128176; Total Revenue</div></div></div>
  </div>
  <div class="tab-custom">
    <button id="tab-btn-cats" onclick="showTab('cats')" class="{{ 'active' if active_tab == 'cats' else '' }}"><i class="bi bi-grid"></i> Categories & Products</button>
    <button id="tab-btn-orders" onclick="showTab('orders')" class="{{ 'active' if active_tab == 'orders' else '' }}"><i class="bi bi-receipt"></i> Orders</button>
  </div>

  <!-- TAB CATS -->
  <div id="tab-cats" style="display:{{ 'block' if active_tab == 'cats' else 'none' }}">
    {% if selected_cat %}
    <div class="breadcrumb-bar">
      <a href="/dashboard">Home</a>
      {% for bc in breadcrumb %}<span style="color:#ccc">&rsaquo;</span>{% if not loop.last %}<a href="/dashboard?cat={{ bc.id }}">{{ bc.name }}</a>{% else %}<span>{{ bc.name }}</span>{% endif %}{% endfor %}
    </div>
    {% endif %}
    <div class="panel mb-3">
      <div class="panel-header">
        <h5><i class="bi bi-folder"></i> {{ 'Sub-categories in "' + selected_cat.name + '"' if selected_cat else 'Main Categories' }}</h5>
        <div class="d-flex gap-2 flex-wrap">
          <small class="text-muted d-flex align-items-center"><i class="bi bi-arrows-move me-1"></i> Drag to reorder</small>
          <button class="btn btn-primary btn-sm" data-bs-toggle="modal" data-bs-target="#addCatModal"><i class="bi bi-plus-lg"></i> {{ 'Add Sub-category' if selected_cat else 'Add Category' }}</button>
        </div>
      </div>
      <div class="panel-body">
        {% if cats %}
        <div class="drag-grid" id="cats-sortable">
          {% for c in cats %}
          <div class="drag-card" data-id="{{ c.id }}" data-kind="cat">
            <span class="drag-handle" title="Drag">&#8999;</span>
            {% if c.image %}<img src="{{ c.image_url }}" alt="">{% else %}<div class="no-img-admin">No Image</div>{% endif %}
            <div class="dc-body">
              <div class="dc-name" title="{{ c.name }}">{{ c.name }}</div>
              <div class="size-btns">
                <button onclick="setSize('cat',{{ c.id }},'small',this)" class="{{ 'active' if c.card_size == 'small' else '' }}">S</button>
                <button onclick="setSize('cat',{{ c.id }},'medium',this)" class="{{ 'active' if (not c.card_size or c.card_size == 'medium') else '' }}">M</button>
                <button onclick="setSize('cat',{{ c.id }},'large',this)" class="{{ 'active' if c.card_size == 'large' else '' }}">L</button>
              </div>
              <div class="dc-actions mt-2">
                <a href="/dashboard?cat={{ c.id }}" class="btn btn-outline-secondary btn-sm"><i class="bi bi-folder2-open"></i></a>
                <button class="btn btn-outline-primary btn-sm" data-bs-toggle="modal" data-bs-target="#editCat{{ c.id }}"><i class="bi bi-pencil"></i></button>
                <form method="post" action="/delete_cat/{{ c.id }}" style="display:inline" onsubmit="return confirm('Delete?')"><button class="btn btn-outline-danger btn-sm"><i class="bi bi-trash"></i></button></form>
              </div>
            </div>
          </div>
          <div class="modal fade" id="editCat{{ c.id }}" tabindex="-1">
            <div class="modal-dialog"><div class="modal-content">
              <form method="post" action="/edit_cat/{{ c.id }}" enctype="multipart/form-data">
                <div class="modal-header"><h5 class="modal-title">Edit Category</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div>
                <div class="modal-body">
                  <label class="form-label fw-bold">Name</label><input name="name" class="form-control mb-3" value="{{ c.name }}" required>
                  <label class="form-label fw-bold">Change Image</label><input type="file" name="image" class="form-control" accept="image/*">
                </div>
                <div class="modal-footer"><button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button><button type="submit" class="btn btn-primary">Save</button></div>
              </form>
            </div></div>
          </div>
          {% endfor %}
        </div>
        {% else %}<p class="text-muted text-center py-3">No categories yet.</p>{% endif %}
      </div>
    </div>

    {% if selected_cat %}
    <div class="panel">
      <div class="panel-header">
        <h5><i class="bi bi-bag"></i> Products in "{{ selected_cat.name }}" ({{ prods|length }})</h5>
        <button class="btn btn-success btn-sm" data-bs-toggle="modal" data-bs-target="#addProdModal"><i class="bi bi-plus-lg"></i> Add Product</button>
      </div>
      <div class="panel-body">
        {% if prods %}
        <div class="drag-grid" id="prods-sortable">
          {% for p in prods %}
          <div class="drag-card" data-id="{{ p.id }}" data-kind="prod">
            <span class="drag-handle">&#8999;</span>
            {% if p.image %}<img src="{{ p.image_url }}" alt="">{% else %}<div class="no-img-admin">No Image</div>{% endif %}
            <div class="dc-body">
              <div class="dc-name" title="{{ p.name }}">{{ p.name }}</div>
              <div class="dc-price">{{ "%.0f"|format(p.price) }}</div>
              <div class="size-btns">
                <button onclick="setSize('prod',{{ p.id }},'small',this)" class="{{ 'active' if p.card_size == 'small' else '' }}">S</button>
                <button onclick="setSize('prod',{{ p.id }},'medium',this)" class="{{ 'active' if (not p.card_size or p.card_size == 'medium') else '' }}">M</button>
                <button onclick="setSize('prod',{{ p.id }},'large',this)" class="{{ 'active' if p.card_size == 'large' else '' }}">L</button>
              </div>
              <div class="dc-actions mt-2">
                <button class="btn btn-outline-primary btn-sm" data-bs-toggle="modal" data-bs-target="#editProd{{ p.id }}"><i class="bi bi-pencil"></i></button>
                <form method="post" action="/delete_prod/{{ p.id }}" style="display:inline" onsubmit="return confirm('Delete?')"><button class="btn btn-outline-danger btn-sm"><i class="bi bi-trash"></i></button></form>
              </div>
            </div>
          </div>
          <div class="modal fade" id="editProd{{ p.id }}" tabindex="-1">
            <div class="modal-dialog"><div class="modal-content">
              <form method="post" action="/edit_prod/{{ p.id }}" enctype="multipart/form-data">
                <div class="modal-header"><h5 class="modal-title">Edit Product</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div>
                <div class="modal-body">
                  <label class="form-label fw-bold">Name</label><input name="name" class="form-control mb-3" value="{{ p.name }}" required>
                  <label class="form-label fw-bold">Price</label><input name="price" type="number" step="0.01" min="0" class="form-control mb-3" value="{{ p.price }}" required>
                  <label class="form-label fw-bold">Category</label>
                  <select name="cat" class="form-select mb-3" required>{% for c in all_cats %}<option value="{{ c.id }}" {{ 'selected' if c.id == p.category_id else '' }}>{{ c.label }}</option>{% endfor %}</select>
                  <label class="form-label fw-bold">Change Image</label><input type="file" name="image" class="form-control" accept="image/*">
                </div>
                <div class="modal-footer"><button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button><button type="submit" class="btn btn-primary">Save</button></div>
              </form>
            </div></div>
          </div>
          {% endfor %}
        </div>
        {% else %}<p class="text-muted text-center py-3">No products in this category.</p>{% endif %}
      </div>
    </div>
    {% else %}
    <div class="panel"><div class="panel-body text-center py-4 text-muted"><i class="bi bi-arrow-up-circle" style="font-size:32px;"></i><p class="mt-2">Select a category above to view its products.</p></div></div>
    {% endif %}
  </div>

  <!-- TAB ORDERS -->
  <div id="tab-orders" style="display:{{ 'block' if active_tab == 'orders' else 'none' }}">
    <div class="panel">
      <div class="panel-header"><h5><i class="bi bi-receipt"></i> Orders ({{ orders|length }})</h5></div>
      <div class="panel-body p-0">
        {% if orders %}
        <div class="table-responsive">
          <table class="orders-table">
            <thead><tr><th>#</th><th>Date</th><th>Email</th><th>Phone</th><th>Address</th><th>Location</th><th>Items</th><th>Total</th><th>Actions</th></tr></thead>
            <tbody>
              {% for o in orders %}
              <tr>
                <td><strong>#{{ o.id }}</strong></td>
                <td style="white-space:nowrap;font-size:12px;">{{ o.created_at }}</td>
                <td>{{ o.email or '' }}</td>
                <td>{{ o.phone }}</td>
                <td>{{ o.address }}</td>
                <td>
                  {% if o.latitude and o.longitude %}
                    <a class="map-link" href="https://www.google.com/maps?q={{ o.latitude }},{{ o.longitude }}" target="_blank">&#128205; Map</a>
                  {% else %}<span style="color:#ccc;font-size:12px;">â€”</span>{% endif %}
                </td>
                <td style="white-space:pre-line;font-size:12px;max-width:180px;">{{ o.items }}</td>
                <td><strong>{{ "%.0f"|format(o.total) }}</strong></td>
                <td>
                  <div class="d-flex gap-1 flex-wrap">
                    <a href="/order/{{ o.id }}/download" class="btn btn-primary btn-sm" title="Download PNG"><i class="bi bi-image"></i></a>
                    <form method="post" action="/delete_order/{{ o.id }}" style="display:inline" onsubmit="return confirm('Delete?')"><button class="btn btn-danger btn-sm"><i class="bi bi-trash"></i></button></form>
                  </div>
                </td>
              </tr>
              {% endfor %}
            </tbody>
          </table>
        </div>
        {% else %}<div class="text-center py-5 text-muted"><i class="bi bi-inbox" style="font-size:40px;"></i><p class="mt-2">No orders yet.</p></div>{% endif %}
      </div>
    </div>
  </div>
</div>

<!-- ADD CAT MODAL -->
<div class="modal fade" id="addCatModal" tabindex="-1">
  <div class="modal-dialog"><div class="modal-content">
    <form method="post" action="/add_cat" enctype="multipart/form-data">
      <div class="modal-header"><h5 class="modal-title">{{ 'Add Sub-category' if selected_cat else 'Add Category' }}</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div>
      <div class="modal-body">
        <input type="hidden" name="parent" value="{{ selected_cat.id if selected_cat else '' }}">
        <label class="form-label fw-bold">Name</label><input name="name" class="form-control mb-3" placeholder="Category name" required>
        <label class="form-label fw-bold">Image (optional)</label><input type="file" name="image" class="form-control" accept="image/*">
      </div>
      <div class="modal-footer"><button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button><button type="submit" class="btn btn-primary">Add</button></div>
    </form>
  </div></div>
</div>

{% if selected_cat %}
<div class="modal fade" id="addProdModal" tabindex="-1">
  <div class="modal-dialog"><div class="modal-content">
    <form method="post" action="/add_prod" enctype="multipart/form-data">
      <div class="modal-header"><h5 class="modal-title">Add Product</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div>
      <div class="modal-body">
        <label class="form-label fw-bold">Name</label><input name="name" class="form-control mb-3" placeholder="Product name" required>
        <label class="form-label fw-bold">Price</label><input name="price" type="number" step="0.01" min="0" class="form-control mb-3" placeholder="Price" required>
        <input type="hidden" name="cat" value="{{ selected_cat.id }}">
        <label class="form-label fw-bold">Image (optional)</label><input type="file" name="image" class="form-control" accept="image/*">
      </div>
      <div class="modal-footer"><button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button><button type="submit" class="btn btn-success">Add</button></div>
    </form>
  </div></div>
</div>
{% endif %}

<div id="save-toast">&#10003; Saved</div>
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
<script>
function toggleSidebar() {
  document.getElementById('sidebar').classList.toggle('open');
  document.getElementById('sidebar-overlay').classList.toggle('show');
}
function showTab(name) {
  ['cats','orders'].forEach(t => {
    document.getElementById('tab-'+t).style.display = t===name?'block':'none';
    const btn=document.getElementById('tab-btn-'+t);
    if(btn) btn.className = t===name?'active':'';
  });
}
function showToast(msg){
  const t=document.getElementById('save-toast');
  t.textContent=msg||'Saved';
  t.classList.add('show');
  setTimeout(()=>t.classList.remove('show'),2000);
}
const catsSortable=document.getElementById('cats-sortable');
if(catsSortable){Sortable.create(catsSortable,{animation:200,handle:'.drag-handle',ghostClass:'sortable-ghost',chosenClass:'sortable-chosen',onEnd:function(){const order=[...catsSortable.querySelectorAll('.drag-card')].map((el,i)=>({id:parseInt(el.dataset.id),order:i}));fetch('/api/reorder_cats',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({order})}).then(()=>showToast('Reordered'));}});}
const prodsSortable=document.getElementById('prods-sortable');
if(prodsSortable){Sortable.create(prodsSortable,{animation:200,handle:'.drag-handle',ghostClass:'sortable-ghost',chosenClass:'sortable-chosen',onEnd:function(){const order=[...prodsSortable.querySelectorAll('.drag-card')].map((el,i)=>({id:parseInt(el.dataset.id),order:i}));fetch('/api/reorder_prods',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({order})}).then(()=>showToast('Reordered'));}});}
function setSize(kind,id,size,btn){const card=btn.closest('.drag-card');card.querySelectorAll('.size-btns button').forEach(b=>b.classList.remove('active'));btn.classList.add('active');fetch('/api/set_size',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind,id,size})}).then(()=>showToast('Size updated'));}
</script>
</body></html>
"""

@app.route("/dashboard")
def dashboard():
    if not require_admin():
        return redirect("/admin")
    orders = q("SELECT * FROM orders ORDER BY id DESC")
    selected_cat_id = request.args.get("cat", type=int)
    selected_cat = None
    prods = []
    breadcrumb = []
    if selected_cat_id:
        selected_cat = q("SELECT * FROM categories WHERE id=%s", (selected_cat_id,), one=True)
    if selected_cat:
        prods_raw = q("SELECT * FROM products WHERE category_id=%s ORDER BY sort_order ASC, id ASC", (selected_cat_id,))
        prods = []
        for p in prods_raw:
            pd = dict(p)
            pd["image_url"] = image_url(pd["image"]) if pd.get("image") else ""
            prods.append(pd)
        cats_raw = q("SELECT * FROM categories WHERE parent_id=%s ORDER BY sort_order ASC, id ASC", (selected_cat_id,))
        cats = []
        for c in cats_raw:
            cd = dict(c)
            cd["image_url"] = image_url(cd["image"]) if cd.get("image") else ""
            cats.append(cd)
        cur = selected_cat
        while cur:
            breadcrumb.insert(0, {"id": cur["id"], "name": cur["name"]})
            cur = q("SELECT * FROM categories WHERE id=%s", (cur["parent_id"],), one=True) if cur["parent_id"] else None
    else:
        cats_raw = q("SELECT * FROM categories WHERE parent_id IS NULL ORDER BY sort_order ASC, id ASC")
        cats = []
        for c in cats_raw:
            cd = dict(c)
            cd["image_url"] = image_url(cd["image"]) if cd.get("image") else ""
            cats.append(cd)

    all_cats_raw = q("SELECT * FROM categories")
    cat_map = {c["id"]: c for c in all_cats_raw}
    def cat_path(c):
        parts = [c["name"]]; parent = c["parent_id"]
        while parent:
            p = cat_map.get(parent)
            if not p: break
            parts.insert(0, p["name"]); parent = p["parent_id"]
        return " / ".join(parts)
    all_cats = [{"id": c["id"], "label": cat_path(c)} for c in all_cats_raw]
    total_prods   = len(q("SELECT id FROM products"))
    total_revenue = sum(float(o["total"]) for o in orders)
    active_tab = "orders" if request.args.get("tab") == "orders" else "cats"
    return render_template_string(DASHBOARD_TEMPLATE, cats=cats, all_cats=all_cats, orders=orders,
        selected_cat=selected_cat, prods=prods, breadcrumb=breadcrumb,
        total_prods=total_prods, total_revenue=total_revenue, active_tab=active_tab)

# ============================================================
# DESIGN PANEL
# ============================================================
@app.route("/design", methods=["GET", "POST"])
def design():
    if not require_admin(): return redirect("/admin")
    if request.method == "POST":
        bg = save(request.files.get("bg"))
        ov = save(request.files.get("overlay"))
        an = save(request.files.get("anim"))
        if bg or ov or an:
            d = q("SELECT * FROM design ORDER BY id DESC LIMIT 1", one=True)
            if d:
                bg = bg or d["background"]; ov = ov or d["overlay"]; an = an or d["animation"]
            q("INSERT INTO design(background, overlay, animation) VALUES(%s,%s,%s)", (bg, ov, an))
        return redirect("/design")
    d = q("SELECT * FROM design ORDER BY id DESC LIMIT 1", one=True)
    bg_url = image_url(d["background"]) if d and d["background"] else ""
    ov_url = image_url(d["overlay"])    if d and d["overlay"]    else ""
    an_url = image_url(d["animation"])  if d and d["animation"]  else ""
    return render_template_string("""<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Design</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
<style>body{font-family:Arial,sans-serif;background:#f0f2f5;}.card{border-radius:14px;border:none;box-shadow:0 2px 10px rgba(0,0,0,0.07);}</style>
</head><body><div class="container mt-4" style="max-width:600px">
<div class="d-flex justify-content-between align-items-center mb-3"><h4>&#127912; Design Panel</h4><a href="/dashboard" class="btn btn-secondary btn-sm">Back</a></div>
<div class="card p-4 mb-3"><form method="post" enctype="multipart/form-data">
<div class="mb-3"><label class="form-label fw-bold">Background Image</label><input type="file" name="bg" class="form-control" accept="image/*"></div>
<div class="mb-3"><label class="form-label fw-bold">Overlay Image</label><input type="file" name="overlay" class="form-control" accept="image/png,image/gif"></div>
<div class="mb-3"><label class="form-label fw-bold">Animation (GIF)</label><input type="file" name="anim" class="form-control" accept="image/gif,image/png"></div>
<button class="btn btn-primary w-100">Save Design</button></form></div>
{% if d %}
<div class="card p-4"><h5 class="mb-3">Current Design</h5><ul class="list-group list-group-flush">
<li class="list-group-item"><strong>Background:</strong> {% if d.background %}<a href="{{ bg_url }}" target="_blank">{{ d.background }}</a>{% else %}None{% endif %}</li>
<li class="list-group-item"><strong>Overlay:</strong> {% if d.overlay %}<a href="{{ ov_url }}" target="_blank">{{ d.overlay }}</a>{% else %}None{% endif %}</li>
<li class="list-group-item"><strong>Animation:</strong> {% if d.animation %}<a href="{{ an_url }}" target="_blank">{{ d.animation }}</a>{% else %}None{% endif %}</li>
</ul></div>{% endif %}
</div></body></html>""", d=d, bg_url=bg_url, ov_url=ov_url, an_url=an_url)

# ============================================================
# CRUD ROUTES
# ============================================================
@app.route("/edit_cat/<int:id>", methods=["POST"])
def edit_cat(id):
    if not require_admin(): return redirect("/admin")
    name = request.form.get("name","").strip()
    if not name: return redirect("/dashboard")
    cat = q("SELECT * FROM categories WHERE id=%s", (id,), one=True)
    img = save(request.files.get("image"))
    if img: q("UPDATE categories SET name=%s, image=%s WHERE id=%s", (name, img, id))
    else:   q("UPDATE categories SET name=%s WHERE id=%s", (name, id))
    parent_id = cat["parent_id"] if cat else None
    return redirect(f"/dashboard?cat={parent_id}" if parent_id else "/dashboard")

@app.route("/edit_prod/<int:id>", methods=["POST"])
def edit_prod(id):
    if not require_admin(): return redirect("/admin")
    name   = request.form.get("name","").strip()
    cat_id = request.form.get("cat")
    try:
        price = float(request.form.get("price","0"))
        if price < 0: raise ValueError
    except ValueError: return redirect("/dashboard")
    if not name or not cat_id: return redirect("/dashboard")
    img = save(request.files.get("image"))
    if img: q("UPDATE products SET name=%s, price=%s, image=%s, category_id=%s WHERE id=%s", (name, price, img, cat_id, id))
    else:   q("UPDATE products SET name=%s, price=%s, category_id=%s WHERE id=%s", (name, price, cat_id, id))
    return redirect(f"/dashboard?cat={cat_id}")

@app.route("/add_cat", methods=["POST"])
def add_cat():
    if not require_admin(): return redirect("/admin")
    name = request.form.get("name","").strip()
    if not name: return redirect("/dashboard")
    parent = request.form.get("parent","").strip()
    parent_id = int(parent) if parent else None
    if parent_id is None:
        max_order = q("SELECT MAX(sort_order) as m FROM categories WHERE parent_id IS NULL", one=True)
    else:
        max_order = q("SELECT MAX(sort_order) as m FROM categories WHERE parent_id=%s", (parent_id,), one=True)
    new_order = (max_order["m"] or 0) + 1
    q("INSERT INTO categories(name, image, parent_id, sort_order) VALUES(%s,%s,%s,%s)",
      (name, save(request.files.get("image")), parent_id, new_order))
    return redirect(f"/dashboard?cat={parent_id}" if parent_id else "/dashboard")

@app.route("/add_prod", methods=["POST"])
def add_prod():
    if not require_admin(): return redirect("/admin")
    name   = request.form.get("name","").strip()
    cat_id = request.form.get("cat")
    try:
        price = float(request.form.get("price","0"))
        if price < 0: raise ValueError
    except ValueError: return redirect("/dashboard")
    if not name or not cat_id: return redirect("/dashboard")
    max_order = q("SELECT MAX(sort_order) as m FROM products WHERE category_id=%s", (cat_id,), one=True)
    new_order = (max_order["m"] or 0) + 1
    q("INSERT INTO products(name, price, image, category_id, sort_order) VALUES(%s,%s,%s,%s,%s)",
      (name, price, save(request.files.get("image")), cat_id, new_order))
    return redirect(f"/dashboard?cat={cat_id}")

@app.route("/delete_cat/<int:id>", methods=["POST"])
def del_cat(id):
    if not require_admin(): return redirect("/admin")
    cat = q("SELECT * FROM categories WHERE id=%s", (id,), one=True)
    parent_id = cat["parent_id"] if cat else None
    def delete_recursive(cid):
        for sub in q("SELECT id FROM categories WHERE parent_id=%s", (cid,)): delete_recursive(sub["id"])
        q("DELETE FROM products WHERE category_id=%s", (cid,))
        q("DELETE FROM categories WHERE id=%s", (cid,))
    delete_recursive(id)
    return redirect(f"/dashboard?cat={parent_id}" if parent_id else "/dashboard")

@app.route("/delete_prod/<int:id>", methods=["POST"])
def del_prod(id):
    if not require_admin(): return redirect("/admin")
    p = q("SELECT category_id FROM products WHERE id=%s", (id,), one=True)
    q("DELETE FROM products WHERE id=%s", (id,))
    cat_id = p["category_id"] if p else None
    return redirect(f"/dashboard?cat={cat_id}" if cat_id else "/dashboard")

# ============================================================
# ORDER DOWNLOAD
# ============================================================
@app.route("/order/<int:id>/download")
def download_order(id):
    if not require_admin(): return redirect("/admin")
    o = q("SELECT * FROM orders WHERE id=%s", (id,), one=True)
    if not o: return redirect("/dashboard")
    order_dict = dict(o)
    png_buf = generate_order_png(order_dict)
    return Response(
        png_buf.read(),
        mimetype="image/png",
        headers={"Content-Disposition": f"attachment; filename=order_{o['id']}.png"}
    )

@app.route("/delete_order/<int:id>", methods=["POST"])
def del_order(id):
    if not require_admin(): return redirect("/admin")
    q("DELETE FROM orders WHERE id=%s", (id,))
    return redirect("/dashboard")

# ============================================================
# DATABASE INFO PAGE
# ============================================================
@app.route("/admin/database")
def admin_database():
    if not require_admin(): return redirect("/admin")
    cloud_name = os.environ.get("CLOUDINARY_CLOUD_NAME", "â€”")
    db_url_masked = DATABASE_URL[:40] + "..." if len(DATABASE_URL) > 40 else DATABASE_URL
    return render_template_string("""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Database & Storage</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
</head><body>
<div class="container mt-5" style="max-width:540px">
  <div class="d-flex justify-content-between align-items-center mb-4">
    <h4>&#128506;&#65039; Database & Storage</h4>
    <a href="/dashboard" class="btn btn-secondary btn-sm">Back</a>
  </div>
  <div class="alert alert-success">
    &#9989; Database connected to <strong>Neon PostgreSQL</strong><br>
    <small class="text-muted">{{ db_url }}</small>
  </div>
  <div class="alert alert-info">
    &#128444;&#65039; Images stored on <strong>Cloudinary</strong><br>
    <small class="text-muted">Cloud name: <strong>{{ cloud_name }}</strong></small>
  </div>
  <div class="d-grid gap-2">
    <a href="https://console.neon.tech" target="_blank" class="btn btn-dark">Open Neon Console</a>
    <a href="https://cloudinary.com/console" target="_blank" class="btn btn-primary">Open Cloudinary Console</a>
  </div>
</div>
</body></html>""", db_url=db_url_masked, cloud_name=cloud_name)

# ============================================================
if __name__ == "__main__":
    import socket
    port = int(os.environ.get("PORT", 5000))
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        local_ip = "127.0.0.1"
    print("=" * 45)
    print("  Server is running!")
    print(f"  Local:   http://localhost:{port}")
    print(f"  Network: http://{local_ip}:{port}")
    print("=" * 45)
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG","false").lower()=="true")
