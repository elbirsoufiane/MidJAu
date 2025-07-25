import os
import json
import pandas as pd
import requests
import time
import uuid
import difflib
from urllib.parse import urlparse

from user_utils import (
    get_user_settings_path,
    get_user_failed_prompts_path,
    get_user_log_path,
    get_user_images_dir,
    get_user_prompts_path
)

print("🟢 MidjourneyU4 script started...", flush=True)

# === ENVIRONMENT SETUP ===
USER_EMAIL = os.environ.get("USER_EMAIL")
PROMPTS_FILE = os.environ.get("PROMPTS_FILE")

if not USER_EMAIL or not PROMPTS_FILE:
    raise Exception("❌ USER_EMAIL or PROMPTS_FILE not set in environment variables.")

OUTPUT_DIR = get_user_images_dir(USER_EMAIL)
FAILED_PROMPTS_PATH = get_user_failed_prompts_path(USER_EMAIL)

prompts = pd.read_excel(PROMPTS_FILE)["prompt"].dropna().tolist()

with open(get_user_settings_path(USER_EMAIL)) as f:
    config = json.load(f)

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

os.makedirs(OUTPUT_DIR, exist_ok=True)

def get_user_id():
    res = requests.get("https://discord.com/api/v9/users/@me", headers=HEADERS)
    if res.status_code == 200:
        return res.json().get("id")
    raise Exception("❌ Failed to get user ID.")

def get_messages(limit=100):
    url = f"https://discord.com/api/v9/channels/{CHANNEL_ID}/messages?limit={limit}"
    res = requests.get(url, headers=HEADERS)
    return res.json() if res.status_code == 200 else []

def delete_message(msg_id):
    url = f"https://discord.com/api/v9/channels/{CHANNEL_ID}/messages/{msg_id}"
    res = requests.delete(url, headers=HEADERS)
    return res.status_code in [204, 200]

def clear_discord_channel():
    print("🧹 Clearing Discord channel...", flush=True)
    user_id = get_user_id()
    for _ in range(5):
        messages = get_messages()
        if not messages:
            break
        for msg in messages:
            author_id = msg.get("author", {}).get("id")
            is_grid = author_id == MIDJOURNEY_APP_ID and "components" in msg
            is_upscale = author_id == MIDJOURNEY_APP_ID and msg.get("message_reference")
            is_user = author_id == user_id
            if is_grid or is_upscale or is_user:
                delete_message(msg["id"])
                time.sleep(1)
        time.sleep(1.5)
    print("✅ Channel cleared.", flush=True)

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
        print(f"✅ Prompt sent: {prompt[:60]}...", flush=True)
        return session_id
    else:
        print(f"❌ Failed to send prompt: {res.status_code} | {res.text}", flush=True)
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

def get_latest_messages(limit=100):
    url = f"https://discord.com/api/v9/channels/{CHANNEL_ID}/messages?limit={limit}"
    res = requests.get(url, headers=HEADERS)
    return res.json() if res.status_code == 200 else []

def download_image(url, index):
    ext = os.path.splitext(urlparse(url).path)[1]
    res = requests.get(url)
    if res.status_code == 200:
        filepath = os.path.join(OUTPUT_DIR, f"{index}_U4{ext}")
        with open(filepath, "wb") as f:
            f.write(res.content)
        print(f"✅ Saved: {filepath}", flush=True)
        return filepath
    return None

def process_batch(batch, start_index):
    queue = []
    for i, prompt in enumerate(batch):
        session_id = send_prompt(prompt)
        if session_id:
            queue.append({
                "prompt": prompt,
                "session_id": session_id,
                "message_id": None,
                "u4_clicked": False,
                "image_saved": False,
                "cdn_url": None,
                "prompt_index": start_index + i
            })
        time.sleep(20)

    time.sleep(30)
    print("⏳ Waiting for U4 buttons...", flush=True)

    for attempt in range(len(queue)):
        messages = get_latest_messages(100)
        for msg in reversed(messages):
            if msg.get("author", {}).get("id") != MIDJOURNEY_APP_ID:
                continue
            components = msg.get("components", [])
            if not components:
                continue
            content = msg.get("content", "").lower()
            for button in components[0].get("components", []):
                if button.get("label") == "U4":
                    for q in queue:
                        if q["u4_clicked"]:
                            continue
                        sim = difflib.SequenceMatcher(None, content, q["prompt"].lower()).ratio()
                        if sim > 0.7:
                            trigger_button(button["custom_id"], msg["id"])
                            q["message_id"] = msg["id"]
                            q["u4_clicked"] = True
                            time.sleep(1)  # ⏳ Small delay after clicking
                            break
        if all(q["u4_clicked"] for q in queue):
            break
        time.sleep(10)
        
    print("⏳ Upscaled Images are ready for Download", flush=True)
    time.sleep(30)
    print("👁 Downloading the Uscaled Images...", flush=True)
    for _ in range(len(queue)):
        messages = get_latest_messages()
        for msg in reversed(messages):
            if msg.get("author", {}).get("id") != MIDJOURNEY_APP_ID:
                continue
            for q in queue:
                if q["u4_clicked"] and not q["image_saved"]:
                    ref_id = msg.get("message_reference", {}).get("message_id")
                    if ref_id == q["message_id"]:
                        attachments = msg.get("attachments", [])
                        if attachments:
                            url = attachments[0]["url"]
                            q["cdn_url"] = url
                            if download_image(url, q["prompt_index"]):
                                q["image_saved"] = True
        if all(q["image_saved"] for q in queue):
            break
        time.sleep(10)

    failed = [
        {"index": q["prompt_index"], "prompt": q["prompt"], "cdn_url": q.get("cdn_url")}
        for q in queue if not q["image_saved"]
    ]

    if failed:
        print("⚠️ Saving failed prompts...", flush=True)
        if os.path.exists(FAILED_PROMPTS_PATH):
            with open(FAILED_PROMPTS_PATH, "r") as f:
                existing = json.load(f)
        else:
            existing = []
        existing.extend(failed)
        with open(FAILED_PROMPTS_PATH, "w") as f:
            json.dump(existing, f, indent=2)
        print("💾 Saved to failed_prompts.json", flush=True)
    else:
        print("✅ All images saved.", flush=True)

def main():
    start = time.time()
    for i in range(0, len(prompts), 8):
        batch = prompts[i:i+8]
        print(f"\n🚀 Batch {i//8 + 1} - {len(batch)} prompts...", flush=True)
        try:
            clear_discord_channel()
        except Exception as e:
            print(f"⚠️ Clear failed: {e}", flush=True)
        process_batch(batch, i + 1)
        try:
            clear_discord_channel()
        except Exception as e:
            print(f"⚠️ Clear after batch failed: {e}", flush=True)
    total = time.time() - start
    print(f"\n⏱️ Time: {int(total//60)} min {int(total%60)} sec", flush=True)

if __name__ == "__main__":
    main()
    print("✅ Script completed.", flush=True)