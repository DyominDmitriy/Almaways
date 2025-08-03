import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from data import db_session
from data.user import Route

def main():
    db_session.global_init("databases/places.db")  # ← ОБЯЗАТЕЛЬНО: путь к твоей SQLite базе

    db_sess = db_session.create_session()
    routes = db_sess.query(Route).all()

    for route in routes:
        route.route_key = f"route_cul_{route.id}"
    db_sess.commit()
    db_sess.close()
    print("Готово: route_key обновлены.")

if __name__ == "__main__":
    main()
