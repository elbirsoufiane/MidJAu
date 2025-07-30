import os
import json
import pandas as pd
import requests
import time
import uuid
import difflib
from urllib.parse import urlparse
from rq import get_current_job
from io import BytesIO
from app.tigris_utils import download_file_obj

from .user_utils import (
    get_user_settings_path,
    get_user_failed_prompts_path,
    get_user_log_path,
    get_user_images_dir,
)

# === Globals (set inside main) ===
HEADERS = {}
OUTPUT_DIR = ""
FAILED_PROMPTS_PATH = ""
log_path = ""

# === Utilities ===
def log(*args):
    msg = " ".join(str(a) for a in args)
    print(msg, flush=True)
    if log_path:
        with open(log_path, "a") as f:
            f.write(msg + "\n")

# def check_cancel():
#     job = get_current_job()
#     if job and job.meta.get("cancel_requested"):
#         log("❌ Job canceled by user. Exiting...")
#         raise Exception("Job canceled")

def check_cancel():
    job = get_current_job()
    if job:
        if job.is_canceled or job.meta.get("cancel_requested"):
            print("❌ Job was canceled – exiting early", flush=True)
            exit(0)  # Or raise Exception("Canceled")    

def get_user_id():
    res = requests.get("https://discord.com/api/v9/users/@me", headers=HEADERS)
    return res.json().get("id") if res.status_code == 200 else None

def get_messages(limit=100):
    url = f"https://discord.com/api/v9/channels/{CHANNEL_ID}/messages?limit={limit}"
    return requests.get(url, headers=HEADERS).json()

def delete_message(msg_id):
    url = f"https://discord.com/api/v9/channels/{CHANNEL_ID}/messages/{msg_id}"
    requests.delete(url, headers=HEADERS)

def clear_discord_channel():
    log("\n🧹 Clearing the memory from the previous run...")
    user_id = get_user_id()
    for _ in range(5):
        check_cancel()
        messages = get_messages()
        for msg in messages:
            author_id = msg.get("author", {}).get("id")
            if (author_id == MIDJOURNEY_APP_ID and "components" in msg) or msg.get("message_reference") or author_id == user_id:
                delete_message(msg["id"])
                time.sleep(1)
        time.sleep(1.5)
    log("✅ Memory cleared and environment ready to process prompts.")

def send_prompt(prompt):
    session_id = str(uuid.uuid4())
    payload = {
        "type": 2,
        "application_id": MIDJOURNEY_APP_ID,
        "guild_id": GUILD_ID,
        "channel_id": CHANNEL_ID,
        "session_id": session_id,
        "data": {
            "version": COMMAND_VERSION,
            "id": MIDJOURNEY_COMMAND_ID,
            "name": "imagine",
            "type": 1,
            "options": [{"type": 3, "name": "prompt", "value": prompt}]
        }
    }
    res = requests.post("https://discord.com/api/v9/interactions", headers=HEADERS, json=payload)
    if res.status_code == 204:
        log(f"✅ Prompt sent: {prompt[:60]}...")
        return session_id
    else:
        log(f"❌ Failed to send prompt: {res.status_code} | {res.text}")
        return None

def trigger_button(custom_id, message_id):
    payload = {
        "type": 3,
        "guild_id": GUILD_ID,
        "channel_id": CHANNEL_ID,
        "message_id": message_id,
        "application_id": MIDJOURNEY_APP_ID,
        "session_id": "a" + str(int(time.time() * 1000)),
        "data": {"component_type": 2, "custom_id": custom_id}
    }
    requests.post("https://discord.com/api/v9/interactions", headers=HEADERS, json=payload)

def download_image(url, index):
    ext = os.path.splitext(urlparse(url).path)[1]
    res = requests.get(url)
    if res.status_code == 200:
        filepath = os.path.join(OUTPUT_DIR, f"{index}_U2{ext}")
        with open(filepath, "wb") as f:
            f.write(res.content)
        return filepath
    return None

def process_batch(batch, start_index):
    queue = []
    for i, prompt in enumerate(batch):
        check_cancel()
        if i > 0:
            log("⏳ Waiting before sending the next prompt...")
            time.sleep(20)
        session_id = send_prompt(prompt)
        if session_id:
            queue.append({
                "prompt": prompt,
                "session_id": session_id,
                "message_id": None,
                "u2_clicked": False,
                "image_saved": False,
                "cdn_url": None,
                "prompt_index": start_index + i
            })

    time.sleep(30)
    log("\n👁 Triggering U2 upscaled images is in progress...")
    for _ in range(len(queue)):
        check_cancel()
        messages = get_messages()
        for msg in reversed(messages):
            if msg.get("author", {}).get("id") != MIDJOURNEY_APP_ID:
                continue
            # for button in msg.get("components", [{}])[0].get("components", []):
            components = msg.get("components", [])
            if not components:
                continue
            for button in components[0].get("components", []):
                if button.get("label") == "U2":
                    for q in queue:
                        if q["u2_clicked"]:
                            continue
                        sim = difflib.SequenceMatcher(None, msg.get("content", "").lower(), q["prompt"].lower()).ratio()
                        if sim > 0.7:
                            trigger_button(button["custom_id"], msg["id"])
                            q["message_id"] = msg["id"]
                            q["u2_clicked"] = True
                            time.sleep(1)
                            break
        if all(q["u2_clicked"] for q in queue):
            break
        time.sleep(5)

    time.sleep(10)
    log("\n✅ Upscaled images triggered, waiting for images to save...")
    for _ in range(len(queue)):
        check_cancel()
        messages = get_messages()
        for msg in reversed(messages):
            if msg.get("author", {}).get("id") != MIDJOURNEY_APP_ID:
                continue
            for q in queue:
                if q["u2_clicked"] and not q["image_saved"] and msg.get("message_reference", {}).get("message_id") == q["message_id"]:
                    attachments = msg.get("attachments", [])
                    if attachments:
                        url = attachments[0]["url"]
                        filepath = download_image(url, q["prompt_index"])
                        if filepath:
                            q["cdn_url"] = url
                            q["image_saved"] = True
                            log(f"💾 Saved {os.path.basename(filepath)} for prompt {q['prompt_index']}")

        saved_count = sum(1 for q in queue if q["image_saved"])
        remaining = len(queue) - saved_count
        log(f"⏳ Waiting... {saved_count}/{len(queue)} images saved so far. Remaining: {remaining}")  
        if all(q["image_saved"] for q in queue):
            break
        time.sleep(5)

    log("\nGetting the saved images ready to download and logging any failed prompts...")
    failed = [
        {"index": q["prompt_index"], "prompt": q["prompt"], "cdn_url": q.get("cdn_url")}
        for q in queue if not q["image_saved"]
    ]

    if failed:
        existing = []
        if os.path.exists(FAILED_PROMPTS_PATH):
            try:
                with open(FAILED_PROMPTS_PATH, "r") as f:
                    existing = json.load(f)
            except Exception as e:
                log(f"⚠️ Failed to load {FAILED_PROMPTS_PATH}: {e}")
        existing.extend(failed)
        with open(FAILED_PROMPTS_PATH, "w") as f:
            json.dump(existing, f, indent=2)
        log(f"{len(failed)} failed prompts have been saved to the failed prompts file.")
    else:
        log("✅ All images saved successfully.")

def main(user_email: str, prompts_file: str):
    global HEADERS, OUTPUT_DIR, FAILED_PROMPTS_PATH, CHANNEL_ID, GUILD_ID, MIDJOURNEY_APP_ID, MIDJOURNEY_COMMAND_ID, COMMAND_VERSION, log_path

    OUTPUT_DIR = get_user_images_dir(user_email)
    FAILED_PROMPTS_PATH = get_user_failed_prompts_path(user_email)
    log_path = get_user_log_path(user_email)

    log("🟢 MidjourneyU2 mode started running ...")
    check_cancel()

    # with open(get_user_settings_path(user_email)) as f:
    #     config = json.load(f)

    settings_stream = download_file_obj(f"Users/{user_email}/settings.json")
    config = json.load(settings_stream)

    USER_TOKEN = config["USER TOKEN"]
    CHANNEL_ID = config["CHANNEL ID"]
    GUILD_ID = config["GUILD ID"]
    MIDJOURNEY_APP_ID = config["MIDJOURNEY APP ID"]
    MIDJOURNEY_COMMAND_ID = config["MIDJOURNEY COMMAND ID"]
    COMMAND_VERSION = config["COMMAND VERSION"]

    HEADERS = {
        "Authorization": USER_TOKEN,
        "Content-Type": "application/json"
    }

    # prompts = pd.read_excel(prompts_file)["prompt"].dropna().tolist()
    response = requests.get(prompts_file)
    prompts = pd.read_excel(BytesIO(response.content))["prompt"].dropna().tolist()

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    start = time.time()
    for i in range(0, len(prompts), 10):
        batch = prompts[i:i+10]
        log(f"\n🚀 Processing Batch {i//10 + 1} - {len(batch)} prompts...")
        check_cancel()
        time.sleep(2)
        try:
            clear_discord_channel()
        except Exception as e:
            log("⚠️ Clear failed:", e)
        time.sleep(2)
        log("\n↓↓↓ Starting to send prompts:")
        process_batch(batch, i + 1)
        try:
            clear_discord_channel()
        except Exception as e:
            log("⚠️ Clear after batch failed:", e)

    total = time.time() - start
    log(f"\n⏱️ The run took {int(total // 60)} min {int(total % 60)} sec to complete.")
    log("✅ Execution completed. Images saved in a ZIP folder under Downloads. If there were failed prompts, an Excel file has also been downloaded.")