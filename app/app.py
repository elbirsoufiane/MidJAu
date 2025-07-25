from flask import Flask, render_template, request, redirect, session, url_for, flash
from user_utils import init_user_if_missing, get_user_failed_prompts_path, get_user_log_path, get_user_settings_path, get_user_prompts_path, get_user_images_dir
from user_utils import list_user_image_urls
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


# app = Flask(__name__)
app = Flask(__name__)
app.secret_key = "super_secret_key"  # Replace with a secure key in production

LICENSE_VALIDATION_URL = "https://script.google.com/macros/s/AKfycbx518ZptSKirJKIHRqEd-5PB_wFEY6RMo2WmbKmbwUSFJwzUzosP00tOVWVlYK5iXl1/exec"

running_processes = {}
process_lock = Lock()


@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        key = request.form["key"]
        remember = "remember" in request.form

        try:
            response = requests.get(LICENSE_VALIDATION_URL, params={"email": email, "key": key})
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

    # if request.method == "POST":
    #     mode = request.form["mode"]
    #     file = request.files["prompt_file"]

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

        if file:
            filename = file.filename
            file.save(get_user_prompts_path(email))

            env = os.environ.copy()
            env["PROMPTS_FILE"] = get_user_prompts_path(email)
            env["USER_EMAIL"] = email


            try:
                with open(get_user_settings_path(email)) as f:
                    settings = json.load(f)
                    for k, v in settings.items():
                        env_key = k.replace(" ", "_")
                        env[env_key] = v
            except Exception as e:
                return render_template("dashboard.html", filename=filename, output=f"⚠️ Failed to load settings: {e}")

            script_map = {
                "U1": "app/MidjourneyU1.py",
                "U2": "app/MidjourneyU2.py",
                "U3": "app/MidjourneyU3.py",
                "U4": "app/MidjourneyU4.py",
                "All": "app/MidjourneyAll.py"
            }

            script = script_map.get(mode)
            if script:
                # def run_script():
                #     log_path = get_user_log_path(email)
                #     with open(log_path, "w") as log_file:
                #         process = subprocess.Popen(
                #             ["python3", script],
                #             stdout=subprocess.PIPE,
                #             stderr=subprocess.STDOUT,
                #             text=True,
                #             env=env,
                #             bufsize=1
                #         )
                #         for line in process.stdout:
                #             log_file.write(line)
                #             log_file.flush()
                # Thread(target=run_script).start()


                def run_script():
                    log_path = get_user_log_path(email)
                    with open(log_path, "w") as log_file:
                        process = subprocess.Popen(
                            ["python3", script],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            env=env,
                            bufsize=1
                        )
                        with process_lock:
                            running_processes[email] = process

                        for line in process.stdout:
                            log_file.write(line)
                            log_file.flush()

                        with process_lock:
                            running_processes.pop(email, None)

                Thread(target=run_script).start()


                flash("🟢 Processing started. Follow the progress below.", "success")
            else:
                flash("❌ Invalid mode selected.", "error")
    return render_template("dashboard.html", filename=filename, selected_mode=mode, just_logged_in=session.pop("just_logged_in", False))


@app.route("/live_output")
def live_output():
    if "email" not in session:
        return "Unauthorized", 401

    log_path = get_user_log_path(session["email"])
    try:
        with open(log_path, "r") as f:
            return f.read()
    except FileNotFoundError:
        return "Waiting for output..."

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
        flash("✅ Settings saved!", "success")

    try:
        with open(settings_path) as f:
            current_settings = json.load(f)
    except:
        current_settings = {}

    return render_template("settings.html", settings=current_settings)

# @app.route('/download_zip')
# def download_zip():
#     if "email" not in session:
#         return "Unauthorized", 401

#     email = session["email"]
#     folder = f"uploads/{email}/images"

#     zip_stream = io.BytesIO()
#     with zipfile.ZipFile(zip_stream, 'w', zipfile.ZIP_DEFLATED) as zf:
#         for filename in os.listdir(folder):
#             path = os.path.join(folder, filename)
#             if os.path.isfile(path):
#                 zf.write(path, arcname=filename)
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
        print("❌ No email in session")
        return "Unauthorized", 401

    email = session["email"]
    folder = f"Users/{email}/images"

    print(f"📩 Session email during ZIP: {email}")
    print(f"📁 Zipping folder: {folder}")

    if not os.path.exists(folder):
        print("❌ Folder does not exist.")
        return "No images found", 404

    files = os.listdir(folder)
    print(f"🧾 Files found: {files}")

    zip_stream = io.BytesIO()
    with zipfile.ZipFile(zip_stream, 'w', zipfile.ZIP_DEFLATED) as zf:
        for filename in files:
            path = os.path.join(folder, filename)
            if os.path.isfile(path):
                print(f"📦 Adding to ZIP: {filename}")
                zf.write(path, arcname=filename)
            else:
                print(f"⚠️ Skipped non-file: {filename}")

    zip_stream.seek(0)

    print("✅ ZIP created and ready to download.")
    return send_file(
        zip_stream,
        mimetype='application/zip',
        as_attachment=True,
        download_name='generated_images.zip'
    )


from openpyxl import Workbook
from flask import make_response

@app.route("/download_failed_prompts_excel")
def download_failed_prompts_excel():
    if "email" not in session:
        return "Unauthorized", 401

    email = session["email"]
    failed_path = get_user_failed_prompts_path(email)

    if not os.path.exists(failed_path):
        return "No failed prompts file", 404

    with open(failed_path) as f:
        data = json.load(f)

    if not data:
        return "No failed prompts", 204  # No Content

    wb = Workbook()
    ws = wb.active
    ws.append(["prompt", "indexes"])

    for entry in data:
        prompt = entry.get("prompt", "")
        index = entry.get("index", "")
        ws.append([prompt, index])

    from io import BytesIO
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

    # Delete all images
    if os.path.exists(image_dir):
        for f in os.listdir(image_dir):
            fpath = os.path.join(image_dir, f)
            if os.path.isfile(fpath):
                os.remove(fpath)
    
    # Delete the failed_prompts.json file
    failed_path = get_user_failed_prompts_path(email)
    if os.path.exists(failed_path):
        os.remove(failed_path)

    return "✅ Cleaned up files", 200



@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))

# @app.route("/cancel_script", methods=["POST"])
# def cancel_script():
#     if "email" not in session:
#         return "Unauthorized", 401

#     email = session["email"]
#     with process_lock:
#         proc = running_processes.get(email)
#         if proc and proc.poll() is None:
#             proc.terminate()
#             running_processes.pop(email, None)
#             return "Script cancelled", 200

#     return "No running script", 400



@app.route("/cancel_script", methods=["POST"])
def cancel_script():
    if "email" not in session:
        return "Unauthorized", 401

    email = session["email"]
    with process_lock:
        proc = running_processes.get(email)
        if proc and proc.poll() is None:
            proc.terminate()
            running_processes.pop(email, None)

            # ✅ Perform cleanup (same as /cleanup_files)
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

            return "Operation cancelled. All files have been cleaned up.", 200

    return "No task in progress.", 400



if __name__ == "__main__":
    os.makedirs("Users", exist_ok=True)
    app.run(debug=True)