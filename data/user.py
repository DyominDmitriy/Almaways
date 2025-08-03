import sqlalchemy
from sqlalchemy import orm
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin
from sqlalchemy_serializer import SerializerMixin
from datetime import datetime
from .db_session import SqlAlchemyBase
from sqlalchemy import  DateTime
from sqlalchemy import func, or_
from sqlalchemy import text, String
import re

class User(SqlAlchemyBase, UserMixin, SerializerMixin):
    __tablename__ = 'users'

    id = sqlalchemy.Column(sqlalchemy.Integer,
                    primary_key=True, unique=True)

    name = sqlalchemy.Column(sqlalchemy.String)

    surname = sqlalchemy.Column(sqlalchemy.String)

    email = sqlalchemy.Column(sqlalchemy.String, unique=True)

    phone_num = sqlalchemy.Column(sqlalchemy.String, unique=True)

    password = sqlalchemy.Column(sqlalchemy.String)

    progress = sqlalchemy.Column(sqlalchemy.Integer)

    completed_routes = sqlalchemy.Column(sqlalchemy.JSON, default=dict) 

    avatar = sqlalchemy.Column(sqlalchemy.String, nullable=True)

    is_admin = sqlalchemy.Column(sqlalchemy.Boolean, default=False)

    favourite_routes = sqlalchemy.Column(sqlalchemy.JSON, default=lambda: {
    f"cul_{i}.fav": False for i in range(1, 7)  # 6 маршрутов
})
    route_completion_dates = sqlalchemy.Column(sqlalchemy.JSON, default={})
    
    weekly_streak = sqlalchemy.Column(sqlalchemy.Integer, default=0)

    is_active = sqlalchemy.Column(sqlalchemy.Boolean, default=False, nullable=False)

    def get_completed_cul_ids(self):
        import re
        if not self.completed_routes:
            return []
        completed_ids = []
        for route_id, is_done in self.completed_routes.items():
            if is_done:
                match = re.match(r'(?:route_)?cul_(\d+)', route_id)
                if match:
                    completed_ids.append(f"route_cul_{match.group(1)}")
        return completed_ids

    def get_total_hours(self, db_sess):
        completed_ids = self.get_completed_cul_ids()
        if not completed_ids:
            return 0
        routes = db_sess.query(Route).filter(Route.route_key.in_(completed_ids)).all()
        return sum(route.duration or 0 for route in routes)

    def get_total_photos(self):
        if not self.completed_routes:
            return 0
        return sum(1 for k, v in self.completed_routes.items() if v and k.startswith("cul_") and k.endswith("_photo"))
    
    
    def set_password(self, password):
        self.password = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password, password)
    
    def get_total_photos(self):
        if not self.completed_routes:
            return 0

        # Считаем количество завершённых маршрутов с ключом route_cul_*
        return sum(
            1 for key, completed in self.completed_routes.items()
            if key.startswith("route_cul_") and completed
        )

    @staticmethod
    def get_popular_routes(db_sess, limit=5):
        """Упрощенная и надежная версия для SQLite"""
        try:
            # Получаем всех пользователей с их предпочтениями
            users = db_sess.query(User).filter(
                User.completed_routes.isnot(None) | 
                User.favourite_routes.isnot(None)
            ).all()

            # Собираем статистику
            route_stats = {}
            
            for user in users:
                # Обрабатываем completed_routes
                if user.completed_routes:
                    for route_id, is_completed in user.completed_routes.items():
                        if is_completed:
                            route_stats.setdefault(route_id, {'completed': 0, 'favourite': 0})
                            route_stats[route_id]['completed'] += 1
                
                # Обрабатываем favourite_routes
                if user.favourite_routes:
                    for route_id, is_favourite in user.favourite_routes.items():
                        if is_favourite:
                            route_stats.setdefault(route_id, {'completed': 0, 'favourite': 0})
                            route_stats[route_id]['favourite'] += 1

            # Получаем полные данные о маршрутах
            popular_routes = []
            for route_id, stats in route_stats.items():
                try:
                    # Удаляем префикс 'cul_' если он есть
                    clean_id = int(route_id.replace('cul_', ''))
                    route = db_sess.query(Route).get(clean_id)
                    if route:
                        popularity = (stats['completed'] + stats['favourite']) / 2
                        popular_routes.append({
                            'id': route.id,
                            'title': route.title,
                            'description': route.description,
                            'image_url': route.image_url,
                            'duration': route.duration,
                            'difficulty': route.difficulty,
                            'popularity': popularity
                        })
                except (ValueError, AttributeError):
                    continue

            # Сортируем по популярности
            return sorted(popular_routes, key=lambda x: x['popularity'], reverse=True)[:limit]

        except Exception as e:
            print(f"Error in get_popular_routes: {e}")
            return []
        

class Route(SqlAlchemyBase):
    __tablename__ = 'routes'

    id = sqlalchemy.Column(sqlalchemy.Integer, primary_key=True, unique=True)
    route_key = sqlalchemy.Column(sqlalchemy.String, nullable=False, unique=True)
    title = sqlalchemy.Column(sqlalchemy.String, nullable=False)
    short_description = sqlalchemy.Column(sqlalchemy.String, nullable=True)   # NEW
    description = sqlalchemy.Column(sqlalchemy.Text, nullable=True)           # (было description — оставляем)
    image_url = sqlalchemy.Column(sqlalchemy.String, nullable=True)
    duration = sqlalchemy.Column(sqlalchemy.Float, nullable=True)             # часы
    difficulty = sqlalchemy.Column(sqlalchemy.String, nullable=True)
    map_embed = sqlalchemy.Column(sqlalchemy.Text, nullable=True)  