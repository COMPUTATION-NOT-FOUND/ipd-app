"""Student/local build — no audit logging.

Audit logging is a website concern (it records auth/admin actions to Firestore on
ipd-hub). The local app has no login and no Firestore, so these are no-ops.
"""


def log_audit_event(*args, **kwargs):
    return None


def get_request_context(request=None):
    return {'remote_ip': '', 'user_agent': ''}
