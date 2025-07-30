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
q = Queue(connection=redis_conn)

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
                # session["just_logged_in"] = True
                return redirect(url_for("dashboard"))
        except Exception as e:
            print(f"❌ License validation error: {e}")

        flash("❌ Invalid license. Please try again.", "error")

    return render_template("login.html")


@app.route("/dashboard", methods=["GET", "POST"])
def dashboard():
    if "email" not in session:
        return redirect(url_for("login"))

    filename = None
    email = session["email"]
    mode = None


    if request.method == "POST":
        # 🔐 License revalidation before proceeding
        email = session["email"]
        key = session.get("saved_key") or session.get("key")


        try:
            response = requests.get(LICENSE_VALIDATION_URL, params={"email": email, "key": key})
            data = response.json()
            if not data.get("success"):

                # Cancel running script if any
                with process_lock:
                    proc = running_processes.get(email)
                    if proc and proc.poll() is None:
                        proc.terminate()
                        running_processes.pop(email, None)

                        # Clean up files
                        prompts_path = get_user_prompts_path(email)
                        image_dir = get_user_images_dir(email)
                        failed_path = get_user_failed_prompts_path(email)

                        if os.path.exists(prompts_path):
                            os.remove(prompts_path)

                        if os.path.exists(image_dir):
                            for f in os.listdir(image_dir):
                                fpath = os.path.join(image_dir, f)
                                if os.path.isfile(fpath):
                                    os.remove(fpath)

                        if os.path.exists(failed_path):
                            os.remove(failed_path)
                session.clear()
                flash("❌ Invalid license. Please try again.", "error")
                session["license_failed"] = True
                return redirect(url_for("login"))
        except Exception as e:
            flash(f"⚠️ License check failed: {e}", "error")
            return redirect(url_for("dashboard"))

        # ⏩ Continue with script execution if license is still valid
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
                    return render_template("dashboard.html", filename=None, selected_mode=mode)
            except NoSuchJobError:
                pass  # Remove stale key below
            remove_job_id(email)

        if file:

            # Prepare in-memory file for Tigris
            excel_stream = BytesIO(file.read())
            key = f"Users/{email}/prompts.xlsx"

            success = upload_file_obj(excel_stream, key)

            if not success:
                flash("❌ Failed to upload file to cloud storage", "error")
                return render_template("dashboard.html", filename=None, selected_mode=mode)

            # 🚚 Generate a temporary download URL for the worker
            presigned_url = generate_presigned_url(key)
            if not presigned_url:
                flash("❌ Failed to generate presigned URL", "error")
                return render_template("dashboard.html", filename=None, selected_mode=mode)

            # File was uploaded — you can display the filename
            filename = file.filename

            # ✅ Clear old live output before starting the new job
            redis_conn.delete(get_user_log_key(email))

            env = os.environ.copy()
            env["PROMPTS_FILE"] = presigned_url
            env["USER_EMAIL"] = email


            try:
                settings_stream = download_file_obj(f"Users/{email}/settings.json")
                settings = json.load(settings_stream)
                # with open(get_user_settings_path(email)) as f:
                #     settings = json.load(f)
                for k, v in settings.items():
                    env_key = k.replace(" ", "_")
                    env[env_key] = v

            except Exception as e:
                return render_template("dashboard.html", filename=filename, output=f"⚠️ Failed to load settings: {e}")

            # script_map = {
            #     "U1": "app/MidjourneyU1.py",
            #     "U2": "app/MidjourneyU2.py",
            #     "U3": "app/MidjourneyU3.py",
            #     "U4": "app/MidjourneyU4.py",
            #     "All": "app/MidjourneyAll.py"
            # }

            # script = script_map.get(mode)
            # if script:
            #     if script.endswith("MidjourneyAll.py"):
            #         job = q.enqueue(midjourney_all, email, get_user_prompts_path(email),job_timeout=3600, result_ttl=0)
            #         running_jobs[email] = job.id
            #         flash(f"🟢 Job queued ({job.get_id()[:8]})", "success")
            #     else:
            #         flash("❌ Only 'All' mode is supported in background mode.", "error")

            if mode in ["U1", "U2", "U3", "U4", "All"]:
                job = q.enqueue(run_mode, mode, email, presigned_url, job_timeout=3600, result_ttl=0)
                set_job_id(email, job.id)
                flash(f"🟢 Job queued in mode: {mode}", "success")
            else:
                flash("❌ Invalid mode selected.", "error")

    return render_template("dashboard.html", filename=filename, selected_mode=mode, just_logged_in=session.pop("just_logged_in", False))


@app.route("/live_output")
def live_output():
    if "email" not in session:
        return "Unauthorized", 401

    log_key = get_user_log_key(session["email"])
    logs = redis_conn.lrange(log_key, 0, -1)
    if not logs:
        return "Waiting for output..."
    return "\n".join(m.decode() for m in logs)

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

    if request.method == "POST":
        new_settings = {
            "USER TOKEN": request.form.get("user_token"),
            "BOT TOKEN": request.form.get("bot_token"),
            "CHANNEL ID": request.form.get("channel_id"),
            "GUILD ID": request.form.get("guild_id"),
            "MIDJOURNEY APP ID": request.form.get("midjourney_app_id"),
            "MIDJOURNEY COMMAND ID": request.form.get("midjourney_command_id"),
            "COMMAND VERSION": request.form.get("command_version")
        }
        with open(settings_path, "w") as f:
            json.dump(new_settings, f, indent=4)
        # flash("✅ Settings saved!", "success")
        # Upload to Tigris
        settings_stream = BytesIO(json.dumps(new_settings).encode("utf-8"))
        upload_file_obj(settings_stream, f"Users/{email}/settings.json")

    try:
        with open(settings_path) as f:
            current_settings = json.load(f)
    except:
        current_settings = {}

    return render_template("settings.html", settings=current_settings)


# @app.route('/download_zip')
# def download_zip():
#     if "email" not in session:
#         print("❌ No email in session")
#         return "Unauthorized", 401

#     email = session["email"]
#     folder = f"Users/{email}/images"

#     print(f"📩 Session email during ZIP: {email}")
#     print(f"📁 Zipping folder: {folder}")

#     if not os.path.exists(folder):
#         print("❌ Folder does not exist.")
#         return "No images found", 404

#     files = os.listdir(folder)
#     print(f"🧾 Files found: {files}")

#     zip_stream = io.BytesIO()
#     with zipfile.ZipFile(zip_stream, 'w', zipfile.ZIP_DEFLATED) as zf:
#         for filename in files:
#             path = os.path.join(folder, filename)
#             if os.path.isfile(path):
#                 print(f"📦 Adding to ZIP: {filename}")
#                 zf.write(path, arcname=filename)
#             else:
#                 print(f"⚠️ Skipped non-file: {filename}")

#     zip_stream.seek(0)

#     return send_file(
#         zip_stream,
#         mimetype='application/zip',
#         as_attachment=True,
#         download_name='generated_images.zip'
#     )


@app.route('/download_zip')
def download_zip():
    if "email" not in session:
        return "Unauthorized", 401

    email = session["email"]
    zip_key = f"Users/{email}/images.zip"

    try:
        zip_stream = download_file_obj(zip_key)
        zip_stream.seek(0)
        return send_file(
            zip_stream,
            mimetype='application/zip',
            as_attachment=True,
            download_name='generated_images.zip'
        )
    except Exception as e:
        print(f"❌ Failed to fetch ZIP from Tigris: {e}")
        return "ZIP file not available", 404


from openpyxl import Workbook
from flask import make_response

# @app.route("/download_failed_prompts_excel")
# def download_failed_prompts_excel():
#     if "email" not in session:
#         return "Unauthorized", 401

#     email = session["email"]
#     failed_path = get_user_failed_prompts_path(email)

#     if not os.path.exists(failed_path):
#         return "No failed prompts file", 404

#     with open(failed_path) as f:
#         data = json.load(f)

#     if not data:
#         return "No failed prompts", 204  # No Content

#     wb = Workbook()
#     ws = wb.active
#     ws.append(["prompt", "indexes"])

#     for entry in data:
#         prompt = entry.get("prompt", "")
#         index = entry.get("index", "")
#         ws.append([prompt, index])

#     from io import BytesIO
#     excel_stream = BytesIO()
#     wb.save(excel_stream)
#     excel_stream.seek(0)

#     return send_file(
#         excel_stream,
#         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
#         as_attachment=True,
#         download_name="failed_prompts.xlsx"
#     )



@app.route("/download_failed_prompts_excel")
def download_failed_prompts_excel():
    if "email" not in session:
        return "Unauthorized", 401

    email = session["email"]
    json_key = f"Users/{email}/failed_prompts.json"

    try:
        json_stream = download_file_obj(json_key)
        data = json.load(json_stream)
    except Exception as e:
        print(f"❌ Failed to fetch or parse failed prompts JSON: {e}")
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
    
    # Delete the failed_prompts.json file
    failed_path = get_user_failed_prompts_path(email)
    if os.path.exists(failed_path):
        os.remove(failed_path)
    delete_file(f"Users/{email}/failed_prompts.json")

    return "✅ Cleaned up files", 200



@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


# @app.route("/cancel", methods=["POST"])
# def cancel_script():
#     email = session.get("email")
#     job_id = running_jobs.get(email)

#     if job_id:
#         try:
#             job = Job.fetch(job_id, connection=redis_conn)
#             job.cancel()

#             # ✅ File cleanup logic (restore from v2)
#             prompts_path = get_user_prompts_path(email)
#             image_dir = get_user_images_dir(email)
#             failed_path = get_user_failed_prompts_path(email)

#             if os.path.exists(prompts_path):
#                 os.remove(prompts_path)

#             if os.path.exists(image_dir):
#                 for f in os.listdir(image_dir):
#                     fpath = os.path.join(image_dir, f)
#                     if os.path.isfile(fpath):
#                         os.remove(fpath)

#             if os.path.exists(failed_path):
#                 os.remove(failed_path)

#             return "Job canceled and all files cleaned up.", 200

#         except Exception as e:
#             return f"Error cancelling: {e}", 500

#     return "No running job", 400


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
        return "⚠️ Job already completed or expired from Redis.", 200

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

    if os.path.exists(failed_path):
        os.remove(failed_path)
    delete_file(f"Users/{email}/failed_prompts.json")

    return "Job canceled and all files cleaned up.", 200


if __name__ == "__main__":
    os.makedirs("Users", exist_ok=True)
    app.run(debug=True)