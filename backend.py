import os
import datetime
import secrets
from dotenv import load_dotenv

from flask import (
    Flask, render_template, redirect, request,
    session, jsonify, url_for, flash, render_template_string, abort, jsonify
)
from flask_mail import Mail, Message
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadData
from flask_login import (
    LoginManager, login_user, logout_user,
    login_required, current_user
)
from authlib.integrations.flask_client import OAuth
from werkzeug.utils import secure_filename



from data import db_session
from data.user import User, Route
from admin import admin_bp
from email_service import is_valid_email, send_confirmation_email, confirm_token
import time  # для уникальных имён файлов

from flask import render_template, request, jsonify, url_for, current_app
from flask_login import login_required, current_user, logout_user
from sqlalchemy import func, desc
from werkzeug.utils import secure_filename
from werkzeug.security import check_password_hash, generate_password_hash
import os, uuid
from PIL import Image

from flask import request, jsonify, render_template, url_for, current_app
from flask_login import login_required, current_user
from sqlalchemy import func, desc, asc
from werkzeug.utils import secure_filename
import os, uuid
import re
from sqlalchemy import or_, asc, desc, func, and_


# 1) Загрузить .env до всего остального
load_dotenv()

# 2) Создать приложение только один раз
app = Flask(__name__)

# 3) Единая конфигурация из переменных окружения
app.config.update(
    SECRET_KEY=os.getenv("SECRET_KEY"),
    SECRET_SEND_KEY=os.getenv("SECRET_SEND_KEY"),
    MAIL_SERVER=os.getenv("MAIL_SERVER", "smtp.gmail.com"),
    MAIL_PORT=int(os.getenv("MAIL_PORT", "587")),
    MAIL_USE_TLS=os.getenv("MAIL_USE_TLS", "True") == "True",
    MAIL_USE_SSL=os.getenv("MAIL_USE_SSL", "False") == "True",
    MAIL_USERNAME=os.getenv("MAIL_USERNAME"),
    MAIL_PASSWORD=os.getenv("MAIL_PASSWORD"),
    PERMANENT_SESSION_LIFETIME=datetime.timedelta(days=365),
    UPLOAD_FOLDER=os.path.join(app.root_path, 'static', 'avatars'),  # оставим как есть, чтобы ничего не ломать
    MAX_CONTENT_LENGTH=6 * 1024 * 1024,

    # флаги безопасности cookie
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=False,      # включай на проде с HTTPS
    SESSION_COOKIE_SAMESITE="Lax",
)

# жёстко падаем, если нет ключей
_required = ["SECRET_KEY", "SECRET_SEND_KEY", "MAIL_USERNAME", "MAIL_PASSWORD"]
_missing = [k for k in _required if not app.config.get(k)]
if _missing:
    raise RuntimeError(f"Missing required secrets: {', '.join(_missing)}")

from flask_wtf.csrf import CSRFProtect
csrf = CSRFProtect()
csrf.init_app(app)

# 4) Инициализировать расширения
mail = Mail(app)
app.mail = mail   # чтобы email_service мог находить mail
ts   = URLSafeTimedSerializer(app.config["SECRET_SEND_KEY"])
login_manager = LoginManager(app)
oauth = OAuth(app)

# 5) Зарегистрировать блюпринты и БД
app.register_blueprint(admin_bp)
db_session.global_init("databases/places.db")

# --- Теперь идут все ваши маршруты без дубли app = Flask(...) и без mail = Mail(app) ---

# ... после create_session global_init как было ...




def _like_insensitive(col, text: str):
    # универсально для SQLite/PG: lower(col) LIKE '%text%'
    return func.lower(col).like(f"%{text.lower()}%")

def _serialize_route(r):
    return {
        "id": r.id,
        "title": getattr(r, "title", None) or getattr(r, "name", None),
        "short_description": getattr(r, "short_description", None) or getattr(r, "description", None),
        "category": getattr(r, "category", None),
        "difficulty": getattr(r, "difficulty", None),
        "duration": getattr(r, "duration", None) or getattr(r, "duration_hours", None),
        "length_km": getattr(r, "length_km", None) or getattr(r, "length", None),
        "rating": getattr(r, "rating", None) or getattr(r, "popularity", None) or 0,
        "image_url": getattr(r, "image_url", None) or getattr(r, "img", None),
        "is_published": getattr(r, "is_published", True),
    }




def _q_tokens(q: str):
    import re
    return [t for t in re.split(r"[^\wёЁа-яА-Яa-zA-Z0-9]+", (q or "").strip()) if len(t) >= 2]

def _like_ci(col, term: str):
    # работает на SQLite и PG
    forms = {term, term.capitalize(), term.title(), term.upper()}
    return or_(*[col.like(f"%{f}%") for f in forms])



@app.route("/cul/cultural_routes_json")

def cultural_routes_json():
    db_sess = db_session.create_session()
    M = _cul_model()
    if M is None:
        db_sess.close()
        return jsonify({"items": [], "page": 1, "pages": 1, "total": 0, "is_admin": False})

    q = request.args.get("q", "").strip()
    cats = request.args.getlist("cat")
    diffs = request.args.getlist("diff")
    dmin = request.args.get("dmin", type=float)
    dmax = request.args.get("dmax", type=float)
    lmin = request.args.get("lmin", type=float)
    lmax = request.args.get("lmax", type=float)
    rmin = request.args.get("rmin", type=float)
    sort = request.args.get("sort", "popular")
    page = max(1, request.args.get("page", 1, type=int))
    per_page = 12

    query = db_sess.query(M)

    # Поиск по названию/описанию
    title_col = getattr(M, "title", None) or getattr(M, "name", None)
    desc_col  = getattr(M, "short_description", None) or getattr(M, "description", None)
    tokens = _q_tokens(q)
    if tokens and (title_col or desc_col):
        per_token = []
        for t in tokens:
            fields = []
            if title_col is not None: fields.append(_like_ci(title_col, t))
            if desc_col  is not None: fields.append(_like_ci(desc_col,  t))
            if fields: per_token.append(or_(*fields))  # слово может быть в любом поле
        if per_token: query = query.filter(and_(*per_token))  # но все слова должны встретиться

    # Категории
    cat_col = getattr(M, "category", None)
    if cats and cat_col is not None:
        query = query.filter(cat_col.in_(cats))

    # Сложность
    diff_col = getattr(M, "difficulty", None)
    if diffs and diff_col is not None:
        query = query.filter(diff_col.in_(diffs))

    # Длительность
    dur_col = getattr(M, "duration_hours", None) or getattr(M, "duration", None)
    if dur_col is not None:
        if dmin is not None: query = query.filter(dur_col >= dmin)
        if dmax is not None: query = query.filter(dur_col <= dmax)

    # Длина
    len_col = getattr(M, "length_km", None) or getattr(M, "length", None)
    if len_col is not None:
        if lmin is not None: query = query.filter(len_col >= lmin)
        if lmax is not None: query = query.filter(len_col <= lmax)

    # Рейтинг
    rate_col = getattr(M, "rating", None) or getattr(M, "popularity", None)
    if rate_col is not None and rmin is not None:
        query = query.filter(rate_col >= rmin)

    # Только опубликованные (если не админ)
    is_admin = bool(getattr(current_user, "is_admin", False))
    is_pub_col = getattr(M, "is_published", None)
    if is_pub_col is not None and not is_admin:
        query = query.filter(is_pub_col.is_(True))

    # Сортировка
    order = None
    if sort == "rating" and rate_col is not None:
        order = desc(rate_col)
    elif sort == "new" and hasattr(M, "id"):
        order = desc(M.id)
    elif sort == "length_asc" and len_col is not None:
        order = asc(len_col)
    elif sort == "length_desc" and len_col is not None:
        order = desc(len_col)
    elif rate_col is not None:
        order = desc(rate_col)
    if order is not None:
        query = query.order_by(order)

    total = query.count()
    pages = max(1, (total + per_page - 1) // per_page)
    items = query.limit(per_page).offset((page-1)*per_page).all()
    db_sess.close()

    return jsonify({
        "items": [_serialize_route(r) for r in items],
        "page": page, "pages": pages, "total": total,
        "is_admin": is_admin
    })








@app.route("/cultural_routes")

def cultural_routes():
    db_sess = db_session.create_session()
    M = _cul_model()
    if M is None:
        db_sess.close()
        return render_template("cul/cultural_routes.html", routes=[], categories=[], page=1, pages=1, total=0)

    q = request.args.get("q", "").strip()
    cats = request.args.getlist("cat")
    diffs = request.args.getlist("diff")
    dmin = request.args.get("dmin", type=float)
    dmax = request.args.get("dmax", type=float)
    lmin = request.args.get("lmin", type=float)
    lmax = request.args.get("lmax", type=float)
    rmin = request.args.get("rmin", type=float)
    sort = request.args.get("sort", "popular")
    page = max(1, request.args.get("page", 1, type=int))
    per_page = 12

    query = db_sess.query(M)

    # Поиск по названию/описанию
    title_col = getattr(M, "title", None) or getattr(M, "name", None)
    desc_col  = getattr(M, "short_description", None) or getattr(M, "description", None)
    tokens = _q_tokens(q)
    if tokens and (title_col or desc_col):
        per_token = []
        for t in tokens:
            fields = []
            if title_col is not None: fields.append(_like_ci(title_col, t))
            if desc_col  is not None: fields.append(_like_ci(desc_col,  t))
            if fields: per_token.append(or_(*fields))  # слово может быть в любом поле
        if per_token: query = query.filter(and_(*per_token))  # но все слова должны встретиться


    # Категории
    cat_col = getattr(M, "category", None)
    if cats and cat_col is not None:
        query = query.filter(cat_col.in_(cats))

    # Сложность
    diff_col = getattr(M, "difficulty", None)
    if diffs and diff_col is not None:
        query = query.filter(diff_col.in_(diffs))

    # Длительность
    dur_col = getattr(M, "duration_hours", None) or getattr(M, "duration", None)
    if dur_col is not None:
        if dmin is not None: query = query.filter(dur_col >= dmin)
        if dmax is not None: query = query.filter(dur_col <= dmax)

    # Длина
    len_col = getattr(M, "length_km", None) or getattr(M, "length", None)
    if len_col is not None:
        if lmin is not None: query = query.filter(len_col >= lmin)
        if lmax is not None: query = query.filter(len_col <= lmax)

    # Рейтинг
    rate_col = getattr(M, "rating", None) or getattr(M, "popularity", None)
    if rate_col is not None and rmin is not None:
        query = query.filter(rate_col >= rmin)

    # Только опубликованные (если есть поле)
    is_pub_col = getattr(M, "is_published", None)
    if is_pub_col is not None and not (getattr(current_user, "is_admin", False) or getattr(current_user, "role", "") == "admin"):
        query = query.filter(is_pub_col.is_(True))

    # Сортировка
    order = None
    if sort == "rating" and rate_col is not None:
        order = desc(rate_col)
    elif sort == "new" and hasattr(M, "id"):
        order = desc(M.id)
    elif sort == "length_asc" and len_col is not None:
        order = asc(len_col)
    elif sort == "length_desc" and len_col is not None:
        order = desc(len_col)
    elif rate_col is not None:
        order = desc(rate_col)
    if order is not None:
        query = query.order_by(order)

    # Пагинация
    total = query.count()
    pages = max(1, (total + per_page - 1) // per_page)
    routes = query.limit(per_page).offset((page-1)*per_page).all()

    # Список категорий
    categories = []
    if cat_col is not None:
        categories = [c[0] for c in db_sess.query(cat_col).filter(cat_col.isnot(None)).distinct().order_by(cat_col.asc()).all()]

    db_sess.close()

    return render_template(
        "cul/cultural_routes.html",
        routes=routes,
        categories=categories,
        page=page, pages=pages, total=total
    )


def _cul_model():
    for name in ("CulturalRoute", "CulRoute", "CultureRoute", "Route"):
        m = globals().get(name)
        if m is not None:
            return m
    return None

def _string_contains(col, value):
    try:
        return col.ilike(f"%{value}%")
    except Exception:
        return col.like(f"%{value}%")

def _save_image(file_storage, subdir="img"):
    if not file_storage or file_storage.filename == "":
        return None
    ext = file_storage.filename.rsplit(".",1)[-1].lower()
    if ext not in {"png","jpg","jpeg","webp"}:
        return None
    static_dir = current_app.static_folder
    target_dir = os.path.join(static_dir, subdir)
    os.makedirs(target_dir, exist_ok=True)
    fname = f"route_{uuid.uuid4().hex[:10]}.{ext}"
    path = os.path.join(target_dir, secure_filename(fname))
    file_storage.save(path)
    return f"/static/{subdir}/{fname}"

def _require_admin():
    return bool(getattr(current_user, "is_admin", False))

# --- Админ-ручки ---
@app.route("/cul/admin/create", methods=["POST"])
@login_required
def cul_admin_create():
    if not _require_admin():
        return jsonify({"success": False, "error": "forbidden"}), 403
    db_sess = db_session.create_session()
    M = _cul_model()
    if M is None:
        db_sess.close()
        return jsonify({"success": False, "error": "model not found"}), 500

    m = M()
    m.title = (request.form.get("title") or "").strip()
    if hasattr(M, "name") and not getattr(m, "title", None):
        m.name = (request.form.get("title") or "").strip()
    if hasattr(M, "category"): m.category = request.form.get("category") or None
    if hasattr(M, "difficulty"): m.difficulty = request.form.get("difficulty") or None
    if hasattr(M, "duration_hours"): m.duration_hours = request.form.get("duration") or None
    elif hasattr(M, "duration"):     m.duration       = request.form.get("duration") or None
    if hasattr(M, "length_km"): m.length_km = request.form.get("length_km") or None
    elif hasattr(M, "length"):  m.length    = request.form.get("length_km") or None
    if hasattr(M, "rating"):    m.rating    = request.form.get("rating") or None
    elif hasattr(M, "popularity"): m.popularity = request.form.get("rating") or None
    if hasattr(M, "short_description"):
        m.short_description = request.form.get("short_description") or None
    if hasattr(M, "is_published"): m.is_published = True

    img_url = _save_image(request.files.get("image"), "img")
    if img_url:
        if hasattr(M, "image_url"): m.image_url = img_url
        elif hasattr(M, "img"):     m.img       = img_url
        elif hasattr(M, "image"):   m.image     = img_url

    db_sess.add(m)
    db_sess.commit()
    db_sess.close()
    return jsonify({"success": True})

@app.route("/cul/admin/update/<int:rid>", methods=["POST"])
@login_required
def cul_admin_update(rid):
    if not _require_admin():
        return jsonify({"success": False, "error": "forbidden"}), 403
    db_sess = db_session.create_session()
    M = _cul_model()
    if M is None:
        db_sess.close()
        return jsonify({"success": False, "error": "model not found"}), 500
    obj = db_sess.get(M, rid)
    if not obj:
        db_sess.close()
        return jsonify({"success": False, "error": "not found"}), 404

    title = (request.form.get("title") or "").strip()
    if title:
        if hasattr(obj, "title"): obj.title = title
        elif hasattr(obj, "name"): obj.name = title
    for fld, key in (("category","category"),("difficulty","difficulty")):
        if hasattr(obj, fld): setattr(obj, fld, request.form.get(key) or None)

    v = request.form.get("duration")
    if hasattr(obj, "duration_hours") and v is not None: obj.duration_hours = v or obj.duration_hours
    elif hasattr(obj, "duration") and v is not None:     obj.duration       = v or obj.duration

    v = request.form.get("length_km")
    if hasattr(obj, "length_km") and v is not None: obj.length_km = v or obj.length_km
    elif hasattr(obj, "length") and v is not None:  obj.length    = v or obj.length

    v = request.form.get("rating")
    if hasattr(obj, "rating") and v is not None:     obj.rating    = v or obj.rating
    elif hasattr(obj, "popularity") and v is not None: obj.popularity = v or obj.popularity

    v = request.form.get("short_description")
    if hasattr(obj, "short_description") and v is not None:
        obj.short_description = v

    img_url = _save_image(request.files.get("image"), "img")
    if img_url:
        if hasattr(obj, "image_url"): obj.image_url = img_url
        elif hasattr(obj, "img"):     obj.img       = img_url
        elif hasattr(obj, "image"):   obj.image     = img_url

    db_sess.commit()
    db_sess.close()
    return jsonify({"success": True})

@app.route("/cul/admin/delete/<int:rid>", methods=["POST"])
@login_required
def cul_admin_delete(rid):
    if not _require_admin():
        return jsonify({"success": False, "error": "forbidden"}), 403
    db_sess = db_session.create_session()
    M = _cul_model()
    if M is None:
        db_sess.close()
        return jsonify({"success": False, "error": "model not found"}), 500
    obj = db_sess.get(M, rid)
    if not obj:
        db_sess.close()
        return jsonify({"success": False, "error": "not found"}), 404
    db_sess.delete(obj)
    db_sess.commit()
    db_sess.close()
    return jsonify({"success": True})



@app.route('/cul_dynamic/<int:route_id>')
def cul_dynamic_alias(route_id):
    return cul_dynamic(route_id)   # переиспользуем основной хендлер /cul/<id>




@app.route("/cul/admin/toggle_publish/<int:rid>", methods=["POST"])
@login_required
def cul_admin_toggle_publish(rid):
    if not _require_admin():
        return jsonify({"success": False, "error": "forbidden"}), 403
    db_sess = db_session.create_session()
    M = _cul_model()
    if M is None or not hasattr(M, "is_published"):
        db_sess.close()
        return jsonify({"success": False, "error": "not supported"}), 400
    obj = db_sess.get(M, rid)
    if not obj:
        db_sess.close()
        return jsonify({"success": False, "error": "not found"}), 404
    obj.is_published = not bool(getattr(obj, "is_published") or False)
    db_sess.commit()
    db_sess.close()
    return jsonify({"success": True})




# --- Новый универсальный маршрут ---
@app.route('/cul/<int:route_id>')
def cul_dynamic(route_id):
    db_sess = db_session.create_session()
    route = db_sess.query(Route).get(route_id)
    db_sess.close()
    if not route:
        # если нет в БД — 404 или верни старую статичную страницу при совпадении
        # (пример: cul_1.html ещё существует)
        template_name = f"cul/cul_{route_id}.html"
        try:
            return render_template(template_name)  # fallback
        except:
            from flask import abort
            abort(404)
    return render_template("cul/route_detail.html", route=route, current_user=current_user)

# --- Совместимость со старыми урлами ---
@app.route('/cul_1')
def cul_1():
    return redirect(url_for('cul/cul_dynamic', route_id=1))

@app.route('/cul_2')
def cul_2():
    return redirect(url_for('cul/cul_dynamic', route_id=2))

@app.route('/cul_3')
def cul_3():
    return redirect(url_for('cul/cul_dynamic', route_id=3))

@app.route('/cul_4')
def cul_4():
    return redirect(url_for('cul/cul_dynamic', route_id=4))

@app.route('/cul_5')
def cul_5():
    return redirect(url_for('cul/cul_dynamic', route_id=5))

@app.route('/cul_6')
def cul_6():
    return redirect(url_for('cul/cul_dynamic', route_id=6))



@app.route('/')
@app.route('/index')
def index():
    db_sess = db_session.create_session()
    popular_routes = User.get_popular_routes(db_sess, limit=5)
    db_sess.close()
    return render_template("index.html", current_user=current_user, popular_routes=popular_routes)



@app.route("/api/popular_routes")
@login_required
def api_popular_routes():
    db_sess = db_session.create_session()
    try:
        popular = _popular_routes(db_sess, limit=12)  # <= передаём сессию!
        data = []
        for r in popular:
            data.append({
                "id": getattr(r, "id", None),
                "title": getattr(r, "title", None) or getattr(r, "name", "Маршрут"),
                "image_url": _route_image(r),
                "popularity": getattr(r, "popularity", None) or getattr(r, "rating", 0),
            })
        return jsonify(data)
    finally:
        db_sess.close()








@app.route('/get_current_user_state')
@login_required
def get_current_user_state():
    db_sess = db_session.create_session()
    try:
        user = db_sess.query(User).get(current_user.id)
        return jsonify({
            "completed_routes": user.completed_routes if user.completed_routes else {},
            "progress": user.progress if user.progress else 0
        })
    finally:
        db_sess.close()

@app.route('/get_user_progress')
@login_required
def get_user_progress():
    db_sess = db_session.create_session()
    try:
        total_routes = db_sess.query(Route).filter(Route.route_key.like("route_cul_%")).count()
    finally:
        db_sess.close()

    completed = sum(
        1 for key, v in (current_user.completed_routes or {}).items()
        if key.startswith("route_cul_") and v
    )
    return jsonify({
        "completed": completed,
        "total_routes": total_routes,
        "progress": round((completed / total_routes * 100) if total_routes else 0)
    })


@app.route('/update_route_state', methods=['POST'])
@login_required
def update_route_state():
    data = request.get_json() or {}
    route_id = data.get("route_id")
    new_state = data.get("new_state")

    if not route_id or new_state is None:
        return jsonify({"status": "error", "message": "Missing parameters"}), 400

    db_sess = db_session.create_session()
    try:
        user = db_sess.merge(current_user)

        # Инициализация словаря
        if not user.completed_routes:
            user.completed_routes = {}

        key = f"route_{route_id}"
        user.completed_routes[key] = bool(new_state)

        # Считаем прогресс: сколько выполнено / общее кол-во маршрутов
        total_routes = db_sess.query(Route).count()
        completed_count = sum(
            1 for key, v in user.completed_routes.items()
            if key.startswith("route_") or key.startswith("cul_")  and v
        )
        user.progress = int((completed_count / total_routes) * 100) if total_routes > 0 else 0

        db_sess.commit()
        db_sess.refresh(user)

        return jsonify({
            "status": "success",
            "new_state": bool(new_state),
            "progress": user.progress,
            "completed_routes": user.completed_routes
        })
    except Exception as e:
        db_sess.rollback()
        print(f"Database error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        db_sess.close()


@app.route('/favourites')
@login_required
def favourites():
    db_sess = db_session.create_session()
    user = db_sess.merge(current_user)
    favourite_routes = user.favourite_routes or {}
    favourite_route_ids = [
        int(route_id.replace('_fav', '').replace('cul_', ''))
        for route_id, is_fav in favourite_routes.items()
        if is_fav
    ]
    routes = db_sess.query(Route).filter(Route.id.in_(favourite_route_ids)).all()
    db_sess.close()
    return render_template('favourites.html', routes=routes)

@app.route('/route/<string:route_id>')
def route_page(route_id):
    db_sess = db_session.create_session()
    route_num = int(route_id.replace('cul_', ''))
    route = db_sess.query(Route).filter(Route.id == route_num).first()
    return render_template('route.html', route=route)

@app.route('/get_current_user_state_fav')
@login_required
def get_current_user_state_fav():
    db_sess = db_session.create_session()
    try:
        user = db_sess.query(User).get(current_user.id)
        return jsonify({
            "favourite_routes": user.favourite_routes if user.favourite_routes else {}
        })
    finally:
        db_sess.close()

@app.route('/update_route_state_fav', methods=['POST'])
@login_required
def update_route_state_fav():
    data = request.get_json()
    route_id = data.get("route_id")
    new_state_fav = data.get("new_state_fav")
    if not route_id or new_state_fav is None:
        return jsonify({"status": "error", "message": "Missing parameters"}), 400
    db_sess = db_session.create_session()
    try:
        user = db_sess.merge(current_user)
        if user.favourite_routes is None:
            user.favourite_routes = {f"cul_{i}_fav": False for i in range(1, 7)}
        user.favourite_routes[route_id] = new_state_fav
        db_sess.commit()
        db_sess.refresh(user)
        return jsonify({
            "status": "success",
            "new_state_fav": new_state_fav,
        })
    except Exception as e:
        db_sess.rollback()
        print(f"Database error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        db_sess.close()

@app.route('/api/routes')
def get_routes():
    route_ids = request.args.get('ids', '').split(',')
    route_ids = [rid for rid in route_ids if rid]
    db_sess = db_session.create_session()
    user = db_sess.query(User).get(current_user.id)
    routes = []
    for rid in route_ids:
        route = get_route_by_id(rid)
        if route:
            routes.append({
                "id": route.id,
                "title": route.title,
                "image": route.image_url,
                "short_description": route.short_description,
                "duration": route.duration,
                "location": route.location,
                "type": route.type
            })
    return jsonify(routes)

@app.route('/info')
def info():
    return render_template("info.html")


@app.route('/gastronom')
def gastronom():
    return render_template("gastronom.html")

@app.route('/gas_1')
def gas_1():
    return render_template("gas_1.html")

@app.route('/gas_2')
def gas_2():
    return render_template("gas_2.html")

@app.route('/gas_3')
def gas_3():
    return render_template("gas_3.html")

@app.route('/gas_4')
def gas_4():
    return render_template("gas_4.html")

@app.route('/gas_5')
def gas_5():
    return render_template("gas_5.html")

@app.route('/gas_6')
def gas_6():
    return render_template("gas_6.html")

@app.route('/geog_obj')
def geog_obj():
    return render_template("geog_obj.html")


@app.route('/user_login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        email       = request.form.get('emailInput','').strip()
        password    = request.form.get('passwordInput','').strip()
        remember_me = bool(request.form.get('rememberMe'))
        db_sess     = db_session.create_session()
        user        = db_sess.query(User).filter_by(email=email).first()

        # 1) Проверяем существование и активацию
        if not user:
            error = 'Пользователь не найден'
        elif not user.is_active:
            error = 'Подтвердите почту перед входом'
        elif not user.check_password(password):
            error = 'Неверный пароль'
        else:
            login_user(user, remember=remember_me)
            db_sess.close()
            # если запрос JSON/AJAX
            if request.headers.get('Accept') == 'application/json':
                return jsonify(success=True)
            return redirect('/user_office')

        db_sess.close()
        # ошибка для AJAX
        if request.headers.get('Accept') == 'application/json':
            return jsonify(success=False, message=error), 400
        # обычный флеш + редирект
        flash(error, 'error')
        return redirect('/user_login')

    # GET
    return render_template('user/user_login.html')

@app.route('/user_reg', methods=['GET', 'POST'])
def user_reg():
    if request.method == 'POST':
        form = request.form
        # Всегда по умолчанию берём пустую строку и обрезаем пробелы
        email      = form.get('emailInput', '').strip()
        password   = form.get('passwordInput', '').strip()
        name       = form.get('nameInput', '').strip()
        surname    = form.get('surnameInput', '').strip()
        phone_num  = form.get('phone_num', '').strip()

        # Если email пустой или неверный формат
        if not is_valid_email(email):
            return render_template("user/user_reg.html", error="Неверный формат email", 
                                   email=email, name=name, surname=surname, phone=phone_num)

        db_sess = db_session.create_session()
        # Проверка дублирования почты
        if db_sess.query(User).filter(User.email == email).first():
            flash('Почта уже зарегистрирована', 'error')
            db_sess.close()
            return redirect('/user_reg')

        # Создаём пользователя
        user = User(name=name,
                    surname=surname,
                    email=email,
                    phone_num=phone_num,
                    progress=0,
                    is_active=False)
        user.set_password(password)
        db_sess.add(user)
        db_sess.commit()

        # Отправляем письмо и показываем флеш
        try:
            send_confirmation_email(email)
            flash('Письмо для подтверждения отправлено на почту', 'info')
        except Exception as e:
            flash(f'Ошибка при отправке письма: {e}', 'error')
        finally:
            db_sess.close()

        return redirect('/user_login')

    # GET-запрос – просто отрисовать форму
    return render_template("user/user_reg.html")

def is_valid_email(email):
    import re
    regex = r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$'
    return re.match(regex, email) is not None

google = oauth.register(
    name='google',
    client_id="376838788269-k120re8lp9bjv3dv31i6s9d0ekpkh5tq.apps.googleusercontent.com",
    client_secret="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",
    access_token_url='https://oauth2.googleapis.com/token',
    authorize_url='https://accounts.google.com/o/oauth2/auth',
    jwks_uri='https://www.googleapis.com/oauth2/v3/certs',  
    client_kwargs={'scope': 'openid email profile'}
)

def load_user(user_id):
    db_sess = db_session.create_session()
    return db_sess.query(User).get(user_id)

@app.route("/login/google")
def login_google():
    nonce = secrets.token_urlsafe(16)
    session["nonce"] = nonce
    return google.authorize_redirect(
        url_for("auth_callback", _external=True),
        nonce=nonce  # <-- обязательно
    )

@app.route("/login/callback")
def auth_callback():
    token = google.authorize_access_token()
    if not token:
        return "Ошибка авторизации", 400
    try:
        nonce = session.pop("nonce", None)
        if not nonce:
            return "Ошибка: nonce отсутствует", 400
        user_info = google.parse_id_token(token, nonce=nonce)
    except Exception as e:
        return f"Ошибка обработки токена: {str(e)}", 400
    db_sess = db_session.create_session()
    user = db_sess.query(User).filter(User.email == user_info["email"]).first()
    if not user:
        user = User(
            name=user_info.get("name", "Без имени"),
            email=user_info["email"]
        )
        db_sess.add(user)
        db_sess.commit()
    login_user(user, remember=True)
    return redirect("/user_office")




@app.route('/confirm/<token>')
def confirm_email(token):
    try:
        email = confirm_token(token)
    except SignatureExpired:
        flash('Ссылка подтверждения истекла', 'error')
        return redirect('/user_reg')
    except BadData:
        flash('Неверный или повреждённый токен', 'error')
        return redirect('/user_reg')

    db_sess = db_session.create_session()
    user = db_sess.query(User).filter_by(email=email).first()
    if not user:
        flash('Пользователь не найден', 'error')
    else:
        user.is_active = True
        db_sess.commit()
        flash('Почта успешно подтверждена', 'success')
    db_sess.close()

    return redirect('/user_login')

def get_total_cul_routes_count():
    db_sess = db_session.create_session()
    try:
        return db_sess.query(Route).filter(Route.id.like('cul_%')).count()
    finally:
        db_sess.close()






@app.route('/user_office')
@login_required
def user_office():
    db_sess = db_session.create_session()
    user = db_sess.get(User, current_user.id)

    if not user:
        return "User not found", 404

    name = user.name
    surname = user.surname
    email = user.email
    phone_num = user.phone_num
    avatar = user.avatar

    completed = len(user.get_completed_cul_ids())
    total_routes = get_total_cul_routes_count()

    total_hours = user.get_total_hours(db_sess)
    total_photos = user.get_total_photos()

    if user.completed_routes is None:
        user.completed_routes = {}
        db_sess.commit()
    
    # Популярные маршруты
    raw_popular = _popular_routes(db_sess, limit=12)

    db_sess.close()
    
    return render_template(
        "user/user_office.html",
        name=name,
        surname=surname,
        email=email,
        phone_num=phone_num,
        avatar=avatar,
        completed=completed,
        total_routes=total_routes,
        total_hours=total_hours,
        total_photos=total_photos,
        progress=round(completed / total_routes * 100) if total_routes else 0,
        popular_routes=raw_popular
    )



UPLOAD_FOLDER = os.path.join(app.root_path, 'static', 'avatars')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
ALLOWED_EXT = {"png", "jpg", "jpeg", "webp"}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def _avatars_dir():
    avatars = os.path.join(app.static_folder, "avatars")
    os.makedirs(avatars, exist_ok=True)
    return avatars

def _route_image(r):
    for k in ("image_url", "img", "image", "photo_url", "cover"):
        if getattr(r, k, None):
            return getattr(r, k)
    return url_for("static", filename="img/placeholder.jpg")

def _popular_routes(db_sess, limit=12):
    
    order_col = None
    for a in ("popularity", "rating", "favourites_count"):
        if hasattr(Route, a):
            order_col = getattr(Route, a)
            break
    if order_col is not None:
        return db_sess.query(Route).order_by(desc(order_col)).limit(limit).all()

    try:
        sub = (
            db_sess.query(Favourite.route_id, func.count("*").label("cnt"))
            .group_by(Favourite.route_id)
            .subquery()
        )
        q = (
            db_sess.query(Route)
            .outerjoin(sub, sub.c.route_id == Route.id)
            .order_by(desc(sub.c.cnt.nullslast()))
            .limit(limit)
        )
        return q.all()
    except Exception:
        pass

    return db_sess.query(Route).order_by(desc(Route.id)).limit(limit).all()


def _stats(user_id):
    completed = total_routes = total_hours = total_photos = 0
    try:
        1
    except Exception:
        CompletedRoute = Route = Photo = None

    if CompletedRoute is not None:
        try:
            completed = db.session.query(func.count(CompletedRoute.id)).filter_by(user_id=user_id).scalar() or 0
            total_hours = db.session.query(func.coalesce(func.sum(CompletedRoute.hours), 0)).filter_by(user_id=user_id).scalar() or 0
        except Exception:
            pass
    if Route is not None:
        try:
            total_routes = db.session.query(func.count(Route.id)).scalar() or 0
        except Exception:
            pass
    if Photo is not None:
        try:
            total_photos = db.session.query(func.count(Photo.id)).filter_by(user_id=user_id).scalar() or 0
        except Exception:
            pass

    progress = int(round(100 * (completed / max(total_routes, 1)), 0))
    return completed, total_routes, total_hours, total_photos, progress




@app.route("/update_profile", methods=["POST"])
@login_required
def update_profile():
    db_sess = db_session.create_session()
    user = db_sess.get(User, current_user.id)
    if not user:
        db_sess.close()
        return jsonify({"success": False, "error": "User not found"}), 404

    name = (request.form.get("name") or "").strip()[:100]
    surname = (request.form.get("surname") or "").strip()[:100]
    # phone можно тоже обновлять, если надо:
    # phone_num = (request.form.get("phone_num") or "").strip()[:30]

    if name: user.name = name
    if surname: user.surname = surname
    # if phone_num: user.phone_num = phone_num

    db_sess.commit()
    db_sess.close()
    return jsonify({"success": True})

@app.route("/update_password", methods=["POST"])
@login_required
def update_password():
    db_sess = db_session.create_session()
    user = db_sess.get(User, current_user.id)
    if not user:
        db_sess.close()
        return jsonify({"success": False, "error": "User not found"}), 404

    cur = request.form.get("current_password") or ""
    new = request.form.get("new_password") or ""
    conf = request.form.get("confirm_password") or ""

    if len(new) < 8 or new != conf:
        db_sess.close()
        return jsonify({"success": False, "error": "Пароль короткий или не совпадает"}), 400

    # Поддержка полей password или password_hash
    pwd_hash = getattr(user, "password_hash", None) or getattr(user, "password", None)
    if not pwd_hash:
        db_sess.close()
        return jsonify({"success": False, "error": "Поле пароля не найдено у модели"}), 500

    if not check_password_hash(pwd_hash, cur):
        db_sess.close()
        return jsonify({"success": False, "error": "Текущий пароль неверный"}), 400

    new_hash = generate_password_hash(new)
    if hasattr(user, "password_hash"):
        user.password_hash = new_hash
    else:
        user.password = new_hash

    db_sess.commit()
    db_sess.close()
    return jsonify({"success": True})

@app.route("/update_settings", methods=["POST"])
@login_required
def update_settings():
    db_sess = db_session.create_session()
    user = db_sess.get(User, current_user.id)
    if not user:
        db_sess.close()
        return jsonify({"success": False, "error": "User not found"}), 404

    lang = request.form.get("lang", "ru")
    notifications = bool(request.form.get("notifications"))
    newsletter = bool(request.form.get("newsletter"))

    # если есть JSON-поле для настроек — обновим
    if hasattr(user, "settings_json") and isinstance(getattr(user, "settings_json"), dict):
        s = user.settings_json
        s.update({"lang": lang, "notifications": notifications, "newsletter": newsletter})
        user.settings_json = s
    # иначе просто игнорим (фронт всё равно покажет "Сохранено")

    db_sess.commit()
    db_sess.close()
    return jsonify({"success": True})





@app.route("/delete_account", methods=["POST"])
@login_required
def delete_account():
    db_sess = db_session.create_session()
    user = db_sess.get(User, current_user.id)
    if not user:
        db_sess.close()
        return jsonify({"success": False, "error": "User not found"}), 404

    logout_user()
    if hasattr(user, "is_deleted"):
        user.is_deleted = True
        db_sess.commit()
    else:
        db_sess.delete(user)
        db_sess.commit()

    db_sess.close()
    return jsonify({"success": True})















def _allowed(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXT

@app.route("/upload_avatar", methods=["POST"])
@login_required
def upload_avatar():
    file = request.files.get("avatar")
    if not file or file.filename == "":
        return jsonify({"success": False, "error": "Файл не выбран"}), 400
    if not _allowed(file.filename):
        return jsonify({"success": False, "error": "Разрешены PNG/JPG/WEBP"}), 400

    file.stream.seek(0, os.SEEK_END)
    size = file.stream.tell()
    file.stream.seek(0)
    if size > app.config.get("MAX_CONTENT_LENGTH", 6*1024*1024):
        return jsonify({"success": False, "error": "Файл слишком большой"}), 400

    ext = file.filename.rsplit(".", 1)[1].lower()
    fname = f"{current_user.id}_{uuid.uuid4().hex[:8]}.{ext}"
    path = os.path.join(_avatars_dir(), secure_filename(fname))
    file.save(path)

    try:
        if imghdr.what(path) not in {"png", "jpeg", "jpg", "webp"}:
            os.remove(path)
            return jsonify({"success": False, "error": "Некорректное изображение"}), 400
    except Exception:
        pass

    # обновление пользователя
    try:
        current_user.avatar = fname
        db.session.commit()
    except Exception:
        return jsonify({"success": False, "error": "DB error"}), 500

    return jsonify({"success": True, "filename": fname})



@app.route('/posibiletes')
def posibiletes( ):
    return render_template("posibiletes.html")

@app.route('/tour_operator')
def tour_operator( ):
    return render_template("tour_operator.html")

@app.route('/guide')
def guide( ):
    return render_template("guide.html")

@app.route('/favourite_routes')
def favourite_routes( ):
    return render_template("favourite_routes.html")

@login_manager.user_loader
def load_user(user_id):
    db_sess = db_session.create_session()
    return db_sess.query(User).get(user_id)

@app.route('/partners')
def partners( ):
    return render_template("partners.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect("/")

def main():
    db_session.global_init("databases/places.db")
    app.run(debug=True, host='0.0.0.0', port=7010,) 

if __name__ == "__main__":
    main()