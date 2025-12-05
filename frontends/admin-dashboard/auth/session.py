"""
Session management for admin dashboard authentication.
Provides utilities for creating, validating, and destroying admin sessions.
"""
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict
import psycopg2
from psycopg2.extras import RealDictCursor


def get_db_connection():
    """Get PostgreSQL database connection."""
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        database=os.getenv("POSTGRES_DB", "gpu_orchestrator"),
        user=os.getenv("POSTGRES_USER", "admin"),
        password=os.getenv("POSTGRES_PASSWORD", "admin123"),
        cursor_factory=RealDictCursor
    )


def create_session(
    admin_id: str,
    ip_address: str,
    user_agent: str,
    session_hours: int = 24
) -> str:
    """
    Create a new admin session.

    Args:
        admin_id: UUID of the admin user
        ip_address: IP address of the client
        user_agent: User agent string
        session_hours: Session expiry in hours (default: 24)

    Returns:
        session_id: UUID of the created session
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    session_id = str(uuid.uuid4())
    expires_at = datetime.now(timezone.utc) + timedelta(hours=session_hours)

    cursor.execute("""
        INSERT INTO admin_sessions
        (session_id, admin_id, expires_at, ip_address, user_agent)
        VALUES (%s, %s, %s, %s, %s)
    """, (session_id, admin_id, expires_at, ip_address, user_agent))

    conn.commit()
    cursor.close()
    conn.close()

    return session_id


def validate_session(session_id: str) -> Optional[Dict]:
    """
    Validate a session and return admin info if valid.

    Args:
        session_id: UUID of the session to validate

    Returns:
        Admin user dict if valid, None if invalid/expired
    """
    if not session_id:
        return None

    conn = get_db_connection()
    cursor = conn.cursor()

    # Get session with admin info
    cursor.execute("""
        SELECT
            s.session_id,
            s.admin_id,
            s.expires_at,
            s.last_activity,
            u.username,
            u.email,
            u.is_active
        FROM admin_sessions s
        JOIN admin_users u ON s.admin_id = u.id
        WHERE s.session_id = %s
    """, (session_id,))

    session = cursor.fetchone()

    if not session:
        cursor.close()
        conn.close()
        return None

    # Check if session expired
    if session['expires_at'] < datetime.now(timezone.utc):
        cursor.close()
        conn.close()
        return None

    # Check if user is active
    if not session['is_active']:
        cursor.close()
        conn.close()
        return None

    # Update last activity
    cursor.execute("""
        UPDATE admin_sessions
        SET last_activity = NOW()
        WHERE session_id = %s
    """, (session_id,))

    conn.commit()
    cursor.close()
    conn.close()

    return {
        'session_id': session['session_id'],
        'admin_id': str(session['admin_id']),
        'username': session['username'],
        'email': session['email']
    }


def destroy_session(session_id: str) -> bool:
    """
    Destroy an admin session (logout).

    Args:
        session_id: UUID of the session to destroy

    Returns:
        True if session was destroyed, False otherwise
    """
    if not session_id:
        return False

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        DELETE FROM admin_sessions
        WHERE session_id = %s
    """, (session_id,))

    deleted = cursor.rowcount > 0

    conn.commit()
    cursor.close()
    conn.close()

    return deleted


def cleanup_expired_sessions() -> int:
    """
    Clean up expired sessions from the database.

    Returns:
        Number of sessions cleaned up
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        DELETE FROM admin_sessions
        WHERE expires_at < NOW()
    """)

    count = cursor.rowcount

    conn.commit()
    cursor.close()
    conn.close()

    return count


def update_last_login(admin_id: str) -> None:
    """
    Update the last_login timestamp for an admin user.

    Args:
        admin_id: UUID of the admin user
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE admin_users
        SET last_login = NOW()
        WHERE id = %s
    """, (admin_id,))

    conn.commit()
    cursor.close()
    conn.close()


def log_audit_action(
    admin_id: str,
    action: str,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    details: Optional[Dict] = None,
    ip_address: Optional[str] = None
) -> None:
    """
    Log an admin action to the audit log.

    Args:
        admin_id: UUID of the admin user
        action: Action performed (e.g., 'CANCEL_JOB', 'SWITCH_WORKER')
        resource_type: Type of resource affected (e.g., 'job', 'worker')
        resource_id: ID of the resource
        details: Additional details as JSON
        ip_address: IP address of the client
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO admin_audit_log
        (admin_id, action, resource_type, resource_id, details, ip_address)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (admin_id, action, resource_type, resource_id,
          psycopg2.extras.Json(details) if details else None,
          ip_address))

    conn.commit()
    cursor.close()
    conn.close()
