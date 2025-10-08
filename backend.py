import os
import re
import uuid
import datetime
from dotenv import load_dotenv

from flask import (
    Flask, render_template, redirect, request,
    session, jsonify, url_for, flash, abort, current_app
)
from flask_mail import Mail
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadData
from flask_login import (
    LoginManager, login_user, logout_user,
    login_required, current_user
)
from authlib.integrations.flask_client import OAuth
from werkzeug.utils import secure_filename
from werkzeug.security import check_password_hash, generate_password_hash

from sqlalchemy import func, desc, asc, or_, and_

from data import db_session
from data.user import User, Route
from admin import admin_bp
from email_service import is_valid_email, send_confirmation_email, confirm_token
from cul_routes import cul_bp


# 1) Загрузить .env
load_dotenv()

# 2) Создать приложение и настроить
app = Flask(__name__)
oauth = OAuth(app)

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
    UPLOAD_FOLDER=os.path.join(app.root_path, "static", "avatars"),
    MAX_CONTENT_LENGTH=6 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=False,  # включите True на проде
    SESSION_COOKIE_SAMESITE="Lax",
)

google = oauth.register(
    name="google",
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)

# 3) Проверка обязательных секретов
_required = ["SECRET_KEY", "SECRET_SEND_KEY", "MAIL_USERNAME", "MAIL_PASSWORD"]
_missing = [k for k in _required if not app.config.get(k)]
if _missing:
    raise RuntimeError(f"Missing required secrets: {', '.join(_missing)}")

# 4) Инициализировать расширения
mail = Mail(app)
app.mail = mail  # email_service рассчитывает на current_app.mail

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login_google"

# 5) Блюпринты и БД
app.register_blueprint(cul_bp)
app.register_blueprint(admin_bp)
db_session.global_init("databases/places.db")


# =======================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =======================

def _q_tokens(q: str):
    return [t for t in re.split(r"[^\wёЁа-яА-Яa-zA-Z0-9]+", (q or "").strip()) if len(t) >= 2]


def _like_ci(col, term: str):
    term = str(term)[:100]
    safe_term = term.replace("%", "\\%").replace("_", "\\_")
    forms = {safe_term, safe_term.capitalize(), safe_term.title(), safe_term.upper()}
    return or_(*[col.like(f"%{f}%", escape="\\") for f in forms])


def _serialize_route(r: Route):
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


def _cultural_routes_query(db_sess):
    return (db_sess.query(Route)
            .filter(or_(func.trim(Route.route_key).like('route_cul_%'),
                        func.trim(Route.route_key).like('cul_%'))))


def get_total_cul_routes_count():
    db_sess = db_session.create_session()
    try:
        return _cultural_routes_query(db_sess).count()
    finally:
        db_sess.close()


def _route_image(r: Route):
    for k in ("image_url", "img", "image", "photo_url", "cover"):
        if getattr(r, k, None):
            return getattr(r, k)
    return url_for("static", filename="img/placeholder.jpg")


def _avatars_dir():
    avatars = os.path.join(app.static_folder, "avatars")
    os.makedirs(avatars, exist_ok=True)
    return avatars


ALLOWED_EXT = {"png", "jpg", "jpeg", "webp", "gif"}


def _allowed(filename: str):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXT


def get_route_by_id(rid: str):
    """
    Поддержка форматов:
    - 'cul_6', 'route_cul_6', '6'
    """
    try:
        m = re.search(r'(\d+)$', str(rid))
        if not m:
            return None
        route_id = int(m.group(1))
    except Exception:
        return None
    db_sess = db_session.create_session()
    try:
        return db_sess.get(Route, route_id)
    finally:
        db_sess.close()


# =======================
#        РОУТЫ
# =======================

@login_manager.user_loader
def load_user(user_id: str):
    return db_session.create_session().get(User, int(user_id))


@app.route("/cul/cultural_routes_json")
def cultural_routes_json():
    db_sess = db_session.create_session()
    try:
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

        M = Route
        query = db_sess.query(M)

        # Поиск по названию/описанию
        title_col = getattr(M, "title", None) or getattr(M, "name", None)
        desc_col = getattr(M, "short_description", None) or getattr(M, "description", None)
        tokens = _q_tokens(q)
        if tokens and (title_col or desc_col):
            per_token = []
            for t in tokens:
                fields = []
                if title_col is not None:
                    fields.append(_like_ci(title_col, t))
                if desc_col is not None:
                    fields.append(_like_ci(desc_col, t))
                if fields:
                    per_token.append(or_(*fields))
            if per_token:
                query = query.filter(and_(*per_token))

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
            if dmin is not None:
                query = query.filter(dur_col >= dmin)
            if dmax is not None:
                query = query.filter(dur_col <= dmax)

        # Длина
        len_col = getattr(M, "length_km", None) or getattr(M, "length", None)
        if len_col is not None:
            if lmin is not None:
                query = query.filter(len_col >= lmin)
            if lmax is not None:
                query = query.filter(len_col <= lmax)

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
        items = query.limit(per_page).offset((page - 1) * per_page).all()

        return jsonify({
            "items": [_serialize_route(r) for r in items],
            "page": page, "pages": pages, "total": total,
            "is_admin": is_admin
        })
    finally:
        db_sess.close()


@app.route("/cultural_routes")
def cultural_routes():
    db_sess = db_session.create_session()
    try:
        query = db_sess.query(Route)
        if not bool(getattr(current_user, "is_admin", False)):
            query = query.filter(Route.is_published.is_(True))
        routes = query.order_by(desc(Route.id)).all()
        categories = []
        return render_template("cul/cultural_routes.html", routes=routes, categories=categories)
    finally:
        db_sess.close()


# --- Админ API для культурных маршрутов (совместим с static/js/cul-admin.js) ---

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
        img = request.files.get("image")
        if img and img.filename:
            os.makedirs(os.path.join(app.static_folder, "uploads"), exist_ok=True)
            filename = secure_filename(img.filename)
            path = os.path.join(app.static_folder, "uploads", filename)
            img.save(path)
            r.image_url = filename

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

        if "length_km" in request.form and request.form.get("length_km") != "":
            try:
                obj.length_km = float(request.form.get("length_km"))
            except Exception:
                pass
        if "rating" in request.form and request.form.get("rating") != "":
            try:
                obj.rating = float(request.form.get("rating"))
            except Exception:
                pass
        if "category" in request.form:
            v = (request.form.get("category") or "").strip()
            if v != "":
                obj.category = v

        img = request.files.get("image")
        if img and img.filename:
            os.makedirs(os.path.join(app.static_folder, "uploads"), exist_ok=True)
            filename = secure_filename(img.filename)
            path = os.path.join(app.static_folder, "uploads", filename)
            img.save(path)
            obj.image_url = filename

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


# --- Динамическая страница маршрута + совместимость со старыми урлами ---

@app.route('/cul/<int:route_id>')
def cul_dynamic(route_id):
    db_sess = db_session.create_session()
    route = db_sess.get(Route, route_id)
    db_sess.close()
    if not route:
        template_name = f"cul/cul_{route_id}.html"
        try:
            return render_template(template_name)
        except Exception:
            abort(404)
    return render_template("cul/route_detail.html", route=route, current_user=current_user)


@app.route('/cul_1')
def cul_1():
    return redirect(url_for('cul_dynamic', route_id=1))


@app.route('/cul_2')
def cul_2():
    return redirect(url_for('cul_dynamic', route_id=2))


@app.route('/cul_3')
def cul_3():
    return redirect(url_for('cul_dynamic', route_id=3))


@app.route('/cul_4')
def cul_4():
    return redirect(url_for('cul_dynamic', route_id=4))


@app.route('/cul_5')
def cul_5():
    return redirect(url_for('cul_dynamic', route_id=5))


@app.route('/cul_6')
def cul_6():
    return redirect(url_for('cul_dynamic', route_id=6))


# --- Главная и популярные маршруты ---

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
        popular = _popular_routes(db_sess, limit=12)
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


def _popular_routes(db_sess, limit=12):
    order_col = None
    for a in ("popularity", "rating", "favourites_count"):
        if hasattr(Route, a):
            order_col = getattr(Route, a)
            break
    if order_col is not None:
        return db_sess.query(Route).order_by(desc(order_col)).limit(limit).all()

    # попытка через агрегаты (если модели определены)
    try:
        from data.user import Favourite  # если нет — упадёт и пойдём дальше
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


# --- Состояние пользователя и прогресс ---

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
    route_id = int(data.get("route_id", 0))
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

        total_routes = _cultural_routes_query(db_sess).count()
        rx = re.compile(r'^(?:route_)?cul_\d+$')
        completed = sum(1 for k, v in (user.completed_routes or {}).items() if v and rx.match(k))

        user.progress = round(100 * completed / total_routes) if total_routes else 0
        db_sess.commit()
        return jsonify({"status": "success", "progress": user.progress})
    except Exception as e:
        db_sess.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        db_sess.close()


# --- Избранное ---

@app.route('/favourites')
@login_required
def favourites():
    db_sess = db_session.create_session()
    try:
        user = db_sess.merge(current_user)
        favourite_routes = user.favourite_routes or {}
        favourite_route_ids = [
            int(route_id.replace('_fav', '').replace('cul_', ''))
            for route_id, is_fav in favourite_routes.items()
            if is_fav
        ]
        routes = db_sess.query(Route).filter(Route.id.in_(favourite_route_ids)).all()
        return render_template('favourites.html', routes=routes)
    finally:
        db_sess.close()


@app.route('/route/<string:route_id>')
def route_page(route_id):
    db_sess = db_session.create_session()
    try:
        m = re.search(r'(\d+)$', route_id)
        route_num = int(m.group(1)) if m else 0
        route = db_sess.query(Route).filter(Route.id == route_num).first()
        return render_template('route.html', route=route)
    finally:
        db_sess.close()


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
        db_sess.rollback
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        db_sess.close()


# --- Разное статика/страницы ---

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


# --- Аватарки и профиль ---

@app.route("/upload_avatar", methods=["POST"])
@login_required
def upload_avatar():
    file = request.files.get("avatar")
    if not file or file.filename == "":
        return jsonify({"success": False, "error": "Файл не выбран"}), 400
    if not _allowed(file.filename):
        return jsonify({"success": False, "error": "Разрешены PNG/JPG/WEBP/GIF"}), 400

    file.stream.seek(0, os.SEEK_END)
    size = file.stream.tell()
    file.stream.seek(0)
    if size > app.config.get("MAX_CONTENT_LENGTH", 6 * 1024 * 1024):
        return jsonify({"success": False, "error": "Файл слишком большой"}), 400

    ext = file.filename.rsplit(".", 1)[1].lower()
    fname = f"{current_user.id}_{uuid.uuid4().hex[:8]}.{ext}"
    path = os.path.join(_avatars_dir(), secure_filename(fname))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    file.save(path)

    try:
        import imghdr
        if imghdr.what(path) not in {"png", "jpeg", "jpg", "webp", "gif"}:
            os.remove(path)
            return jsonify({"success": False, "error": "Некорректное изображение"}), 400
    except Exception:
        pass

    db_sess = db_session.create_session()
    try:
        user = db_sess.get(User, current_user.id)
        user.avatar = fname
        db_sess.commit()
    except Exception:
        db_sess.rollback()
        try:
            os.remove(path)
        except Exception:
            pass
        return jsonify({"success": False, "error": "DB error"}), 500
    finally:
        db_sess.close()

    return jsonify({"success": True, "filename": fname})


@app.route('/user_office')
@login_required
def user_office():
    db_sess = db_session.create_session()
    try:
        user = db_sess.get(User, current_user.id)
        if not user:
            return "User not found", 404

        completed = len(user.get_completed_cul_ids())
        total_routes = get_total_cul_routes_count()
        total_hours = user.get_total_hours(db_sess)
        total_photos = user.get_total_photos()

        if user.completed_routes is None:
            user.completed_routes = {}
            db_sess.commit()

        raw_popular = _popular_routes(db_sess, limit=12)
        return render_template(
            "user/user_office.html",
            name=user.name,
            surname=user.surname,
            email=user.email,
            phone_num=user.phone_num,
            avatar=user.avatar,
            completed=completed,
            total_routes=total_routes,
            total_hours=total_hours,
            total_photos=total_photos,
            progress=round(completed / total_routes * 100) if total_routes else 0,
            popular_routes=raw_popular
        )
    finally:
        db_sess.close()


# --- Аутентификация ---

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
            is_active=True
        )
        db_sess.add(user)
        db_sess.commit()

    if not user.is_active:
        user.is_active = True
        db_sess.commit()

    login_user(user, remember=True)
    db_sess.close()

    return redirect(url_for("user_office"))


@app.route('/user_login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('emailInput', '').strip()
        password = request.form.get('passwordInput', '').strip()
        remember_me = bool(request.form.get('rememberMe'))

        db_sess = db_session.create_session()
        user = db_sess.query(User).filter_by(email=email).first()

        if not user:
            error = 'Пользователь не найден'
        elif not user.is_active:
            error = 'Подтвердите почту перед входом'
        elif not user.check_password(password):
            error = 'Неверный пароль'
        else:
            login_user(user, remember=remember_me)
            db_sess.close()
            if request.headers.get('Accept') == 'application/json':
                return jsonify(success=True)
            return redirect('/user_office')

        db_sess.close()
        if request.headers.get('Accept') == 'application/json':
            return jsonify(success=False, message=error), 400
        flash(error, 'error')
        return redirect('/user_login')

    return render_template('user/user_login.html')


@app.route('/user_reg', methods=['GET', 'POST'])
def user_reg():
    if request.method == 'POST':
        form = request.form
        email = form.get('emailInput', '').strip()
        password = form.get('passwordInput', '').strip()
        name = form.get('nameInput', '').strip()
        surname = form.get('surnameInput', '').strip()
        phone_num = form.get('phone_num', '').strip()

        if not is_valid_email(email):
            return render_template("user/user_reg.html", error="Неверный формат email",
                                   email=email, name=name, surname=surname, phone=phone_num)

        db_sess = db_session.create_session()
        if db_sess.query(User).filter(User.email == email).first():
            flash('Почта уже зарегистрирована', 'error')
            db_sess.close()
            return redirect('/user_reg')

        user = User(name=name,
                    surname=surname,
                    email=email,
                    phone_num=phone_num,
                    progress=0,
                    is_active=False)
        user.set_password(password)
        db_sess.add(user)
        db_sess.commit()

        try:
            send_confirmation_email(email)
            flash('Письмо для подтверждения отправлено на почту', 'info')
        except Exception as e:
            flash(f'Ошибка при отправке письма: {e}', 'error')
        finally:
            db_sess.close()

        return redirect('/user_login')

    return render_template("user/user_reg.html")


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


@app.route("/update_profile", methods=["POST"])
@login_required
def update_profile():
    db_sess = db_session.create_session()
    try:
        user = db_sess.get(User, current_user.id)
        if not user:
            return jsonify({"success": False, "error": "User not found"}), 404

        name = (request.form.get("name") or "").strip()[:100]
        surname = (request.form.get("surname") or "").strip()[:100]

        if name:
            user.name = name
        if surname:
            user.surname = surname

        db_sess.commit()
        return jsonify({"success": True})
    finally:
        db_sess.close()


@app.route("/update_password", methods=["POST"])
@login_required
def update_password():
    db_sess = db_session.create_session()
    try:
        user = db_sess.get(User, current_user.id)
        if not user:
            return jsonify({"success": False, "error": "User not found"}), 404

        cur = request.form.get("current_password") or ""
        new = request.form.get("new_password") or ""
        conf = request.form.get("confirm_password") or ""

        if len(new) < 8 or new != conf:
            return jsonify({"success": False, "error": "Пароль короткий или не совпадает"}), 400

        pwd_hash = getattr(user, "password_hash", None) or getattr(user, "password", None)
        if not pwd_hash:
            return jsonify({"success": False, "error": "Поле пароля не найдено у модели"}), 500

        if not check_password_hash(pwd_hash, cur):
            return jsonify({"success": False, "error": "Текущий пароль неверный"}), 400

        new_hash = generate_password_hash(new)
        if hasattr(user, "password_hash"):
            user.password_hash = new_hash
        else:
            user.password = new_hash

        db_sess.commit()
        return jsonify({"success": True})
    finally:
        db_sess.close()


@app.route("/update_settings", methods=["POST"])
@login_required
def update_settings():
    db_sess = db_session.create_session()
    try:
        user = db_sess.get(User, current_user.id)
        if not user:
            return jsonify({"success": False, "error": "User not found"}), 404

        lang = request.form.get("lang", "ru")
        notifications = bool(request.form.get("notifications"))
        newsletter = bool(request.form.get("newsletter"))

        if hasattr(user, "settings_json") and isinstance(getattr(user, "settings_json"), dict):
            s = user.settings_json
            s.update({"lang": lang, "notifications": notifications, "newsletter": newsletter})
            user.settings_json = s

        db_sess.commit()
        return jsonify({"success": True})
    finally:
        db_sess.close()


@app.route("/delete_account", methods=["POST"])
@login_required
def delete_account():
    db_sess = db_session.create_session()
    try:
        user = db_sess.get(User, current_user.id)
        if not user:
            return jsonify({"success": False, "error": "User not found"}), 404

        logout_user()
        if hasattr(user, "is_deleted"):
            user.is_deleted = True
            db_sess.commit()
        else:
            db_sess.delete(user)
            db_sess.commit()

        return jsonify({"success": True})
    finally:
        db_sess.close()


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect("/")


def main():
    # global_init уже вызван выше, но повторный вызов безопасен
    db_session.global_init("databases/places.db")
    app.run(debug=True, host='0.0.0.0', port=7010)


if __name__ == "__main__":
    main()