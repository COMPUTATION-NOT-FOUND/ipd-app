"""Student/local build — NO login, NO Firebase.

The local app is single-user on the student's own machine, so the auth gates are
no-ops: everyone is the implicit local owner. These stubs replace the website's
Firebase-backed auth so the app imports and runs with no firebase-admin dependency.
Real authentication lives only on the WEBSITE (ipd-hub).
"""
from functools import wraps


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        return f(*args, **kwargs)
    return decorated


# Same no-op gate; the local owner is treated as the admin.
admin_required = login_required


def is_admin():
    return True


def get_user_role(uid=None):
    return 'admin'


# Unused locally (no login flow) — present so any import succeeds.
def verify_firebase_token(id_token):
    return None


def create_or_update_user(*args, **kwargs):
    return None
