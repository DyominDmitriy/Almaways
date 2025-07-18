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

    avatar = sqlalchemy.Column(sqlalchemy.String, nullable=True)


    favourite_routes = sqlalchemy.Column(sqlalchemy.JSON, default=lambda: {
    f"cul_{i}.fav": False for i in range(1, 7)  # 6 маршрутов
})

    completed_routes = sqlalchemy.Column(sqlalchemy.JSON, default=lambda: {
    f"cul_{i}": False for i in range(1, 7)  # 6 маршрутов
})
    
    def set_password(self, password):
        self.password = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password, password)
    def get_total_hours(self, db_sess):
        if not self.completed_routes:
            return 0

        # Получаем ID завершенных маршрутов (где значение True)
        completed_ids = [int(route_id.replace('cul_', '')) 
                        for route_id, completed in self.completed_routes.items() 
                        if completed]

        # Запрашиваем длительность этих маршрутов из БД
        routes = db_sess.query(Route.duration).filter(Route.id.in_(completed_ids)).all()
        
        # Суммируем часы (предполагаем, что duration в формате "2 часа")
        total_hours = 0
        for (duration,) in routes:
            if duration:
                hours = duration  # Извлекаем число из строки "2 часа"
                total_hours += hours

        return total_hours
    def get_total_photos(self):
        if not self.completed_routes:
            return 0
        # Считаем количество завершённых маршрутов (True в completed_routes)
        return sum(1 for completed in self.completed_routes.values() if completed)
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

    id = sqlalchemy.Column(sqlalchemy.Integer,
                        primary_key=True, unique=True)
    title = sqlalchemy.Column(sqlalchemy.String)
    description = sqlalchemy.Column(sqlalchemy.Text)
    image_url = sqlalchemy.Column(sqlalchemy.String)
    duration = sqlalchemy.Column(sqlalchemy.FLOAT)
    difficulty = sqlalchemy.Column(sqlalchemy.String)
