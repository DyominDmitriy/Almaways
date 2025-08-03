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

# 1) Загрузить .env до всего остального
load_dotenv()

# 2) Создать приложение только один раз
app = Flask(__name__)

# 3) Единая конфигурация из переменных окружения
app.config.update(
    SECRET_KEY       = os.getenv("SECRET_KEY"),
    SECRET_SEND_KEY  = os.getenv("SECRET_SEND_KEY"),
    MAIL_SERVER      = os.getenv("MAIL_SERVER", "smtp.gmail.com"),
    MAIL_PORT        = int(os.getenv("MAIL_PORT", "587")),
    MAIL_USE_TLS     = os.getenv("MAIL_USE_TLS", "True")  == "True",
    MAIL_USE_SSL     = os.getenv("MAIL_USE_SSL", "False") == "True",
    MAIL_USERNAME    = os.getenv("MAIL_USERNAME"),
    MAIL_PASSWORD    = os.getenv("MAIL_PASSWORD"),
    PERMANENT_SESSION_LIFETIME = datetime.timedelta(days=365),
    UPLOAD_FOLDER    = os.path.join(app.root_path, 'static', 'avatars'),
)

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

@app.route('/cultural_routes')
def cultural_routes():
    # тянем данные из БД — чтобы карточки были редактируемые
    db_sess = db_session.create_session()
    routes = db_sess.query(Route).order_by(Route.id).all()
    db_sess.close()
    return render_template("cul/cultural_routes.html", routes=routes, current_user=current_user)

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
    
    db_sess.close()
    
    print("DEBUG: completed =", completed)
    

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
        progress=round(completed / total_routes * 100) if total_routes else 0
    )


UPLOAD_FOLDER = os.path.join(app.root_path, 'static', 'avatars')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/upload_avatar', methods=['POST'])
@login_required
def upload_avatar():
    try:
        if 'avatar' not in request.files:
            return jsonify({'error': 'No file uploaded'}), 400

        file = request.files['avatar']
        if file.filename == '':
            return jsonify({'error': 'Empty filename'}), 400

        ext = file.filename.rsplit('.', 1)[-1].lower()
        filename = secure_filename(f"{current_user.id}.{ext}")
        filepath = os.path.join(app.root_path, 'static', 'avatars', filename)
        file.save(filepath)

        # Удаляем старый аватар, если он есть и не дефолтный
        session = db_session.create_session()
        user = session.query(User).get(current_user.id)
        old_avatar = user.avatar
        if old_avatar and old_avatar != 'default.png':
            old_avatar_path = os.path.join(app.root_path, 'static', 'avatars', old_avatar)
            if os.path.exists(old_avatar_path) and old_avatar_path != filepath:
                try:
                    os.remove(old_avatar_path)
                except Exception as e:
                    print(f"Не удалось удалить старый аватар: {e}")

        file.save(filepath)

        # Обновляем поле в БД
        user = session.query(User).get(current_user.id)
        user.avatar = filename
        session.commit()

        return jsonify({'success': True, 'filename': filename})
    
    except Exception as e:
        print("🔥 Ошибка при загрузке:", e)
        return jsonify({'error': str(e)}), 500

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