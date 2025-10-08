import os
from flask import Blueprint, jsonify, render_template, request, abort, current_app, url_for
from flask_login import login_required, current_user
from sqlalchemy import desc, or_
from data import db_session
from data.user import Route

cul_bp = Blueprint("cul", __name__)  # без url_prefix, оставляем существующие пути

def _is_admin() -> bool:
    return bool(getattr(current_user, "is_admin", False))

# JSON для витрины (с фильтрами и учетом is_published)
@cul_bp.route("/cul/cultural_routes_json")
def cultural_routes_json():
    db_sess = db_session.create_session()
    try:
        q     = (request.args.get("q") or "").strip()
        diffs = request.args.getlist("diff")
        dmin  = request.args.get("dmin", type=float)
        dmax  = request.args.get("dmax", type=float)
        sort  = request.args.get("sort", "popular")
        page  = max(1, request.args.get("page", 1, type=int))
        per_page = 12

        query = db_sess.query(Route)

        if q:
            like = f"%{q}%"
            query = query.filter(or_(
                Route.title.like(like),
                Route.description.like(like),
                Route.short_description.like(like),
            ))
        if diffs:
            query = query.filter(Route.difficulty.in_(diffs))
        if dmin is not None:
            query = query.filter(Route.duration >= dmin)
        if dmax is not None:
            query = query.filter(Route.duration <= dmax)

        if not _is_admin():
            query = query.filter(Route.is_published.is_(True))

        if sort == "new":
            query = query.order_by(desc(Route.id))
        else:
            query = query.order_by(desc(Route.id))

        total = query.count()
        pages = max(1, (total + per_page - 1) // per_page)
        items = query.limit(per_page).offset((page - 1) * per_page).all()

        def ser(r: Route):
            return {
                "id": r.id,
                "title": r.title,
                "short_description": r.short_description,
                "difficulty": r.difficulty,
                "duration": r.duration,
                "image_url": r.image_url or url_for('static', filename='img/placeholder.jpg'),
                "is_published": bool(r.is_published),
            }

        return jsonify({
            "items": [ser(r) for r in items],
            "page": page, "pages": pages, "total": total,
            "is_admin": _is_admin(),
        })
    finally:
        db_sess.close()

# SSR-страница витрины (если зайдут напрямую)
@cul_bp.route("/cultural_routes")
def cultural_routes():
    db_sess = db_session.create_session()
    try:
        q = db_sess.query(Route)
        if not _is_admin():
            q = q.filter(Route.is_published.is_(True))
        routes = q.order_by(desc(Route.id)).all()
        return render_template("cul/cultural_routes.html", routes=routes, categories=[])
    finally:
        db_sess.close()

# Детальная страница маршрута
@cul_bp.route('/cul/<int:route_id>')
def cul_dynamic(route_id):
    db_sess = db_session.create_session()
    try:
        route = db_sess.get(Route, route_id)
    finally:
        db_sess.close()
    if not route:
        abort(404)
    if not route.is_published and not _is_admin():
        abort(404)
    return render_template("cul/route_detail.html", route=route, current_user=current_user)

# -------- Админ API --------

# Получить данные маршрута для предзаполнения формы
@cul_bp.route("/cul/admin/get/<int:rid>", methods=["GET"])
@login_required
def cul_admin_get(rid):
    if not _is_admin():
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
            "description": obj.description,
            "difficulty": obj.difficulty,
            "duration": obj.duration,
            "image_url": obj.image_url,
            "is_published": bool(obj.is_published),
        }
        return jsonify({"success": True, "data": data})
    finally:
        db_sess.close()

# Создать маршрут
@cul_bp.route("/cul/admin/create", methods=["POST"])
@login_required
def cul_admin_create():
    if not _is_admin():
        return jsonify({"success": False, "error": "forbidden"}), 403
    db_sess = db_session.create_session()
    try:
        title = (request.form.get("title") or "").strip()
        if not title:
            return jsonify({"success": False, "error": "title required"}), 400

        r = Route(
            title=title,
            short_description=request.form.get("short_description") or "",
            description=request.form.get("description") or "",
            duration=(float(request.form.get("duration")) if request.form.get("duration") else None),
            difficulty=request.form.get("difficulty") or "",
            is_published=True,
        )

        img = request.files.get("image")
        if img and img.filename:
            updir = os.path.join(current_app.static_folder, "uploads")
            os.makedirs(updir, exist_ok=True)
            path = os.path.join(updir, img.filename)
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

# Обновить маршрут (не затираем пустыми значениями)
@cul_bp.route("/cul/admin/update/<int:rid>", methods=["POST"])
@login_required
def cul_admin_update(rid):
    if not _is_admin():
        return jsonify({"success": False, "error": "forbidden"}), 403
    db_sess = db_session.create_session()
    try:
        obj = db_sess.get(Route, rid)
        if not obj:
            return jsonify({"success": False, "error": "not found"}), 404

        def set_if_nonempty(attr, key, conv=None):
            if key in request.form:
                v = request.form.get(key)
                if v is not None and v != "":
                    obj.__setattr__(attr, conv(v) if conv else v)

        set_if_nonempty("title", "title", str)
        set_if_nonempty("short_description", "short_description", str)
        set_if_nonempty("description", "description", str)
        set_if_nonempty("difficulty", "difficulty", str)
        set_if_nonempty("duration", "duration", float)

        img = request.files.get("image")
        if img and img.filename:
            updir = os.path.join(current_app.static_folder, "uploads")
            os.makedirs(updir, exist_ok=True)
            path = os.path.join(updir, img.filename)
            img.save(path)
            obj.image_url = img.filename

        db_sess.commit()
        return jsonify({"success": True})
    except Exception as e:
        db_sess.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        db_sess.close()

# Удалить маршрут
@cul_bp.route("/cul/admin/delete/<int:rid>", methods=["POST"])
@login_required
def cul_admin_delete(rid):
    if not _is_admin():
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

# Включить/выключить публикацию
@cul_bp.route("/cul/admin/toggle_publish/<int:rid>", methods=["POST"])
@login_required
def cul_admin_toggle_publish(rid):
    if not _is_admin():
        return jsonify({"success": False, "error": "forbidden"}), 403
    db_sess = db_session.create_session()
    try:
        obj = db_sess.get(Route, rid)
        if not obj:
            return jsonify({"success": False, "error": "not found"}), 404
        obj.is_published = not bool(obj.is_published)
        db_sess.commit()
        return jsonify({"success": True, "is_published": bool(obj.is_published)})
    finally:
        db_sess.close()