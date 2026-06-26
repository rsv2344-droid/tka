from flask import Flask, request, redirect, session, render_template_string, send_from_directory, Response, jsonify
import sqlite3
import os
from werkzeug.utils import secure_filename
import secrets
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
import io
import textwrap
import hashlib
import math
import requests

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR", BASE_DIR)
app.config["UPLOAD_FOLDER"] = os.path.join(DATA_DIR, "uploads")

ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "admin123")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "db.sqlite3")

FONT_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD    = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_MONO    = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def q(sql, args=(), one=False):
    conn = db()
    try:
        cur = conn.cursor()
        cur.execute(sql, args)
        rows = cur.fetchall()
        conn.commit()
        return (rows[0] if rows else None) if one else rows
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

# ============================================================
# SCHEMA SETUP
# ============================================================
q("""CREATE TABLE IF NOT EXISTS categories(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    image TEXT,
    parent_id INTEGER,
    sort_order INTEGER DEFAULT 0,
    card_size TEXT DEFAULT 'medium',
    is_open INTEGER DEFAULT 1,
    show_status INTEGER DEFAULT 1
)""")
q("""CREATE TABLE IF NOT EXISTS products(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    price REAL NOT NULL,
    image TEXT,
    category_id INTEGER,
    sort_order INTEGER DEFAULT 0,
    card_size TEXT DEFAULT 'medium',
    FOREIGN KEY(category_id) REFERENCES categories(id)
)""")
q("CREATE TABLE IF NOT EXISTS design(id INTEGER PRIMARY KEY AUTOINCREMENT, background TEXT, overlay TEXT, animation TEXT)")
q("""CREATE TABLE IF NOT EXISTS orders(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT,
    phone TEXT,
    address TEXT,
    details TEXT,
    items TEXT,
    total REAL,
    latitude TEXT,
    longitude TEXT,
    status TEXT DEFAULT 'pending',
    worker_id INTEGER,
    FOREIGN KEY(worker_id) REFERENCES workers(id)
)""")
q("""CREATE TABLE IF NOT EXISTS workers(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    display_name TEXT NOT NULL,
    phone TEXT,
    created_at TEXT,
    active INTEGER DEFAULT 1
)""")
q("""CREATE TABLE IF NOT EXISTS settings(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
)""")
q("""CREATE TABLE IF NOT EXISTS sub_admins(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    display_name TEXT NOT NULL,
    phone TEXT,
    created_at TEXT,
    active INTEGER DEFAULT 1
)""")
q("""CREATE TABLE IF NOT EXISTS sub_admin_categories(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sub_admin_id INTEGER NOT NULL,
    category_id INTEGER NOT NULL,
    FOREIGN KEY(sub_admin_id) REFERENCES sub_admins(id),
    FOREIGN KEY(category_id) REFERENCES categories(id),
    UNIQUE(sub_admin_id, category_id)
)""")

DEFAULT_SETTINGS = {
    "checkout_title":           "Complete Your Order",
    "checkout_phone_label":     "Phone Number ",
    "checkout_phone_placeholder": "e.g. 0912345678",
    "checkout_address_label":   "Delivery Address ",
    "checkout_address_placeholder": "Street, Building, Area...",
    "checkout_notes_label":     "Additional Notes (optional)",
    "checkout_notes_placeholder": "Any special instructions...",
    "checkout_confirm_btn":     "Confirm Order",
    "checkout_back_btn":        "Back to Cart",
    "store_name":               "My Store",
    "cart_btn_label":           "Cart",
    "order_confirmed_title":    "Order Confirmed!",
    "order_confirmed_msg":      "Thank you, we received your order.",
    "order_confirmed_redirect": "Redirecting to the store...",
    "order_confirmed_back_btn": "Back to Store",
    "delivery_min_price": "0",
    "delivery_per_100m": "0",
    "delivery_discount_500m": "0",
    "delivery_discount_1000m": "0",
    "delivery_discount_2000m": "0",
    "delivery_free_distance": "0",
    "store_latitude": "",
    "store_longitude": "",
    "delivery_enabled": "1",
    "delivery_label": "Delivery Fee",
    "delivery_free_label": "Free Delivery",
    "google_maps_api_key": "",
    "show_category_status": "1",
}

for k, v in DEFAULT_SETTINGS.items():
    q("INSERT OR IGNORE INTO settings(key, value) VALUES(?,?)", (k, v))

for migration in [
    "ALTER TABLE categories ADD COLUMN parent_id INTEGER",
    "ALTER TABLE categories ADD COLUMN sort_order INTEGER DEFAULT 0",
    "ALTER TABLE categories ADD COLUMN card_size TEXT DEFAULT 'medium'",
    "ALTER TABLE products ADD COLUMN sort_order INTEGER DEFAULT 0",
    "ALTER TABLE products ADD COLUMN card_size TEXT DEFAULT 'medium'",
    "ALTER TABLE orders ADD COLUMN latitude TEXT",
    "ALTER TABLE orders ADD COLUMN longitude TEXT",
    "ALTER TABLE orders ADD COLUMN status TEXT DEFAULT 'pending'",
    "ALTER TABLE orders ADD COLUMN worker_id INTEGER",
    "ALTER TABLE orders ADD COLUMN delivery_fee REAL DEFAULT 0",
    "ALTER TABLE orders ADD COLUMN distance_meters REAL DEFAULT 0",
    "ALTER TABLE categories ADD COLUMN is_open INTEGER DEFAULT 1",
    "ALTER TABLE categories ADD COLUMN show_status INTEGER DEFAULT 1",
    "ALTER TABLE sub_admins ADD COLUMN phone TEXT",
]:
    try:
        q(migration)
    except Exception:
        pass

def get_setting(key):
    row = q("SELECT value FROM settings WHERE key=?", (key,), one=True)
    return row["value"] if row else DEFAULT_SETTINGS.get(key, "")

def get_all_settings():
    rows = q("SELECT key, value FROM settings")
    return {r["key"]: r["value"] for r in rows}

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def save(file):
    if file and file.filename:
        if not allowed_file(file.filename):
            return None
        name = secure_filename(file.filename)
        unique_name = f"{secrets.token_hex(8)}_{name}"
        file.save(os.path.join(app.config["UPLOAD_FOLDER"], unique_name))
        return unique_name
    return None

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

# ============================================================
# PERMISSION HELPERS (FIXED: Now supports nested categories for sub-admins)
# ============================================================
def is_super_admin():
    return session.get("admin") is True

def is_sub_admin():
    return session.get("sub_admin_id") is not None

def is_any_admin():
    return is_super_admin() or is_sub_admin()

def get_current_admin_id():
    if is_super_admin():
        return "super", None
    return "sub", session.get("sub_admin_id")

def can_manage_category(cat_id):
    if is_super_admin():
        return True
    if is_sub_admin():
        sub_admin_id = session.get("sub_admin_id")
        row = q("SELECT id FROM sub_admin_categories WHERE sub_admin_id=? AND category_id=?",
                (sub_admin_id, cat_id), one=True)
        if row is not None:
            return True
        cat = q("SELECT * FROM categories WHERE id=?", (cat_id,), one=True)
        while cat and cat["parent_id"]:
            parent_id = cat["parent_id"]
            row = q("SELECT id FROM sub_admin_categories WHERE sub_admin_id=? AND category_id=?",
                    (sub_admin_id, parent_id), one=True)
            if row is not None:
                return True
            cat = q("SELECT * FROM categories WHERE id=?", (parent_id,), one=True)
        return False
    return False

def get_managed_categories():
    if is_super_admin():
        return [c["id"] for c in q("SELECT id FROM categories")]
    if is_sub_admin():
        sub_admin_id = session.get("sub_admin_id")
        direct_cats = [r["category_id"] for r in q("SELECT category_id FROM sub_admin_categories WHERE sub_admin_id=?", (sub_admin_id,))]
        all_managed = set(direct_cats)
        def add_sub_categories(parent_ids):
            if not parent_ids:
                return
            placeholders = ','.join(['?'] * len(parent_ids))
            subs = q(f"SELECT id FROM categories WHERE parent_id IN ({placeholders})", tuple(parent_ids))
            new_ids = [s["id"] for s in subs]
            for nid in new_ids:
                if nid not in all_managed:
                    all_managed.add(nid)
            add_sub_categories(new_ids)
        add_sub_categories(direct_cats)
        return list(all_managed)
    return []

def require_admin():
    return is_any_admin()

def require_super_admin():
    return is_super_admin()

def require_worker():
    return session.get("worker_id") is not None

# ============================================================
# DELIVERY FEE CALCULATOR
# ============================================================
def haversine_distance(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1 = math.radians(float(lat1))
    phi2 = math.radians(float(lat2))
    dphi = math.radians(float(lat2) - float(lat1))
    dlambda = math.radians(float(lon2) - float(lon1))
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

def get_driving_distance_osrm(lat1, lon1, lat2, lon2):
    try:
        url = f"http://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2}?overview=false"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("routes") and len(data["routes"]) > 0:
                return data["routes"][0].get("distance", 0)
    except Exception:
        pass
    return None

def get_driving_distance_google(lat1, lon1, lat2, lon2, api_key):
    try:
        url = "https://maps.googleapis.com/maps/api/distancematrix/json"
        params = {
            "origins": f"{lat1},{lon1}",
            "destinations": f"{lat2},{lon2}",
            "mode": "driving",
            "key": api_key
        }
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("rows") and len(data["rows"]) > 0:
                elements = data["rows"][0].get("elements", [])
                if elements and len(elements) > 0:
                    element = elements[0]
                    if element.get("status") == "OK":
                        return element.get("distance", {}).get("value", 0)
    except Exception:
        pass
    return None

def get_driving_distance(lat1, lon1, lat2, lon2):
    osrm_distance = get_driving_distance_osrm(lat1, lon1, lat2, lon2)
    if osrm_distance is not None and osrm_distance > 0:
        return osrm_distance, "osrm"
    google_api_key = get_setting("google_maps_api_key")
    if google_api_key:
        google_distance = get_driving_distance_google(lat1, lon1, lat2, lon2, google_api_key)
        if google_distance is not None and google_distance > 0:
            return google_distance, "google"
    return haversine_distance(lat1, lon1, lat2, lon2), "haversine"

def calculate_delivery_fee(lat, lon):
    if not lat or not lon:
        return 0, 0, None
    store_lat = get_setting("store_latitude")
    store_lon = get_setting("store_longitude")
    if not store_lat or not store_lon:
        return 0, 0, None
    try:
        distance_m, provider = get_driving_distance(float(store_lat), float(store_lon), float(lat), float(lon))
    except:
        return 0, 0, None
    if get_setting("delivery_enabled") != "1":
        return 0, distance_m, provider
    min_price = float(get_setting("delivery_min_price") or 0)
    per_100m = float(get_setting("delivery_per_100m") or 0)
    discount_500m = float(get_setting("delivery_discount_500m") or 0)
    discount_1000m = float(get_setting("delivery_discount_1000m") or 0)
    discount_2000m = float(get_setting("delivery_discount_2000m") or 0)
    free_distance = float(get_setting("delivery_free_distance") or 0)
    if free_distance > 0 and distance_m <= free_distance:
        return 0, distance_m, provider
    fee = min_price
    extra_distance = max(0, distance_m - 500)
    fee += (extra_distance / 100) * per_100m
    if distance_m <= 500 and discount_500m > 0:
        fee = fee * (1 - discount_500m / 100)
    elif distance_m <= 1000 and discount_1000m > 0:
        fee = fee * (1 - discount_1000m / 100)
    elif distance_m <= 2000 and discount_2000m > 0:
        fee = fee * (1 - discount_2000m / 100)
    return max(0, fee), distance_m, provider

@app.route("/uploads/<filename>")
def uploads(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], secure_filename(filename))

SIZE_HEIGHT = {"small": "100px", "medium": "140px", "large": "200px"}

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
        f_title   = ImageFont.truetype(FONT_BOLD,    28)
        f_header  = ImageFont.truetype(FONT_BOLD,    16)
        f_label   = ImageFont.truetype(FONT_BOLD,    14)
        f_value   = ImageFont.truetype(FONT_REGULAR, 14)
        f_mono    = ImageFont.truetype(FONT_MONO,    13)
        f_small   = ImageFont.truetype(FONT_REGULAR, 12)
        f_total   = ImageFont.truetype(FONT_BOLD,    20)
        f_id      = ImageFont.truetype(FONT_BOLD,    36)
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

    worker_name = ""
    if order.get("worker_id"):
        w = q("SELECT display_name FROM workers WHERE id=?", (order["worker_id"],), one=True)
        if w:
            worker_name = w["display_name"]

    distance_provider = "Direct Line"
    google_key = get_setting("google_maps_api_key")
    if google_key:
        distance_provider = "Google Maps"
    else:
        distance_provider = "OpenStreetMap"

    delivery_fee = float(order.get("delivery_fee", 0) or 0)
    distance_m = float(order.get("distance_meters", 0) or 0)
    has_delivery = delivery_fee > 0 or distance_m > 0

    H = 80 + 70 + 20 + 50 + 10 + 1 + 16 + 330
    if notes:
        H += 30 + (len(note_lines) - 1) * 18
    if has_location:
        H += 30
    if worker_name:
        H += 30
    if has_delivery:
        H += 30
    H += 16 + 1 + 16 + 24 + 10 + len(wrapped_items)*20 + 10 + 1 + 16 + 36 + 36

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

    if worker_name:
        y = info_row("Assigned to", worker_name, y)

    if has_delivery:
        dist_km = distance_m / 1000
        delivery_label = f"Delivery ({dist_km:.1f} km - {distance_provider})"
        y = info_row(delivery_label, f"{delivery_fee:.0f}", y)

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
# MOBILE STORE FRONTEND
# ============================================================
MOBILE_BASE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
<style>
* { box-sizing: border-box; }
body { font-family: Arial, sans-serif; background: #f5f6fa; min-height: 100vh; padding-bottom: 100px; }
.store-nav { background: #fff; padding: 14px 16px; display: flex; align-items: center; justify-content: space-between; box-shadow: 0 1px 8px rgba(0,0,0,0.08); position: sticky; top: 0; z-index: 100; }
.store-nav .store-name { font-size: 18px; font-weight: 700; color: #111; text-decoration: none; }
.nav-cart-btn { background: #111; color: #fff; border: none; border-radius: 50px; padding: 8px 16px; font-size: 14px; font-weight: 600; display: flex; align-items: center; gap: 6px; text-decoration: none; }
.nav-cart-btn .badge { background: #e53935; color: #fff; border-radius: 50px; padding: 2px 7px; font-size: 11px; }
.products-grid { display: grid; gap: 12px; padding: 14px; }
.product-card { background: #fff; border-radius: 14px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.07); display: flex; flex-direction: column; text-decoration: none; color: inherit; }
.product-card img { width: 100%; object-fit: cover; }
.product-card .no-img { width: 100%; background: #eee; display: flex; align-items: center; justify-content: center; color: #aaa; font-size: 13px; }
.product-card .card-info { padding: 10px 10px 12px; flex: 1; display: flex; flex-direction: column; gap: 6px; }
.product-card .card-name { font-size: 13px; font-weight: 600; color: #111; }
.product-card .card-price { font-size: 14px; font-weight: 700; color: #111; }
.qty-controls { display: flex; align-items: center; background: #f0f0f0; border-radius: 50px; overflow: hidden; width: 100%; }
.qty-controls form { flex: 1; }
.qty-controls button { width: 100%; background: none; border: none; font-size: 18px; font-weight: 700; padding: 6px 0; color: #111; cursor: pointer; }
.qty-controls .qty-num { font-size: 14px; font-weight: 700; color: #111; min-width: 28px; text-align: center; }
.add-btn { width: 100%; background: #111; color: #fff; border: none; border-radius: 50px; padding: 8px 0; font-size: 13px; font-weight: 600; cursor: pointer; text-align: center; text-decoration: none; display: block; }
.back-btn { display: inline-flex; align-items: center; gap: 6px; background: #fff; color: #333; font-size: 14px; font-weight: 600; text-decoration: none; padding: 10px 18px; border-radius: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.07); margin: 12px 14px 8px; }
.section-title { display: inline-block; font-size: 15px; font-weight: 700; color: #333; background: #fff; padding: 10px 18px; border-radius: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.07); margin: 12px 14px 8px; }
.cart-item { background: #fff; border-radius: 14px; padding: 14px; margin: 0 14px 10px; display: flex; align-items: center; gap: 12px; box-shadow: 0 1px 6px rgba(0,0,0,0.06); }
.cart-item img { width: 60px; height: 60px; border-radius: 10px; object-fit: cover; flex-shrink: 0; }
.cart-item .no-img-sm { width: 60px; height: 60px; border-radius: 10px; background: #eee; flex-shrink: 0; }
.cart-item .item-details { flex: 1; min-width: 0; }
.cart-item .item-name { font-size: 14px; font-weight: 600; color: #111; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cart-item .item-price { font-size: 13px; color: #555; margin-top: 2px; }
.cart-item .item-subtotal { font-size: 14px; font-weight: 700; color: #111; }
.qty-row { display: flex; align-items: center; gap: 8px; margin-top: 8px; }
.qty-row form button { width: 30px; height: 30px; border-radius: 50%; border: 1.5px solid #ddd; background: #fff; font-size: 16px; font-weight: 700; display: flex; align-items: center; justify-content: center; cursor: pointer; }
.qty-row .qty-num { font-size: 14px; font-weight: 700; min-width: 20px; text-align: center; }
.delete-btn { background: none; border: none; color: #e53935; font-size: 18px; cursor: pointer; padding: 4px; margin-left: auto; }
.cart-footer { position: fixed; bottom: 0; left: 0; right: 0; background: #fff; padding: 14px 16px; box-shadow: 0 -2px 12px rgba(0,0,0,0.1); z-index: 200; }
.cart-footer .total-row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
.cart-footer .total-label { font-size: 15px; color: #555; }
.cart-footer .total-val { font-size: 20px; font-weight: 700; color: #111; }
.checkout-btn { display: block; width: 100%; background: #111; color: #fff; border: none; border-radius: 50px; padding: 14px; font-size: 16px; font-weight: 700; text-align: center; text-decoration: none; cursor: pointer; }
.cat-status-badge { display: inline-block; padding: 3px 10px; border-radius: 20px; font-size: 11px; font-weight: 700; margin-top: 6px; }
.cat-status-open { background: #e8f5e9; color: #2e7d32; }
.cat-status-closed { background: #ffebee; color: #c62828; }
{BG_STYLE}
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
        p = q("SELECT price FROM products WHERE id=?", (prod_id,), one=True)
        if p:
            total += float(p["price"]) * cnt
    return qty, total

def get_mobile_base():
    d = q("SELECT * FROM design ORDER BY id DESC LIMIT 1", one=True)
    bg_style = ""
    if d and d["background"]:
        bg_style = f"body::before {{ content: ''; position: fixed; top: 0; left: 0; right: 0; bottom: 0; width: 100%; height: 100%; background-image: url('/uploads/{d['background']}'); background-size: cover; background-position: center; background-repeat: no-repeat; z-index: -1; }}"
    return MOBILE_BASE.replace("{BG_STYLE}", bg_style)

def get_navbar(cart_qty=0, cart_total=0):
    store_name = get_setting("store_name")
    cart_label = get_setting("cart_btn_label")
    badge = f'<span class="badge">{cart_qty}</span>' if cart_qty > 0 else ""
    total_str = f" &middot; {int(cart_total)}" if cart_qty > 0 else ""
    return f"""
    <nav class="store-nav">
        <a href="/" class="store-name">{store_name}</a>
        <a href="/cart" class="nav-cart-btn">{cart_label}{total_str} {badge}</a>
    </nav>"""

def render_grid(items, href_fn, action_fn, size_field="card_size", show_status=False):
    if not items:
        return ""
    html = '<div style="display:grid;gap:12px;padding:14px;grid-template-columns:repeat(2,1fr);">'
    for row in items:
        item = dict(row)
        size = item.get(size_field, "medium") or "medium"
        size = size if size in SIZE_HEIGHT else "medium"
        cols = {"small": "span 1", "medium": "span 1", "large": "span 2"}.get(size, "span 1")
        height = SIZE_HEIGHT.get(size, "140px")
        href = href_fn(item) if href_fn else None
        action = action_fn(item) if action_fn else ""
        item_image = item.get("image")
        img_html = f'<img src="/uploads/{item_image}" style="width:100%;height:{height};object-fit:cover;" alt="">' if item_image else f'<div class="no-img" style="height:{height};">No Image</div>'
        name = item.get("name", "")
        item_price = item.get("price")
        price_html = f'<div class="card-price">{int(item_price)}</div>' if item_price is not None else ""

        status_html = ""
        if show_status and get_setting("show_category_status") == "1":
            is_open = item.get("is_open", 1)
            if is_open == 1:
                status_html = '<span class="cat-status-badge cat-status-open">&#9679; Open</span>'
            else:
                status_html = '<span class="cat-status-badge cat-status-closed">&#9679; Closed</span>'

        if href:
            inner = f'<a href="{href}" class="product-card" style="grid-column:{cols};">{img_html}<div class="card-info"><div class="card-name">{name}</div>{status_html}</div></a>'
        else:
            inner = f'<div class="product-card" style="grid-column:{cols};">{img_html}<div class="card-info"><div class="card-name">{name}</div>{price_html}{action}</div></div>'
        html += inner
    html += "</div>"
    return html

@app.route("/")
def home():
    show_status = get_setting("show_category_status") == "1"
    if is_any_admin():
        cats = q("SELECT * FROM categories WHERE parent_id IS NULL ORDER BY sort_order ASC, id ASC")
    else:
        cats = q("SELECT * FROM categories WHERE parent_id IS NULL AND is_open = 1 ORDER BY sort_order ASC, id ASC")

    base = get_mobile_base()
    qty, total = get_cart_summary()
    navbar = get_navbar(qty, total)
    if cats:
        cats_html = render_grid(cats, href_fn=lambda c: f"/category/{c['id']}", action_fn=None, show_status=show_status)
    else:
        cats_html = '<p class="text-muted px-3">No categories yet.</p>'
    return base + navbar + f'<p class="section-title mt-3">Categories</p>{cats_html}<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script></body></html>'

@app.route("/category/<int:id>")
def category(id):
    cat = q("SELECT * FROM categories WHERE id=?", (id,), one=True)
    if not cat:
        return '<h3>Category not found.</h3><a href="/">Back</a>', 404

    if cat["is_open"] == 0 and not is_any_admin():
        return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
        <style>body{{font-family:Arial,sans-serif;background:#f5f6fa;min-height:100vh;display:flex;align-items:center;justify-content:center;}}
        .closed-card{{background:#fff;border-radius:16px;padding:40px;text-align:center;box-shadow:0 4px 20px rgba(0,0,0,0.08);max-width:340px;margin:20px;}}
        </style></head><body>
        <div class="closed-card"><div style="font-size:60px;margin-bottom:16px;">&#128274;</div>
        <h3 style="font-size:20px;font-weight:700;margin-bottom:8px;">Category Closed</h3>
        <p style="color:#666;font-size:14px;">This category is currently closed. Please check back later.</p>
        <a href="/" style="display:inline-block;margin-top:16px;background:#111;color:#fff;border-radius:50px;padding:10px 28px;font-size:14px;font-weight:600;text-decoration:none;">Back to Store</a>
        </div></body></html>""", 403

    subcats = q("SELECT * FROM categories WHERE parent_id=? ORDER BY sort_order ASC, id ASC", (id,))
    prods = q("SELECT * FROM products WHERE category_id=? ORDER BY sort_order ASC, id ASC", (id,))
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
        return f'<a href="/add/{pid}" class="add-btn">Add to Cart</a>'
    prods_html = render_grid(prods, href_fn=None, action_fn=prod_action) if prods else (
        '<p class="text-muted px-3">No products in this category.</p>' if not subcats else ""
    )
    sections = subcats_html + prods_html
    return base + navbar + f'<a href="{back_url}" class="back-btn">&larr; Back</a><p class="section-title">{cat["name"]}</p>{sections}<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script></body></html>'

@app.route("/add/<int:id>")
def add(id):
    product = q("SELECT id, category_id FROM products WHERE id=?", (id,), one=True)
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
        p = q("SELECT * FROM products WHERE id=?", (prod_id,), one=True)
        if p:
            subtotal = float(p["price"]) * cnt
            items.append({"id": prod_id, "name": p["name"], "price": p["price"], "image": p["image"], "qty": cnt, "subtotal": subtotal})
            total += subtotal
    base = get_mobile_base()
    qty_total, _ = get_cart_summary()
    navbar = get_navbar(qty_total, total)
    if not items:
        return base + navbar + '<div style="text-align:center;padding:60px 20px;"><div style="font-size:60px;">&#128722;</div><p style="font-size:17px;font-weight:600;margin-top:12px;">Your cart is empty</p><a href="/" class="checkout-btn" style="display:inline-block;width:auto;padding:12px 32px;margin-top:16px;">Browse Products</a></div><script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script></body></html>'
    items_html = ""
    for i in items:
        img = f'<img src="/uploads/{i["image"]}" alt="">' if i["image"] else '<div class="no-img-sm"></div>'
        items_html += f'<div class="cart-item">{img}<div class="item-details"><div class="item-name">{i["name"]}</div><div class="item-price">{int(i["price"])} each</div><div class="qty-row"><form method="post" action="/cart/remove/{i["id"]}"><button>-</button></form><span class="qty-num">{i["qty"]}</span><form method="post" action="/cart/add_one/{i["id"]}"><button>+</button></form><span class="item-subtotal">{int(i["subtotal"])}</span><form method="post" action="/cart/delete/{i["id"]}" style="margin-left:auto"><button class="delete-btn">&#128465;</button></form></div></div></div>'
    return base + navbar + f'<a href="/" class="back-btn">&larr; Continue Shopping</a><p class="section-title">My Cart</p><div style="margin-top:8px;">{items_html}</div><div class="cart-footer"><div class="total-row"><span class="total-label">Total</span><span class="total-val">{int(total)}</span></div><a href="/checkout" class="checkout-btn">Checkout &rarr;</a></div><script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script></body></html>'

@app.route("/cart/remove/<int:prod_id>", methods=["POST"])
def cart_remove(prod_id):
    cart = session.get("cart", [])
    if prod_id in cart:
        cart.remove(prod_id)
    session["cart"] = cart
    return redirect(request.args.get("next", "/cart"))

@app.route("/cart/add_one/<int:prod_id>", methods=["POST"])
def cart_add_one(prod_id):
    if q("SELECT id FROM products WHERE id=?", (prod_id,), one=True):
        cart = session.get("cart", [])
        cart.append(prod_id)
        session["cart"] = cart
    return redirect(request.args.get("next", "/cart"))

@app.route("/cart/delete/<int:prod_id>", methods=["POST"])
def cart_delete(prod_id):
    session["cart"] = [i for i in session.get("cart", []) if i != prod_id]
    return redirect("/cart")

@app.route("/checkout", methods=["GET", "POST"])
def checkout():
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
            p = q("SELECT * FROM products WHERE id=?", (prod_id,), one=True)
            if p:
                subtotal = float(p["price"]) * cnt
                total += subtotal
                cat = q("SELECT name FROM categories WHERE id=?", (p["category_id"],), one=True)
                cat_name = cat["name"] if cat else "-"
                lines.append(f"{p['name']} ({cat_name}) x {cnt} = {subtotal:.0f}")
        delivery_fee, distance_m, provider = calculate_delivery_fee(lat, lon)
        final_total = total + delivery_fee
        q("INSERT INTO orders(created_at, phone, address, details, items, total, latitude, longitude, status, delivery_fee, distance_meters) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
          (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), phone, address, details,
           "\n".join(lines), final_total, lat or None, lon or None, "pending", delivery_fee, distance_m))
        session["cart"] = []
        return redirect("/order_confirmed")
    if not session.get("cart"):
        return redirect("/cart")
    base = get_mobile_base()

    ids = session.get("cart", [])
    counts = {}
    for i in ids:
        counts[i] = counts.get(i, 0) + 1
    total = 0.0
    for prod_id, cnt in counts.items():
        p = q("SELECT price FROM products WHERE id=?", (prod_id,), one=True)
        if p:
            total += float(p["price"]) * cnt

    delivery_script = """
<script>
(function(){
  var latField = document.getElementById('lat');
  var lonField = document.getElementById('lon');
  var geoStatus = document.getElementById('geo-status');
  var deliveryPreview = document.getElementById('delivery-preview');
  var totalDisplay = document.getElementById('total-display');
  var cartTotal = parseFloat(totalDisplay.dataset.cartTotal) || 0;

  function setStatus(msg, color){
    if(geoStatus){ geoStatus.textContent = msg; geoStatus.style.color = color; }
  }
  function updateDeliveryFee(lat, lon) {
    if(!deliveryPreview || !lat || !lon) return;
    fetch('/api/calculate_delivery?lat='+lat+'&lon='+lon)
      .then(r=>r.json())
      .then(d=>{
        if(d.ok){
          var fee = d.fee;
          var dist = d.distance;
          var distKm = (dist/1000).toFixed(1);
          var provider = d.provider || '';
          if(fee > 0){
            deliveryPreview.innerHTML = '<div style="display:flex;justify-content:space-between;align-items:center;padding:12px 14px;background:#fff3e0;border-radius:12px;border:1.5px solid #ffcc80;"><div><div style="font-size:13px;font-weight:700;color:#e65100;">'+d.label+'</div><div style="font-size:12px;color:#999;">Distance: '+distKm+' km'+(provider?' - '+provider:'')+'</div></div><div style="font-size:16px;font-weight:700;color:#e65100;">'+Math.round(fee)+'</div></div>';
          } else {
            deliveryPreview.innerHTML = '<div style="display:flex;justify-content:space-between;align-items:center;padding:12px 14px;background:#e8f5e9;border-radius:12px;border:1.5px solid #a5d6a7;"><div><div style="font-size:13px;font-weight:700;color:#2e7d32;">'+d.label+'</div><div style="font-size:12px;color:#999;">Distance: '+distKm+' km'+(provider?' - '+provider:'')+'</div></div><div style="font-size:16px;font-weight:700;color:#2e7d32;">FREE</div></div>';
          }
          var finalTotal = cartTotal + fee;
          totalDisplay.innerHTML = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;"><span style="font-size:15px;color:#555;">Subtotal</span><span style="font-size:15px;font-weight:600;">'+Math.round(cartTotal)+'</span></div><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;"><span style="font-size:15px;color:#555;">Delivery</span><span style="font-size:15px;font-weight:600;">'+Math.round(fee)+'</span></div><div style="display:flex;justify-content:space-between;align-items:center;border-top:2px solid #111;padding-top:10px;"><span style="font-size:18px;font-weight:700;">Total</span><span style="font-size:22px;font-weight:700;color:#111;">'+Math.round(finalTotal)+'</span></div>';
        }
      });
  }
  if(navigator.geolocation){
    setStatus('Detecting location...', '#888');
    navigator.geolocation.getCurrentPosition(
      function(pos){
        var lat = pos.coords.latitude.toFixed(7);
        var lon = pos.coords.longitude.toFixed(7);
        latField.value = lat;
        lonField.value = lon;
        setStatus('Location detected \\u2713', '#2ecc71');
        updateDeliveryFee(lat, lon);
      },
      function(err){
        setStatus('Location not available', '#e53935');
      },
      { enableHighAccuracy: true, timeout: 8000, maximumAge: 0 }
    );
  } else {
    setStatus('Geolocation not supported', '#e53935');
  }
})();
</script>
"""

    geo_ui = """
<div style="display:flex;align-items:center;gap:8px;background:#f8f8f8;border:1.5px solid #eee;border-radius:12px;padding:11px 14px;margin-top:4px;">
  <span style="font-size:20px;">&#128205;</span>
  <span id="geo-status" style="font-size:13px;color:#888;font-weight:600;">Waiting for location...</span>
</div>
<input type="hidden" name="lat" id="lat" value="">
<input type="hidden" name="lon" id="lon" value="">
"""

    return base + f"""
<div style="min-height:100vh;padding:20px;">
  <a href="/cart" class="back-btn" style="margin:0 0 20px;">&larr; {s.get('checkout_back_btn','Back to Cart')}</a>
  <h2 style="font-size:20px;font-weight:700;margin-bottom:20px;">{s.get('checkout_title','Complete Your Order')}</h2>
  <form method="post" style="display:flex;flex-direction:column;gap:14px;">
    <div>
      <label style="font-size:13px;font-weight:600;color:#555;display:block;margin-bottom:6px;">{s.get('checkout_phone_label','Phone Number ')}</label>
      <input type="tel" name="phone" required style="width:100%;padding:13px 14px;border:1.5px solid #ddd;border-radius:12px;font-size:15px;outline:none;" placeholder="{s.get('checkout_phone_placeholder','e.g. 0912345678')}">
    </div>
    <div>
      <label style="font-size:13px;font-weight:600;color:#555;display:block;margin-bottom:6px;">{s.get('checkout_address_label','Delivery Address ')}</label>
      <input type="text" name="address" required style="width:100%;padding:13px 14px;border:1.5px solid #ddd;border-radius:12px;font-size:15px;outline:none;" placeholder="{s.get('checkout_address_placeholder','Street, Building, Area...')}">
    </div>
    <div>
      <label style="font-size:13px;font-weight:600;color:#555;display:block;margin-bottom:6px;">{s.get('checkout_notes_label','Additional Notes (optional)')}</label>
      <textarea name="details" rows="3" style="width:100%;padding:13px 14px;border:1.5px solid #ddd;border-radius:12px;font-size:15px;outline:none;resize:none;" placeholder="{s.get('checkout_notes_placeholder','Any special instructions...')}"></textarea>
    </div>
    <div>
      <label style="font-size:13px;font-weight:600;color:#555;display:block;margin-bottom:6px;">&#128205; Your Location</label>
      {geo_ui}
    </div>
    <div id="delivery-preview" style="margin-top:4px;"></div>
    <div id="total-display" data-cart-total="{total}" style="background:#f8f9fc;border-radius:12px;padding:14px 16px;margin-top:4px;">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">
        <span style="font-size:15px;color:#555;">Subtotal</span>
        <span style="font-size:15px;font-weight:600;">{int(total)}</span>
      </div>
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">
        <span style="font-size:15px;color:#555;">Delivery</span>
        <span style="font-size:15px;font-weight:600;color:#888;">Calculating...</span>
      </div>
      <div style="display:flex;justify-content:space-between;align-items:center;border-top:2px solid #111;padding-top:10px;">
        <span style="font-size:18px;font-weight:700;">Total</span>
        <span style="font-size:22px;font-weight:700;color:#111;">{int(total)}</span>
      </div>
    </div>
    <button type="submit" style="width:100%;background:#111;color:#fff;border:none;border-radius:50px;padding:15px;font-size:16px;font-weight:700;margin-top:6px;cursor:pointer;">{s.get('checkout_confirm_btn','Confirm Order')}</button>
  </form>
</div>
{delivery_script}
</body></html>
"""

@app.route("/order_confirmed")
def order_confirmed():
    s = get_all_settings()
    title    = s.get('order_confirmed_title',    'Order Confirmed!')
    msg      = s.get('order_confirmed_msg',      'Thank you, we received your order.')
    redir_msg= s.get('order_confirmed_redirect', 'Redirecting to the store...')
    back_btn = s.get('order_confirmed_back_btn', 'Back to Store')
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"><meta http-equiv="refresh" content="4;url=/"><style>body{{font-family:Arial,sans-serif;background:#f5f6fa;}}</style></head><body><div style="min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px;"><div style="text-align:center;"><div style="font-size:70px;">&#9989;</div><h2 style="font-size:22px;font-weight:700;margin-top:16px;">{title}</h2><p style="color:#555;font-size:15px;">{msg}</p><p style="color:#999;font-size:13px;margin-top:6px;">{redir_msg}</p><a href="/" style="display:inline-block;margin-top:20px;background:#111;color:#fff;border-radius:50px;padding:13px 32px;font-size:15px;font-weight:600;text-decoration:none;">{back_btn}</a></div></div></body></html>"""

# ============================================================
# ADMIN LOGIN (Super Admin)
# ============================================================
@app.route("/admin", methods=["GET", "POST"])
def admin():
    error = None
    if request.method == "POST":
        if request.form.get("u") == ADMIN_USER and request.form.get("p") == ADMIN_PASS:
            session["admin"] = True
            session.pop("sub_admin_id", None)
            session.pop("worker_id", None)
            return redirect("/dashboard")
        error = "Invalid username or password."
    return render_template_string("""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
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
.sub-admin-link { text-align: center; margin-top: 16px; font-size: 13px; }
.sub-admin-link a { color: #4361ee; text-decoration: none; font-weight: 600; }
</style></head><body>
<div class="login-card">
<h3>&#128274; Super Admin Login</h3>
{% if error %}<div class="alert alert-danger py-2">{{ error }}</div>{% endif %}
<form method="post">
<input name="u" class="form-control mb-3" placeholder="Username" required autocomplete="username">
<input name="p" type="password" class="form-control mb-3" placeholder="Password" required autocomplete="current-password">
<button class="btn-login">Login</button>
</form>
<div class="sub-admin-link">
<a href="/sub_admin/login">&#128640; Sub-Admin Login</a> | <a href="/worker/login">&#128100; Worker Login</a>
</div></div></body></html>""", error=error)

# ============================================================
# SUB-ADMIN LOGIN
# ============================================================
@app.route("/sub_admin/login", methods=["GET", "POST"])
def sub_admin_login():
    error = None
    if request.method == "POST":
        username = request.form.get("u", "").strip().lower()
        password = request.form.get("p", "").strip()
        sub_admin = q("SELECT * FROM sub_admins WHERE username=? AND active=1", (username,), one=True)
        if sub_admin and sub_admin["password_hash"] == hash_password(password):
            session["sub_admin_id"] = sub_admin["id"]
            session.pop("admin", None)
            session.pop("worker_id", None)
            return redirect("/dashboard")
        error = "Invalid username or password."
    return render_template_string("""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sub-Admin Login</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
<style>
body { font-family: Arial, sans-serif; background: #f0f2f5; min-height: 100vh; display: flex; align-items: center; justify-content: center; }
.login-card { background: #fff; border-radius: 18px; box-shadow: 0 4px 24px rgba(0,0,0,0.10); padding: 40px 32px; width: 100%; max-width: 380px; }
.login-card h3 { font-size: 22px; font-weight: 700; margin-bottom: 24px; text-align: center; color: #4361ee; }
.form-control { border-radius: 10px; padding: 12px 14px; font-size: 15px; border: 1.5px solid #e0e0e0; }
.form-control:focus { border-color: #4361ee; box-shadow: 0 0 0 3px rgba(67,97,238,0.12); }
.btn-login { background: #4361ee; color: #fff; border: none; border-radius: 50px; padding: 13px; font-size: 16px; font-weight: 700; width: 100%; margin-top: 8px; }
.btn-login:hover { background: #3451c7; color: #fff; }
.back-link { text-align: center; margin-top: 16px; font-size: 13px; }
.back-link a { color: #666; text-decoration: none; }
</style></head><body>
<div class="login-card">
<h3>&#128640; Sub-Admin Login</h3>
{% if error %}<div class="alert alert-danger py-2">{{ error }}</div>{% endif %}
<form method="post">
<input name="u" class="form-control mb-3" placeholder="Username" required autocomplete="username">
<input name="p" type="password" class="form-control mb-3" placeholder="Password" required autocomplete="current-password">
<button class="btn-login">Login</button>
</form>
<div class="back-link"><a href="/admin">&#8592; Super Admin Login</a></div>
</div></body></html>""", error=error)

@app.route("/sub_admin/logout")
def sub_admin_logout():
    session.pop("sub_admin_id", None)
    return redirect("/sub_admin/login")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/admin")

# ============================================================
# WORKER LOGIN
# ============================================================
@app.route("/worker/login", methods=["GET", "POST"])
def worker_login():
    error = None
    if request.method == "POST":
        username = request.form.get("u", "").strip()
        password = request.form.get("p", "").strip()
        worker = q("SELECT * FROM workers WHERE username=? AND active=1", (username,), one=True)
        if worker and worker["password_hash"] == hash_password(password):
            session["worker_id"] = worker["id"]
            session["worker_name"] = worker["display_name"]
            session.pop("admin", None)
            session.pop("sub_admin_id", None)
            return redirect("/worker/dashboard")
        error = "Invalid username or password."
    return render_template_string("""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Worker Login</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
<style>
body { font-family: Arial, sans-serif; background: #f0f2f5; min-height: 100vh; display: flex; align-items: center; justify-content: center; }
.login-card { background: #fff; border-radius: 18px; box-shadow: 0 4px 24px rgba(0,0,0,0.10); padding: 40px 32px; width: 100%; max-width: 380px; }
.login-card h3 { font-size: 22px; font-weight: 700; margin-bottom: 24px; text-align: center; color: #2ecc71; }
.form-control { border-radius: 10px; padding: 12px 14px; font-size: 15px; border: 1.5px solid #e0e0e0; }
.form-control:focus { border-color: #2ecc71; box-shadow: 0 0 0 3px rgba(46,204,113,0.12); }
.btn-login { background: #2ecc71; color: #fff; border: none; border-radius: 50px; padding: 13px; font-size: 16px; font-weight: 700; width: 100%; margin-top: 8px; }
.btn-login:hover { background: #27ae60; color: #fff; }
.back-link { text-align: center; margin-top: 16px; font-size: 13px; }
.back-link a { color: #666; text-decoration: none; }
</style></head><body>
<div class="login-card">
<h3>&#128100; Worker Login</h3>
{% if error %}<div class="alert alert-danger py-2">{{ error }}</div>{% endif %}
<form method="post">
<input name="u" class="form-control mb-3" placeholder="Username" required autocomplete="username">
<input name="p" type="password" class="form-control mb-3" placeholder="Password" required autocomplete="current-password">
<button class="btn-login">Login</button>
</form>
<div class="back-link"><a href="/admin">&#8592; Admin Login</a></div>
</div></body></html>""", error=error)

@app.route("/worker/logout")
def worker_logout():
    session.pop("worker_id", None)
    session.pop("worker_name", None)
    return redirect("/worker/login")

# ============================================================
# API ROUTES
# ============================================================
@app.route("/api/reorder_cats", methods=["POST"])
def api_reorder_cats():
    if not require_admin():
        return {"ok": False}, 403
    data = request.get_json()
    for item in data.get("order", []):
        if is_sub_admin() and not can_manage_category(item["id"]):
            continue
        q("UPDATE categories SET sort_order=? WHERE id=?", (item["order"], item["id"]))
    return {"ok": True}

@app.route("/api/reorder_prods", methods=["POST"])
def api_reorder_prods():
    if not require_admin():
        return {"ok": False}, 403
    data = request.get_json()
    for item in data.get("order", []):
        prod = q("SELECT category_id FROM products WHERE id=?", (item["id"],), one=True)
        if prod:
            if is_sub_admin() and not can_manage_category(prod["category_id"]):
                continue
            q("UPDATE products SET sort_order=? WHERE id=?", (item["order"], item["id"]))
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
        if is_sub_admin() and not can_manage_category(id_):
            return {"ok": False, "error": "permission denied"}, 403
        q("UPDATE categories SET card_size=? WHERE id=?", (size, id_))
    elif kind == "prod":
        prod = q("SELECT category_id FROM products WHERE id=?", (id_,), one=True)
        if prod and is_sub_admin() and not can_manage_category(prod["category_id"]):
            return {"ok": False, "error": "permission denied"}, 403
        q("UPDATE products SET card_size=? WHERE id=?", (size, id_))
    else:
        return {"ok": False}, 400
    return {"ok": True}

@app.route("/api/forward_order", methods=["POST"])
def api_forward_order():
    if not require_super_admin():
        return {"ok": False, "error": "Super admin only"}, 403
    data = request.get_json()
    order_id = data.get("order_id")
    worker_id = data.get("worker_id")
    if not order_id or not worker_id:
        return {"ok": False, "error": "Missing order_id or worker_id"}, 400
    worker = q("SELECT id FROM workers WHERE id=? AND active=1", (worker_id,), one=True)
    if not worker:
        return {"ok": False, "error": "Worker not found"}, 404
    order = q("SELECT id FROM orders WHERE id=? AND (worker_id IS NULL OR worker_id = '')", (order_id,), one=True)
    if not order:
        return {"ok": False, "error": "Order not found or already assigned"}, 404
    q("UPDATE orders SET worker_id=?, status='assigned' WHERE id=?", (worker_id, order_id))
    return {"ok": True}

@app.route("/api/unassign_order", methods=["POST"])
def api_unassign_order():
    if not require_super_admin():
        return {"ok": False, "error": "Super admin only"}, 403
    data = request.get_json()
    order_id = data.get("order_id")
    if not order_id:
        return {"ok": False, "error": "Missing order_id"}, 400
    q("UPDATE orders SET worker_id=NULL, status='pending' WHERE id=?", (order_id,))
    return {"ok": True}

@app.route("/api/toggle_category_status", methods=["POST"])
def api_toggle_category_status():
    if not require_admin():
        return {"ok": False}, 403
    data = request.get_json()
    cat_id = data.get("category_id")
    is_open = data.get("is_open")
    if cat_id is None or is_open is None:
        return {"ok": False, "error": "Missing parameters"}, 400

    if is_sub_admin() and not can_manage_category(cat_id):
        return {"ok": False, "error": "permission denied"}, 403

    q("UPDATE categories SET is_open=? WHERE id=?", (1 if is_open else 0, cat_id))
    return {"ok": True}

@app.route("/api/calculate_delivery")
def api_calculate_delivery():
    lat = request.args.get("lat", "").strip()
    lon = request.args.get("lon", "").strip()
    if not lat or not lon:
        return {"ok": False, "error": "Missing coordinates"}, 400
    fee, distance, provider = calculate_delivery_fee(lat, lon)
    label = get_setting("delivery_label") or "Delivery Fee"
    if fee == 0 and distance > 0:
        label = get_setting("delivery_free_label") or "Free Delivery"
    provider_names = {"osrm": "OpenStreetMap", "google": "Google Maps", "haversine": "Direct Line"}
    return {"ok": True, "fee": fee, "distance": distance, "label": label, "provider": provider_names.get(provider, provider)}

@app.route("/api/update_store_location", methods=["POST"])
def api_update_store_location():
    if not require_super_admin():
        return {"ok": False, "error": "Super admin only"}, 403
    data = request.get_json()
    lat = data.get("lat", "").strip()
    lon = data.get("lon", "").strip()
    if lat and lon:
        q("INSERT OR REPLACE INTO settings(key, value) VALUES(?,?)", ("store_latitude", lat))
        q("INSERT OR REPLACE INTO settings(key, value) VALUES(?,?)", ("store_longitude", lon))
    return {"ok": True}

@app.route("/api/toggle_show_status", methods=["POST"])
def api_toggle_show_status():
    if not require_super_admin():
        return {"ok": False, "error": "Super admin only"}, 403
    data = request.get_json()
    show = data.get("show", 1)
    q("INSERT OR REPLACE INTO settings(key, value) VALUES(?,?)", ("show_category_status", str(show)))
    return {"ok": True}

# ============================================================
# SETTINGS PANEL
# ============================================================
SETTINGS_FIELDS = [
    {"section": "Store Info", "fields": [
        {"key": "store_name", "label": "Store Name", "ref": "store_name"},
        {"key": "cart_btn_label", "label": "Cart Button Label", "ref": "cart_btn_label"},
    ]},
    {"section": "Checkout Page", "fields": [
        {"key": "checkout_title", "label": "Checkout Title", "ref": "checkout_title"},
        {"key": "checkout_back_btn", "label": "Back Button", "ref": "checkout_back_btn"},
        {"key": "checkout_phone_label", "label": "Phone Label", "ref": "checkout_phone_label"},
        {"key": "checkout_phone_placeholder", "label": "Phone Placeholder", "ref": "checkout_phone_placeholder"},
        {"key": "checkout_address_label", "label": "Address Label", "ref": "checkout_address_label"},
        {"key": "checkout_address_placeholder", "label": "Address Placeholder", "ref": "checkout_address_placeholder"},
        {"key": "checkout_notes_label", "label": "Notes Label", "ref": "checkout_notes_label"},
        {"key": "checkout_notes_placeholder", "label": "Notes Placeholder", "ref": "checkout_notes_placeholder"},
        {"key": "checkout_confirm_btn", "label": "Confirm Button", "ref": "checkout_confirm_btn"},
    ]},
    {"section": "Order Confirmed Page", "fields": [
        {"key": "order_confirmed_title", "label": "Confirmed Title", "ref": "order_confirmed_title"},
        {"key": "order_confirmed_msg", "label": "Confirmed Message", "ref": "order_confirmed_msg"},
        {"key": "order_confirmed_redirect", "label": "Redirect Message", "ref": "order_confirmed_redirect"},
        {"key": "order_confirmed_back_btn", "label": "Back Button", "ref": "order_confirmed_back_btn"},
    ]},
    {"section": "Delivery Settings", "fields": [
        {"key": "delivery_enabled", "label": "Delivery Enabled (1=Yes, 0=No)", "ref": "delivery_enabled"},
        {"key": "store_latitude", "label": "Store Latitude", "ref": "store_latitude"},
        {"key": "store_longitude", "label": "Store Longitude", "ref": "store_longitude"},
        {"key": "google_maps_api_key", "label": "Google Maps API Key (optional - uses OSRM if empty)", "ref": "google_maps_api_key"},
        {"key": "delivery_min_price", "label": "Minimum Delivery Price", "ref": "delivery_min_price"},
        {"key": "delivery_per_100m", "label": "Price per 100 Meters (after 500m)", "ref": "delivery_per_100m"},
        {"key": "delivery_discount_500m", "label": "Discount % (up to 500m)", "ref": "delivery_discount_500m"},
        {"key": "delivery_discount_1000m", "label": "Discount % (up to 1000m)", "ref": "delivery_discount_1000m"},
        {"key": "delivery_discount_2000m", "label": "Discount % (up to 2000m)", "ref": "delivery_discount_2000m"},
        {"key": "delivery_free_distance", "label": "Free Delivery Distance (meters)", "ref": "delivery_free_distance"},
        {"key": "delivery_label", "label": "Delivery Label", "ref": "delivery_label"},
        {"key": "delivery_free_label", "label": "Free Delivery Label", "ref": "delivery_free_label"},
    ]},
    {"section": "Category Status Feature", "fields": [
        {"key": "show_category_status", "label": "Show Category Status (1=Yes, 0=No)", "ref": "show_category_status"},
    ]},
]

SETTINGS_TEMPLATE = """<!DOCTYPE html>
<html lang="en" dir="ltr">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Settings</title>
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
.map-frame-container { width: 100%; height: 300px; border-radius: 12px; border: 1.5px solid var(--border); overflow: hidden; background: #f0f0f0; }
@media (max-width: 600px) { .field-row { grid-template-columns: 1fr; } .field-meta { border-left: none; border-bottom: 1px solid var(--border); } }
</style></head><body>
<div class="topbar"><h1>&#9881;&#65039; Settings</h1><a href="/dashboard">&#8594; Dashboard</a></div>
<div class="page-wrap">
{% if saved %}<div class="alert alert-success mb-3" style="border-radius:12px;">Settings saved!</div>{% endif %}
<form method="post">
{% for section in sections %}
<div class="section-card">
<div class="section-header">{{ section.section }}</div>
{% for f in section.fields %}
<div class="field-row">
<div class="field-meta"><span class="field-label">{{ f.label }}</span><span class="field-ref">{{ f.ref }}</span></div>
<div class="field-input"><input type="text" name="{{ f.key }}" id="input-{{ f.key }}" value="{{ values.get(f.key, '') }}" placeholder="{{ f.label }}"></div>
</div>
{% endfor %}
</div>
{% endfor %}
<div class="section-card">
<div class="section-header">&#128205; Store Location</div>
<div style="padding:16px;">
<div style="display:flex;gap:10px;margin-bottom:12px;flex-wrap:wrap;">
<input type="text" id="map-lat" class="form-control" placeholder="Latitude (e.g. 24.7136)" value="{{ values.get('store_latitude', '') }}" style="flex:1;min-width:140px;">
<input type="text" id="map-lon" class="form-control" placeholder="Longitude (e.g. 46.6753)" value="{{ values.get('store_longitude', '') }}" style="flex:1;min-width:140px;">
<button type="button" class="btn btn-success" onclick="detectLocation()" style="border-radius:10px;white-space:nowrap;"><i class="bi bi-geo-alt"></i> Detect My Location</button>
<button type="button" class="btn btn-primary" onclick="saveStoreLocation()" style="border-radius:10px;white-space:nowrap;"><i class="bi bi-check-lg"></i> Save</button>
</div>
<div id="map-frame-container" style="width:100%;height:300px;border-radius:12px;border:1.5px solid var(--border);overflow:hidden;background:#f0f0f0;display:flex;align-items:center;justify-content:center;">
  <div id="map-placeholder" style="text-align:center;padding:20px;">
    <div style="font-size:40px;margin-bottom:8px;">&#128205;</div>
    <div style="font-size:14px;font-weight:600;color:#666;">Enter coordinates above</div>
    <div style="font-size:12px;color:#999;margin-top:4px;">Or click Detect My Location</div>
  </div>
  <iframe id="map-frame" style="width:100%;height:100%;border:none;display:none;" allowfullscreen></iframe>
</div>
<div style="margin-top:10px;padding:10px;background:#e8f5e9;border-radius:8px;font-size:12px;color:#2e7d32;">
<i class="bi bi-info-circle"></i> <strong>Distance Calculation:</strong> Uses OpenStreetMap (free) for driving distance. Add Google Maps API key for more accurate results.
</div>
</div>
</div>
<div class="save-bar">
<span style="font-size:13px;color:#888;">Changes are saved immediately</span>
<button type="submit" class="save-btn">&#10003; Save Settings</button>
</div>
</form></div>
<script>
function updateMapPreview() {
  var lat = document.getElementById('map-lat').value.trim();
  var lon = document.getElementById('map-lon').value.trim();
  var placeholder = document.getElementById('map-placeholder');
  var frame = document.getElementById('map-frame');
  if(lat && lon) {
    var apiKey = '{{ values.get("google_maps_api_key", "") }}';
    if(apiKey) {
      frame.src = 'https://www.google.com/maps/embed/v1/place?key=' + apiKey + '&q=' + lat + ',' + lon + '&zoom=15';
    } else {
      frame.src = 'https://maps.google.com/maps?q=' + lat + ',' + lon + '&z=15&output=embed';
    }
    frame.style.display = 'block';
    placeholder.style.display = 'none';
    document.getElementById('input-store_latitude').value = lat;
    document.getElementById('input-store_longitude').value = lon;
  }
}
function detectLocation() {
  if(!navigator.geolocation) {
    alert('Geolocation not supported by your browser');
    return;
  }
  var btn = document.querySelector('button[onclick="detectLocation()"]');
  btn.innerHTML = '<i class="bi bi-arrow-repeat"></i> Detecting...';
  navigator.geolocation.getCurrentPosition(
    function(pos) {
      document.getElementById('map-lat').value = pos.coords.latitude.toFixed(6);
      document.getElementById('map-lon').value = pos.coords.longitude.toFixed(6);
      updateMapPreview();
      btn.innerHTML = '<i class="bi bi-geo-alt"></i> Detect My Location';
    },
    function(err) {
      alert('Could not detect location: ' + err.message);
      btn.innerHTML = '<i class="bi bi-geo-alt"></i> Detect My Location';
    },
    { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 }
  );
}
function saveStoreLocation() {
  var lat = document.getElementById('map-lat').value.trim();
  var lon = document.getElementById('map-lon').value.trim();
  if(!lat || !lon) { alert('Please enter coordinates or detect location'); return; }
  document.getElementById('input-store_latitude').value = lat;
  document.getElementById('input-store_longitude').value = lon;
  fetch('/api/update_store_location', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({lat:lat, lon:lon})})
    .then(r=>r.json()).then(d=>{ if(d.ok){ alert('Location saved!'); } else { alert('Error'); } });
}
{% if values.get('store_latitude') and values.get('store_longitude') %}
document.addEventListener('DOMContentLoaded', function() {
  updateMapPreview();
});
{% endif %}
document.getElementById('map-lat').addEventListener('change', updateMapPreview);
document.getElementById('map-lon').addEventListener('change', updateMapPreview);
</script>
</body></html>"""

@app.route("/settings", methods=["GET", "POST"])
def settings_panel():
    if not require_super_admin():
        return redirect("/admin")
    saved = False
    if request.method == "POST":
        for section in SETTINGS_FIELDS:
            for f in section["fields"]:
                val = request.form.get(f["key"], "").strip()
                if val:
                    q("INSERT OR REPLACE INTO settings(key, value) VALUES(?,?)", (f["key"], val))
        saved = True
    current_vals = get_all_settings()
    return render_template_string(SETTINGS_TEMPLATE, sections=SETTINGS_FIELDS, values=current_vals, saved=saved)

# ============================================================
# DASHBOARD (supports both Super Admin and Sub-Admin)
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
.worker-badge { display:inline-flex;align-items:center;gap:4px;background:#e3f2fd;color:#1976d2;border-radius:6px;padding:3px 8px;font-size:11px;font-weight:700; }
.status-badge { display:inline-block;border-radius:6px;padding:3px 8px;font-size:11px;font-weight:700; }
.status-pending { background:#fff3e0;color:#e65100; }
.status-assigned { background:#e3f2fd;color:#1976d2; }
.status-delivered { background:#e8f5e9;color:#2e7d32; }
.forward-btn { background:#4361ee;color:#fff;border:none;border-radius:6px;padding:4px 10px;font-size:12px;font-weight:600;cursor:pointer; }
.forward-btn:hover { background:#3451c7; }
.unassign-btn { background:#ff9800;color:#fff;border:none;border-radius:6px;padding:4px 10px;font-size:12px;font-weight:600;cursor:pointer; }
.worker-row { display:flex;align-items:center;gap:12px;padding:12px 16px;border-bottom:1px solid var(--border); }
.worker-row:last-child { border-bottom:none; }
.worker-avatar { width:42px;height:42px;border-radius:50%;background:linear-gradient(135deg,#4361ee,#3a0ca3);color:#fff;display:flex;align-items:center;justify-content:center;font-size:16px;font-weight:700; }
.worker-info { flex:1; }
.worker-name { font-size:14px;font-weight:700; }
.worker-meta { font-size:12px;color:var(--muted); }
.worker-balance { font-size:18px;font-weight:700;color:#2e7d32; }
.worker-actions { display:flex;gap:6px; }
.cat-status-toggle { display: inline-flex; align-items: center; gap: 6px; margin-top: 6px; }
.cat-status-toggle .toggle-label { font-size: 11px; font-weight: 600; }
.cat-status-toggle .toggle-open { color: #2e7d32; }
.cat-status-toggle .toggle-closed { color: #c62828; }
.toggle-switch { position: relative; width: 36px; height: 20px; }
.toggle-switch input { opacity: 0; width: 0; height: 0; }
.toggle-slider { position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background-color: #ccc; border-radius: 20px; transition: .3s; }
.toggle-slider:before { position: absolute; content: ''; height: 14px; width: 14px; left: 3px; bottom: 3px; background-color: white; border-radius: 50%; transition: .3s; }
.toggle-switch input:checked + .toggle-slider { background-color: #2ecc71; }
.toggle-switch input:checked + .toggle-slider:before { transform: translateX(16px); }
.sub-admin-row { display:flex;align-items:center;gap:12px;padding:12px 16px;border-bottom:1px solid var(--border); }
.sub-admin-row:last-child { border-bottom:none; }
.sub-admin-avatar { width:42px;height:42px;border-radius:50%;background:linear-gradient(135deg,#ff9800,#f57c00);color:#fff;display:flex;align-items:center;justify-content:center;font-size:16px;font-weight:700; }
.sub-admin-info { flex:1; }
.sub-admin-name { font-size:14px;font-weight:700; }
.sub-admin-meta { font-size:12px;color:var(--muted); }
.sub-admin-cats { font-size:11px;color:#4361ee;font-weight:600; }
@media (max-width: 768px) { .sidebar { transform: translateX(-100%); } .sidebar.open { transform: translateX(0); } .main-content { margin-left: 0; padding: 14px; } .hamburger { display: block; } .drag-grid { grid-template-columns: repeat(auto-fill, minmax(130px, 1fr)); } .orders-table { font-size: 12px; } }
@media (max-width: 480px) { .drag-grid { grid-template-columns: repeat(2, 1fr); } }
.sidebar-overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.35); z-index: 850; }
.sidebar-overlay.show { display: block; }
#save-toast { position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%); background: #222; color: #fff; padding: 10px 22px; border-radius: 30px; font-size: 13px; font-weight: 600; opacity: 0; transition: opacity 0.3s; z-index: 9999; pointer-events: none; }
#save-toast.show { opacity: 1; }
</style></head><body>
<nav class="topnav">
<div style="display:flex;align-items:center;gap:10px;">
<button class="hamburger" onclick="toggleSidebar()"><i class="bi bi-list"></i></button>
<span class="brand">&#128722; {% if is_super_admin %}Super Admin{% else %}Sub-Admin{% endif %} Dashboard</span>
</div>
<div class="topnav-actions">
{% if is_super_admin %}
<a href="/settings" class="btn-topnav"><i class="bi bi-translate"></i> <span>Texts</span></a>
<a href="/design" class="btn-topnav"><i class="bi bi-palette"></i> <span>Design</span></a>
{% endif %}
<a href="/" class="btn-topnav" target="_blank"><i class="bi bi-shop"></i> <span>Store</span></a>
<a href="/logout" class="btn-topnav danger"><i class="bi bi-box-arrow-right"></i> <span>Logout</span></a>
</div></nav>
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
<div class="nav-section">Inside {{ selected_cat.name }}</div>
<div class="nav-item active"><i class="bi bi-grid"></i> Sub-categories & Products</div>
{% else %}
<div class="nav-item active"><i class="bi bi-grid"></i> Main Categories</div>
{% endif %}
{% if is_super_admin %}
<div class="nav-section">Orders</div>
<a href="/dashboard?tab=orders" class="nav-item {{ 'active' if active_tab == 'orders' else '' }}"><i class="bi bi-receipt"></i> Orders ({{ orders|length }})</a>
<div class="nav-section">Workers</div>
<a href="/dashboard?tab=workers" class="nav-item {{ 'active' if active_tab == 'workers' else '' }}"><i class="bi bi-people"></i> Workers ({{ workers|length }})</a>
<div class="nav-section">Sub-Admins</div>
<a href="/dashboard?tab=sub_admins" class="nav-item {{ 'active' if active_tab == 'sub_admins' else '' }}"><i class="bi bi-shield-lock"></i> Sub-Admins ({{ sub_admins|length }})</a>
{% endif %}
<div class="nav-section">Settings</div>
{% if is_super_admin %}
<a href="/settings" class="nav-item"><i class="bi bi-translate"></i> Texts & Labels</a>
{% endif %}
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
{% if is_super_admin %}
<button id="tab-btn-orders" onclick="showTab('orders')" class="{{ 'active' if active_tab == 'orders' else '' }}"><i class="bi bi-receipt"></i> Orders</button>
<button id="tab-btn-workers" onclick="showTab('workers')" class="{{ 'active' if active_tab == 'workers' else '' }}"><i class="bi bi-people"></i> Workers</button>
<button id="tab-btn-sub_admins" onclick="showTab('sub_admins')" class="{{ 'active' if active_tab == 'sub_admins' else '' }}"><i class="bi bi-shield-lock"></i> Sub-Admins</button>
{% endif %}
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
<h5><i class="bi bi-folder"></i> {{ 'Sub-categories in ' + selected_cat.name + '' if selected_cat else 'Main Categories' }}</h5>
<div class="d-flex gap-2 flex-wrap">
<small class="text-muted d-flex align-items-center"><i class="bi bi-arrows-move me-1"></i> Drag to reorder</small>
<button class="btn btn-primary btn-sm" data-bs-toggle="modal" data-bs-target="#addCatModal"><i class="bi bi-plus-lg"></i> {{ 'Add Sub-category' if selected_cat else 'Add Category' }}</button>
</div></div>
<div class="panel-body">
{% if cats %}
<div class="drag-grid" id="cats-sortable">
{% for c in cats %}
<div class="drag-card" data-id="{{ c.id }}" data-kind="cat">
<span class="drag-handle" title="Drag">&#8999;</span>
{% if c.image %}<img src="/uploads/{{ c.image }}" alt="">{% else %}<div class="no-img-admin">No Image</div>{% endif %}
<div class="dc-body">
<div class="dc-name" title="{{ c.name }}">{{ c.name }}</div>
{% if show_status_feature %}
<div class="cat-status-toggle">
<span class="toggle-label {{ 'toggle-open' if c.is_open else 'toggle-closed' }}">{{ 'Open' if c.is_open else 'Closed' }}</span>
<label class="toggle-switch">
  <input type="checkbox" {{ 'checked' if c.is_open else '' }} onchange="toggleCatStatus({{ c.id }}, this.checked)">
  <span class="toggle-slider"></span>
</label>
</div>
{% endif %}
<div class="size-btns">
<button onclick="setSize('cat',{{ c.id }},'small',this)" class="{{ 'active' if c.card_size == 'small' else '' }}">S</button>
<button onclick="setSize('cat',{{ c.id }},'medium',this)" class="{{ 'active' if (not c.card_size or c.card_size == 'medium') else '' }}">M</button>
<button onclick="setSize('cat',{{ c.id }},'large',this)" class="{{ 'active' if c.card_size == 'large' else '' }}">L</button>
</div>
<div class="dc-actions mt-2">
<a href="/dashboard?cat={{ c.id }}" class="btn btn-outline-secondary btn-sm"><i class="bi bi-folder2-open"></i></a>
<button class="btn btn-outline-primary btn-sm" data-bs-toggle="modal" data-bs-target="#editCat{{ c.id }}"><i class="bi bi-pencil"></i></button>
<form method="post" action="/delete_cat/{{ c.id }}" style="display:inline" onsubmit="return confirm('Delete?')"><button class="btn btn-outline-danger btn-sm"><i class="bi bi-trash"></i></button></form>
</div></div></div>
<div class="modal fade" id="editCat{{ c.id }}" tabindex="-1">
<div class="modal-dialog"><div class="modal-content">
<form method="post" action="/edit_cat/{{ c.id }}" enctype="multipart/form-data">
<div class="modal-header"><h5 class="modal-title">Edit Category</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div>
<div class="modal-body">
<label class="form-label fw-bold">Name</label><input name="name" class="form-control mb-3" value="{{ c.name }}" required>
<label class="form-label fw-bold">Change Image</label><input type="file" name="image" class="form-control" accept="image/*">
</div>
<div class="modal-footer"><button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button><button type="submit" class="btn btn-primary">Save</button></div>
</form></div></div></div>
{% endfor %}
</div>
{% else %}<p class="text-muted text-center py-3">No categories yet.</p>{% endif %}
</div></div>

{% if selected_cat %}
<div class="panel">
<div class="panel-header">
<h5><i class="bi bi-bag"></i> Products in {{ selected_cat.name }} ({{ prods|length }})</h5>
<button class="btn btn-success btn-sm" data-bs-toggle="modal" data-bs-target="#addProdModal"><i class="bi bi-plus-lg"></i> Add Product</button>
</div>
<div class="panel-body">
{% if prods %}
<div class="drag-grid" id="prods-sortable">
{% for p in prods %}
<div class="drag-card" data-id="{{ p.id }}" data-kind="prod">
<span class="drag-handle">&#8999;</span>
{% if p.image %}<img src="/uploads/{{ p.image }}" alt="">{% else %}<div class="no-img-admin">No Image</div>{% endif %}
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
</div></div></div>
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
</form></div></div></div>
{% endfor %}
</div>
{% else %}<p class="text-muted text-center py-3">No products in this category.</p>{% endif %}
</div></div>
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
<thead><tr><th>#</th><th>Date</th><th>Phone</th><th>Address</th><th>Location</th><th>Status</th><th>Assigned</th><th>Items</th><th>Total</th><th>Actions</th></tr></thead>
<tbody>
{% for o in orders %}
<tr>
<td><strong>#{{ o.id }}</strong></td>
<td style="white-space:nowrap;font-size:12px;">{{ o.created_at }}</td>
<td>{{ o.phone }}</td>
<td>{{ o.address }}</td>
<td>
{% if o.latitude and o.longitude %}
<a class="map-link" href="https://www.google.com/maps?q={{ o.latitude }},{{ o.longitude }}" target="_blank">&#128205; Map</a>
<a class="map-link" style="background:#e3f2fd;color:#1976d2;" href="https://www.google.com/maps/dir?api=1&destination={{ o.latitude }},{{ o.longitude }}" target="_blank">&#128663; Directions</a>
{% else %}<span style="color:#ccc;font-size:12px;">-</span>{% endif %}
</td>
<td><span class="status-badge status-{{ o.status }}">{{ o.status|title }}</span></td>
<td>
{% if o.worker_name %}
<span class="worker-badge">{{ o.worker_name }}</span>
{% else %}
<select class="form-select form-select-sm" style="width:140px;" onchange="forwardOrder({{ o.id }}, this.value)">
<option value="">Forward to...</option>
{% for w in workers %}<option value="{{ w.id }}">{{ w.display_name }}</option>{% endfor %}
</select>
{% endif %}
</td>
<td style="white-space:pre-line;font-size:12px;max-width:180px;">{{ o.items }}</td>
<td><strong>{{ "%.0f"|format(o.total) }}</strong>{% if o.delivery_fee %}<div style="font-size:11px;color:#e65100;">+{{ "%.0f"|format(o.delivery_fee) }} delivery</div>{% endif %}</td>
<td>
<div class="d-flex gap-1 flex-wrap">
{% if o.worker_id %}<button class="unassign-btn" onclick="unassignOrder({{ o.id }})" title="Unassign"><i class="bi bi-arrow-return-left"></i></button>{% endif %}
<a href="/order/{{ o.id }}/download" class="btn btn-primary btn-sm" title="Download PNG"><i class="bi bi-image"></i></a>
<form method="post" action="/delete_order/{{ o.id }}" style="display:inline" onsubmit="return confirm('Delete?')"><button class="btn btn-danger btn-sm"><i class="bi bi-trash"></i></button></form>
</div>
</td>
</tr>
{% endfor %}
</tbody></table>
</div>
{% else %}<div class="text-center py-5 text-muted"><i class="bi bi-inbox" style="font-size:40px;"></i><p class="mt-2">No orders yet.</p></div>{% endif %}
</div></div></div>

<!-- TAB WORKERS -->
<div id="tab-workers" style="display:{{ 'block' if active_tab == 'workers' else 'none' }}">
<div class="panel mb-3">
<div class="panel-header">
<h5><i class="bi bi-people"></i> Delivery Workers ({{ workers|length }})</h5>
<button class="btn btn-primary btn-sm" data-bs-toggle="modal" data-bs-target="#addWorkerModal"><i class="bi bi-plus-lg"></i> Add Worker</button>
</div>
<div class="panel-body p-0">
{% if workers %}
{% for w in workers %}
<div class="worker-row">
<div class="worker-avatar">{{ w.display_name[0]|upper }}</div>
<div class="worker-info">
<div class="worker-name">{{ w.display_name }}</div>
<div class="worker-meta">@{{ w.username }} {% if w.phone %} | {{ w.phone }}{% endif %} | {{ w.order_count }} orders</div>
</div>
<div class="worker-balance">{{ "%.0f"|format(w.balance) }}</div>
<div class="worker-actions">
<form method="post" action="/toggle_worker/{{ w.id }}" style="display:inline">
<button class="btn btn-sm {{ 'btn-outline-success' if not w.active else 'btn-outline-secondary' }}" title="{{ 'Activate' if not w.active else 'Deactivate' }}"><i class="bi {{ 'bi-check-circle' if not w.active else 'bi-pause-circle' }}"></i></button>
</form>
<form method="post" action="/delete_worker/{{ w.id }}" style="display:inline" onsubmit="return confirm('Delete worker and unassign all their orders')"><button class="btn btn-outline-danger btn-sm"><i class="bi bi-trash"></i></button></form>
</div></div>
{% endfor %}
{% else %}<div class="text-center py-5 text-muted"><i class="bi bi-people" style="font-size:40px;"></i><p class="mt-2">No workers yet. Add your first delivery worker.</p></div>{% endif %}
</div></div></div>

<!-- TAB SUB-ADMINS -->
<div id="tab-sub_admins" style="display:{{ 'block' if active_tab == 'sub_admins' else 'none' }}">
<div class="panel mb-3">
<div class="panel-header">
<h5><i class="bi bi-shield-lock"></i> Sub-Admins ({{ sub_admins|length }})</h5>
<button class="btn btn-primary btn-sm" data-bs-toggle="modal" data-bs-target="#addSubAdminModal"><i class="bi bi-plus-lg"></i> Add Sub-Admin</button>
</div>
<div class="panel-body p-0">
{% if sub_admins %}
{% for sa in sub_admins %}
<div class="sub-admin-row">
<div class="sub-admin-avatar">{{ sa.display_name[0]|upper }}</div>
<div class="sub-admin-info">
<div class="sub-admin-name">{{ sa.display_name }}</div>
<div class="sub-admin-meta">@{{ sa.username }} {% if sa.phone %} | {{ sa.phone }}{% endif %}</div>
<div class="sub-admin-cats"><i class="bi bi-folder"></i> {{ sa.cat_count }} category(s)</div>
</div>
<div class="worker-actions">
<button class="btn btn-outline-primary btn-sm" data-bs-toggle="modal" data-bs-target="#editSubAdmin{{ sa.id }}"><i class="bi bi-pencil"></i></button>
<form method="post" action="/toggle_sub_admin/{{ sa.id }}" style="display:inline">
<button class="btn btn-sm {{ 'btn-outline-success' if not sa.active else 'btn-outline-secondary' }}" title="{{ 'Activate' if not sa.active else 'Deactivate' }}"><i class="bi {{ 'bi-check-circle' if not sa.active else 'bi-pause-circle' }}"></i></button>
</form>
<form method="post" action="/delete_sub_admin/{{ sa.id }}" style="display:inline" onsubmit="return confirm('Delete sub-admin')"><button class="btn btn-outline-danger btn-sm"><i class="bi bi-trash"></i></button></form>
</div></div>
<!-- Edit Sub-Admin Modal -->
<div class="modal fade" id="editSubAdmin{{ sa.id }}" tabindex="-1">
<div class="modal-dialog"><div class="modal-content">
<form method="post" action="/edit_sub_admin/{{ sa.id }}">
<div class="modal-header"><h5 class="modal-title">Edit Sub-Admin</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div>
<div class="modal-body">
<label class="form-label fw-bold">Display Name</label><input name="display_name" class="form-control mb-3" value="{{ sa.display_name }}" required>
<label class="form-label fw-bold">Password (leave empty to keep current)</label><input name="password" type="password" class="form-control mb-3" placeholder="New password">
<label class="form-label fw-bold">Phone</label><input name="phone" class="form-control mb-3" value="{{ sa.phone or '' }}" placeholder="Phone number">
<label class="form-label fw-bold">Assigned Categories</label>
<div style="max-height:200px;overflow-y:auto;border:1px solid #e0e0e0;border-radius:8px;padding:10px;">
{% for c in all_cats_list %}
<div class="form-check">
  <input class="form-check-input" type="checkbox" name="cat_ids" value="{{ c.id }}" id="sa_cat_{{ sa.id }}_{{ c.id }}" {{ 'checked' if c.id in sa.assigned_cat_ids else '' }}>
  <label class="form-check-label" for="sa_cat_{{ sa.id }}_{{ c.id }}" style="font-size:13px;">{{ c.name }}</label>
</div>
{% endfor %}
</div>
</div>
<div class="modal-footer"><button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button><button type="submit" class="btn btn-primary">Save</button></div>
</form></div></div></div>
{% endfor %}
{% else %}<div class="text-center py-5 text-muted"><i class="bi bi-shield-lock" style="font-size:40px;"></i><p class="mt-2">No sub-admins yet. Add your first sub-admin.</p></div>{% endif %}
</div></div></div>

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
</form></div></div></div>

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
</form></div></div></div>
{% endif %}

<!-- ADD WORKER MODAL -->
<div class="modal fade" id="addWorkerModal" tabindex="-1">
<div class="modal-dialog"><div class="modal-content">
<form method="post" action="/add_worker">
<div class="modal-header"><h5 class="modal-title">Add Delivery Worker</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div>
<div class="modal-body">
<label class="form-label fw-bold">Username</label><input name="username" class="form-control mb-3" placeholder="worker_username" required>
<label class="form-label fw-bold">Display Name</label><input name="display_name" class="form-control mb-3" placeholder="Worker Name" required>
<label class="form-label fw-bold">Password</label><input name="password" type="password" class="form-control mb-3" placeholder="Password" required>
<label class="form-label fw-bold">Phone (optional)</label><input name="phone" class="form-control" placeholder="Phone number">
</div>
<div class="modal-footer"><button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button><button type="submit" class="btn btn-primary">Add Worker</button></div>
</form></div></div></div>

<!-- ADD SUB-ADMIN MODAL -->
<div class="modal fade" id="addSubAdminModal" tabindex="-1">
<div class="modal-dialog"><div class="modal-content">
<form method="post" action="/add_sub_admin">
<div class="modal-header"><h5 class="modal-title">Add Sub-Admin</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div>
<div class="modal-body">
<label class="form-label fw-bold">Username</label><input name="username" class="form-control mb-3" placeholder="subadmin_username" required>
<label class="form-label fw-bold">Display Name</label><input name="display_name" class="form-control mb-3" placeholder="Sub-Admin Name" required>
<label class="form-label fw-bold">Password</label><input name="password" type="password" class="form-control mb-3" placeholder="Password" required>
<label class="form-label fw-bold">Phone (optional)</label><input name="phone" class="form-control mb-3" placeholder="Phone number">
<label class="form-label fw-bold">Assigned Categories</label>
<div style="max-height:200px;overflow-y:auto;border:1px solid #e0e0e0;border-radius:8px;padding:10px;">
{% for c in all_cats_list %}
<div class="form-check">
  <input class="form-check-input" type="checkbox" name="cat_ids" value="{{ c.id }}" id="new_sa_cat_{{ c.id }}">
  <label class="form-check-label" for="new_sa_cat_{{ c.id }}" style="font-size:13px;">{{ c.name }}</label>
</div>
{% endfor %}
</div>
</div>
<div class="modal-footer"><button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button><button type="submit" class="btn btn-primary">Add Sub-Admin</button></div>
</form></div></div></div>

<div id="save-toast">&#10003; Saved</div>
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
<script>
function toggleSidebar() {
  document.getElementById('sidebar').classList.toggle('open');
  document.getElementById('sidebar-overlay').classList.toggle('show');
}
function showTab(name) {
  var tabs = ['cats','orders','workers','sub_admins'];
  tabs.forEach(t => {
    var el = document.getElementById('tab-'+t);
    if(el) el.style.display = t===name?'block':'none';
    var btn = document.getElementById('tab-btn-'+t);
    if(btn) btn.className = t===name?'active':'';
  });
  history.replaceState(null, '', '/dashboard?tab='+name);
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
function toggleCatStatus(catId, isOpen) {
  fetch('/api/toggle_category_status',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({category_id:catId,is_open:isOpen?1:0})})
    .then(r=>r.json()).then(d=>{if(d.ok){showToast(isOpen?'Category opened':'Category closed');location.reload();}else{showToast('Error');}});
}
function forwardOrder(orderId, workerId) {
  if(!workerId) return;
  if(!confirm('Forward this order to the selected worker')) { event.target.value=''; return; }
  fetch('/api/forward_order',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({order_id:orderId,worker_id:parseInt(workerId)})})
  .then(r=>r.json()).then(d=>{if(d.ok){showToast('Order forwarded');location.reload();}else{showToast('Error: '+d.error);}});
}
function unassignOrder(orderId) {
  if(!confirm('Unassign this order from the worker')) return;
  fetch('/api/unassign_order',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({order_id:orderId})})
  .then(r=>r.json()).then(d=>{if(d.ok){showToast('Order unassigned');location.reload();}else{showToast('Error');}});
}
</script>
</body></html>"""

@app.route("/dashboard")
def dashboard():
    if not require_admin():
        return redirect("/admin")

    is_super = is_super_admin()
    is_sub = is_sub_admin()

    orders = q("SELECT * FROM orders ORDER BY id DESC")
    workers = q("SELECT * FROM workers ORDER BY id DESC")
    worker_map = {w["id"]: w for w in workers}
    orders_enriched = []
    for o in orders:
        o_dict = dict(o)
        if o_dict.get("worker_id") and o_dict["worker_id"] in worker_map:
            o_dict["worker_name"] = worker_map[o_dict["worker_id"]]["display_name"]
        else:
            o_dict["worker_name"] = None
        orders_enriched.append(o_dict)

    selected_cat_id = request.args.get("cat", type=int)
    selected_cat = None
    prods = []
    breadcrumb = []

    if selected_cat_id:
        selected_cat = q("SELECT * FROM categories WHERE id=?", (selected_cat_id,), one=True)

    if selected_cat:
        prods = q("SELECT * FROM products WHERE category_id=? ORDER BY sort_order ASC, id ASC", (selected_cat_id,))
        cats = q("SELECT * FROM categories WHERE parent_id=? ORDER BY sort_order ASC, id ASC", (selected_cat_id,))
        cur = selected_cat
        while cur:
            breadcrumb.insert(0, {"id": cur["id"], "name": cur["name"]})
            cur = q("SELECT * FROM categories WHERE id=?", (cur["parent_id"],), one=True) if cur["parent_id"] else None
    else:
        cats = q("SELECT * FROM categories WHERE parent_id IS NULL ORDER BY sort_order ASC, id ASC")

    # Filter categories for sub-admin
    if is_sub:
        managed_ids = get_managed_categories()
        cats = [c for c in cats if c["id"] in managed_ids]
        if selected_cat and selected_cat_id not in managed_ids:
            return redirect("/dashboard")

    all_cats_raw = q("SELECT * FROM categories")
    cat_map = {c["id"]: c for c in all_cats_raw}
    def cat_path(c):
        parts=[c["name"]]; parent=c["parent_id"]
        while parent:
            p=cat_map.get(parent);
            if not p: break
            parts.insert(0,p["name"]); parent=p["parent_id"]
        return " / ".join(parts)
    all_cats = [{"id": c["id"], "label": cat_path(c)} for c in all_cats_raw]

    total_prods = len(q("SELECT id FROM products"))
    total_revenue = sum(float(o["total"]) for o in orders)
    active_tab = request.args.get("tab", "cats")
    if active_tab not in ("cats", "orders", "workers", "sub_admins"):
        active_tab = "cats"

    workers_enriched = []
    for w in workers:
        w_dict = dict(w)
        assigned = q("SELECT COUNT(*) as cnt, COALESCE(SUM(total), 0) as bal FROM orders WHERE worker_id=?", (w["id"],), one=True)
        w_dict["order_count"] = assigned["cnt"] if assigned else 0
        w_dict["balance"] = float(assigned["bal"]) if assigned and assigned["bal"] else 0.0
        workers_enriched.append(w_dict)

    # Sub-admins data (super admin only)
    sub_admins_enriched = []
    if is_super:
        sub_admins = q("SELECT * FROM sub_admins ORDER BY id DESC")
        for sa in sub_admins:
            sa_dict = dict(sa)
            cat_count = q("SELECT COUNT(*) as cnt FROM sub_admin_categories WHERE sub_admin_id=?", (sa["id"],), one=True)
            sa_dict["cat_count"] = cat_count["cnt"] if cat_count else 0
            assigned = q("SELECT category_id FROM sub_admin_categories WHERE sub_admin_id=?", (sa["id"],))
            sa_dict["assigned_cat_ids"] = [a["category_id"] for a in assigned] if assigned else []
            sub_admins_enriched.append(sa_dict)
    else:
        sub_admins_enriched = []

    show_status_feature = get_setting("show_category_status") == "1"
    all_cats_list = [{"id": c["id"], "name": c["name"]} for c in all_cats_raw]

    return render_template_string(DASHBOARD_TEMPLATE,
        cats=cats, all_cats=all_cats, orders=orders_enriched,
        selected_cat=selected_cat, prods=prods, breadcrumb=breadcrumb,
        total_prods=total_prods, total_revenue=total_revenue, active_tab=active_tab,
        workers=workers_enriched, sub_admins=sub_admins_enriched,
        is_super_admin=is_super, is_sub_admin=is_sub,
        show_status_feature=show_status_feature, all_cats_list=all_cats_list)

# ============================================================
# WORKER DASHBOARD
# ============================================================
WORKER_DASHBOARD_TEMPLATE = """<!DOCTYPE html>
<html lang="en" dir="ltr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Worker Dashboard</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.0/font/bootstrap-icons.css">
<style>
:root { --accent: #4361ee; --success: #2ecc71; --bg: #f0f2f5; --card: #fff; --border: #e8e8e8; --text: #1a1a2e; --muted: #6c757d; }
body { font-family: Arial, sans-serif; background: var(--bg); color: var(--text); margin: 0; padding: 0; }
.topbar { background: var(--accent); padding: 14px 20px; display: flex; align-items: center; justify-content: space-between; position: sticky; top: 0; z-index: 100; }
.topbar h1 { color: #fff; font-size: 17px; font-weight: 700; margin: 0; }
.topbar .logout { color: #fff; text-decoration: none; background: rgba(255,255,255,0.15); border-radius: 8px; padding: 7px 14px; font-size: 13px; font-weight: 600; }
.main { max-width: 900px; margin: 0 auto; padding: 20px 16px 60px; }
.balance-card { background: linear-gradient(135deg, #4361ee 0%, #3a0ca3 100%); border-radius: 16px; padding: 24px 20px; color: #fff; margin-bottom: 20px; box-shadow: 0 4px 16px rgba(67,97,238,0.25); }
.balance-card .balance-label { font-size: 13px; opacity: 0.9; margin-bottom: 6px; }
.balance-card .balance-value { font-size: 36px; font-weight: 700; }
.balance-card .balance-meta { font-size: 13px; opacity: 0.8; margin-top: 4px; }
.order-card { background: var(--card); border-radius: 14px; box-shadow: 0 2px 10px rgba(0,0,0,0.06); margin-bottom: 14px; overflow: hidden; }
.order-header { padding: 14px 18px; border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: space-between; }
.order-header .order-id { font-size: 15px; font-weight: 700; }
.order-header .order-date { font-size: 12px; color: var(--muted); }
.order-body { padding: 14px 18px; }
.order-row { display: flex; justify-content: space-between; align-items: center; padding: 6px 0; font-size: 13px; }
.order-row .label { color: var(--muted); font-weight: 600; }
.order-row .value { color: var(--text); font-weight: 700; }
.order-items { background: #f8f9fc; border-radius: 10px; padding: 12px 14px; margin-top: 10px; font-size: 13px; white-space: pre-line; line-height: 1.6; }
.order-footer { padding: 12px 18px; border-top: 1px solid var(--border); display: flex; gap: 8px; }
.map-btn { background: #e8f5e9; color: #2e7d32; border: none; border-radius: 8px; padding: 8px 14px; font-size: 13px; font-weight: 600; text-decoration: none; display: inline-flex; align-items: center; gap: 6px; }
.png-btn { background: #e3f2fd; color: #1976d2; border: none; border-radius: 8px; padding: 8px 14px; font-size: 13px; font-weight: 600; text-decoration: none; display: inline-flex; align-items: center; gap: 6px; }
.empty-state { text-align: center; padding: 60px 20px; }
.empty-state .icon { font-size: 60px; margin-bottom: 12px; }
.empty-state h3 { font-size: 18px; font-weight: 700; color: var(--text); }
.empty-state p { color: var(--muted); font-size: 14px; }
</style></head><body>
<div class="topbar">
<h1>&#128640; {{ worker_name }}'s Dashboard</h1>
<a href="/worker/logout" class="logout"><i class="bi bi-box-arrow-right"></i> Logout</a>
</div>
<div class="main">
<div class="balance-card">
<div class="balance-label"><i class="bi bi-wallet2"></i> Your Balance</div>
<div class="balance-value">{{ "%.0f"|format(balance) }}</div>
<div class="balance-meta">From {{ orders|length }} assigned order(s)</div>
</div>
<h5 style="font-size:16px;font-weight:700;margin-bottom:14px;"><i class="bi bi-receipt"></i> Assigned Orders</h5>
{% if orders %}
{% for o in orders %}
<div class="order-card">
<div class="order-header">
<span class="order-id">Order #{{ o.id }}</span>
<span class="order-date">{{ o.created_at }}</span>
</div>
<div class="order-body">
<div class="order-row"><span class="label">Phone</span><span class="value">{{ o.phone }}</span></div>
<div class="order-row"><span class="label">Address</span><span class="value">{{ o.address }}</span></div>
{% if o.details %}
<div class="order-row"><span class="label">Notes</span><span class="value">{{ o.details }}</span></div>
{% endif %}
<div class="order-row"><span class="label">Subtotal</span><span class="value">{{ "%.0f"|format(o.total - (o.delivery_fee or 0)) }}</span></div>
{% if o.delivery_fee %}<div class="order-row"><span class="label">Delivery</span><span class="value" style="color:#e65100;">{{ "%.0f"|format(o.delivery_fee) }}</span></div>{% endif %}
<div class="order-row"><span class="label">Total</span><span class="value" style="color:#4361ee;font-size:16px;">{{ "%.0f"|format(o.total) }}</span></div>
<div class="order-items">{{ o.items }}</div>
</div>
<div class="order-footer">
{% if o.latitude and o.longitude %}
<a class="map-btn" href="https://www.google.com/maps?q={{ o.latitude }},{{ o.longitude }}" target="_blank"><i class="bi bi-geo-alt"></i> Open Map</a>
<a class="map-btn" style="background:#e3f2fd;color:#1976d2;" href="https://www.google.com/maps/dir?api=1&destination={{ o.latitude }},{{ o.longitude }}" target="_blank"><i class="bi bi-car-front"></i> Directions</a>
{% endif %}
<a class="png-btn" href="/order/{{ o.id }}/download" target="_blank"><i class="bi bi-image"></i> Download Receipt</a>
</div></div>
{% endfor %}
{% else %}
<div class="empty-state">
<div class="icon">&#128230;</div>
<h3>No orders assigned yet</h3>
<p>When the admin forwards an order to you, it will appear here.</p>
</div>
{% endif %}
</div></body></html>"""

@app.route("/worker/dashboard")
def worker_dashboard():
    if not require_worker():
        return redirect("/worker/login")
    worker_id = session.get("worker_id")
    worker = q("SELECT * FROM workers WHERE id=?", (worker_id,), one=True)
    if not worker:
        session.clear()
        return redirect("/worker/login")
    orders = q("SELECT * FROM orders WHERE worker_id=? ORDER BY id DESC", (worker_id,))
    balance_row = q("SELECT COALESCE(SUM(total), 0) as bal FROM orders WHERE worker_id=?", (worker_id,), one=True)
    balance = float(balance_row["bal"]) if balance_row and balance_row["bal"] else 0.0
    return render_template_string(WORKER_DASHBOARD_TEMPLATE, worker_name=worker["display_name"], orders=orders, balance=balance)

# ============================================================
# DESIGN PANEL
# ============================================================
@app.route("/design", methods=["GET", "POST"])
def design():
    if not require_super_admin():
        return redirect("/admin")
    if request.method == "POST":
        bg = save(request.files.get("bg"))
        ov = save(request.files.get("overlay"))
        an = save(request.files.get("anim"))
        if bg or ov or an:
            d = q("SELECT * FROM design ORDER BY id DESC LIMIT 1", one=True)
            if d:
                bg = bg or d["background"]; ov = ov or d["overlay"]; an = an or d["animation"]
            q("INSERT INTO design(background, overlay, animation) VALUES(?,?,?)", (bg, ov, an))
        return redirect("/design")
    d = q("SELECT * FROM design ORDER BY id DESC LIMIT 1", one=True)
    return render_template_string("""<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Design</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"><style>body{font-family:Arial,sans-serif;background:#f0f2f5;}.card{border-radius:14px;border:none;box-shadow:0 2px 10px rgba(0,0,0,0.07);}</style></head><body><div class="container mt-4" style="max-width:600px"><div class="d-flex justify-content-between align-items-center mb-3"><h4>&#127912; Design Panel</h4><a href="/dashboard" class="btn btn-secondary btn-sm">Back</a></div><div class="card p-4 mb-3"><form method="post" enctype="multipart/form-data"><div class="mb-3"><label class="form-label fw-bold">Background Image</label><input type="file" name="bg" class="form-control" accept="image/*"></div><div class="mb-3"><label class="form-label fw-bold">Overlay Image</label><input type="file" name="overlay" class="form-control" accept="image/png,image/gif"></div><div class="mb-3"><label class="form-label fw-bold">Animation (GIF)</label><input type="file" name="anim" class="form-control" accept="image/gif,image/png"></div><button class="btn btn-primary w-100">Save Design</button></form></div>{% if d %}<div class="card p-4"><h5 class="mb-3">Current Design</h5><ul class="list-group list-group-flush"><li class="list-group-item"><strong>Background:</strong> {% if d.background %}<a href="/uploads/{{ d.background }}" target="_blank">{{ d.background }}</a>{% else %}None{% endif %}</li><li class="list-group-item"><strong>Overlay:</strong> {% if d.overlay %}<a href="/uploads/{{ d.overlay }}" target="_blank">{{ d.overlay }}</a>{% else %}None{% endif %}</li><li class="list-group-item"><strong>Animation:</strong> {% if d.animation %}<a href="/uploads/{{ d.animation }}" target="_blank">{{ d.animation }}</a>{% else %}None{% endif %}</li></ul></div>{% endif %}</div></body></html>""", d=d)

# ============================================================
# SUB-ADMIN CRUD
# ============================================================
@app.route("/add_sub_admin", methods=["POST"])
def add_sub_admin():
    if not require_super_admin():
        return redirect("/admin")
    username = request.form.get("username", "").strip().lower()
    display_name = request.form.get("display_name", "").strip()
    password = request.form.get("password", "").strip()
    phone = request.form.get("phone", "").strip()
    cat_ids = request.form.getlist("cat_ids")

    if not username or not display_name or not password:
        return redirect("/dashboard?tab=sub_admins")
    existing = q("SELECT id FROM sub_admins WHERE username=?", (username,), one=True)
    if existing:
        return "Username already exists", 400

    q("INSERT INTO sub_admins(username, password_hash, display_name, phone, created_at, active) VALUES(?,?,?,?,?,1)",
      (username, hash_password(password), display_name, phone, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))

    sub_admin = q("SELECT id FROM sub_admins WHERE username=?", (username,), one=True)
    if sub_admin and cat_ids:
        for cat_id in cat_ids:
            q("INSERT OR IGNORE INTO sub_admin_categories(sub_admin_id, category_id) VALUES(?,?)", (sub_admin["id"], cat_id))

    return redirect("/dashboard?tab=sub_admins")

@app.route("/edit_sub_admin/<int:id>", methods=["POST"])
def edit_sub_admin(id):
    if not require_super_admin():
        return redirect("/admin")
    display_name = request.form.get("display_name", "").strip()
    password = request.form.get("password", "").strip()
    phone = request.form.get("phone", "").strip()
    cat_ids = request.form.getlist("cat_ids")

    if not display_name:
        return redirect("/dashboard?tab=sub_admins")

    if password:
        q("UPDATE sub_admins SET display_name=?, password_hash=?, phone=? WHERE id=?",
          (display_name, hash_password(password), phone, id))
    else:
        q("UPDATE sub_admins SET display_name=?, phone=? WHERE id=?",
          (display_name, phone, id))

    q("DELETE FROM sub_admin_categories WHERE sub_admin_id=?", (id,))
    if cat_ids:
        for cat_id in cat_ids:
            q("INSERT OR IGNORE INTO sub_admin_categories(sub_admin_id, category_id) VALUES(?,?)", (id, cat_id))

    return redirect("/dashboard?tab=sub_admins")

@app.route("/toggle_sub_admin/<int:id>", methods=["POST"])
def toggle_sub_admin(id):
    if not require_super_admin():
        return redirect("/admin")
    sub_admin = q("SELECT active FROM sub_admins WHERE id=?", (id,), one=True)
    if sub_admin:
        new_active = 0 if sub_admin["active"] else 1
        q("UPDATE sub_admins SET active=? WHERE id=?", (new_active, id))
    return redirect("/dashboard?tab=sub_admins")

@app.route("/delete_sub_admin/<int:id>", methods=["POST"])
def delete_sub_admin(id):
    if not require_super_admin():
        return redirect("/admin")
    q("DELETE FROM sub_admin_categories WHERE sub_admin_id=?", (id,))
    q("DELETE FROM sub_admins WHERE id=?", (id,))
    return redirect("/dashboard?tab=sub_admins")

# ============================================================
# WORKER CRUD
# ============================================================
@app.route("/add_worker", methods=["POST"])
def add_worker():
    if not require_super_admin():
        return redirect("/admin")
    username = request.form.get("username", "").strip().lower()
    display_name = request.form.get("display_name", "").strip()
    password = request.form.get("password", "").strip()
    phone = request.form.get("phone", "").strip()
    if not username or not display_name or not password:
        return redirect("/dashboard?tab=workers")
    existing = q("SELECT id FROM workers WHERE username=?", (username,), one=True)
    if existing:
        return "Username already exists", 400
    q("INSERT INTO workers(username, password_hash, display_name, phone, created_at, active) VALUES(?,?,?,?,?,1)",
      (username, hash_password(password), display_name, phone, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    return redirect("/dashboard?tab=workers")

@app.route("/toggle_worker/<int:id>", methods=["POST"])
def toggle_worker(id):
    if not require_super_admin():
        return redirect("/admin")
    worker = q("SELECT active FROM workers WHERE id=?", (id,), one=True)
    if worker:
        new_active = 0 if worker["active"] else 1
        q("UPDATE workers SET active=? WHERE id=?", (new_active, id))
    return redirect("/dashboard?tab=workers")

@app.route("/delete_worker/<int:id>", methods=["POST"])
def delete_worker(id):
    if not require_super_admin():
        return redirect("/admin")
    q("UPDATE orders SET worker_id=NULL, status='pending' WHERE worker_id=?", (id,))
    q("DELETE FROM workers WHERE id=?", (id,))
    return redirect("/dashboard?tab=workers")

# ============================================================
# CRUD ROUTES (with permission checks for sub-admins)
# ============================================================
@app.route("/edit_cat/<int:id>", methods=["POST"])
def edit_cat(id):
    if not require_admin():
        return redirect("/admin")
    if is_sub_admin() and not can_manage_category(id):
        return "Permission denied", 403
    name = request.form.get("name","").strip()
    if not name:
        return redirect("/dashboard")
    cat = q("SELECT * FROM categories WHERE id=?", (id,), one=True)
    img = save(request.files.get("image"))
    if img:
        q("UPDATE categories SET name=?, image=? WHERE id=?", (name, img, id))
    else:
        q("UPDATE categories SET name=? WHERE id=?", (name, id))
    parent_id = cat["parent_id"] if cat else None
    return redirect(f"/dashboard?cat={parent_id}" if parent_id else "/dashboard")

@app.route("/edit_prod/<int:id>", methods=["POST"])
def edit_prod(id):
    if not require_admin():
        return redirect("/admin")
    name   = request.form.get("name","").strip()
    cat_id = request.form.get("cat")
    try:
        price = float(request.form.get("price",0))
        if price < 0: raise ValueError
    except ValueError:
        return redirect("/dashboard")
    if not name or not cat_id:
        return redirect("/dashboard")

    if is_sub_admin():
        prod = q("SELECT category_id FROM products WHERE id=?", (id,), one=True)
        if prod and not can_manage_category(prod["category_id"]):
            return "Permission denied", 403
        if not can_manage_category(int(cat_id)):
            return "Permission denied", 403

    img = save(request.files.get("image"))
    if img:
        q("UPDATE products SET name=?, price=?, image=?, category_id=? WHERE id=?", (name, price, img, cat_id, id))
    else:
        q("UPDATE products SET name=?, price=?, category_id=? WHERE id=?", (name, price, cat_id, id))
    return redirect(f"/dashboard?cat={cat_id}")

@app.route("/add_cat", methods=["POST"])
def add_cat():
    if not require_admin():
        return redirect("/admin")
    name = request.form.get("name","").strip()
    if not name:
        return redirect("/dashboard")
    parent = request.form.get("parent","").strip()
    parent_id = int(parent) if parent else None

    if is_sub_admin() and parent_id is not None and not can_manage_category(parent_id):
        return "Permission denied", 403

    if parent_id is None:
        max_order = q("SELECT MAX(sort_order) as m FROM categories WHERE parent_id IS NULL", one=True)
    else:
        max_order = q("SELECT MAX(sort_order) as m FROM categories WHERE parent_id=?", (parent_id,), one=True)
    new_order = (max_order["m"] or 0) + 1
    q("INSERT INTO categories(name, image, parent_id, sort_order, is_open) VALUES(?,?,?,?,1)",
      (name, save(request.files.get("image")), parent_id, new_order))

    if is_sub_admin() and parent_id is not None:
        new_cat = q("SELECT id FROM categories WHERE name=? AND parent_id=? ORDER BY id DESC LIMIT 1", (name, parent_id), one=True)
        if new_cat:
            q("INSERT OR IGNORE INTO sub_admin_categories(sub_admin_id, category_id) VALUES(?,?)",
              (session.get("sub_admin_id"), new_cat["id"]))

    return redirect(f"/dashboard?cat={parent_id}" if parent_id else "/dashboard")

@app.route("/add_prod", methods=["POST"])
def add_prod():
    if not require_admin():
        return redirect("/admin")
    name   = request.form.get("name","").strip()
    cat_id = request.form.get("cat")
    try:
        price = float(request.form.get("price",0))
        if price < 0: raise ValueError
    except ValueError:
        return redirect("/dashboard")
    if not name or not cat_id:
        return redirect("/dashboard")

    if is_sub_admin() and not can_manage_category(int(cat_id)):
        return "Permission denied", 403

    max_order = q("SELECT MAX(sort_order) as m FROM products WHERE category_id=?", (cat_id,), one=True)
    new_order = (max_order["m"] or 0) + 1
    q("INSERT INTO products(name, price, image, category_id, sort_order) VALUES(?,?,?,?,?)",
      (name, price, save(request.files.get("image")), cat_id, new_order))
    return redirect(f"/dashboard?cat={cat_id}")

@app.route("/delete_cat/<int:id>", methods=["POST"])
def del_cat(id):
    if not require_admin():
        return redirect("/admin")
    if is_sub_admin() and not can_manage_category(id):
        return "Permission denied", 403
    cat = q("SELECT * FROM categories WHERE id=?", (id,), one=True)
    parent_id = cat["parent_id"] if cat else None
    def delete_recursive(cid):
        for sub in q("SELECT id FROM categories WHERE parent_id=?", (cid,)): delete_recursive(sub["id"])
        q("DELETE FROM products WHERE category_id=?", (cid,))
        q("DELETE FROM sub_admin_categories WHERE category_id=?", (cid,))
        q("DELETE FROM categories WHERE id=?", (cid,))
    delete_recursive(id)
    return redirect(f"/dashboard?cat={parent_id}" if parent_id else "/dashboard")

@app.route("/delete_prod/<int:id>", methods=["POST"])
def del_prod(id):
    if not require_admin():
        return redirect("/admin")
    p = q("SELECT category_id FROM products WHERE id=?", (id,), one=True)
    if p and is_sub_admin() and not can_manage_category(p["category_id"]):
        return "Permission denied", 403
    q("DELETE FROM products WHERE id=?", (id,))
    cat_id = p["category_id"] if p else None
    return redirect(f"/dashboard?cat={cat_id}" if cat_id else "/dashboard")

# ============================================================
# ORDER DOWNLOAD & DELETE
# ============================================================
@app.route("/order/<int:id>/download")
def download_order(id):
    if not require_admin() and not require_worker():
        return redirect("/admin")
    if require_worker():
        o = q("SELECT * FROM orders WHERE id=? AND worker_id=?", (id, session.get("worker_id")), one=True)
    else:
        o = q("SELECT * FROM orders WHERE id=?", (id,), one=True)
    if not o:
        return redirect("/dashboard")
    order_dict = dict(o)
    png_buf = generate_order_png(order_dict)
    return Response(
        png_buf.read(),
        mimetype="image/png",
        headers={"Content-Disposition": f"attachment; filename=order_{o['id']}.png"}
    )

@app.route("/delete_order/<int:id>", methods=["POST"])
def del_order(id):
    if not require_super_admin():
        return redirect("/admin")
    q("DELETE FROM orders WHERE id=?", (id,))
    return redirect("/dashboard")

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
    print(f"  Worker:  http://{local_ip}:{port}/worker/login")
    print(f"  Sub-Admin: http://{local_ip}:{port}/sub_admin/login")
    print("=" * 45)
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG","false").lower()=="true")
