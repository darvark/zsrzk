from __future__ import annotations

from datetime import datetime
from functools import wraps

from flask import flash, g, redirect, request, session, url_for

from .extensions import db
from .models import AdminUser


SESSION_KEY = "admin_user_id"


def get_current_admin_user() -> AdminUser | None:
    """Return the signed-in admin user for the current request, if any."""
    if hasattr(g, "current_admin_user"):
        return g.current_admin_user

    user_id = session.get(SESSION_KEY)
    if not user_id:
        g.current_admin_user = None
        return None

    user = db.session.get(AdminUser, user_id)
    g.current_admin_user = user
    return user


def login_admin_user(user: AdminUser) -> None:
    """Persist an admin login in the session and update audit metadata."""
    session[SESSION_KEY] = user.id
    user.last_login_at = datetime.utcnow()
    db.session.commit()
    g.current_admin_user = user


def logout_admin_user() -> None:
    """Clear the current admin session."""
    session.pop(SESSION_KEY, None)
    g.current_admin_user = None


def admin_login_required(view):
    """Require an authenticated admin user before executing a view."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if get_current_admin_user() is None:
            flash("Zaloguj się, aby uzyskać dostęp do panelu administracyjnego.", "error")
            return redirect(url_for("admin.login", next=request.full_path if request.query_string else None))
        return view(*args, **kwargs)

    return wrapped