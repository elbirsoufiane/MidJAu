from flask import Flask, render_template, request, redirect, session, url_for, flash
from .user_utils import (
    init_user_if_missing,
    get_user_failed_prompts_path,
    get_user_log_key,
    get_user_settings_path,
    get_user_prompts_path,
    get_user_images_dir,
)
from .user_utils import list_user_image_urls
import requests
import os
import time
import subprocess
import json
from threading import Thread
from flask import send_from_directory
from flask import send_file
import zipfile
import io
from multiprocessing import Lock
from subprocess import Popen
from dotenv import load_dotenv
load_dotenv()
from redis import Redis
from rq import Queue
from io import BytesIO
from app.tigris_utils import (
    upload_file_obj,
    download_file_obj,
    generate_presigned_url,
    delete_file,
)
import pandas as pd
from rq import Worker



# from app.tasks import midjourney_all
from app.tasks import run_mode

from rq.job import Job
from rq.exceptions import NoSuchJobError
import shutil



app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev_key")
LICENSE_VALIDATION_URL = os.getenv("LICENSE_VALIDATION_URL")

running_processes = {}
process_lock = Lock()

redis_conn = Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))
# default_queue = Queue(connection=redis_conn)

def get_user_queue(email: str) -> Queue:
    """Return the proper RQ Queue object for this user’s tier."""
    key  = session.get("saved_key") or session.get("key")
    info = check_license_and_quota(email, key)
    tier = (info or {}).get("tier", "default")
    name = tier if tier in {"Tier1", "Tier2", "Tier3"} else "default"
    return Queue(name=name, connection=redis_conn)


# Rough per-prompt runtimes (seconds) for each mode
MODE_RUNTIME = {
    "U1": 42,
    "U2": 42,
    "U3": 42,
    "U4": 42,
    "All": 58,
}

# Average runtime of a queued job in seconds (for ETA of queue start)
TYPICAL_JOB_RUNTIME = 300

# Redis hash used for tracking running jobs
RUNNING_JOBS_HASH = "running_jobs"


def set_job_id(email: str, job_id: str) -> None:
    """Store the RQ job ID for a user in Redis."""
    redis_conn.hset(RUNNING_JOBS_HASH, email, job_id)


def get_job_id(email: str) -> str | None:
    """Retrieve the stored job ID for a user from Redis."""
    jid = redis_conn.hget(RUNNING_JOBS_HASH, email)
    return jid.decode() if jid else None


def remove_job_id(email: str) -> None:
    """Remove a stored job ID for a user from Redis."""
    redis_conn.hdel(RUNNING_JOBS_HASH, email)

# Callback for RQ jobs to clear job ID when they finish
def clear_job_id_on_success(job, connection, result):
    email = job.meta.get("user_email")
    if email:
        remove_job_id(email)

def estimate_queue_eta_parallel(email, queue, redis_conn, num_workers=1):
    """
    Returns (position_in_queue, eta_minutes)
    - position_in_queue: 0 = running, 1 = next, 2 = after, etc
    - eta_minutes: estimated wait time in minutes until user's job starts
    """
    jobs = queue.jobs  # queued jobs, oldest first
    running_job_ids = queue.started_job_registry.get_job_ids()
    job_list = []

    # Add currently running jobs first (oldest first)
    for job_id in running_job_ids:
        try:
            job = Job.fetch(job_id, connection=redis_conn)
            job_list.append(job)
        except Exception:
            pass

    # Add queued jobs
    for job in jobs:
        job_list.append(job)

    # Estimate remaining time for each running job
    worker_available_at = [0] * num_workers
    idx = 0
    user_position = None
    eta_seconds = 0

    for job in job_list:
        meta = getattr(job, 'meta', {})
        mode = meta.get("mode")
        prompts = meta.get("total_prompts")
        job_email = meta.get("user_email")
        completed = meta.get("completed_prompts", 0)

        # fallback for old jobs
        if not prompts and hasattr(job, 'args') and len(job.args) >= 2:
            prompts = job.args[2]
        if not mode and hasattr(job, 'args') and len(job.args) >= 1:
            mode = job.args[0]

        per_prompt = MODE_RUNTIME.get(mode, 60)

        # Calculate remaining time
        if idx < num_workers:
            # For running jobs, use remaining only
            remaining = (prompts or 0) - (completed or 0)
            if remaining < 0:
                remaining = 0
            job_time = remaining * per_prompt
            worker_available_at[idx] = job_time
        else:
            # For queued jobs: assign to soonest available worker
            soonest_worker = min(range(num_workers), key=lambda i: worker_available_at[i])
            start_time = worker_available_at[soonest_worker]
            job_time = (prompts or 0) * per_prompt
            worker_available_at[soonest_worker] += job_time

            if job_email == email and user_position is None:
                user_position = idx
                eta_seconds = start_time

        if job_email == email and user_position is None:
            # If user's job is running now
            user_position = idx
            eta_seconds = 0

        idx += 1

    eta_minutes = int(eta_seconds / 60)
    return user_position, eta_minutes



def get_active_worker_count(redis_conn, queue_name="default"):
    """
    Returns the number of active RQ workers listening to the given queue.
    """
    # Worker.all() returns all workers known to Redis (not just the current process)
    return sum(queue_name in worker.queue_names() for worker in Worker.all(connection=redis_conn))

def check_license_and_quota(email, license_key):
    params = {
        "email": email,
        "key": license_key
    }
    try:
        resp = requests.get(LICENSE_VALIDATION_URL, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()  # Should have success, tier, dailyQuota, jobQuota, promptsToday
    except Exception as e:
        print(f"License/Quota check error: {e}")
        return {"success": False, "reason": "Quota check failed"}
    


FLY_API = "https://api.machines.dev/v1"
FLY_TOKEN = os.getenv("FLY_API_TOKEN")

def fly_request(method, path, **kwargs):
    if not FLY_TOKEN:
        raise RuntimeError("FLY_API_TOKEN is not set")
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"Bearer {FLY_TOKEN}"
    headers["Content-Type"] = "application/json"
    url = f"{FLY_API}{path}"
    resp = requests.request(method, url, headers=headers, timeout=10, **kwargs)
    resp.raise_for_status()
    return resp

def list_machines_api(app_name: str) -> list[dict]:
    r = fly_request("GET", f"/apps/{app_name}/machines")
    return r.json() or []

def ensure_worker_for_queue(queue_name: str, timeout: int = 30, poll: int = 3) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        if get_active_worker_count(redis_conn, queue_name=queue_name) > 0:
            return True
        app.logger.info(f"[ensure_worker] Waiting for worker on '{queue_name}'...")
        time.sleep(poll)
    return False


@app.route('/queue_eta')
def queue_eta():
    if "email" not in session:
        return {"error": "Unauthorized"}, 401

    email = session["email"]
    q = get_user_queue(email)
    num = get_active_worker_count(redis_conn, queue_name=q.name)
    if num <= 0:
        return {"num_workers": 0, "position": None, "eta_minutes": None}
    pos, eta = estimate_queue_eta_parallel(email, q, redis_conn, num_workers=num)
    if pos is None:
        job_id = get_job_id(email)
        if job_id:
            try:
                job = Job.fetch(job_id, connection=redis_conn)
                if job.get_status() == "started":
                    pos = 0
            except NoSuchJobError:
                pass
    return {"num_workers": num, "position": pos, "eta_minutes": eta}



@app.route('/job_progress')
def job_progress():
    if "email" not in session:
        return {"error": "Unauthorized"}, 401

    email = session["email"]
    job_id = get_job_id(email)
    if not job_id:
        return {"status": "none"}
    try:
        job = Job.fetch(job_id, connection=redis_conn)
    except NoSuchJobError:
        remove_job_id(email)
        return {"status": "none"}

    if job.get_status() == "started":
        meta = job.meta
        completed = meta.get("completed_prompts", 0)
        total = meta.get("total_prompts", 0)
        mode = meta.get("mode")
        per_prompt = MODE_RUNTIME.get(mode, 60)
        remaining = max(0, total - completed)
        remaining_seconds = remaining * per_prompt
        return {
            "status": "running",
            "completed_prompts": completed,
            "total_prompts": total,
            "remaining_seconds": remaining_seconds
        }
    elif job.get_status() == "queued":
        meta = job.meta
        total = meta.get("total_prompts", 0)
        mode = meta.get("mode")
        per_prompt = MODE_RUNTIME.get(mode, 60)
        duration_estimate = total * per_prompt
        return {
            "status": "queued",
            "total_prompts": total,
            "duration_estimate": duration_estimate
        }
    else:
        return {"status": job.get_status()}



@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        key = request.form["key"]
        remember = "remember" in request.form

        try:
            response = requests.get(LICENSE_VALIDATION_URL, params={"email": email, "key": key})
            
            print("✅ RAW RESPONSE:", response.text, flush=True)
            print("✅ RESPONSE JSON:", response.json(), flush=True)
            print("✅ RAW RESPONSE:", response.text)

            data = response.json()
            if data.get("success"):
                session["email"] = email
                session["key"] = key
                if remember:
                    session["saved_email"] = email
                    session["saved_key"] = key
                else:
                    session.pop("saved_email", None)
                    session.pop("saved_key", None)
                init_user_if_missing(email)
                flash("✅ Welcome! You have been logged in successfully", "success")
                session["just_logged_in"] = True
                return redirect(url_for("dashboard"))
        except Exception as e:
            print(f"❌ License validation error: {e}")

        flash("❌ Invalid license. Please try again.", "error")

    return render_template("login.html")


@app.route("/dashboard", methods=["GET", "POST"])
def dashboard():
    if "email" not in session:
        return redirect(url_for("login"))


    email = session["email"]

    # 👇 If this is a GET, use counters from session (if present)
    if request.method == "GET":
        queued_info = session.pop('dashboard_counters', {})
        print("🔎 dashboard_counters found in session (popped):", queued_info, flush=True)

        selected_mode = queued_info.get('mode')
        initial_queue_pos = queued_info.get('queue_position')

        key = session.get("saved_key") or session.get("key")
        license_info = check_license_and_quota(email, key)
        q = get_user_queue(email)

        num_workers = get_active_worker_count(redis_conn, queue_name=q.name)
        if not num_workers:
            num_workers = 1

        position, eta_minutes = estimate_queue_eta_parallel(email, q, redis_conn, num_workers=num_workers)

        print("🖥️ Rendering dashboard with queued_info:", queued_info, flush=True)

        return render_template(
            "dashboard.html",
            filename=None,
            selected_mode=selected_mode,
            row_count=None,
            duration_estimate=None,
            queue_eta=None,
            just_logged_in=session.pop("just_logged_in", False),
            queue_position=position,
            queue_eta_minutes=eta_minutes,
            queued_mode=selected_mode,
            queued_position=initial_queue_pos,
        )

    # Otherwise, continue with POST logic...
    filename = None
    mode = None
    row_count = None
    duration_estimate = None
    queue_eta = None



    if request.method == "POST":
        # 🔐 License revalidation before proceeding
        email = session["email"]
        key = session.get("saved_key") or session.get("key")

        # 1. Call the Apps Script to get quotas
        license_info = check_license_and_quota(email, key)
        tier = license_info.get("tier", "default")
        if not license_info.get("success"):
            flash("❌ License check/validation failed. Please try again.", "error")
            return redirect(url_for("dashboard"))


        try:
            response = requests.get(
                LICENSE_VALIDATION_URL, params={"email": email, "key": key}
            )
            data = response.json()
            if not data.get("success"):
                session.clear()
                flash("❌ Invalid license. Please try again.", "error")
                session["license_failed"] = True
                return redirect(url_for("login"))
        except Exception as e:
            flash(f"⚠️ License check failed: {e}", "error")
            return redirect(url_for("dashboard"))

        # ⏩ Continue with script execution if license is still valid
        # Clear any previous live output log before proceeding
        redis_conn.delete(get_user_log_key(email))
        mode = request.form["mode"]
        file = request.files["prompt_file"]

        # Check if a job is already running for this user
        existing_job_id = get_job_id(email)
        if existing_job_id:
            try:
                existing_job = Job.fetch(existing_job_id, connection=redis_conn)
                if existing_job.get_status() in ("queued", "started"):
                    flash(
                        "⚠️ A job is already running for this account. Please cancel it before queuing another.",
                        "error",
                    )
                    return render_template(
                        "dashboard.html",
                        filename=None,
                        selected_mode=mode,
                        row_count=row_count,
                        duration_estimate=duration_estimate,
                        queue_eta=queue_eta,
                        queue_position=None,
                        queue_eta_minutes=None,
                    )
            except NoSuchJobError:
                pass  # Remove stale key below
            remove_job_id(email)


        if file:
            file_bytes = file.read()
            # Two independent streams
            excel_stream_upload = BytesIO(file_bytes)
            excel_stream_pandas = BytesIO(file_bytes)
            key = f"Users/{email}/prompts.xlsx"

            # Upload to Tigris
            success = upload_file_obj(excel_stream_upload, key)
            if not success:
                flash("❌ Failed to upload prompts Excel file to cloud storage", "error")
                return render_template(
                    "dashboard.html",
                    filename=None,
                    selected_mode=mode,
                    row_count=row_count,
                    duration_estimate=duration_estimate,
                    queue_eta=queue_eta,
                    queue_position=None,
                    queue_eta_minutes=None,
                )

            # Count rows using pandas (with a fresh, untouched BytesIO)
            try:
                df = pd.read_excel(excel_stream_pandas)
                if "prompt" in df.columns:
                    row_count = df["prompt"].dropna().size
                else:
                    row_count = len(df)
            except Exception as e:
                print("Row count failed", e)
                row_count = 0

            # 2. Enforce quotas
            job_quota = int(license_info.get("jobQuota", 0))
            daily_quota = int(license_info.get("dailyQuota", 0))
            prompts_today = int(license_info.get("promptsToday", 0))
            if row_count > job_quota:
                flash(f"❌ Your current tier only allows {job_quota} prompts per job. Your file has {row_count}.", "error")
                return render_template(
                    "dashboard.html",
                    filename=None,
                    selected_mode=mode,
                    row_count=row_count,
                    duration_estimate=None,
                    queue_eta=None,
                    start_failed=True,
                    queue_position=None,
                    queue_eta_minutes=None,
                )
            if prompts_today + row_count > daily_quota:
                flash(f"❌ Daily quota exceeded! You have used {prompts_today}/{daily_quota} prompts today.", "error")
                return render_template(
                    "dashboard.html",
                    filename=None,
                    selected_mode=mode,
                    row_count=row_count,
                    duration_estimate=None,
                    queue_eta=None,
                    start_failed=True,
                    queue_position=None,
                    queue_eta_minutes=None,
                )
                


            # 🚚 Generate a temporary download URL for the worker
            presigned_url = generate_presigned_url(key)
            if not presigned_url:
                flash("❌ Failed to generate presigned URL", "error")
                return render_template(
                    "dashboard.html",
                    filename=None,
                    selected_mode=mode,
                    row_count=row_count,
                    duration_estimate=duration_estimate,
                    queue_eta=queue_eta,
                    queue_position=None,
                    queue_eta_minutes=None,
                )

            # File was uploaded — you can display the filename
            filename = file.filename

            env = os.environ.copy()
            env["PROMPTS_FILE"] = presigned_url
            env["USER_EMAIL"] = email


            try:
                settings_stream = download_file_obj(f"Users/{email}/settings.json")
                if not settings_stream:
                    flash("❌ Make sure all of your settings fields are populated, correct and saved.", "error")
                    return render_template(
                        "dashboard.html",
                        filename=filename,
                        selected_mode=mode,
                        row_count=row_count,
                        duration_estimate=duration_estimate,
                        queue_eta=queue_eta,
                        start_failed=True,
                        queue_position=None,
                        queue_eta_minutes=None,
                    )
                settings = json.load(settings_stream)
                required = [
                    "USER TOKEN", "CHANNEL ID", "GUILD ID",
                    "MIDJOURNEY APP ID", "MIDJOURNEY COMMAND ID", "COMMAND VERSION",
                ]
                if any(not settings.get(k) for k in required):
                    flash("❌ Make sure all of your settings fields are populated and correct.", "error")
                    return render_template(
                        "dashboard.html",
                        filename=filename,
                        selected_mode=mode,
                        row_count=row_count,
                        duration_estimate=duration_estimate,
                        queue_eta=queue_eta,
                        start_failed=True,
                        queue_position=None,
                        queue_eta_minutes=None,
                    )
                for k, v in settings.items():
                    env_key = k.replace(" ", "_")
                    env[env_key] = v

            except Exception as e:
                flash(f"⚠️ Failed to load settings: {e}", "error")
                return render_template(
                    "dashboard.html",
                    filename=filename,
                    selected_mode=mode,
                    row_count=row_count,
                    duration_estimate=duration_estimate,
                    queue_eta=queue_eta,
                    queue_position=None,
                    queue_eta_minutes=None,
                )

            # Estimate duration and queue start
            per_prompt = MODE_RUNTIME.get(mode, 60)
            duration_estimate = int((row_count * per_prompt) / 60)
            # queued_ahead = int(tier_queue.count)
            q = get_user_queue(email) 
            queued_ahead = int(q.count)
            queue_eta = int((queued_ahead * TYPICAL_JOB_RUNTIME) / 60)


            if mode in ["U1", "U2", "U3", "U4", "All"]:
                key = session.get("saved_key") or session.get("key")
                print("🔎 ENQUEUE: key =", key)


                q = get_user_queue(email)


                
                job = q.enqueue(
                    run_mode,
                    mode,
                    email,
                    presigned_url,
                    key,
                    job_timeout=7200,
                    result_ttl=0,
                    on_success=clear_job_id_on_success,
                    meta={
                        "user_email": email,
                        "mode": mode,
                        "total_prompts": row_count,   # row_count is number of prompts for this job
                        "completed_prompts": 0,       # Optional; update from worker as the job progresses
                    },
                )
                set_job_id(email, job.id)
                if not ensure_worker_for_queue(q.name, timeout=30, poll=3):
                    app.logger.warning("[worker-start] No active workers after waiting.")
                    flash("❌ No active workers available. Please try again later.", "error")
                    return render_template(
                        "dashboard.html",
                        filename=filename,
                        selected_mode=mode,
                        row_count=row_count,
                        duration_estimate=duration_estimate,
                        queue_eta=queue_eta,
                        start_failed=True,
                        queue_position=None,
                        queue_eta_minutes=None,
                    )

                num_workers = get_active_worker_count(redis_conn, queue_name=q.name)
                position, _ = estimate_queue_eta_parallel(email, q, redis_conn, num_workers=num_workers)

                session['dashboard_counters'] = {
                    'mode': mode,
                    'queue_position': position,
                }

                print("🚩 Set dashboard_counters in session:", session['dashboard_counters'], flush=True)


                return redirect(url_for("dashboard"))

            else:
                flash("❌ Invalid mode selected.", "error")

    return render_template(
        "dashboard.html",
        filename=filename,
        selected_mode=mode,
        row_count=row_count,
        duration_estimate=duration_estimate,
        queue_eta=queue_eta,
        just_logged_in=session.pop("just_logged_in", False),
        queue_position=None,
        queue_eta_minutes=None,
    )


@app.route("/live_output")
def live_output():
    if "email" not in session:
        return "Unauthorized", 401

    log_key = get_user_log_key(session["email"])
    logs = redis_conn.lrange(log_key, 0, -1)
    if not logs:
        return "Waiting for output..."
    return "\n".join(m.decode() for m in logs)


@app.route("/queue_length")
def queue_length():
    """Return current number of queued jobs."""
    email = session.get("email")
    q = get_user_queue(email) if email else Queue(connection=redis_conn)
    return {"count": int(q.count)}


@app.route("/Users/<path:filepath>")
def uploaded_file(filepath):
    safe_path = os.path.join("Users", *filepath.split("/"))
    directory = os.path.dirname(safe_path)
    filename = os.path.basename(safe_path)

    if not os.path.exists(os.path.join(directory, filename)):
        return f"❌ File not found: {filepath}", 404

    return send_from_directory(directory, filename)

@app.route("/settings", methods=["GET", "POST"])
def settings():
    if "email" not in session:
        return redirect(url_for("login"))

    email = session["email"]
    settings_path = get_user_settings_path(email)

    try:
        remote_stream = download_file_obj(f"Users/{email}/settings.json")
        if remote_stream:
            remote_settings = json.load(remote_stream)
            os.makedirs(os.path.dirname(settings_path), exist_ok=True)
            with open(settings_path, "w") as f:
                json.dump(remote_settings, f, indent=4)
        else:
            flash("⚠️ No settings found in cloud storage", "error")
    except Exception as e:
        flash(f"⚠️ Failed to download settings: {e}", "error")

    if request.method == "POST":
        new_settings = {
            "USER TOKEN": request.form.get("user_token"),
            "CHANNEL ID": request.form.get("channel_id"),
            "GUILD ID": request.form.get("guild_id"),
            "MIDJOURNEY APP ID": request.form.get("midjourney_app_id"),
            "MIDJOURNEY COMMAND ID": request.form.get("midjourney_command_id"),
            "COMMAND VERSION": request.form.get("command_version")
        }
        with open(settings_path, "w") as f:
            json.dump(new_settings, f, indent=4)
        settings_stream = BytesIO(json.dumps(new_settings).encode("utf-8"))
        success = upload_file_obj(settings_stream, f"Users/{email}/settings.json")
        if success:
            flash("✅ Settings saved!", "success")
        else:
            flash("❌ Failed to upload settings to cloud storage", "error")

    try:
        with open(settings_path) as f:
            current_settings = json.load(f)
    except Exception:
        current_settings = {}

    return render_template("settings.html", settings=current_settings)


@app.route("/subscription")
def subscription():
    if "email" not in session:
        return redirect(url_for("login"))

    email = session["email"]
    key   = session.get("saved_key") or session.get("key")

    info = check_license_and_quota(email, key)
    if not info.get("success"):
        flash("⚠️ Unable to fetch subscription data.", "error")
        return redirect(url_for("dashboard"))
    
    # ─── NEW BLOCK ───────────────────────────────────────────────
    expiry_raw = info.get("expiry")              # "2025-08-11T23:00:00.000Z"
    if expiry_raw:
        date_only      = expiry_raw[:10]         # "2025-08-11"
        expiry_pretty  = f"{date_only} at 12:00AM CST"
    else:
        expiry_pretty  = "—"

    # • translate numeric tier → marketing name
    TIER_NAMES = {
        "Tier1": "Basic",
        "Tier2": "Pro",
        "Tier3": "Premium",
    }
    pretty_tier = TIER_NAMES.get(info.get("tier"), info.get("tier", "—"))
    # ─────────────────────────────────────────────────────────────

    details = {
        "tier": pretty_tier,
        "expiry": expiry_pretty,
        "daily_quota": info.get("dailyQuota"),
        "job_quota": info.get("jobQuota"),
        "prompts_today": info.get("promptsToday"),
    }
    return render_template("subscription.html", details=details)


@app.route('/download_zip')
def download_zip():
    if "email" not in session:
        return "Unauthorized", 401

    email = session["email"]
    zip_key = f"Users/{email}/images.zip"

    zip_stream = download_file_obj(zip_key)
    if not zip_stream:
        flash("❌ ZIP file not found in cloud storage", "error")
        return "ZIP file not available", 404

    try:
        zip_stream.seek(0)
        return send_file(
            zip_stream,
            mimetype='application/zip',
            as_attachment=True,
            download_name='generated_images.zip'
        )
    except Exception as e:
        flash(f"❌ Failed to send ZIP file: {e}", "error")
        return "ZIP file not available", 404


@app.route('/download_images_excel')
def download_images_excel():
    if "email" not in session:
        return "Unauthorized", 401

    email = session["email"]
    excel_key = f"Users/{email}/images.xlsx"

    excel_stream = download_file_obj(excel_key)
    if not excel_stream:
        flash("❌ Excel file not found in cloud storage", "error")
        return "Excel file not available", 404

    try:
        excel_stream.seek(0)
        return send_file(
            excel_stream,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name='images.xlsx'
        )
    except Exception as e:
        flash(f"❌ Failed to send Excel file: {e}", "error")
        return "Excel file not available", 404

from openpyxl import Workbook
from flask import make_response


@app.route("/download_failed_prompts_excel")
def download_failed_prompts_excel():
    if "email" not in session:
        return "Unauthorized", 401

    email = session["email"]
    json_key = f"Users/{email}/failed_prompts.json"

    json_stream = download_file_obj(json_key)
    if not json_stream:
        flash("❌ Failed to download failed prompts", "error")
        return "No failed prompts file", 404
    try:
        data = json.load(json_stream)
    except Exception as e:
        flash(f"❌ Failed to parse failed prompts: {e}", "error")
        return "No failed prompts file", 404

    if not data:
        return "No failed prompts", 204  # No Content

    wb = Workbook()
    ws = wb.active
    ws.append(["prompt", "indexes"])

    for entry in data:
        prompt = entry.get("prompt", "")
        index = entry.get("index", "")
        ws.append([prompt, index])

    excel_stream = BytesIO()
    wb.save(excel_stream)
    excel_stream.seek(0)

    return send_file(
        excel_stream,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name="failed_prompts.xlsx"
    )




@app.route("/cleanup_files", methods=["POST"])
def cleanup_files():
    if "email" not in session:
        return "Unauthorized", 401

    email = session["email"]
    prompts_path = get_user_prompts_path(email)
    image_dir = get_user_images_dir(email)

    # Delete prompts.xlsx
    if os.path.exists(prompts_path):
        os.remove(prompts_path)
    delete_file(f"Users/{email}/prompts.xlsx")

    # Delete all images
    if os.path.exists(image_dir):
        for f in os.listdir(image_dir):
            fpath = os.path.join(image_dir, f)
            if os.path.isfile(fpath):
                os.remove(fpath)
    delete_file(f"Users/{email}/images.zip")

    images_excel_path = os.path.join(os.path.dirname(image_dir), "images.xlsx")
    if os.path.exists(images_excel_path):
        os.remove(images_excel_path)
    delete_file(f"Users/{email}/images.xlsx")

    # Delete the failed_prompts.json file
    failed_path = get_user_failed_prompts_path(email)
    if os.path.exists(failed_path):
        os.remove(failed_path)
    delete_file(f"Users/{email}/failed_prompts.json")

    return "✅ Cleaned up files", 200



@app.route("/logout")
def logout():
    session.clear()
    flash("❌ You have been logged out.", "success")
    return redirect(url_for("login"))



@app.route("/cancel", methods=["POST"])
def cancel_script():
    email = session.get("email")
    if not email:
        return "❌ Not logged in", 401

    job_id = get_job_id(email)

    if not job_id:
        return "⚠️ No running job to cancel", 200
    try:
        job = Job.fetch(job_id, connection=redis_conn)
    except NoSuchJobError:
        remove_job_id(email)
        return "⚠️ Job already completed or expired.", 200

    if job.is_finished:
        remove_job_id(email)
        return "⚠️ Job already completed. Nothing to cancel.", 200

    if job.is_canceled:
        remove_job_id(email)
        return "⚠️ Job was already canceled.", 200

    # ✅ Set manual cancel flag for U1–U4 modes
    job.meta["cancel_requested"] = True
    job.save_meta()

    # ✅ Native RQ cancel (for MidjourneyAll)
    job.cancel()

    remove_job_id(email)

    # ✅ Optional: File cleanup logic
    prompts_path = get_user_prompts_path(email)
    image_dir = get_user_images_dir(email)
    failed_path = get_user_failed_prompts_path(email)

    if os.path.exists(prompts_path):
        os.remove(prompts_path)
    delete_file(f"Users/{email}/prompts.xlsx")

    if os.path.exists(image_dir):
        for f in os.listdir(image_dir):
            fpath = os.path.join(image_dir, f)
            if os.path.isfile(fpath):
                os.remove(fpath)
    delete_file(f"Users/{email}/images.zip")

    images_excel_path = os.path.join(os.path.dirname(image_dir), "images.xlsx")
    if os.path.exists(images_excel_path):
        os.remove(images_excel_path)
    delete_file(f"Users/{email}/images.xlsx")

    if os.path.exists(failed_path):
        os.remove(failed_path)
    delete_file(f"Users/{email}/failed_prompts.json")

    return "Job canceled and all files cleaned up.", 200


if __name__ == "__main__":
    os.makedirs("Users", exist_ok=True)
    app.run(debug=True)


