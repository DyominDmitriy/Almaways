import re
import smtplib
import dns.resolver
import datetime
from flask import Flask
from flask import Flask, render_template, redirect, request, Response, session, send_file, jsonify, Blueprint, abort
from data import db_session
from data.user import User
from data.user import Route
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask import Flask, redirect, url_for, session
from authlib.integrations.flask_client import OAuth  
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from data import db_session
import secrets
from flask import session
from flask import jsonify, request
import logging
from flask import request, redirect, url_for
import os
from werkzeug.utils import secure_filename



app = Flask(__name__)
app.config['SECRET_KEY'] = "mishadimamax200620072008"
app.config['PERMANENT_SESSION_LIFETIME'] = datetime.timedelta(days=365)
login_manager = LoginManager()
login_manager.init_app(app)
oauth = OAuth(app)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


UPLOAD_FOLDER = 'static/avatars'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/upload_avatar', methods=['POST'])
@login_required
def upload_avatar():
    db_sess = db_session.create_session()
    user = db_sess.query(User).get(current_user.id)
    file = request.files['avatar']
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        # Сохраняем имя файла в БД
        current_user.avatar = filename
        db_sess.commit()
    return redirect(url_for('private_office'))
@app.route('/')
@app.route('/index')
def index():
    db_sess = db_session.create_session()
    popular_routes = User.get_popular_routes(db_sess, limit=5)
    db_sess.close()
    return render_template("index.html", current_user=current_user, popular_routes=popular_routes)
@app.route('/debug_user')
@login_required
def debug_user():
    db_sess = db_session.create_session()
    user = db_sess.query(User).get(current_user.id)
    return jsonify({
        "id": user.id,
        "completed_routes": user.completed_routes,
        "progress": user.progress
    })

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
MAX_ROUTES = 6  # Константа в начале файла


@app.route('/get_user_progress')
@login_required
def get_user_progress():
    return jsonify({
        "progress": current_user.progress,
        "max_routes": 6
    })


@app.route('/update_route_state', methods=['POST'])
@login_required
def update_route_state():
    data = request.get_json()
    route_id = data.get("route_id")
    new_state = data.get("new_state")
    
    if not route_id or new_state is None:
        return jsonify({"status": "error", "message": "Missing parameters"}), 400
    
    db_sess = db_session.create_session()
    try:
        # Важно: использовать merge для прикрепления объекта к сессии
        user = db_sess.merge(current_user)
        
        # Инициализация если None
        if user.completed_routes is None:
            user.completed_routes = {
                "cul_1": False,
                "cul_2": False,
                "cul_3": False,
                "cul_4": False,
                "cul_5": False,
                "cul_6": False
            }
        
        # Обновляем состояние
        user.completed_routes[route_id] = new_state
        user.progress = sum(1 for v in user.completed_routes.values() if v)
        
        # Явное сохранение
        db_sess.commit()
        
        # Принудительное обновление из БД
        db_sess.refresh(user)
        
        return jsonify({
            "status": "success",
            "new_state": new_state,
            "progress": user.progress,
            "all_routes": user.completed_routes  # Для отладки
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
    
    # Получаем избранные маршруты пользователя
    user = db_sess.merge(current_user)  # merge для работы с сессией
    favourite_routes = user.favourite_routes or {}  # Если None, то пустой словарь
    
    # Получаем ID маршрутов, которые в избранном (ключи вида "cul_1_fav" -> преобразуем в число)
    favourite_route_ids = [
        int(route_id.replace('_fav', '').replace('cul_', '')) 
        for route_id, is_fav in favourite_routes.items() 
        if is_fav
    ]
    
    # Загружаем маршруты из базы
    routes = db_sess.query(Route).filter(Route.id.in_(favourite_route_ids)).all()
    
    db_sess.close()
    
    return render_template('favourites.html', routes=routes)

@app.route('/route/<string:route_id>')  # Принимает 'cul_1' или '1'
def route_page(route_id):
    db_sess = db_session.create_session()
    # Удаляем 'cul_' для поиска в БД
    route_num = int(route_id.replace('cul_', ''))
    route = db_sess.query(Route).filter(Route.id == route_num).first()
    return render_template('route.html', route=route)


@app.route('/debug_user_fav')
@login_required
def debug_user_fav():
    db_sess = db_session.create_session()
    user = db_sess.query(User).get(current_user.id)
    return jsonify({
        "id": user.id,
        "favourite_routes": user.favourite_routes
    })

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
MAX_ROUTES = 6  # Константа в начале файла

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
        
        # Инициализация если None
        if user.favourite_routes is None:
            user.favourite_routes = {
                "cul_1_fav": False,
                "cul_2_fav": False,
                "cul_3_fav": False,
                "cul_4_fav": False,
                "cul_5_fav": False,
                "cul_6_fav": False
            }
        
        # Обновляем состояние
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
    route_ids = [rid for rid in route_ids if rid]  # Фильтруем пустые значения
    db_sess = db_session.create_session()
    user = db_sess.query(User).get(current_user.id)
    # Здесь должна быть логика получения маршрутов из БД
    routes = []
    
    for rid in route_ids:
        route = get_route_by_id(rid)  # Ваша функция получения маршрута
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

@app.route('/cultural_routes')
def cultural_routes():
    return render_template("cultural_routes.html")



@app.route('/cul_1')
def cul_1():
    return render_template("cul_1.html")
    




@app.route('/cul_2')
def cul_2():
    return render_template("cul_2.html")

@app.route('/cul_3')
def cul_3():
    return render_template("cul_3.html")

@app.route('/cul_4')
def cul_4():
    return render_template("cul_4.html")

@app.route('/cul_5')
def cul_5():
    return render_template("cul_5.html")

@app.route('/cul_6')
def cul_6():
    return render_template("cul_6.html")

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

@app.route('/user_login')
def user_login():
    return render_template("user_login.html")

@app.route('/user_reg')
def user_reg():
    return render_template("user_reg.html")


def is_valid_email(email):
    regex = r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$'
    return re.match(regex, email) is not None
    
google = oauth.register(
    name='google',
    client_id="376838788269-k120re8lp9bjv3dv31i6s9d0ekpkh5tq.apps.googleusercontent.com",
    client_secret="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",
    access_token_url='https://oauth2.googleapis.com/token',
    authorize_url='https://accounts.google.com/o/oauth2/auth',
    jwks_uri='https://www.googleapis.com/oauth2/v3/certs',  
    client_kwargs={'scope': 'openid email profile', 'nonce': 'random_nonce_value'}
)

login_manager = LoginManager()
login_manager.init_app(app)

def load_user(user_id):
    db_sess = db_session.create_session()
    return db_sess.query(User).get(user_id)

@app.route("/login/google")
def login_google():
    nonce = secrets.token_urlsafe(16)  
    session["nonce"] = nonce  
    return google.authorize_redirect(
    url_for("auth_callback", _external=True),
    nonce=nonce  
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
    return redirect("/private_office")

@app.route('/reg_form', methods=["POST"])
def reg_form():
    form = request.form
    email = form.get('emailInput')
    password = form.get("passwordInput")
    name = form.get("nameInput")
    surname = form.get("surnameInput")
    phone_num = form.get("phone_num")
    db_sess = db_session.create_session()
    user = User()
    if not is_valid_email(email):
        return render_template("user_reg.html", error="Неверный формат email")
    existing_user = db_sess.query(User).filter(User.email == email).first()
    if existing_user:
        db_sess.close()
        return render_template("user_reg.html", error="Этот email уже зарегистрирован")
    user.name = name
    user.surname = surname
    user.email = email
    user.phone_num = phone_num
    user.set_password(password)
    user.progress = 0
    db_sess.add(user)
    db_sess.commit()
    db_sess.close()
    return redirect('/user_login')


@app.route('/private_office')
def private_office():
    name = current_user.name
    surname = current_user.surname
    email = current_user.email
    phone_num = current_user.phone_num
    current_user.avatar = ''
    db_sess = db_session.create_session()
    user = db_sess.merge(current_user)
    total_hours = user.get_total_hours(db_sess)
    db_sess.close()
    db_sess = db_session.create_session()
    user = db_sess.merge(current_user)
    total_photos = user.get_total_photos()  # Новый метод
    db_sess.close()
    
    
    
    return render_template("private_office.html", total_hours=total_hours, 
                    total_photos=total_photos)

@app.before_request
def make_session_permanent():
    session.permanent = False



@app.route('/login', methods=["POST", "GET"])
def login():
    form = request.form
    if request.method=="POST":
        if form.get('rememberMe'):
            remember_me =form.get("rememberMe") == 'on'  
        else:
            remember_me = 'False'
        print(remember_me)
    email = form.get('emailInput')
    password = form.get("passwordInput")
    db_sess = db_session.create_session()
    user = db_sess.query(User).filter(User.email == email).first()
    if user and user.check_password(password):
        login_user(user, remember=remember_me)
        return redirect("/private_office")
    else:
        db_sess.close()
        print("Неверный email или пароль.", "error")
        return redirect(url_for('user_login'))
    

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

#
# @app.route('/info')
# def info():
#     return render_template("info.html")
#
#
# @app.route('/info')
# def info():
#     return render_template("info.html")


def main():
    db_session.global_init("databases/places.db")
    app.run(debug=True, host='0.0.0.0', port=5000,)


if __name__ == "__main__":
    main()
    
