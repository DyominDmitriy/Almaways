import os
import datetime
import secrets
from dotenv import load_dotenv
import bleach
from flask_login import UserMixin
allowed_tags = ['iframe']
allowed_attrs = {
    'iframe': ['src', 'width', 'height', 'style']
}
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
import os, uuid, imghdr

from flask import request, jsonify, render_template, url_for, current_app
from flask_login import login_required, current_user
from sqlalchemy import func, desc, asc
from werkzeug.utils import secure_filename
import os, uuid
import re
from sqlalchemy import or_, asc, desc, func, and_
from cul_routes import cul_bp





# 1) Загрузить .env до всего остального
load_dotenv()

# 2) Создать приложение только один раз
app = Flask(__name__)
oauth = OAuth(app)
app.register_blueprint(cul_bp)
# 3) Единая конфигурация из переменных окружения
BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:7010")
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
    UPLOAD_FOLDER=os.path.join(app.root_path, 'static', 'avatars'),
    MAX_CONTENT_LENGTH=6 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=False,   # включи на проде
    SESSION_COOKIE_SAMESITE="Lax",
)
google = oauth.register(
    name="google",
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)
# жёстко падаем, если нет ключей
_required = ["SECRET_KEY", "SECRET_SEND_KEY", "MAIL_USERNAME", "MAIL_PASSWORD"]
_missing = [k for k in _required if not app.config.get(k)]
if _missing:
    raise RuntimeError(f"Missing required secrets: {', '.join(_missing)}")



# 4) Инициализировать расширения
mail = Mail(app)
app.mail = mail   # чтобы email_service мог находить mail
ts   = URLSafeTimedSerializer(app.config["SECRET_SEND_KEY"])

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login_google"



from flask_login import UserMixin

# 5) Зарегистрировать блюпринты и БД
app.register_blueprint(admin_bp)
db_session.global_init("databases/places.db")

# --- Теперь идут все ваши маршруты без дубли app = Flask(...) и без mail = Mail(app) ---

# ... после create_session global_init как было ...




def _like_insensitive(col, text: str):
    # универсально для SQLite/PG: lower(col) LIKE '%text%'
   return func.lower(col).like(f"%{text.lower()}%", escape="\\")

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

from sqlalchemy import or_

def _like_ci(col, term: str):
    # Ограничиваем длину
    term = str(term)[:100]
    # Экранируем спецсимволы
    safe_term = term.replace("%", "\\%").replace("_", "\\_")
    
    forms = {
        safe_term,
        safe_term.capitalize(),
        safe_term.title(),
        safe_term.upper(),
    }
    return or_(*[col.like(f"%{f}%", escape="\\") for f in forms])



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

    if not is_admin:
        query = query.filter(Route.is_published.is_(True))

    



    return jsonify({
        "items": [_serialize_route(r) for r in items],
        "page": page, "pages": pages, "total": total,
        "is_admin": is_admin
    })








@app.route("/cultural_routes")
def cultural_routes():
    db_sess = db_session.create_session()
    try:
        query = db_sess.query(Route)
        if not bool(getattr(current_user, "is_admin", False)):
            query = query.filter(Route.is_published.is_(True))
        routes = query.order_by(desc(Route.id)).all()
        if not bool(getattr(current_user, "is_admin", False)):
            query = query.filter(Route.is_published.is_(True))

        categories = []  # если поле category появится — можно собрать distinct
        return render_template("cul/cultural_routes.html", routes=routes, categories=categories)
    finally:
        db_sess.close()



def _string_contains(col, value):
    value = str(value)[:100]
    try:
        return col.ilike(f"%{value}%", escape="\\")
    except Exception:
        return col.like(f"%{value}%", escape="\\")

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
    if not getattr(current_user, "is_admin", False):
        return jsonify({"success": False, "error": "forbidden"}), 403
    db_sess = db_session.create_session()
    try:
        r = Route(
            title=(request.form.get("title") or "").strip(),
            short_description=request.form.get("short_description") or "",
            description=request.form.get("description") or "",
            duration=(float(request.form.get("duration")) if request.form.get("duration") else None),
            difficulty=request.form.get("difficulty") or "",
            is_published=True,
        )
        # картинка (по желанию: secure_filename и своя папка)
        img = request.files.get("image")
        if img and img.filename:
            os.makedirs(os.path.join(app.static_folder, "uploads"), exist_ok=True)
            path = os.path.join(app.static_folder, "uploads", img.filename)
            img.save(path)
            r.image_url = img.filename

        db_sess.add(r)
        db_sess.flush()
        r.route_key = f"route_cul_{r.id}"
        db_sess.commit()
        return jsonify({"success": True, "id": r.id})
    except Exception as e:
        db_sess.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        db_sess.close()

@app.route("/cul/admin/update/<int:rid>", methods=["POST"])
@login_required
def cul_admin_update(rid):
    if not getattr(current_user, "is_admin", False):
        return jsonify({"success": False, "error": "forbidden"}), 403
    db_sess = db_session.create_session()
    try:
        obj = db_sess.get(Route, rid)
        if not obj:
            return jsonify({"success": False, "error": "not found"}), 404

        def set_if_nonempty(attr, form_key, conv=None):
            if form_key in request.form:
                v = request.form.get(form_key)
                if v is not None and v != "":
                    obj.__setattr__(attr, conv(v) if conv else v)

        set_if_nonempty("title", "title", str)
        set_if_nonempty("short_description", "short_description", str)
        set_if_nonempty("description", "description", str)
        set_if_nonempty("difficulty", "difficulty", str)
        set_if_nonempty("duration", "duration", float)

        # необязательные поля, если появятся
        if "length_km" in request.form and request.form.get("length_km") != "":
            try: obj.length_km = float(request.form.get("length_km"))
            except: pass
        if "rating" in request.form and request.form.get("rating") != "":
            try: obj.rating = float(request.form.get("rating"))
            except: pass
        if "category" in request.form:
            v = (request.form.get("category") or "").strip()
            if v != "":
                obj.category = v

        # картинка
        img = request.files.get("image")
        if img and img.filename:
            os.makedirs(os.path.join(app.static_folder, "uploads"), exist_ok=True)
            path = os.path.join(app.static_folder, "uploads", img.filename)
            img.save(path)
            obj.image_url = img.filename

        db_sess.commit()
        return jsonify({"success": True})
    except Exception as e:
        db_sess.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        db_sess.close()


@app.route("/cul/admin/get/<int:rid>", methods=["GET"])
@login_required
def cul_admin_get(rid):
    if not getattr(current_user, "is_admin", False):
        return jsonify({"success": False, "error": "forbidden"}), 403
    db_sess = db_session.create_session()
    try:
        obj = db_sess.get(Route, rid)
        if not obj:
            return jsonify({"success": False, "error": "not found"}), 404
        data = {
            "id": obj.id,
            "title": obj.title,
            "short_description": obj.short_description,
            "difficulty": obj.difficulty,
            "duration": obj.duration,
            "length_km": getattr(obj, "length_km", None),
            "rating": getattr(obj, "rating", None),
            "category": getattr(obj, "category", None),
            "image_url": obj.image_url,
            "is_published": obj.is_published,
        }
        return jsonify({"success": True, "data": data})
    finally:
        db_sess.close()


@app.route("/cul/admin/delete/<int:rid>", methods=["POST"])
@login_required
def cul_admin_delete(rid):
    if not getattr(current_user, "is_admin", False):
        return jsonify({"success": False, "error": "forbidden"}), 403
    db_sess = db_session.create_session()
    try:
        obj = db_sess.get(Route, rid)
        if not obj:
            return jsonify({"success": False, "error": "not found"}), 404
        db_sess.delete(obj)
        db_sess.commit()
        return jsonify({"success": True})
    finally:
        db_sess.close()

# после создания app и login_manager.init_app(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login_google"  # или твой логин-роут

@login_manager.user_loader  
def load_user(user_id: str):
    # ВАЖНО: возвращаем пользователя по ID через Session.get (SQLAlchemy 2.x)
    return db_session.create_session().get(User, int(user_id))



@app.route("/cul/admin/toggle_publish/<int:rid>", methods=["POST"])
@login_required
def cul_admin_toggle_publish(rid):
    if not getattr(current_user, "is_admin", False):
        return jsonify({"success": False, "error": "forbidden"}), 403
    db_sess = db_session.create_session()
    try:
        obj = db_sess.get(Route, rid)
        if not obj:
            return jsonify({"success": False, "error": "not found"}), 404
        obj.is_published = not bool(obj.is_published)
        db_sess.commit()
        return jsonify({"success": True, "is_published": obj.is_published})
    finally:
        db_sess.close()



# --- Новый универсальный маршрут ---
@app.route('/cul/<int:route_id>')
def cul_dynamic(route_id):
    db_sess = db_session.create_session()
    route = db_sess.get(Route, route_id)
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
        user = db_sess.get(User, current_user.id)
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
        total_routes = _cultural_routes_query(db_sess).count()

    finally:
        db_sess.close()

    import re
    RX_DONE = re.compile(r'^(?:route_)?cul_\d+$')
    completed = sum(1 for k, v in (current_user.completed_routes or {}).items()
                    if v and RX_DONE.match((k or '').strip()))


    return jsonify({
        "completed": completed,
        "total_routes": total_routes,
        "progress": round((completed / total_routes * 100) if total_routes else 0)
    })



@app.route('/update_route_state', methods=['POST'])
@login_required
def update_route_state():
    data = request.get_json() or {}
    route_id  = int(data.get("route_id", 0))
    new_state = bool(data.get("new_state"))

    if not route_id:
        return jsonify({"status": "error", "message": "Missing route_id"}), 400

    db_sess = db_session.create_session()
    try:
        user = db_sess.merge(current_user)
        if not user.completed_routes:
            user.completed_routes = {}

        key = f'route_cul_{route_id}'
        user.completed_routes[key] = new_state


        # тот же способ подсчёта total_routes
        total_routes = _cultural_routes_query(db_sess).count()

        import re
        rx = re.compile(r'^(?:route_)?cul_\d+$')
        completed = sum(1 for k, v in (user.completed_routes or {}).items() if v and rx.match(k))

        user.progress = round(100 * completed / (total_routes)) if total_routes else 0
        db_sess.commit()
        return jsonify({"status": "success", "progress": user.progress})
    except Exception as e:
        db_sess.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        db_sess.close()

import re

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
        user = db_sess.get(User, current_user.id)   
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




@app.route("/login/google")
def login_google():
    redirect_uri = url_for("auth_callback", _external=True)
    return google.authorize_redirect(redirect_uri)


@app.route("/login/callback")
def auth_callback():
    token = google.authorize_access_token()
    if not token:
        return "Ошибка авторизации", 400

    resp = google.get("https://openidconnect.googleapis.com/v1/userinfo")
    user_info = resp.json()

    db_sess = db_session.create_session()
    user = db_sess.query(User).filter_by(email=user_info["email"]).first()
    if not user:
        user = User(
            name=user_info.get("name", "Без имени"),
            email=user_info["email"],
            is_active=True   # <-- важно, иначе не войдёт
        )
        db_sess.add(user)
        db_sess.commit()

    # если у существующего пользователя is_active=False → активируем
    if not user.is_active:
        user.is_active = True
        db_sess.commit()

    login_user(user, remember=True)
    db_sess.close()

    return redirect(url_for("user_office"))



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

from sqlalchemy import or_



def get_total_cul_routes_count():
    db_sess = db_session.create_session()
    try:
        return db_sess.query(Route).filter(
            or_(Route.route_key.like('route_cul_%'),
                Route.route_key.like('cul_%'))   # на всякий случай старые ключи
        ).count()
    finally:
        db_sess.close()


# добавь вверху
from sqlalchemy import or_, func

def _cultural_routes_query(db_sess):
    # учитываем и старые ключи, и лишние пробелы
    return (db_sess.query(Route)
            .filter(or_(func.trim(Route.route_key).like('route_cul_%'),
                        func.trim(Route.route_key).like('cul_%'))))

def get_total_cul_routes_count():
    db_sess = db_session.create_session()
    try:
        return _cultural_routes_query(db_sess).count()
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
    print(total_routes)
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



def repair_route_keys():
    db_sess = db_session.create_session()
    try:
        dirty = False
        for r in db_sess.query(Route).all():
            key = (r.route_key or '').strip()

            # route_cul_{id} — наш канон
            if not key:
                r.route_key = f'route_cul_{r.id}'; dirty = True
                continue

            # старые форматы → к канону
            if key.startswith('cul_'):
                # cul_123 → route_cul_123
                suf = key.split('_', 1)[1]
                r.route_key = f'route_cul_{suf}'; dirty = True
            elif key.startswith('route_') and not key.startswith('route_cul_'):
                # route_123 → route_cul_123
                suf = key.split('_', 1)[1]
                if suf.isdigit():
                    r.route_key = f'route_cul_{suf}'; dirty = True
            else:
                # на всякий trimming
                if key != r.route_key:
                    r.route_key = key; dirty = True

        if dirty:
            db_sess.commit()
    finally:
        db_sess.close()

# вызови один раз при старте приложения (например, в create_app или перед run)
repair_route_keys()


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

    # лимит и имя файла
    file.stream.seek(0, os.SEEK_END); size = file.stream.tell(); file.stream.seek(0)
    if size > app.config.get("MAX_CONTENT_LENGTH", 6*1024*1024):
        return jsonify({"success": False, "error": "Файл слишком большой"}), 400
    ext = file.filename.rsplit(".", 1)[1].lower()
    fname = f"{current_user.id}_{uuid.uuid4().hex[:8]}.{ext}"
    path = os.path.join(_avatars_dir(), secure_filename(fname))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    file.save(path)

    # (опционально) валидация картинки
    try:
        if imghdr.what(path) not in {"png","jpeg","jpg","webp"}:
            os.remove(path)
            return jsonify({"success": False, "error": "Некорректное изображение"}), 400
    except Exception:
        pass

    # ✅ правильный коммит через нашу сессию
    db_sess = db_session.create_session()
    try:
        user = db_sess.get(User, current_user.id)  # или db_sess.merge(current_user)
        user.avatar = fname
        db_sess.commit()
    except Exception:
        db_sess.rollback()
        try: os.remove(path)
        except: pass
        return jsonify({"success": False, "error": "DB error"}), 500
    finally:
        db_sess.close()

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