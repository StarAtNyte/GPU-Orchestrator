"""Authentication module for admin dashboard."""
from .session import (
    create_session,
    validate_session,
    destroy_session,
    cleanup_expired_sessions,
    update_last_login,
    log_audit_action,
    get_db_connection
)

__all__ = [
    'create_session',
    'validate_session',
    'destroy_session',
    'cleanup_expired_sessions',
    'update_last_login',
    'log_audit_action',
    'get_db_connection'
]
