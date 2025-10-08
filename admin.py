import os
from functools import wraps
from flask import Blueprint, render_template, request, redirect, url_for, abort, current_app, flash
from flask_login import current_user
from data import db_session
from data.user import Route

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

def _upload_dir():
    return os.path.join(current_app.root_path, 'static', 'uploads')

def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated or not getattr(current_user, 'is_admin', False):
            abort(403)
        return view(*args, **kwargs)
    return wrapped

@admin_bp.route('/routes')
@admin_required
def routes_list():
    session = db_session.create_session()
    routes = session.query(Route).order_by(Route.id).all()
    session.close()
    return render_template('cul/admin_routes.html', routes=routes)

@admin_bp.route('/routes/add', methods=['GET', 'POST'])
@admin_required
def add_route():
    if request.method == 'POST':
        session = db_session.create_session()

        image_file = request.files.get('image')
        image_filename = None
        if image_file and image_file.filename:
            os.makedirs(_upload_dir(), exist_ok=True)
            image_filename = image_file.filename
            image_path = os.path.join(_upload_dir(), image_filename)
            image_file.save(image_path)

        route = Route(
            title=request.form['title'],
            short_description=request.form.get('short_description') or '',
            description=request.form.get('description') or '',
            image_url=image_filename,
            duration=float(request.form.get('duration') or 0),
            difficulty=request.form.get('difficulty') or '',
            map_embed=request.form.get('map_embed') or ''
        )
        session.add(route)
        session.flush()  # чтобы был route.id
        route.route_key = f"route_cul_{route.id}"
        session.commit()
        session.close()

        flash('Маршрут создан.', 'success')
        return redirect(url_for('admin.routes_list'))

    return render_template('cul/admin_edit_route.html', route=None)

@admin_bp.route('/routes/edit/<int:id>', methods=['GET', 'POST'])
@admin_required
def edit_route(id):
    session = db_session.create_session()
    route = session.get(Route, id)
    if not route:
        session.close()
        abort(404)

    if request.method == 'POST':
        route.title = request.form['title']
        route.short_description = request.form.get('short_description') or ''
        route.description = request.form.get('description') or ''
        try:
            route.duration = float(request.form.get('duration') or 0)
        except ValueError:
            route.duration = 0
        route.difficulty = request.form.get('difficulty') or ''
        route.map_embed = request.form.get('map_embed') or ''

        image_file = request.files.get('image')
        if image_file and image_file.filename:
            os.makedirs(_upload_dir(), exist_ok=True)
            image_filename = image_file.filename
            image_path = os.path.join(_upload_dir(), image_filename)
            image_file.save(image_path)
            route.image_url = image_filename

        session.commit()
        session.close()
        flash('Изменения сохранены.', 'success')
        return redirect(url_for('admin.routes_list'))

    # GET: отдадим объект в шаблон и закроем сессию безопасно
    session.expunge(route)  # отделяем, чтобы атрибуты были доступны после close()
    session.close()
    return render_template('cul/admin_edit_route.html', route=route)

@admin_bp.route('/routes/delete/<int:id>')
@admin_required
def delete_route(id):
    session = db_session.create_session()
    route = session.get(Route, id)
    if route:
        session.delete(route)
        session.commit()
        flash('Маршрут удалён.', 'success')
    session.close()
    return redirect(url_for('admin.routes_list'))