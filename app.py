import os
import json
import random
import secrets
import datetime
import smtplib
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, jsonify
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "coachpage-dz-secret-2024-XK9!")

_database_url = os.environ.get("DATABASE_URL", "").strip()
if _database_url:
    # Postgres (Supabase) — production. Normalize old-style scheme if present.
    if _database_url.startswith("postgres://"):
        _database_url = _database_url.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = _database_url
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
    }
else:
    # SQLite — local dev fallback only.
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{os.path.join(BASE_DIR, 'coachpage.db')}"
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "connect_args": {"check_same_thread": False, "timeout": 30},
        "pool_size": 30,
        "max_overflow": 60,
        "pool_timeout": 20,
        "pool_recycle": 1800,
        "pool_pre_ping": True,
    }
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["UPLOAD_FOLDER"] = os.path.join(BASE_DIR, "static", "uploads")
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("SESSION_COOKIE_SECURE", "false").lower() == "true"
app.config["PERMANENT_SESSION_LIFETIME"] = datetime.timedelta(days=30)

# ── SIMPLE IN-MEMORY CACHE ────────────────────────────────────────
_cache: dict = {}

def cache_get(key: str):
    entry = _cache.get(key)
    if not entry:
        return None
    if datetime.datetime.utcnow() > entry["expires"]:
        _cache.pop(key, None)
        return None
    return entry["value"]

def cache_set(key: str, value, ttl_seconds: int = 60):
    _cache[key] = {"value": value, "expires": datetime.datetime.utcnow() + datetime.timedelta(seconds=ttl_seconds)}

CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin2024")
GMAIL_USER = os.environ.get("GMAIL_USER", "anissoanis62@gmail.com")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")


def upload_coach_photo(file_storage, coach_id):
    """يرفع صورة المدرب لـ Supabase Storage ويرجع الرابط العام، أو None عند الفشل."""
    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        return None
    ext = file_storage.filename.rsplit(".", 1)[-1].lower() if "." in file_storage.filename else "jpg"
    if ext not in ("jpg", "jpeg", "png", "webp"):
        return None
    mt = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}[ext]
    path = f"coach_{coach_id}.{ext}"
    try:
        r = requests.post(
            f"{SUPABASE_URL}/storage/v1/object/coach-photos/{path}",
            headers={"Authorization": f"Bearer {SUPABASE_ANON_KEY}", "apikey": SUPABASE_ANON_KEY,
                     "Content-Type": mt, "x-upsert": "true"},
            data=file_storage.read(), timeout=15,
        )
        if r.status_code in (200, 201):
            return f"{SUPABASE_URL}/storage/v1/object/public/coach-photos/{path}?v={int(datetime.datetime.utcnow().timestamp())}"
    except Exception:
        pass
    return None

# ── IN-MEMORY ADMIN RESET TOKEN ───────────────────────────────────
# { token: expires_datetime }  — no DB needed for single admin
_admin_reset_tokens: dict = {}

def send_reset_email(to_email: str, reset_url: str, name: str = "") -> bool:
    """Send password reset email via Gmail SMTP. Returns True on success."""
    if not GMAIL_APP_PASSWORD:
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "إعادة تعيين كلمة السر — CoachPage DZ"
        msg["From"] = f"CoachPage DZ <{GMAIL_USER}>"
        msg["To"] = to_email

        greeting = f"مرحباً {name}،" if name else "مرحباً،"
        html = f"""
<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f5f5f5;font-family:Arial,sans-serif;">
  <div style="max-width:480px;margin:40px auto;background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,0.08);">
    <div style="background:#121212;padding:28px 32px;text-align:center;">
      <div style="display:inline-block;background:#22c55e;border-radius:12px;padding:12px 20px;">
        <span style="color:#fff;font-size:18px;font-weight:900;letter-spacing:1px;">CoachPage DZ</span>
      </div>
    </div>
    <div style="padding:32px;">
      <h2 style="color:#121212;font-size:20px;margin:0 0 12px;">إعادة تعيين كلمة السر</h2>
      <p style="color:#555;font-size:14px;line-height:1.7;margin:0 0 24px;">{greeting}<br>
      وصلنا طلب إعادة تعيين كلمة السر ديالك. اضغط الزر باه تختار كلمة سر جديدة:</p>
      <div style="text-align:center;margin:28px 0;">
        <a href="{reset_url}" style="display:inline-block;background:#22c55e;color:#fff;text-decoration:none;font-size:16px;font-weight:700;padding:16px 36px;border-radius:12px;">
          إعادة تعيين كلمة السر
        </a>
      </div>
      <div style="background:#f8f8f8;border-radius:10px;padding:14px 16px;margin-bottom:20px;">
        <p style="color:#888;font-size:12px;margin:0;line-height:1.6;">
          ⏱️ هذا الرابط صالح لمدة <strong>ساعة واحدة</strong> فقط.<br>
          🔒 إذا ما طلبتيش هذا، تجاهل الإيميل — كلمة السر ديالك بخير.
        </p>
      </div>
      <p style="color:#bbb;font-size:11px;margin:0;text-align:center;">CoachPage DZ · منصة التدريب الجزائرية</p>
    </div>
  </div>
</body>
</html>"""
        text = f"{greeting}\n\nرابط إعادة تعيين كلمة السر:\n{reset_url}\n\nصالح ساعة واحدة فقط."
        msg.attach(MIMEText(text, "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_USER, to_email, msg.as_string())
        return True
    except Exception:
        return False

# ── RATE LIMITING (in-memory, per IP) ───────────────────────────
_RATE: dict = {}

def _rl_key(prefix: str, ip: str) -> str:
    return f"{prefix}:{ip}"

def rl_check(prefix: str, max_attempts: int = 5, lockout_minutes: int = 30) -> tuple:
    ip = request.remote_addr or "unknown"
    key = _rl_key(prefix, ip)
    now = datetime.datetime.utcnow()
    rec = _RATE.get(key, {"count": 0, "until": None})
    if rec["until"] and now < rec["until"]:
        remaining = int((rec["until"] - now).total_seconds() / 60) + 1
        return False, remaining
    return True, 0

def rl_fail(prefix: str, max_attempts: int = 5, lockout_minutes: int = 30):
    ip = request.remote_addr or "unknown"
    key = _rl_key(prefix, ip)
    now = datetime.datetime.utcnow()
    rec = _RATE.get(key, {"count": 0, "until": None})
    rec["count"] = rec.get("count", 0) + 1
    if rec["count"] >= max_attempts:
        rec["until"] = now + datetime.timedelta(minutes=lockout_minutes)
        rec["count"] = 0
    _RATE[key] = rec

def rl_clear(prefix: str):
    ip = request.remote_addr or "unknown"
    _RATE.pop(_rl_key(prefix, ip), None)

db = SQLAlchemy(app)


# ═══════════════════════════ MODELS ════════════════════════════

class PlatformSettings(db.Model):
    __tablename__ = "platform_settings"
    id = db.Column(db.Integer, primary_key=True)
    owner_ccp = db.Column(db.String(100), nullable=False, default="")
    owner_whatsapp = db.Column(db.String(30), nullable=False, default="")
    coach_monthly_price = db.Column(db.Integer, nullable=False, default=4200)
    platform_name = db.Column(db.String(100), nullable=False, default="CoachPage DZ")
    admin_password_hash = db.Column(db.String(256), nullable=True, default=None)


class Coach(db.Model):
    __tablename__ = "coaches"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    full_name = db.Column(db.String(120), nullable=False, default="")
    password_hash = db.Column(db.String(256), nullable=False)
    phone_number = db.Column(db.String(20), nullable=True, default="")
    email = db.Column(db.String(120), nullable=True, default="")
    baridimob_ccp = db.Column(db.String(50), nullable=False, default="")
    edahabia_card = db.Column(db.String(50), nullable=True, default="")
    whatsapp_number = db.Column(db.String(20), nullable=False, default="")
    bio = db.Column(db.Text, nullable=False, default="")
    subscription_price = db.Column(db.Integer, nullable=False, default=4000)
    status = db.Column(db.String(20), nullable=False, default="pending")
    registered_at = db.Column(db.Date, nullable=False, default=datetime.date.today)
    trial_expires_at = db.Column(db.Date, nullable=True, default=None)
    admin_notes = db.Column(db.Text, nullable=True, default="")
    reset_token = db.Column(db.String(80), nullable=True, default=None)
    reset_token_expires = db.Column(db.DateTime, nullable=True, default=None)
    specialty = db.Column(db.String(120), nullable=False, default="")
    referral_slug = db.Column(db.String(120), nullable=True, default="")
    photo_url = db.Column(db.String(500), nullable=True, default="")
    clients = db.relationship("Client", backref="coach", lazy=True)

    def trial_status(self):
        if not self.trial_expires_at:
            return "none"
        today = datetime.date.today()
        if today > self.trial_expires_at:
            return "expired"
        days_left = (self.trial_expires_at - today).days
        if days_left <= 3:
            return "expiring_soon"
        return "active"

    def trial_days_left(self):
        if not self.trial_expires_at:
            return None
        return max(0, (self.trial_expires_at - datetime.date.today()).days)


class Client(db.Model):
    __tablename__ = "clients"
    id = db.Column(db.Integer, primary_key=True)
    coach_id = db.Column(db.Integer, db.ForeignKey("coaches.id"), nullable=False)
    full_name = db.Column(db.String(120), nullable=False)
    age = db.Column(db.Integer, nullable=False)
    weight = db.Column(db.Float, nullable=False)
    height = db.Column(db.Float, nullable=False)
    gender = db.Column(db.String(10), nullable=False, default="male")
    activity_level = db.Column(db.String(20), nullable=False, default="moderate")
    fitness_goal = db.Column(db.String(30), nullable=False)
    calories_target = db.Column(db.Integer, nullable=False, default=0)
    protein_target = db.Column(db.Integer, nullable=False, default=0)
    carbs_target = db.Column(db.Integer, nullable=False, default=0)
    fats_target = db.Column(db.Integer, nullable=False, default=0)
    active_status = db.Column(db.Boolean, nullable=False, default=True)
    access_code = db.Column(db.String(10), nullable=False, default="")
    phone_number = db.Column(db.String(20), nullable=True, default="")
    coach_notes = db.Column(db.Text, nullable=True, default="")
    subscription_expires_at = db.Column(db.Date, nullable=True, default=None)
    subscription_plan = db.Column(db.String(20), nullable=True, default="monthly")
    subscription_active = db.Column(db.Boolean, nullable=False, default=False)
    logs = db.relationship("DailyLog", backref="client", lazy=True)
    weight_logs = db.relationship("WeightLog", backref="client", lazy=True,
                                  order_by="WeightLog.date")


class DailyLog(db.Model):
    __tablename__ = "daily_logs"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)
    date = db.Column(db.Date, nullable=False, default=datetime.date.today)
    calories_consumed = db.Column(db.Integer, nullable=False, default=0)
    protein_consumed = db.Column(db.Float, nullable=False, default=0)
    carbs_consumed = db.Column(db.Float, nullable=False, default=0)
    fats_consumed = db.Column(db.Float, nullable=False, default=0)
    water_ml = db.Column(db.Integer, nullable=False, default=0)
    adherence_score = db.Column(db.String(10), nullable=False, default="red")


class WeightLog(db.Model):
    __tablename__ = "weight_logs"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)
    date = db.Column(db.Date, nullable=False, default=datetime.date.today)
    weight = db.Column(db.Float, nullable=False)


class Recipe(db.Model):
    __tablename__ = "recipes"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    ingredients = db.Column(db.Text, nullable=False)
    instructions = db.Column(db.Text, nullable=False)
    total_calories = db.Column(db.Integer, nullable=False, default=0)
    protein = db.Column(db.Float, nullable=False, default=0)
    carbs = db.Column(db.Float, nullable=False, default=0)
    fats = db.Column(db.Float, nullable=False, default=0)


# ═══════════════════════ HELPERS ═══════════════════════════════

def get_settings():
    s = PlatformSettings.query.first()
    if not s:
        s = PlatformSettings()
        db.session.add(s)
        db.session.commit()
    return s


def safe_int(value, default=0):
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def safe_float(value, default=0.0):
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def admin_required():
    return session.get("is_admin", False)


def coach_required(coach):
    return session.get(f"coach_{coach.id}", False)


def coach_gate(coach, username):
    """يرجع redirect إذا الوصول ممنوع (جلسة غير صالحة أو اشتراك منتهي)، أو None إذا الوصول مسموح."""
    if not coach_required(coach):
        return redirect(url_for("coach_login", username=username))
    if coach.trial_status() == "expired":
        return redirect(url_for("coach_subscription_expired", username=username))
    return None


def generate_access_code():
    for _ in range(50):
        code = str(random.randint(100000, 999999))
        if not Client.query.filter_by(access_code=code).first():
            return code
    # fallback شبه مستحيل الوصول إليه: كل المجال (900,000 كود) مستعمل
    raise RuntimeError("تعذّر توليد كود دخول فريد — المجال ممتلئ")


# ═══════════════════════ BIOMETRIC ENGINE ══════════════════════

ACTIVITY_MULTIPLIERS = {
    "sedentary": 1.2, "light": 1.375,
    "moderate": 1.55, "active": 1.725, "very_active": 1.9,
}
ACTIVITY_LABELS = {
    "sedentary": "بلا نشاط", "light": "نشاط خفيف",
    "moderate": "نشاط متوسط", "active": "نشاط كبير", "very_active": "نشاط شديد",
}

FITNESS_GOALS_CONFIG = {
    "تنشيف":             {"delta": -300, "p": 0.40, "c": 0.30, "f": 0.30},
    "تضخيم":             {"delta": +350, "p": 0.30, "c": 0.50, "f": 0.20},
    "خسارة وزن سريعة":  {"delta": -500, "p": 0.45, "c": 0.25, "f": 0.30},
    "زيادة وزن":         {"delta": +500, "p": 0.25, "c": 0.55, "f": 0.20},
    "الحفاظ على الوزن": {"delta": 0,    "p": 0.30, "c": 0.45, "f": 0.25},
    "لياقة عامة":        {"delta": -100, "p": 0.30, "c": 0.45, "f": 0.25},
    "رياضة تنافسية":     {"delta": +200, "p": 0.35, "c": 0.50, "f": 0.15},
}
FITNESS_GOALS_LIST = list(FITNESS_GOALS_CONFIG.keys())


def calculate_bmr(w, h, a, g="male"):
    return 10*w + 6.25*h - 5*a + (5 if g == "male" else -161)


def calculate_tdee(bmr, lvl="moderate"):
    return bmr * ACTIVITY_MULTIPLIERS.get(lvl, 1.55)


def calculate_macros(tdee, goal):
    cfg = FITNESS_GOALS_CONFIG.get(goal, FITNESS_GOALS_CONFIG["تنشيف"])
    cal = max(int(tdee + cfg["delta"]), 1200)
    return {
        "calories": cal,
        "protein": int((cal * cfg["p"]) / 4),
        "carbs": int((cal * cfg["c"]) / 4),
        "fats": int((cal * cfg["f"]) / 9),
    }


def recalculate_client_targets(client):
    bmr = calculate_bmr(client.weight, client.height, client.age, client.gender)
    tdee = calculate_tdee(bmr, client.activity_level)
    m = calculate_macros(tdee, client.fitness_goal)
    client.calories_target = m["calories"]
    client.protein_target = m["protein"]
    client.carbs_target = m["carbs"]
    client.fats_target = m["fats"]


# ═══════════════════════ HEATMAP ═══════════════════════════════

def build_heatmap(client):
    today = datetime.date.today()
    log_map = {l.date.isoformat(): l.adherence_score for l in client.logs}
    days, d = [], today - datetime.timedelta(days=89)
    while d <= today:
        days.append({"date": d.isoformat(), "status": log_map.get(d.isoformat(), "empty")})
        d += datetime.timedelta(days=1)
    return days


# ═══════════════════════ STATIC RECIPES DB (50 RECIPES) ═══════════════════

RECIPES_50 = [
    {"id":1,"title":"طبق البروتين الكامل","category":"فطور","tags":"تونة · أفوكادو · بيض مسلوق","ingredients":"علبة تونة في الماء (مصفّاة), بيضتان مسلوقتان, نصف حبة أفوكادو, طماطم وخيار مقطّعان, فلفل ألوان ملح وفلفل أسود","instructions":"رتّب جميع المكوّنات في الطبق ورشّ بالفلفل والملح.","calories":359,"protein":34.4,"carbs":9.1,"fat":21.6},
    {"id":2,"title":"بول الكيوي والموز","category":"فطور","tags":"كيوي · موز · يوغورت · عسل","ingredients":"حبة كيوي مقشّرة ومقطّعة, موزة متوسطة ناضجة, كوب يوغورت طبيعي, ملعقة صغيرة عسل طبيعي, رشة قرفة وبذور شيا","instructions":"اخلط الفواكه مع اليوغورت والعسل وقدّمه مع القرفة وبذور الشيا.","calories":192,"protein":7.3,"carbs":33.0,"fat":3.5},
    {"id":3,"title":"أومليت بالطماطم والمعدنوس","category":"فطور","tags":"بيض · طماطم · معدنوس · زيت الزيتون","ingredients":"3 بيضات طازجة, طماطم مقطّعة, حفنة معدنوس طازج, ملعقة صغيرة زيت الزيتون, ملح وفلفل أسود وبابريكا","instructions":"اخفق البيض مع البهارات. أضف الطماطم. اطهِ على نار هادئة حتى ينضج.","calories":210,"protein":13.6,"carbs":3.0,"fat":16.1},
    {"id":4,"title":"طبق الفراولة والبيض المسلوق","category":"فطور","tags":"فراولة طازجة · بيض · خيار","ingredients":"كوب فراولة طازجة, بيضتان مسلوقتان صلبتان, خيار مقطّع شرائح, رشة ملح وفلفل أسود, عصير نصف ليمونة","instructions":"رتّب جميع المكوّنات في الطبق. رشّ الملح والفلفل. قدّمه طازجاً.","calories":190,"protein":13.9,"carbs":9.3,"fat":11.3},
    {"id":5,"title":"توست القمح مع البيض والقهوة","category":"فطور","tags":"بيض · جبن · خبز القمح الكامل · زبدة الفول السوداني","ingredients":"بيضتان مخفوقتان, شريحة جبن طبيعي, شريحتا خبز القمح الكامل, ملعقة زبدة الفول السوداني, قهوة سوداء بدون سكر","instructions":"اطهِ البيض المخفوق مع الجبن. ضعه على التوست مع زبدة الفول السوداني.","calories":362,"protein":17.3,"carbs":25.0,"fat":22.5},
    {"id":6,"title":"شوفان الليلة بالحليب والفواكه","category":"فطور","tags":"شوفان · حليب · تفاح · عسل · قرفة","ingredients":"كوب شوفان ملفوف, كوب حليب خالي الدسم, تفاحة مبشورة, ملعقة صغيرة عسل طبيعي, رشة قرفة وفواكه موسمية","instructions":"اخلط الشوفان مع الحليب والعسل والقرفة. غطّه وضعه في الثلاجة طوال الليل. قدّمه باردًا مع الفواكه.","calories":280,"protein":10.0,"carbs":45.0,"fat":5.0},
    {"id":7,"title":"توست الأفوكادو بالبيض المسلوق","category":"فطور","tags":"توست قمح · أفوكادو · بيض مسلوق","ingredients":"شريحتا خبز القمح الكامل, نصف حبة أفوكادو ناضجة, بيضة مسلوقة صلبة, ملح وفلفل أسود, عصير نصف ليمونة وبابريكا","instructions":"هرّس الأفوكادو مع الليمون والملح. وزّعه على التوست. ضع شرائح البيض المقطّعة فوقه.","calories":290,"protein":12.0,"carbs":24.0,"fat":18.0},
    {"id":8,"title":"بانكيك الشوفان بالموز","category":"فطور","tags":"شوفان · موز · بيض · عسل · قرفة","ingredients":"موزة ناضجة كبيرة, كوب شوفان مطحون, بيضتان, ملعقة عسل طبيعي, رشة قرفة وفانيليا","instructions":"اهرس الموز مع البيض والشوفان واخلطها جيدًا. اطهِ على مقلاة بدون دهون حتى ينضج الجانبين.","calories":280,"protein":14.0,"carbs":42.0,"fat":7.0},
    {"id":9,"title":"شكشوكة بالفلفل والطماطم","category":"فطور","tags":"بيض · فلفل · طماطم · بصل","ingredients":"بيضتان طازجتان, فليفلة حمراء مقطّعة, طماطم مقطّعة, بصلة مفرومة, ملعقة زيت زيتون وكمون وبابريكا","instructions":"شوّح البصل والفلفل في الزيت. أضف الطماطم والبهارات. اكسر البيض فوق الخليط على نار هادئة.","calories":200,"protein":12.0,"carbs":10.0,"fat":14.0},
    {"id":10,"title":"عصيدة الشوفان بالتمر واللوز","category":"فطور","tags":"شوفان · تمر · لوز · قرفة · حليب","ingredients":"نصف كوب شوفان ملفوف, كوب حليب خالي الدسم, 2 تمرات جزائرية مفرومة, ملعقة لوز مفروم, رشة قرفة","instructions":"اطهِ الشوفان مع الحليب على نار هادئة 5 دقائق. أضف التمر والقرفة. قدّمه ساخناً مزيّناً باللوز.","calories":250,"protein":9.0,"carbs":40.0,"fat":6.0},
    {"id":11,"title":"عصير الجزر والبرتقال","category":"عصائر","tags":"جزر · برتقال · ليمون · عسل","ingredients":"4 جزرات مقشّرة, 2 برتقالتان, عصير نصف ليمونة, نصف كوب ماء, ملعقة عسل","instructions":"اعصر الجزر والبرتقال في الخلاط. أضف الماء والعسل واخلط جيداً. صبّه في كأس وقدّمه بارداً.","calories":235,"protein":3.0,"carbs":54.0,"fat":0.5},
    {"id":12,"title":"سموثي الفراولة بالحليب","category":"عصائر","tags":"فراولة · حليب · عسل · ثلج","ingredients":"كوب فراولة طازجة, كوب حليب خالي الدسم, ملعقتان عسل طبيعي, مكعبات ثلج, نصف موزة","instructions":"امزج جميع المكوّنات في الخلاط حتى تصبح ناعمة. قدّمها باردة فوراً.","calories":165,"protein":6.0,"carbs":28.0,"fat":3.0},
    {"id":13,"title":"عصير الخوخ والبرتقال","category":"عصائر","tags":"خوخ · برتقال · ليمون · عسل","ingredients":"2 خوختان ناضجتان, برتقالتان, عصير نصف ليمونة, ملعقة عسل, نصف كوب ماء","instructions":"اخلط جميع المكوّنات في الخلاط. صفّه إذا رغبت. قدّمه بارداً فوراً.","calories":219,"protein":5.0,"carbs":46.0,"fat":1.0},
    {"id":14,"title":"سموثي الأناناس والموز","category":"عصائر","tags":"أناناس · موز · يوغورت · عسل · جوز الهند","ingredients":"كوب أناناس طازج مقطّع, موزة ناضجة, نصف كوب يوغورت طبيعي, ملعقة عسل, نصف كوب حليب جوز الهند","instructions":"اخلط جميع المكوّنات في الخلاط حتى تصبح ناعمة ومتجانسة.","calories":334,"protein":7.0,"carbs":72.0,"fat":2.0},
    {"id":15,"title":"العصير الأخضر المنعش","category":"عصائر","tags":"خيار · ليمون · نعناع · عسل · سبانخ","ingredients":"نصف خيار مقشّر, عصير نصف ليمونة, حفنة نعناع طازج, نصف كوب ماء, ملعقة عسل وحفنة سبانخ","instructions":"ضع كل المكوّنات في الخلاط واخلطها جيداً. صبّه في كأس وزيّنه بشريحة ليمون.","calories":45,"protein":1.0,"carbs":10.0,"fat":0.0},
    {"id":16,"title":"سموثي المانجو واليوغورت","category":"عصائر","tags":"مانجو · يوغورت يوناني · عسل · ليمون","ingredients":"مانجو طازجة كبيرة مقطّعة, نصف كوب يوغورت يوناني, ملعقة عسل, عصير نصف ليمونة, نصف كوب ماء","instructions":"اخلط المانجو مع اليوغورت والعسل والليمون والماء حتى يصبح ناعماً كريمياً.","calories":220,"protein":7.0,"carbs":44.0,"fat":1.0},
    {"id":17,"title":"عصير التفاح والزنجبيل والليمون","category":"عصائر","tags":"تفاح · زنجبيل · ليمون · عسل","ingredients":"2 تفاحتان خضراوتان, قطعة زنجبيل طازج, عصير ليمونة, ملعقة عسل, نصف كوب ماء","instructions":"اعصر التفاح والزنجبيل. أضف الليمون والعسل والماء. اخلط وقدّمه بارداً.","calories":120,"protein":0.5,"carbs":30.0,"fat":0.0},
    {"id":18,"title":"عصير البطيخ بالنعناع","category":"عصائر","tags":"بطيخ · نعناع · ليمون · ثلج","ingredients":"3 كوارع بطيخ مقطّع, حفنة نعناع طازج, عصير نصف ليمونة, مكعبات ثلج, رشة ملح البحر","instructions":"اخلط البطيخ مع النعناع والليمون. صبّه فوق الثلج وقدّمه فوراً.","calories":90,"protein":2.0,"carbs":22.0,"fat":0.0},
    {"id":19,"title":"عصير الرمان والتفاح","category":"عصائر","tags":"رمان · تفاح · ليمون · عسل","ingredients":"حبة رمان كبيرة, تفاحة حمراء, عصير نصف ليمونة, ملعقة عسل صغيرة","instructions":"اعصر الرمان والتفاح. امزجهما مع الليمون والعسل. قدّمه طازجاً.","calories":150,"protein":1.0,"carbs":35.0,"fat":0.5},
    {"id":20,"title":"سموثي السبانخ والموز الأخضر","category":"عصائر","tags":"سبانخ · موز · حليب نباتي · عسل · بذور كتان","ingredients":"حفنة سبانخ طازجة, موزة ناضجة, كوب حليب لوز, ملعقة عسل, ملعقة بذور كتان وثلج","instructions":"ضع جميع المكوّنات في الخلاط. اخلطها حتى تصبح ناعمة خالية من القطع.","calories":190,"protein":4.0,"carbs":38.0,"fat":2.0},
    {"id":21,"title":"أصابع الخضار مع الحمص","category":"سناك","tags":"خيار · جزر · فلفل · حمص","ingredients":"خيار مقطّع أصابع, جزرتان مقطّعتان, فليفلة مقطّعة, 3 ملاعق حمص طبيعي, عصير ليمون","instructions":"رتّب الخضار في طبق. ضع الحمص في المنتصف. قدّمه مبرّداً.","calories":120,"protein":5.0,"carbs":15.0,"fat":4.0},
    {"id":22,"title":"لوز وتمر — طاقة طبيعية","category":"سناك","tags":"لوز · تمر · جوز · ملح البحر","ingredients":"15 حبة لوز خام, 3-2 تمرات جزائرية, 5 حبات جوز, رشة ملح البحر","instructions":"ضع اللوز والمكسرات في علبة. أضف التمرات المقطّعة. احفظه في الثلاجة.","calories":200,"protein":5.0,"carbs":22.0,"fat":11.0},
    {"id":23,"title":"يوغورت يوناني بالعسل والبذور","category":"سناك","tags":"يوغورت يوناني · عسل · بذور شيا · مكسرات","ingredients":"كوب يوغورت يوناني, ملعقة عسل طبيعي, ملعقة بذور شيا, حفنة مكسرات مشكلة, رشة قرفة","instructions":"صب اليوغورت في وعاء. أضف العسل وبذور الشيا والمكسرات والقرفة.","calories":180,"protein":15.0,"carbs":18.0,"fat":4.0},
    {"id":24,"title":"بيض مسلوق بالكمون والملح","category":"سناك","tags":"بيض · كمون · ملح البحر · بابريكا","ingredients":"بيضتان طازجتان, رشة كمون, ملح البحر, رشة فلفل أسود, بابريكا","instructions":"اسلق البيض 8-10 دقائق. برّده في ماء مثلوج ثم قشّره. رشّه بالكمون والملح والبابريكا.","calories":155,"protein":13.0,"carbs":1.0,"fat":11.0},
    {"id":25,"title":"تفاح بزبدة الفول السوداني","category":"سناك","tags":"تفاح · زبدة الفول السوداني · قرفة · عسل","ingredients":"تفاحة مقطّعة شرائح, ملعقتان زبدة الفول السوداني, رشة قرفة, ملعقة صغيرة عسل","instructions":"رتّب شرائح التفاح في طبق. ضع زبدة الفول السوداني إلى جانبها أو فوقها. رشّ القرفة والعسل.","calories":200,"protein":6.0,"carbs":24.0,"fat":9.0},
    {"id":26,"title":"جبن قريش مع الخضار الطازجة","category":"سناك","tags":"جبن قريش · خيار · طماطم كرزية · نعناع","ingredients":"كوب جبن قريش, نصف خيار مقطّع, حفنة طماطم كرزية, نعناع طازج, ملح وعصير ليمون","instructions":"اخلط الجبن مع الليمون والملح. أضف الخيار والطماطم. زيّنه بأوراق النعناع.","calories":160,"protein":18.0,"carbs":8.0,"fat":5.0},
    {"id":27,"title":"مكسرات وعنب طبيعي","category":"سناك","tags":"لوز · جوز · عنب · كاجو · فستق","ingredients":"ملعقتان لوز خام, ملعقة جوز مفروم, كوب عنب طازج, ملعقة كاجو, ملعقة فستق","instructions":"اخلط المكسرات مع العنب في وعاء. قدّمه مباشرة.","calories":170,"protein":4.0,"carbs":20.0,"fat":9.0},
    {"id":28,"title":"زبادي بالفواكه الموسمية","category":"سناك","tags":"زبادي · فراولة · موز · عسل · قرفة","ingredients":"كوب زبادي طبيعي, نصف كوب فراولة مقطّعة, نصف موزة مقطّعة, ملعقة عسل, رشة قرفة","instructions":"صب الزبادي في وعاء. أضف الفواكه والعسل والقرفة. قدّمه مبرّداً.","calories":150,"protein":8.0,"carbs":25.0,"fat":2.0},
    {"id":29,"title":"رقائق الشوفان المحمّصة بالعسل","category":"سناك","tags":"شوفان · عسل · جوز الهند · قرفة","ingredients":"كوبان شوفان ملفوف, 3 ملاعق عسل طبيعي, ملعقة زيت جوز الهند, ملعقة قرفة, رشة ملح","instructions":"اخلط الشوفان مع العسل والزيت والقرفة. اخبزه 175 درجة لمدة 15 دقيقة. اتركه يبرد.","calories":180,"protein":4.0,"carbs":30.0,"fat":6.0},
    {"id":30,"title":"فراولة مع الشوكولاتة الداكنة","category":"سناك","tags":"فراولة · شوكولاتة داكنة · ملح البحر","ingredients":"كوب فراولة طازجة, 30 غ شوكولاتة داكنة 70%, رشة ملح البحر","instructions":"أذب الشوكولاتة في حمام مائي. اغمس الفراولة جزئياً. رشّها بالملح وضعها في الثلاجة 15 دقيقة.","calories":140,"protein":2.0,"carbs":18.0,"fat":7.0},
    {"id":31,"title":"كرات التمر بالكاكاو وجوز الهند","category":"سناك","tags":"تمر · كاكاو · لوز · جوز الهند","ingredients":"كوب تمر منزوع النواة, ملعقتان كاكاو طبيعي, نصف كوب لوز مطحون, جوز الهند المبشور","instructions":"اخلط التمر مع اللوز والكاكاو في محضّرة الطعام. شكّله كرات صغيرة. لفّ كل كرة في جوز الهند.","calories":190,"protein":4.0,"carbs":28.0,"fat":8.0},
    {"id":32,"title":"توست الجاودار بالجبن والخضار","category":"سناك","tags":"خبز جاودار · جبن · خيار · طماطم","ingredients":"شريحتا خبز جاودار, ملعقتان جبن كريم طبيعي, خيار مقطّع شرائح, طماطم مقطّعة","instructions":"وزّع الجبن الكريم على التوست. رتّب شرائح الخيار والطماطم فوقه.","calories":170,"protein":9.0,"carbs":20.0,"fat":6.0},
    {"id":33,"title":"شوربة الفريك بالدجاج","category":"وجبة","tags":"فريك · دجاج · بصل · جزر · بهارات جزائرية","ingredients":"كوب حبوب فريك, 200 غ صدر دجاج مسلوق, بصلة, جزرة وكرفس, ملح وبهارات جزائرية وزيت زيتون","instructions":"شوّح البصل في الزيت. أضف الفريك والماء والبهارات 20 دقيقة. أضف الدجاج المقطّع والخضار.","calories":280,"protein":24.0,"carbs":32.0,"fat":5.0},
    {"id":34,"title":"شوربة العدس بالليمون","category":"وجبة","tags":"عدس أصفر · بصل · ثوم · كمون · ليمون","ingredients":"كوب عدس أصفر, بصلة كبيرة, فصّا ثوم, ملعقة كمون, عصير ليمونة وزيت زيتون","instructions":"شوّح البصل والثوم في الزيت. أضف العدس والماء والكمون حتى ينضج. اخلطه ناعماً وأضف الليمون.","calories":220,"protein":16.0,"carbs":35.0,"fat":3.0},
    {"id":35,"title":"طاجين الخضار الجزائري","category":"وجبة","tags":"بطاطا · جزر · كوسة · طماطم · رأس الحانوت","ingredients":"بطاطاتان, جزرتان, كوسة, 2 طماطم, بصلة وفلفل ورأس الحانوت وزيت زيتون","instructions":"شوّح البصل في الزيت. أضف الخضار المقطّعة والبهارات والماء. اتركها تطهى على نار هادئة.","calories":195,"protein":5.0,"carbs":35.0,"fat":5.0},
    {"id":36,"title":"مرق الحمص والسبانخ","category":"وجبة","tags":"حمص · سبانخ · طماطم · كمون · بابريكا","ingredients":"كوبان حمص مطبوخ, كوب سبانخ طازجة, 2 طماطم, بصلة وفصّا ثوم وكمون وكزبرة وبابريكا","instructions":"شوّح البصل والثوم ثم أضف الطماطم والبهارات. أضف الحمص والماء 10 دقائق. أضف السبانخ.","calories":240,"protein":12.0,"carbs":38.0,"fat":5.0},
    {"id":37,"title":"سلطة الجزر بالكمون والليمون","category":"وجبة","tags":"جزر · كمون · ليمون · كزبرة · زيت زيتون","ingredients":"4 جزرات كبيرة مبشورة, ملعقة كمون, عصير ليمونتين, ملعقتان زيت زيتون, ثوم وكزبرة","instructions":"اخلط الجزر المبشور مع الكمون والثوم. أضف الليمون والزيتون. رشّ الكزبرة وقدّمه مبرّداً.","calories":130,"protein":2.0,"carbs":16.0,"fat":7.0},
    {"id":38,"title":"عجة الخضار الجزائرية","category":"وجبة","tags":"بيض · فلفل · طماطم · بصل · كزبرة","ingredients":"4 بيضات, فليفلة حمراء وخضراء, بندورة مقطّعة, بصلة, كزبرة ومعدنوس طازجين وزيت زيتون","instructions":"شوّح البصل والفلفل والبندورة في الزيت. اخفق البيض مع الأعشاب والملح. اسكبه واطهِه على نار هادئة.","calories":215,"protein":16.0,"carbs":8.0,"fat":13.0},
    {"id":39,"title":"سلطة الشلغم بالكراوية","category":"وجبة","tags":"شلغم · كراوية · خل · ليمون · زيت","ingredients":"2 شلغمتان مبشورتان, ملعقة بذور كراوية, ملعقة خل, عصير ليمونة, ملعقة زيت زيتون وملح","instructions":"اخلط الشلغم المبشور مع الكراوية. أضف الخل والليمون والزيت والملح. قدّمه بارداً.","calories":110,"protein":2.0,"carbs":10.0,"fat":7.0},
    {"id":40,"title":"يخنة الفاصولياء البيضاء","category":"وجبة","tags":"فاصولياء بيضاء · طماطم · ثوم · كمون · بابريكا","ingredients":"كوبان فاصولياء بيضاء مطبوخة, 3 طماطم, 3 فصوص ثوم, بصلة وكمون وبابريكا وزيت زيتون","instructions":"شوّح البصل والثوم في الزيت. أضف الطماطم والبهارات. أضف الفاصولياء والماء 15 دقيقة.","calories":230,"protein":13.0,"carbs":38.0,"fat":3.0},
    {"id":41,"title":"سلطة الطماطم بالنعناع والخيار","category":"وجبة","tags":"طماطم · خيار · نعناع · بصل أخضر · زيت","ingredients":"3 طماطم مقطّعة, خيار مقطّع, حفنة نعناع طازج, بصل أخضر, ملعقتان زيت زيتون وليمون وملح","instructions":"اخلط الطماطم والخيار والنعناع والبصل. رشّ الزيت والليمون والملح. قدّمه طازجاً.","calories":95,"protein":2.0,"carbs":9.0,"fat":6.0},
    {"id":42,"title":"كسكسو الخضار الخفيف","category":"وجبة","tags":"كسكسو · كوسة · جزر · حمص · بهارات","ingredients":"كوب كسكسو مطبوخ, كوسة مقطّعة, جزرة, كوب حمص مسلوق, بصلة وبهارات جزائرية وزيت","instructions":"اطهِ الخضار مع الحمص والبهارات. صبّه فوق الكسكسو المبخّر.","calories":260,"protein":8.0,"carbs":48.0,"fat":4.0},
    {"id":43,"title":"شوربة الخضار بالشعيرية","category":"وجبة","tags":"شعيرية · جزر · بطاطا · كوسة · بهارات","ingredients":"نصف كوب شعيرية, جزرة, بطاطة صغيرة, كوسة, بصلة وملح وبهارات وزيت زيتون","instructions":"شوّح البصل في الزيت. أضف الخضار المقطّعة والماء. بعد النضج أضف الشعيرية 10 دقائق.","calories":180,"protein":6.0,"carbs":30.0,"fat":4.0},
    {"id":44,"title":"آيس كريم الموز الطبيعي","category":"حلويات","tags":"موز · فانيليا","ingredients":"3 موزات ناضجة مجمّدة, ملعقة صغيرة فانيليا طبيعية, ملعقة عسل (اختياري)","instructions":"اخلط الموز المجمّد في المحضّرة حتى يصبح كريمياً. أضف الفانيليا والعسل. قدّمه فوراً.","calories":100,"protein":1.0,"carbs":26.0,"fat":0.0},
    {"id":45,"title":"بسكويت الشوفان بالتمر والجوز","category":"حلويات","tags":"شوفان · تمر · جوز · عسل · قرفة","ingredients":"كوب شوفان مطحون, كوب تمر منزوع النواة, نصف كوب جوز مفروم, ملعقة عسل, بيضة وقرفة وفانيليا وملح","instructions":"اخلط جميع المكوّنات. شكّله كرات وضعها في صينية. اخبزه 175 درجة لمدة 12 دقيقة.","calories":150,"protein":3.0,"carbs":22.0,"fat":6.0},
    {"id":46,"title":"بودينغ الشيا بالمانجو والعسل","category":"حلويات","tags":"شيا · مانجو · حليب جوز الهند · عسل","ingredients":"3 ملاعق بذور شيا, كوب حليب جوز الهند, نصف مانجو مقطّعة, ملعقة عسل, رشة فانيليا","instructions":"اخلط الشيا مع الحليب والعسل. ضعه في الثلاجة ساعتين. ضع المانجو فوقه عند التقديم.","calories":180,"protein":4.0,"carbs":26.0,"fat":8.0},
    {"id":47,"title":"كيك الموز بدون دقيق","category":"حلويات","tags":"موز · بيض · لوز · عسل · قرفة","ingredients":"3 موزات ناضجة, بيضتان, كوب لوز مطحون, ملعقتان عسل, ملعقة قرفة وبيكينج باودر","instructions":"اهرس الموز مع البيض والعسل. أضف اللوز المطحون والقرفة والبيكينج باودر. اخبزه 175 درجة 25 دقيقة.","calories":200,"protein":6.0,"carbs":30.0,"fat":7.0},
    {"id":48,"title":"جيلاتين الفواكه الطبيعي","category":"حلويات","tags":"جيلاتين · فراولة · كيوي · برتقال","ingredients":"2 كيس جيلاتين بدون سكر, 2 كوب ماء, فراولة مقطّعة, كيوي مقطّع, برتقال","instructions":"حلّ الجيلاتين في الماء الساخن. أضف الفواكه. ضعه في الثلاجة حتى يتماسك.","calories":80,"protein":3.0,"carbs":18.0,"fat":0.0},
    {"id":49,"title":"تفاحة مخبوزة بالقرفة والعسل","category":"حلويات","tags":"تفاح · قرفة · عسل · جوز · زبيب","ingredients":"2 تفاحتان كبيرتان, ملعقتان عسل, ملعقة قرفة, ملعقة جوز مفروم, ملعقة زبيب وزبدة لوز","instructions":"أفرغ وسط التفاح. امزج العسل والقرفة والجوز والزبيب. ضعه في التفاح. اخبزه 180 درجة 25 دقيقة.","calories":220,"protein":3.0,"carbs":38.0,"fat":7.0},
    {"id":50,"title":"موس الشوكولاتة بالأفوكادو","category":"حلويات","tags":"أفوكادو · شوكولاتة داكنة · عسل · كاكاو · فانيليا","ingredients":"2 حبتا أفوكادو ناضجتان, 3 ملاعق كاكاو طبيعي, 3 ملاعق عسل, ملعقة فانيليا, رشة ملح البحر","instructions":"اخلط الأفوكادو مع الكاكاو والعسل والفانيليا في المحضّرة حتى يصبح ناعماً وكريمياً. ضعه في الثلاجة ساعة.","calories":210,"protein":3.0,"carbs":22.0,"fat":12.0},
]


def search_recipes(query="", category=""):
    results = RECIPES_50
    if category and category != "الكل":
        results = [r for r in results if r["category"] == category]
    if query:
        q = query.strip().lower()
        results = [r for r in results
                   if q in r["title"].lower() or q in r["ingredients"].lower() or q in r["tags"].lower()]
    return results


# ═══════════════════════ CLIENT STATS ══════════════════════════

def calc_client_stats(client):
    logs = client.logs
    total = len(logs)
    green = sum(1 for l in logs if l.adherence_score == "green")
    today = datetime.date.today()
    log_map = {l.date: l for l in logs}
    streak = 0
    d = today
    while True:
        l = log_map.get(d)
        if l and l.adherence_score == "green":
            streak += 1
            d -= datetime.timedelta(days=1)
        else:
            break
    return {
        "total_logs": total,
        "green_days": green,
        "adherence_pct": int(green / total * 100) if total > 0 else 0,
        "streak": streak,
    }


# ═══════════════════════ SHOPPING LIST ═════════════════════════

ALGERIAN_FOODS = {
    "protein": ["صدر الدجاج", "بيض", "تونة معلبة", "جبن قريش", "لحم مفروم"],
    "carbs": ["شوفان", "عدس", "خبز قمح كامل", "بطاطا", "أرز بني"],
    "fats": ["زيت زيتون", "لوز", "كاكاو طبيعي", "تمر"],
    "vegetables": ["خضار ورقية", "طماطم", "خيار", "فلفل ألوان", "جزر"],
}


def generate_shopping_list(client):
    lines = [
        f"── البروتين (الأسبوع: {client.protein_target*7}غ) ──",
        *[f"  • {f}" for f in ALGERIAN_FOODS["protein"]],
        f"── الكربوهيدرات (الأسبوع: {client.carbs_target*7}غ) ──",
        *[f"  • {f}" for f in ALGERIAN_FOODS["carbs"]],
        f"── الدهون (الأسبوع: {client.fats_target*7}غ) ──",
        *[f"  • {f}" for f in ALGERIAN_FOODS["fats"]],
        "── خضار وأساسيات ──",
        *[f"  • {f}" for f in ALGERIAN_FOODS["vegetables"]],
    ]
    return "\n".join(lines)


# ═══════════════════════════ ROUTES ════════════════════════════

# ── GUIDES ──────────────────────────────────────────────────────
PLATFORM_KNOWLEDGE = """أنت مساعد ذكي فمنصة "CoachPage DZ" — منصة جزائرية لمدربي اللياقة البدنية والتغذية.
جاوب بالدارجة الجزائرية المكتوبة بحروف عربية، بإيجاز ووضوح (سطرين أو 3 أقصى).
معلومات حقيقية على المنصة:
- المنصة تخلي المدرب يبني صفحته الخاصة، يضيف عملاءه، والمنصة تحسب السعرات والماكروز أوتوماتيكيا حسب وزن وطول وهدف كل عميل.
- كل عميل عندو كود دخول خاص بيه (6 أرقام) يدخل بيه لصفحته يسجل وجباته ووزنه اليومي.
- تجربة مجانية 7 أيام للمدرب الجديد، بلا كارت بنكي.
- بعد التجربة، المدرب يدفع اشتراك شهري (السعر يحدده صاحب المنصة) عبر BaridiMob (CCP) أو بطاقة الذهبية، والتفعيل يدوي من المشرف بعد استلام الدفع.
- المدرب بدوره يحدد سعر اشتراكه الخاص مع عملائه، ويستلم الدفع مباشرة فحسابه (CCP/الذهبية) بلا وسيط.
- من ميزات المنصة: خريطة انضباط يومية (heatmap)، وصفات جزائرية صحية، قائمة تسوق أسبوعية أوتوماتيكية، تحليل صور الأكل بالذكاء الاصطناعي، تذكير قبل انتهاء الاشتراك.
- المنصة بالعربية بالكامل (RTL) ومبنية خصيصا للسوق الجزائري.
إذا سؤال ماعندوش علاقة بالمنصة، رد بأدب أنك تقدر تجاوب غير على أسئلة CoachPage DZ."""


@app.route("/dz/chatbot-ask", methods=["POST"])
def api_chatbot_ask():
    message = (request.get_json(silent=True) or {}).get("message", "").strip()
    if not message:
        return jsonify({"reply": "اكتب سؤالك وأنا نجاوبك!"})
    if not CLAUDE_API_KEY:
        return jsonify({"reply": "المساعد الذكي مايتفعلش حاليا. تصفح قسم الأسئلة الشائعة تحت، أو تواصل معنا مباشرة."})
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": CLAUDE_API_KEY, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": "claude-haiku-4-5-20251001", "max_tokens": 300,
                  "system": PLATFORM_KNOWLEDGE,
                  "messages": [{"role": "user", "content": message}]},
            timeout=20,
        )
        r.raise_for_status()
        reply = r.json()["content"][0]["text"]
        return jsonify({"reply": reply})
    except Exception:
        return jsonify({"reply": "صرا خطأ، حاول مرة أخرى أو تصفح الأسئلة الشائعة تحت."}), 200



def guide():
    if not admin_required():
        return redirect(url_for("admin_login"))
    settings = get_settings()
    shown_pw = "•••••••• (تم تغييرها — محفوظة بأمان)" if settings.admin_password_hash else ADMIN_PASSWORD
    return render_template("guide.html", settings=settings, admin_password=shown_pw)


@app.route("/guide/coach")
def guide_coach():
    username = request.args.get("u", "")
    return render_template("guide_coach.html", coach_username=username)


@app.route("/guide/client")
def guide_client():
    return render_template("guide_client.html")


# ── CENTRAL COACH LOGIN ─────────────────────────────────────────
@app.route("/login", methods=["GET", "POST"])
def login_central():
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        coach = Coach.query.filter_by(username=username).first()
        if not coach:
            flash("اسم المستخدم غير موجود", "error")
            return render_template("login_central.html")
        allowed, wait_min = rl_check(f"coach_login_{coach.id}", max_attempts=8, lockout_minutes=15)
        if not allowed:
            flash(f"تم تجميد الدخول. حاول بعد {wait_min} دقيقة.", "error")
            return render_template("login_central.html")
        if check_password_hash(coach.password_hash, password):
            if coach.status == "suspended":
                flash("حسابك موقّف. تواصل مع المشرف.", "error")
                return render_template("login_central.html")
            rl_clear(f"coach_login_{coach.id}")
            session[f"coach_{coach.id}"] = True
            remember = request.form.get("remember_me", "0") == "1"
            session.permanent = remember  # False = كوكي جلسة عادية (تنتهي بغلق المتصفح)، بلا تعديل إعداد عام يأثر على كل المستخدمين
            return redirect(url_for("coach_dashboard", username=coach.username))
        rl_fail(f"coach_login_{coach.id}", max_attempts=8, lockout_minutes=15)
        flash("كلمة السر غلطة!", "error")
    return render_template("login_central.html")


# ── CLIENT JOIN VIA REFERRAL ─────────────────────────────────────
@app.route("/join/<username>")
def coach_join(username):
    coach = Coach.query.filter_by(username=username, status="active").first_or_404()
    return render_template("join_coach.html", coach=coach)

@app.route("/join/<username>/enter", methods=["POST"])
def coach_join_enter(username):
    coach = Coach.query.filter_by(username=username, status="active").first_or_404()
    code = request.form.get("access_code", "").strip()
    client = Client.query.filter_by(access_code=code, coach_id=coach.id).first()
    if not client:
        flash("الكود غلط أو ما خاصكش بهذا المدرب. تأكد من الكود أو تواصل مع مدربك.", "error")
        return redirect(url_for("coach_join", username=username))
    session["client_id"] = client.id
    return redirect(url_for("client_dashboard", cid=client.id))


# ── HOME ────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


# ── COACH LANDING ───────────────────────────────────────────────
@app.route("/coach/<username>")
def coach_landing(username):
    coach = Coach.query.filter_by(username=username).first_or_404()
    settings = get_settings()
    wa_msg = f"السلام عليكم! بغيت نشترك في برنامجك. CCP: {coach.baridimob_ccp}"
    wa_link = f"https://wa.me/{coach.whatsapp_number}?text={requests.utils.quote(wa_msg)}"
    return render_template("landing.html", coach=coach, wa_link=wa_link, settings=settings)


# ── COACH AUTH ──────────────────────────────────────────────────
@app.route("/coach/<username>/login", methods=["GET", "POST"])
def coach_login(username):
    coach = Coach.query.filter_by(username=username).first_or_404()
    if request.method == "POST":
        allowed, wait_min = rl_check(f"coach_login_{coach.id}", max_attempts=8, lockout_minutes=15)
        if not allowed:
            flash(f"تم تجميد الدخول مؤقتاً. حاول بعد {wait_min} دقيقة.", "error")
            return render_template("login.html", coach=coach)
        if check_password_hash(coach.password_hash, request.form.get("password", "")):
            if coach.status == "suspended":
                flash("حسابك موقّف. تواصل مع المشرف لمعرفة السبب.", "error")
                return render_template("login.html", coach=coach)
            rl_clear(f"coach_login_{coach.id}")
            session[f"coach_{coach.id}"] = True
            remember = request.form.get("remember_me", "0") == "1"
            session.permanent = remember  # False = كوكي جلسة عادية (تنتهي بغلق المتصفح)، بلا تعديل إعداد عام يأثر على كل المستخدمين
            return redirect(url_for("coach_dashboard", username=username))
        rl_fail(f"coach_login_{coach.id}", max_attempts=8, lockout_minutes=15)
        flash("كلمة السر غلطة!", "error")
    return render_template("login.html", coach=coach)


@app.route("/coach/<username>/logout")
def coach_logout(username):
    coach = Coach.query.filter_by(username=username).first_or_404()
    session.pop(f"coach_{coach.id}", None)
    return redirect(url_for("coach_landing", username=username))


@app.route("/coach/<username>/subscription-expired")
def coach_subscription_expired(username):
    coach = Coach.query.filter_by(username=username).first_or_404()
    if not coach_required(coach):
        return redirect(url_for("coach_login", username=username))
    settings = get_settings()
    return render_template("subscription_expired.html", coach=coach, settings=settings)


# ── COACH DASHBOARD ─────────────────────────────────────────────
@app.route("/coach/<username>/dashboard")
def coach_dashboard(username):
    coach = Coach.query.filter_by(username=username).first_or_404()
    _gate = coach_gate(coach, username)
    if _gate:
        return _gate
    clients = Client.query.filter_by(coach_id=coach.id).all()
    settings = get_settings()
    return render_template("coach_dashboard.html", coach=coach, clients=clients,
                           today_date=datetime.date.today(), settings=settings,
                           fitness_goals=FITNESS_GOALS_LIST)


@app.route("/coach/<username>/add-client", methods=["POST"])
def add_client(username):
    coach = Coach.query.filter_by(username=username).first_or_404()
    _gate = coach_gate(coach, username)
    if _gate:
        return _gate
    client = Client(
        coach_id=coach.id,
        full_name=request.form.get("full_name", "").strip(),
        age=safe_int(request.form.get("age", 25)),
        weight=safe_float(request.form.get("weight", 70)),
        height=safe_float(request.form.get("height", 170)),
        gender=request.form.get("gender", "male"),
        activity_level=request.form.get("activity_level", "moderate"),
        fitness_goal=request.form.get("fitness_goal", "تنشيف"),
        access_code=generate_access_code(),
    )
    recalculate_client_targets(client)
    db.session.add(client)
    db.session.commit()
    flash(f"تم إضافة {client.full_name}! كود الدخول: {client.access_code}", "success")
    return redirect(url_for("coach_dashboard", username=username))


@app.route("/coach/<username>/client/<int:cid>")
def coach_view_client(username, cid):
    coach = Coach.query.filter_by(username=username).first_or_404()
    _gate = coach_gate(coach, username)
    if _gate:
        return _gate
    client = Client.query.filter_by(id=cid, coach_id=coach.id).first_or_404()
    today_log = DailyLog.query.filter_by(client_id=cid, date=datetime.date.today()).first()
    bmr = calculate_bmr(client.weight, client.height, client.age, client.gender)
    return render_template("client_dashboard.html", client=client, coach=coach,
                           heatmap=build_heatmap(client), today_log=today_log,
                           bmr=int(bmr), tdee=int(calculate_tdee(bmr, client.activity_level)),
                           is_coach_view=True, shopping_list=generate_shopping_list(client),
                           activity_label=ACTIVITY_LABELS.get(client.activity_level, ""),
                           client_stats=calc_client_stats(client),
                           today_date=datetime.date.today())


@app.route("/coach/<username>/client/<int:cid>/delete", methods=["POST"])
def delete_client(username, cid):
    coach = Coach.query.filter_by(username=username).first_or_404()
    _gate = coach_gate(coach, username)
    if _gate:
        return _gate
    client = Client.query.filter_by(id=cid, coach_id=coach.id).first_or_404()
    name = client.full_name
    DailyLog.query.filter_by(client_id=cid).delete()
    WeightLog.query.filter_by(client_id=cid).delete()
    db.session.delete(client)
    db.session.commit()
    flash(f"تم حذف العميل {name}.", "success")
    return redirect(url_for("coach_dashboard", username=username))


@app.route("/coach/<username>/client/<int:cid>/reset-code", methods=["POST"])
def reset_client_code(username, cid):
    coach = Coach.query.filter_by(username=username).first_or_404()
    _gate = coach_gate(coach, username)
    if _gate:
        return _gate
    client = Client.query.filter_by(id=cid, coach_id=coach.id).first_or_404()
    client.access_code = generate_access_code()
    db.session.commit()
    flash(f"تم تجديد كود {client.full_name}: {client.access_code}", "success")
    return redirect(url_for("coach_view_client", username=username, cid=cid))


@app.route("/coach/<username>/settings", methods=["GET", "POST"])
def coach_settings(username):
    coach = Coach.query.filter_by(username=username).first_or_404()
    _gate = coach_gate(coach, username)
    if _gate:
        return _gate
    if request.method == "POST":
        action = request.form.get("action", "profile")
        if action == "profile":
            coach.full_name = request.form.get("full_name", coach.full_name).strip()
            coach.bio = request.form.get("bio", coach.bio).strip()
            coach.specialty = request.form.get("specialty", getattr(coach, "specialty", "")).strip()
            coach.baridimob_ccp = request.form.get("baridimob_ccp", coach.baridimob_ccp).strip()
            coach.edahabia_card = request.form.get("edahabia_card", coach.edahabia_card or "").strip()
            coach.whatsapp_number = request.form.get("whatsapp_number", coach.whatsapp_number).strip()
            coach.phone_number = request.form.get("phone_number", coach.phone_number or "").strip()
            coach.email = request.form.get("email", coach.email or "").strip()
            coach.subscription_price = safe_int(request.form.get("subscription_price", coach.subscription_price))
            photo = request.files.get("photo")
            if photo and photo.filename:
                url = upload_coach_photo(photo, coach.id)
                if url:
                    coach.photo_url = url
                else:
                    flash("تعذّر رفع الصورة، حاول صورة أصغر (أقل من 5 ميغا) بصيغة jpg/png.", "error")
            db.session.commit()
            flash("تم تحديث ملفك الشخصي!", "success")
        elif action == "password":
            current = request.form.get("current_password", "")
            new_pw = request.form.get("new_password", "")
            confirm = request.form.get("confirm_password", "")
            if not check_password_hash(coach.password_hash, current):
                flash("كلمة السر الحالية غلطة!", "error")
            elif len(new_pw) < 6:
                flash("كلمة السر الجديدة قصيرة جداً (6 أحرف على الأقل).", "error")
            elif new_pw != confirm:
                flash("كلمتا السر غير متطابقتين!", "error")
            else:
                coach.password_hash = generate_password_hash(new_pw)
                db.session.commit()
                flash("تم تغيير كلمة السر بنجاح!", "success")
        return redirect(url_for("coach_settings", username=username))
    return render_template("coach_settings.html", coach=coach)


@app.route("/coach/<username>/update-client/<int:cid>", methods=["POST"])
def update_client(username, cid):
    coach = Coach.query.filter_by(username=username).first_or_404()
    _gate = coach_gate(coach, username)
    if _gate:
        return _gate
    client = Client.query.filter_by(id=cid, coach_id=coach.id).first_or_404()
    client.weight = safe_float(request.form.get("weight", client.weight))
    client.height = safe_float(request.form.get("height", client.height))
    client.age = safe_int(request.form.get("age", client.age))
    client.activity_level = request.form.get("activity_level", client.activity_level)
    client.fitness_goal = request.form.get("fitness_goal", client.fitness_goal)
    client.active_status = request.form.get("active_status") == "on"
    oc = request.form.get("calories_override", "").strip()
    if oc:
        client.calories_target = int(oc)
        client.protein_target = safe_int(request.form.get("protein_override", client.protein_target))
        client.carbs_target = safe_int(request.form.get("carbs_override", client.carbs_target))
        client.fats_target = safe_int(request.form.get("fats_override", client.fats_target))
    else:
        recalculate_client_targets(client)
    db.session.commit()
    flash("تم تحديث البيانات!", "success")
    return redirect(url_for("coach_view_client", username=username, cid=cid))


# ── CLIENT ACCESS ───────────────────────────────────────────────
@app.route("/client/access", methods=["GET", "POST"])
def client_access():
    locked_min = 0
    if request.method == "POST":
        allowed, wait_min = rl_check("client_access", max_attempts=10, lockout_minutes=15)
        if not allowed:
            locked_min = wait_min
            flash(f"محاولات كثيرة. حاول بعد {wait_min} دقيقة.", "error")
            return render_template("client_access.html", locked_min=locked_min)
        code = request.form.get("access_code", "").strip()
        client = Client.query.filter_by(access_code=code).first()
        if client:
            rl_clear("client_access")
            session.permanent = True
            session[f"client_{client.id}"] = True
            return redirect(url_for("client_dashboard", client_id=client.id))
        rl_fail("client_access", max_attempts=10, lockout_minutes=15)
        flash("الكود غلط، اسأل مدربك!", "error")
    return render_template("client_access.html", locked_min=locked_min)


@app.route("/client/<int:client_id>/dashboard")
def client_dashboard(client_id):
    if not session.get(f"client_{client_id}"):
        return redirect(url_for("client_access"))
    client = Client.query.get_or_404(client_id)
    today_log = DailyLog.query.filter_by(client_id=client_id, date=datetime.date.today()).first()
    bmr = calculate_bmr(client.weight, client.height, client.age, client.gender)
    return render_template("client_dashboard.html", client=client, coach=client.coach,
                           heatmap=build_heatmap(client), today_log=today_log,
                           bmr=int(bmr), tdee=int(calculate_tdee(bmr, client.activity_level)),
                           is_coach_view=False, shopping_list=generate_shopping_list(client),
                           activity_label=ACTIVITY_LABELS.get(client.activity_level, ""),
                           client_stats=calc_client_stats(client),
                           today_date=datetime.date.today())


@app.route("/client/<int:client_id>/logout")
def client_logout(client_id):
    session.pop(f"client_{client_id}", None)
    return redirect(url_for("client_access"))


@app.route("/client/<int:client_id>/log", methods=["POST"])
def submit_log(client_id):
    client = Client.query.get_or_404(client_id)
    authed = session.get(f"client_{client_id}") or session.get(f"coach_{client.coach_id}")
    if not authed:
        return redirect(url_for("client_access"))
    cal = safe_int(request.form.get("calories_consumed", 0))
    pro = safe_float(request.form.get("protein_consumed", 0))
    carb = safe_float(request.form.get("carbs_consumed", 0))
    fat = safe_float(request.form.get("fats_consumed", 0))
    water = safe_int(request.form.get("water_ml", 0))
    cal_ok = abs(cal - client.calories_target) / max(client.calories_target, 1) <= 0.10
    pro_ok = abs(pro - client.protein_target) / max(client.protein_target, 1) <= 0.10
    adh = "green" if (cal_ok and pro_ok) else "red"
    existing = DailyLog.query.filter_by(client_id=client_id, date=datetime.date.today()).first()
    if existing:
        existing.calories_consumed, existing.protein_consumed = cal, pro
        existing.carbs_consumed, existing.fats_consumed, existing.adherence_score = carb, fat, adh
        existing.water_ml = water
    else:
        db.session.add(DailyLog(client_id=client_id, date=datetime.date.today(),
                                calories_consumed=cal, protein_consumed=pro,
                                carbs_consumed=carb, fats_consumed=fat,
                                water_ml=water, adherence_score=adh))
    db.session.commit()
    flash("تم تسجيل وجباتك اليوم!", "success")
    if session.get(f"client_{client_id}"):
        return redirect(url_for("client_dashboard", client_id=client_id))
    coach = Client.query.get(client_id).coach
    return redirect(url_for("coach_view_client", username=coach.username, cid=client_id))


@app.route("/client/<int:client_id>/weight", methods=["POST"])
def log_weight(client_id):
    client = Client.query.get_or_404(client_id)
    authed = session.get(f"client_{client_id}") or session.get(f"coach_{client.coach_id}")
    if not authed:
        return redirect(url_for("client_access"))
    w = safe_float(request.form.get("weight", 0))
    if w > 0:
        existing = WeightLog.query.filter_by(client_id=client_id, date=datetime.date.today()).first()
        if existing:
            existing.weight = w
        else:
            db.session.add(WeightLog(client_id=client_id, date=datetime.date.today(), weight=w))
        db.session.commit()
        flash("تم تسجيل وزنك!", "success")
    ref = request.form.get("redirect_to", "")
    if ref and ref.startswith("/") and not ref.startswith("//"):
        return redirect(ref)
    if session.get(f"client_{client_id}"):
        return redirect(url_for("client_dashboard", client_id=client_id))
    client = Client.query.get(client_id)
    return redirect(url_for("coach_view_client", username=client.coach.username, cid=client_id))


# ── COACH REGISTRATION ──────────────────────────────────────────
@app.route("/register", methods=["GET", "POST"])
def register():
    settings = get_settings()
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower().replace(" ", "_")
        full_name = request.form.get("full_name", "").strip()
        password = request.form.get("password", "")
        ccp = request.form.get("baridimob_ccp", "").strip()
        whatsapp = request.form.get("whatsapp_number", "").strip()
        bio = request.form.get("bio", "").strip()
        price = safe_int(request.form.get("subscription_price", 4000))

        phone = request.form.get("phone_number", "").strip()
        email = request.form.get("email", "").strip()
        if not username or not password or len(password) < 6:
            flash("اسم المستخدم وكلمة السر (6 أحرف على الأقل) مطلوبان.", "error")
            return render_template("register.html", settings=settings)
        if not email or "@" not in email:
            flash("الإيميل مطلوب لاستعادة كلمة المرور.", "error")
            return render_template("register.html", settings=settings)
        if not phone:
            flash("رقم الهاتف مطلوب لاستعادة كلمة المرور.", "error")
            return render_template("register.html", settings=settings)
        if Coach.query.filter_by(username=username).first():
            flash("هذا الاسم محجوز! جرّب اسم آخر.", "error")
            return render_template("register.html", settings=settings)
        if Coach.query.filter(Coach.email == email).first():
            flash("هذا الإيميل مسجّل بالفعل — استعمل إيميل آخر أو ادخل لحسابك.", "error")
            return render_template("register.html", settings=settings)

        trial_end = datetime.date.today() + datetime.timedelta(days=7)
        coach = Coach(
            username=username, full_name=full_name,
            password_hash=generate_password_hash(password),
            baridimob_ccp=ccp, whatsapp_number=whatsapp,
            trial_expires_at=trial_end,
            bio=bio, subscription_price=price, status="pending",
            phone_number=phone, email=email,
        )
        db.session.add(coach)
        db.session.commit()
        session["new_coach_id"] = coach.id
        return redirect(url_for("register_pending", coach_id=coach.id))

    return render_template("register.html", settings=settings)


@app.route("/register/pending/<int:coach_id>")
def register_pending(coach_id):
    coach = Coach.query.get_or_404(coach_id)
    settings = get_settings()
    return render_template("register_pending.html", coach=coach, settings=settings)


# ── ADMIN ────────────────────────────────────────────────────────
@app.route("/admin", methods=["GET", "POST"])
def admin_login():
    if admin_required():
        return redirect(url_for("admin_dashboard"))
    locked_min = 0
    if request.method == "POST":
        allowed, wait_min = rl_check("admin_login", max_attempts=5, lockout_minutes=30)
        if not allowed:
            locked_min = wait_min
            flash(f"تم تجميد الدخول للحماية. حاول بعد {wait_min} دقيقة.", "error")
            return render_template("admin_login.html", locked_min=locked_min)
        entered_pw = request.form.get("password", "")
        settings = get_settings()
        if settings.admin_password_hash:
            pw_ok = check_password_hash(settings.admin_password_hash, entered_pw)
        else:
            pw_ok = (entered_pw == ADMIN_PASSWORD)
        if pw_ok:
            rl_clear("admin_login")
            session["is_admin"] = True
            session.permanent = True
            return redirect(url_for("admin_dashboard"))
        rl_fail("admin_login", max_attempts=5, lockout_minutes=30)
        flash("كلمة السر غلطة!", "error")
    return render_template("admin_login.html", locked_min=locked_min)


@app.route("/admin/logout")
def admin_logout():
    session.pop("is_admin", None)
    return redirect(url_for("admin_login"))


@app.route("/admin/dashboard")
def admin_dashboard():
    if not admin_required():
        return redirect(url_for("admin_login"))
    coaches = Coach.query.order_by(Coach.registered_at.desc()).all()
    settings = get_settings()
    stats = {
        "total": len(coaches),
        "active": sum(1 for c in coaches if c.status == "active"),
        "pending": sum(1 for c in coaches if c.status == "pending"),
        "suspended": sum(1 for c in coaches if c.status == "suspended"),
        "total_clients": Client.query.count(),
        "revenue_month": sum(1 for c in coaches if c.status == "active") * settings.coach_monthly_price,
    }
    return render_template("admin_dashboard.html", coaches=coaches, settings=settings, stats=stats)


@app.route("/admin/coach/<int:coach_id>/status", methods=["POST"])
def admin_set_coach_status(coach_id):
    if not admin_required():
        return redirect(url_for("admin_login"))
    coach = Coach.query.get_or_404(coach_id)
    new_status = request.form.get("status")
    if new_status in ("active", "pending", "suspended"):
        coach.status = new_status
        db.session.commit()
        labels = {"active": "مفعّل", "pending": "قيد المراجعة", "suspended": "موقّف"}
        flash(f"تم تغيير حالة {coach.username} إلى: {labels[new_status]}", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/coach/<int:coach_id>/renew", methods=["POST"])
def admin_renew_coach(coach_id):
    if not admin_required():
        return redirect(url_for("admin_login"))
    coach = Coach.query.get_or_404(coach_id)
    months = safe_int(request.form.get("months", 1), 1)
    today = datetime.date.today()
    base = coach.trial_expires_at if (coach.trial_expires_at and coach.trial_expires_at > today) else today
    coach.trial_expires_at = base + datetime.timedelta(days=30 * months)
    coach.status = "active"
    db.session.commit()
    flash(f"تم تجديد اشتراك {coach.username} لمدة {months} شهر — جديد لين {coach.trial_expires_at.strftime('%Y-%m-%d')}.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/settings", methods=["POST"])
def admin_update_settings():
    if not admin_required():
        return redirect(url_for("admin_login"))
    s = get_settings()
    s.owner_ccp = request.form.get("owner_ccp", "").strip()
    s.owner_whatsapp = request.form.get("owner_whatsapp", "").strip()
    s.coach_monthly_price = safe_int(request.form.get("coach_monthly_price", 2000))
    db.session.commit()
    flash("تم حفظ إعدادات المنصة!", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/coach/<int:coach_id>/delete", methods=["POST"])
def admin_delete_coach(coach_id):
    if not admin_required():
        return redirect(url_for("admin_login"))
    coach = Coach.query.get_or_404(coach_id)
    username = coach.username
    for client in coach.clients:
        DailyLog.query.filter_by(client_id=client.id).delete()
        WeightLog.query.filter_by(client_id=client.id).delete()
        db.session.delete(client)
    db.session.delete(coach)
    db.session.commit()
    flash(f"تم حذف المدرب @{username} وكل بياناته نهائياً.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/coach/<int:coach_id>/note", methods=["POST"])
def admin_coach_note(coach_id):
    if not admin_required():
        return redirect(url_for("admin_login"))
    coach = Coach.query.get_or_404(coach_id)
    coach.admin_notes = request.form.get("notes", "").strip()
    db.session.commit()
    flash("تم حفظ الملاحظة.", "success")
    return redirect(url_for("admin_dashboard"))


@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404


@app.errorhandler(500)
def server_error(e):
    return render_template("500.html"), 500


# ── APIS ─────────────────────────────────────────────────────────
@app.route("/dz/recipes")
def api_recipes():
    q = request.args.get("q", "").strip()
    cat = request.args.get("cat", "").strip()
    results = search_recipes(q, cat)
    return jsonify(results)


@app.route("/client/<int:client_id>/update-self", methods=["POST"])
def client_update_self(client_id):
    if not session.get(f"client_{client_id}"):
        return redirect(url_for("client_access"))
    client = Client.query.get_or_404(client_id)
    client.weight = safe_float(request.form.get("weight", client.weight) or client.weight)
    client.height = safe_float(request.form.get("height", client.height) or client.height)
    client.age = safe_int(request.form.get("age", client.age) or client.age)
    client.activity_level = request.form.get("activity_level", client.activity_level)
    client.fitness_goal = request.form.get("fitness_goal", client.fitness_goal)
    client.phone_number = request.form.get("phone_number", client.phone_number or "").strip()
    recalculate_client_targets(client)
    db.session.commit()
    flash("تم تحديث بياناتك!", "success")
    return redirect(url_for("client_dashboard", client_id=client_id))


@app.route("/coach/<username>/client/<int:cid>/toggle-subscription", methods=["POST"])
def toggle_client_subscription(username, cid):
    coach = Coach.query.filter_by(username=username).first_or_404()
    _gate = coach_gate(coach, username)
    if _gate:
        return _gate
    client = Client.query.filter_by(id=cid, coach_id=coach.id).first_or_404()
    action = request.form.get("action", "activate")
    if action == "activate":
        months = safe_int(request.form.get("months", 1))
        client.subscription_active = True
        client.active_status = True
        today = datetime.date.today()
        if client.subscription_expires_at and client.subscription_expires_at > today:
            client.subscription_expires_at = client.subscription_expires_at + datetime.timedelta(days=30 * months)
        else:
            client.subscription_expires_at = today + datetime.timedelta(days=30 * months)
        plan_map = {1: "monthly", 3: "3months", 5: "5months"}
        client.subscription_plan = plan_map.get(months, "monthly")
        db.session.commit()
        flash(f"تم تفعيل اشتراك {client.full_name} لمدة {months} شهر.", "success")
    elif action == "deactivate":
        client.subscription_active = False
        client.active_status = False
        db.session.commit()
        flash(f"تم إلغاء اشتراك {client.full_name}.", "success")
    return redirect(url_for("coach_view_client", username=username, cid=cid))


@app.route("/dz/analyze-food-photo", methods=["POST"])
def api_analyze_food_photo():
    if not CLAUDE_API_KEY:
        return jsonify({
            "foods": [{"name": "وجبة تقديرية", "amount": "100غ", "calories": 280, "protein": 18, "carbs": 32, "fats": 9}],
            "total": {"calories": 280, "protein": 18, "carbs": 32, "fats": 9},
            "description": "تحليل تقديري — أضف CLAUDE_API_KEY للتحليل الحقيقي",
            "mock": True,
        })
    if "photo" not in request.files or request.files["photo"].filename == "":
        return jsonify({"error": "لا توجد صورة"}), 400
    f = request.files["photo"]
    import base64
    img_data = base64.b64encode(f.read()).decode()
    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else "jpg"
    mt = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
          "webp": "image/webp"}.get(ext, "image/jpeg")
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": CLAUDE_API_KEY, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": "claude-haiku-4-5-20251001", "max_tokens": 600,
                  "messages": [{"role": "user", "content": [
                      {"type": "image", "source": {"type": "base64", "media_type": mt, "data": img_data}},
                      {"type": "text", "text": "حلّل هذه الوجبة وأعطني تقدير السعرات والماكروز. رد بـJSON فقط: {\"foods\":[{\"name\":\"...\",\"amount\":\"...\",\"calories\":0,\"protein\":0,\"carbs\":0,\"fats\":0}],\"total\":{\"calories\":0,\"protein\":0,\"carbs\":0,\"fats\":0},\"description\":\"...\"}"}
                  ]}]},
            timeout=30,
        )
        r.raise_for_status()
        text = r.json()["content"][0]["text"]
        result = json.loads(text[text.find("{"):text.rfind("}")+1])
        result["mock"] = False
        return jsonify(result)
    except Exception:
        return jsonify({"error": "تعذّر تحليل الصورة، حاول مرة أخرى"}), 500


@app.route("/coach/<username>/forgot-password", methods=["GET", "POST"])
def forgot_password(username):
    coach = Coach.query.filter_by(username=username).first_or_404()
    if request.method == "POST":
        if not coach.email or "@" not in coach.email:
            return render_template("forgot_password.html", coach=coach,
                                   sent=False, no_email=True)
        token = secrets.token_urlsafe(32)
        coach.reset_token = token
        coach.reset_token_expires = datetime.datetime.utcnow() + datetime.timedelta(hours=1)
        db.session.commit()
        # Build absolute reset URL
        base = request.host_url.rstrip("/")
        reset_url = f"{base}/coach/{username}/reset-password?token={token}"
        ok = send_reset_email(coach.email, reset_url, coach.full_name)
        return render_template("forgot_password.html", coach=coach,
                               sent=True, email_ok=ok, email=coach.email)
    return render_template("forgot_password.html", coach=coach,
                           sent=False, no_email=False)


@app.route("/coach/<username>/reset-password", methods=["GET", "POST"])
def reset_password(username):
    coach = Coach.query.filter_by(username=username).first_or_404()
    token = request.args.get("token", "")
    # Validate token before showing form
    token_valid = (
        coach.reset_token
        and coach.reset_token == token
        and coach.reset_token_expires
        and datetime.datetime.utcnow() < coach.reset_token_expires
    )
    if request.method == "POST":
        new_pw = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        tok = request.form.get("token", token)
        token_valid_now = (
            coach.reset_token
            and coach.reset_token == tok
            and coach.reset_token_expires
            and datetime.datetime.utcnow() < coach.reset_token_expires
        )
        if not token_valid_now:
            flash("الرابط منتهي الصلاحية أو غير صالح — اطلب رابط جديد.", "error")
            return redirect(url_for("forgot_password", username=username))
        if len(new_pw) < 6:
            flash("كلمة السر قصيرة جداً (6 أحرف على الأقل).", "error")
        elif new_pw != confirm:
            flash("كلمتا السر غير متطابقتين.", "error")
        else:
            coach.password_hash = generate_password_hash(new_pw)
            coach.reset_token = None
            coach.reset_token_expires = None
            db.session.commit()
            flash("تم تغيير كلمة السر بنجاح! ادخل الآن.", "success")
            return redirect(url_for("coach_login", username=username))
    if not token_valid and token:
        flash("الرابط منتهي الصلاحية أو غير صالح — اطلب رابط جديد.", "error")
        return redirect(url_for("forgot_password", username=username))
    return render_template("reset_password.html", coach=coach, token=token)


# ── ADMIN PASSWORD RESET ──────────────────────────────────────────
@app.route("/admin/forgot-password", methods=["GET", "POST"])
def admin_forgot_password():
    if request.method == "POST":
        token = secrets.token_urlsafe(32)
        expires = datetime.datetime.utcnow() + datetime.timedelta(hours=1)
        _admin_reset_tokens[token] = expires
        # Clean expired tokens
        now = datetime.datetime.utcnow()
        for t in list(_admin_reset_tokens.keys()):
            if _admin_reset_tokens[t] < now:
                del _admin_reset_tokens[t]
        base = request.host_url.rstrip("/")
        reset_url = f"{base}/admin/reset-password?token={token}"
        ok = send_reset_email(GMAIL_USER, reset_url, "المشرف")
        return render_template("admin_forgot_password.html", sent=True, email_ok=ok)
    return render_template("admin_forgot_password.html", sent=False)


@app.route("/admin/reset-password", methods=["GET", "POST"])
def admin_reset_password():
    token = request.args.get("token", "")
    now = datetime.datetime.utcnow()
    token_valid = token in _admin_reset_tokens and _admin_reset_tokens.get(token, now) > now
    if not token_valid and request.method == "GET" and token:
        flash("الرابط منتهي الصلاحية أو غير صالح — اطلب رابط جديد.", "error")
        return redirect(url_for("admin_forgot_password"))
    if request.method == "POST":
        tok = request.form.get("token", token)
        new_pw = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        now2 = datetime.datetime.utcnow()
        if tok not in _admin_reset_tokens or _admin_reset_tokens.get(tok, now2) <= now2:
            flash("الرابط منتهي الصلاحية — اطلب رابط جديد.", "error")
            return redirect(url_for("admin_forgot_password"))
        if len(new_pw) < 8:
            flash("كلمة السر قصيرة جداً (8 أحرف على الأقل للمشرف).", "error")
        elif new_pw != confirm:
            flash("كلمتا السر غير متطابقتين.", "error")
        else:
            settings = get_settings()
            settings.admin_password_hash = generate_password_hash(new_pw)
            db.session.commit()
            del _admin_reset_tokens[tok]
            flash("تم تغيير كلمة سر المشرف بنجاح! ادخل الآن.", "success")
            return redirect(url_for("admin_login"))
    return render_template("admin_reset_password.html", token=token)


@app.route("/coach/<username>/client/<int:cid>/notes", methods=["POST"])
def update_client_notes(username, cid):
    coach = Coach.query.filter_by(username=username).first_or_404()
    _gate = coach_gate(coach, username)
    if _gate:
        return _gate
    client = Client.query.filter_by(id=cid, coach_id=coach.id).first_or_404()
    client.coach_notes = request.form.get("coach_notes", "").strip()
    db.session.commit()
    flash("تم حفظ الملاحظات.", "success")
    return redirect(url_for("coach_view_client", username=username, cid=cid))


@app.route("/dz/weight-history/<int:client_id>")
def api_weight_history(client_id):
    logs = WeightLog.query.filter_by(client_id=client_id).order_by(WeightLog.date).all()
    return jsonify([{"date": l.date.isoformat(), "weight": l.weight} for l in logs])


@app.route("/dz/shopping-list/<int:client_id>")
def api_shopping_list(client_id):
    return jsonify({"list": generate_shopping_list(Client.query.get_or_404(client_id))})


# ═══════════════════════ DB MIGRATION ══════════════════════════


# ═══════════════════════ SEED DATA ═════════════════════════════

def seed_database():
    if PlatformSettings.query.count() == 0:
        db.session.add(PlatformSettings(owner_ccp="0079876543 clé 17", owner_whatsapp="0550000000", coach_monthly_price=4200))
        db.session.commit()

    if Coach.query.count() > 0:
        return

    trial_end = datetime.date.today() + datetime.timedelta(days=7)

    karim = Coach(
        username="karim_coach",
        full_name="كريم بوعلام",
        password_hash=generate_password_hash("coach123"),
        baridimob_ccp="0079876543 clé 17",
        edahabia_card="5182 8810 0012 3456",
        whatsapp_number="0550123456",
        bio="مدرب رياضي محترف متخصص في التنشيف والتضخيم — 5 سنوات خبرة مع أكثر من 200 عميل ناجح في الجزائر.",
        subscription_price=4500,
        status="active",
        trial_expires_at=trial_end,
        phone_number="0550123456",
        email="karim@coachpage.dz",
    )
    sara = Coach(
        username="sara_nutrition",
        full_name="سارة بن خليل",
        password_hash=generate_password_hash("sara2024"),
        baridimob_ccp="0087654321 clé 08",
        whatsapp_number="0661234567",
        bio="خبيرة تغذية رياضية متحصلة على شهادة جامعية في علم التغذية — متخصصة في برامج التنشيف وصحة المرأة.",
        subscription_price=3800,
        status="active",
        trial_expires_at=trial_end,
        phone_number="0661234567",
        email="sara@coachpage.dz",
    )
    db.session.add_all([karim, sara])
    db.session.flush()

    clients_data = [
        ("رضا بن علي",     25, 82, 178, "male",   "active",   "تنشيف", "123456", karim.id),
        ("أمين خالدي",     22, 68, 172, "male",   "moderate", "تضخيم", "234567", karim.id),
        ("هناء بوزيدي",    28, 65, 164, "female", "light",    "تنشيف", "345678", karim.id),
        ("يمينة فارسي",    31, 72, 162, "female", "moderate", "تنشيف", "456789", sara.id),
        ("نسرين بلعيد",    26, 58, 168, "female", "active",   "تضخيم", "567890", sara.id),
    ]

    activity_map = {
        "sedentary": 1.2, "light": 1.375, "moderate": 1.55, "active": 1.725, "very_active": 1.9
    }

    for name, age, wt, ht, gender, activity, goal, code, cid in clients_data:
        if gender == "male":
            bmr = 10 * wt + 6.25 * ht - 5 * age + 5
        else:
            bmr = 10 * wt + 6.25 * ht - 5 * age - 161
        tdee = int(bmr * activity_map.get(activity, 1.55))
        cal = int(tdee * (0.85 if goal == "تنشيف" else 1.15))
        prot = int(wt * 2.0)
        fats = int(cal * 0.25 / 9)
        carbs = int((cal - prot * 4 - fats * 9) / 4)

        client = Client(
            coach_id=cid,
            full_name=name, age=age, weight=wt, height=ht,
            gender=gender, activity_level=activity, fitness_goal=goal,
            calories_target=cal, protein_target=prot,
            carbs_target=max(carbs, 50), fats_target=fats,
            access_code=code, active_status=True,
        )
        db.session.add(client)

    db.session.commit()
    print("[CoachPage DZ] Demo data seeded — karim_coach/coach123 & sara_nutrition/sara2024")


def migrate_db():
    from sqlalchemy import text, inspect
    inspector = inspect(db.engine)

    def add_col(table, col, col_type):
        try:
            cols = [c["name"] for c in inspector.get_columns(table)]
            if col not in cols:
                with db.engine.connect() as conn:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}"))
                    conn.commit()
        except Exception:
            pass

    add_col("coaches", "phone_number",      "TEXT DEFAULT ''")
    add_col("coaches", "email",             "TEXT DEFAULT ''")
    add_col("coaches", "edahabia_card",     "TEXT DEFAULT ''")
    add_col("coaches", "reset_token",       "TEXT")
    add_col("coaches", "trial_expires_at",  "TEXT")
    add_col("clients", "phone_number",      "TEXT DEFAULT ''")
    add_col("clients", "coach_notes",       "TEXT DEFAULT ''")

    def create_index(name, table, col):
        try:
            with db.engine.connect() as conn:
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS {name} ON {table}({col})"))
                conn.commit()
        except Exception:
            pass

    create_index("idx_coaches_username",      "coaches",    "username")
    create_index("idx_coaches_status",        "coaches",    "status")
    create_index("idx_clients_access_code",   "clients",    "access_code")
    create_index("idx_clients_coach_id",      "clients",    "coach_id")
    create_index("idx_daily_logs_client_date","daily_logs", "client_id, date")
    create_index("idx_weight_logs_client",    "weight_logs","client_id")
    add_col("daily_logs", "water_ml",       "INTEGER DEFAULT 0")
    add_col("coaches",    "specialty",           "TEXT DEFAULT ''")
    add_col("coaches",    "referral_slug",       "TEXT DEFAULT ''")
    add_col("coaches",    "reset_token_expires", "TEXT")
    add_col("clients", "subscription_expires_at", "DATE")
    add_col("clients", "subscription_plan",       "VARCHAR(20) DEFAULT 'monthly'")
    add_col("clients", "subscription_active",     "BOOLEAN DEFAULT 0")
    add_col("coaches", "photo_url", "TEXT DEFAULT ''")
    add_col("platform_settings", "admin_password_hash", "VARCHAR(256)")


# ═══════════════════════ STARTUP ═══════════════════════════════

with app.app_context():
    db.create_all()
    migrate_db()
    seed_database()
    if not _database_url:
        from sqlalchemy import text as _text
        try:
            with db.engine.connect() as _conn:
                _conn.execute(_text("PRAGMA journal_mode=WAL"))
                _conn.execute(_text("PRAGMA cache_size=-65536"))
                _conn.execute(_text("PRAGMA synchronous=NORMAL"))
                _conn.execute(_text("PRAGMA temp_store=MEMORY"))
                _conn.execute(_text("PRAGMA mmap_size=268435456"))
                _conn.commit()
            print("[CoachPage DZ] SQLite WAL mode enabled — ready for high concurrency")
        except Exception:
            pass
    else:
        print("[CoachPage DZ] Connected to Postgres (Supabase) — production mode")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
