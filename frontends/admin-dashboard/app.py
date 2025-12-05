"""
GPU Orchestrator Admin Dashboard
FastAPI application for managing GPU workers, jobs, and monitoring metrics.
"""
import os
import json
import asyncio
from typing import Optional
from datetime import datetime

import bcrypt
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, Request, Depends, HTTPException, Form, Response
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from auth import (
    create_session,
    validate_session,
    destroy_session,
    update_last_login,
    log_audit_action,
    get_db_connection
)

# Configuration
ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://orchestrator:8080")
SESSION_COOKIE_NAME = "admin_session"
SESSION_MAX_AGE = 86400  # 24 hours

# Initialize FastAPI app
app = FastAPI(
    title="GPU Orchestrator Admin Dashboard",
    description="Admin dashboard for managing GPU workers and jobs",
    version="1.0.0"
)

# Mount static files and templates
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# Dependency for protected routes
def get_current_admin(request: Request):
    """
    Dependency to get the current authenticated admin.
    Raises HTTPException if not authenticated.
    """
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_id:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated",
            headers={"Location": "/login"}
        )

    admin = validate_session(session_id)
    if not admin:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired session",
            headers={"Location": "/login"}
        )

    return admin


def get_client_ip(request: Request) -> str:
    """Get client IP address from request."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# Authentication routes
@app.get("/", response_class=RedirectResponse)
async def root():
    """Redirect root to dashboard."""
    return RedirectResponse(url="/dashboard")


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Render login page."""
    # Check if already logged in
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if session_id and validate_session(session_id):
        return RedirectResponse(url="/dashboard")

    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...)
):
    """
    Handle login form submission.
    Validates credentials and creates session.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get user from database
    cursor.execute("""
        SELECT id, username, password_hash, email, is_active
        FROM admin_users
        WHERE username = %s
    """, (username,))

    user = cursor.fetchone()
    cursor.close()
    conn.close()

    # Validate credentials
    if not user or not user['is_active']:
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": "Invalid username or password"
            }
        )

    # Check password
    password_match = bcrypt.checkpw(
        password.encode('utf-8'),
        user['password_hash'].encode('utf-8')
    )

    if not password_match:
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": "Invalid username or password"
            }
        )

    # Create session
    session_id = create_session(
        admin_id=str(user['id']),
        ip_address=get_client_ip(request),
        user_agent=request.headers.get("User-Agent", "")
    )

    # Update last login
    update_last_login(str(user['id']))

    # Log audit action
    log_audit_action(
        admin_id=str(user['id']),
        action="LOGIN",
        ip_address=get_client_ip(request)
    )

    # Set cookie and redirect
    response = RedirectResponse(url="/dashboard", status_code=303)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_id,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax"
    )

    return response


@app.get("/logout")
async def logout(request: Request):
    """Handle logout - destroy session and redirect to login."""
    session_id = request.cookies.get(SESSION_COOKIE_NAME)

    if session_id:
        admin = validate_session(session_id)
        if admin:
            log_audit_action(
                admin_id=admin['admin_id'],
                action="LOGOUT",
                ip_address=get_client_ip(request)
            )
        destroy_session(session_id)

    response = RedirectResponse(url="/login")
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


# Dashboard pages
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(
    request: Request,
    admin: dict = Depends(get_current_admin)
):
    """Render dashboard overview page."""
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "admin": admin,
        "active_page": "dashboard"
    })


@app.get("/jobs", response_class=HTMLResponse)
async def jobs_page(
    request: Request,
    admin: dict = Depends(get_current_admin)
):
    """Render jobs management page."""
    return templates.TemplateResponse("jobs.html", {
        "request": request,
        "admin": admin,
        "active_page": "jobs"
    })


@app.get("/workers", response_class=HTMLResponse)
async def workers_page(
    request: Request,
    admin: dict = Depends(get_current_admin)
):
    """Render workers monitoring page."""
    return templates.TemplateResponse("workers.html", {
        "request": request,
        "admin": admin,
        "active_page": "workers"
    })


@app.get("/metrics", response_class=HTMLResponse)
async def metrics_page(
    request: Request,
    admin: dict = Depends(get_current_admin)
):
    """Render GPU metrics page."""
    return templates.TemplateResponse("metrics.html", {
        "request": request,
        "admin": admin,
        "active_page": "metrics"
    })


@app.get("/config", response_class=HTMLResponse)
async def config_page(
    request: Request,
    admin: dict = Depends(get_current_admin)
):
    """Render configuration viewer page."""
    return templates.TemplateResponse("config.html", {
        "request": request,
        "admin": admin,
        "active_page": "config"
    })


# API endpoints - Job Management
@app.get("/api/jobs")
async def list_jobs(
    request: Request,
    status: str = "",
    app_id: str = "",
    worker_id: str = "",
    limit: int = 50,
    offset: int = 0,
    admin: dict = Depends(get_current_admin)
):
    """List jobs with filtering and pagination."""
    try:
        params = {
            "status": status,
            "app_id": app_id,
            "worker_id": worker_id,
            "limit": limit,
            "offset": offset
        }
        response = requests.get(
            f"{ORCHESTRATOR_URL}/admin/jobs",
            params=params,
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch jobs: {str(e)}")


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(
    request: Request,
    job_id: str,
    admin: dict = Depends(get_current_admin)
):
    """Cancel a job."""
    try:
        # Log audit action
        log_audit_action(
            admin_id=admin['admin_id'],
            action="CANCEL_JOB",
            resource_type="job",
            resource_id=job_id,
            ip_address=get_client_ip(request)
        )

        response = requests.post(
            f"{ORCHESTRATOR_URL}/admin/jobs/{job_id}/cancel",
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to cancel job: {str(e)}")


@app.post("/api/jobs/{job_id}/retry")
async def retry_job(
    request: Request,
    job_id: str,
    admin: dict = Depends(get_current_admin)
):
    """Retry a failed job."""
    try:
        # Log audit action
        log_audit_action(
            admin_id=admin['admin_id'],
            action="RETRY_JOB",
            resource_type="job",
            resource_id=job_id,
            ip_address=get_client_ip(request)
        )

        response = requests.post(
            f"{ORCHESTRATOR_URL}/admin/jobs/{job_id}/retry",
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retry job: {str(e)}")


@app.get("/stream/jobs")
async def stream_jobs(
    request: Request,
    admin: dict = Depends(get_current_admin)
):
    """Server-Sent Events stream for real-time job updates."""
    async def event_generator():
        while True:
            if await request.is_disconnected():
                break

            try:
                # Fetch recent jobs
                response = requests.get(
                    f"{ORCHESTRATOR_URL}/admin/jobs",
                    params={"limit": 20, "offset": 0},
                    timeout=5
                )
                if response.ok:
                    data = response.json()
                    yield f"data: {json.dumps(data, default=str)}\n\n"
            except Exception as e:
                pass

            await asyncio.sleep(5)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# API endpoints - Worker Management
@app.get("/api/workers/status")
async def get_workers_status(admin: dict = Depends(get_current_admin)):
    """Get worker status from orchestrator."""
    try:
        response = requests.get(
            f"{ORCHESTRATOR_URL}/admin/workers/status",
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch worker status: {str(e)}")


@app.post("/api/workers/action")
async def worker_action(
    request: Request,
    admin: dict = Depends(get_current_admin)
):
    """Perform worker action (start, stop, switch)."""
    try:
        data = await request.json()
        action = data.get("action")
        worker_name = data.get("worker_name", "")

        # Log audit action
        log_audit_action(
            admin_id=admin['admin_id'],
            action=f"WORKER_{action.upper()}",
            resource_type="worker",
            resource_id=worker_name,
            details=data,
            ip_address=get_client_ip(request)
        )

        response = requests.post(
            f"{ORCHESTRATOR_URL}/admin/workers/action",
            json=data,
            timeout=60
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Worker action failed: {str(e)}")


@app.get("/stream/workers")
async def stream_workers(
    request: Request,
    admin: dict = Depends(get_current_admin)
):
    """Server-Sent Events stream for real-time worker updates."""
    async def event_generator():
        while True:
            if await request.is_disconnected():
                break

            try:
                response = requests.get(
                    f"{ORCHESTRATOR_URL}/admin/workers/status",
                    timeout=5
                )
                if response.ok:
                    data = response.json()
                    yield f"data: {json.dumps(data, default=str)}\n\n"
            except Exception as e:
                pass

            await asyncio.sleep(5)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# API endpoints - GPU Metrics
@app.get("/api/metrics/gpu")
async def get_gpu_metrics(
    worker_id: str,
    range: str = "24h",
    admin: dict = Depends(get_current_admin)
):
    """Get GPU metrics time-series data."""
    try:
        response = requests.get(
            f"{ORCHESTRATOR_URL}/admin/metrics/gpu",
            params={"worker_id": worker_id, "range": range},
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch GPU metrics: {str(e)}")


@app.get("/api/metrics/latest")
async def get_latest_metrics(admin: dict = Depends(get_current_admin)):
    """Get latest GPU metrics for all workers."""
    try:
        response = requests.get(
            f"{ORCHESTRATOR_URL}/admin/metrics/latest",
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch latest metrics: {str(e)}")


@app.get("/api/metrics/summary")
async def get_summary_metrics(admin: dict = Depends(get_current_admin)):
    """Get dashboard summary metrics."""
    try:
        response = requests.get(
            f"{ORCHESTRATOR_URL}/admin/metrics/summary",
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch summary metrics: {str(e)}")


@app.get("/api/config")
async def get_config(admin: dict = Depends(get_current_admin)):
    """Get system configuration."""
    try:
        response = requests.get(
            f"{ORCHESTRATOR_URL}/admin/config",
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch config: {str(e)}")


# Health check
@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "admin-dashboard"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8090)
