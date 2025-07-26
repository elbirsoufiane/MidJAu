import os
import json
import pandas as pd
import requests
import time
import uuid
import difflib
from urllib.parse import urlparse


from .user_utils import (
    get_user_settings_path,
    get_user_failed_prompts_path,
    get_user_log_path,
    get_user_images_dir,
    get_user_prompts_path
)

print("🟢 MidjourneyU4 mode started running ...", flush=True)

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
    print("\n 🧹 Clearing the memory from the previous run...", flush=True)
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
    print("✅ Memory cleared and environment ready to process prompts.", flush=True)

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
        # print(f"✅ Saved: {filepath}", flush=True)
        return filepath
    return None

def process_batch(batch, start_index):
    queue = []
    for i, prompt in enumerate(batch):
        if i > 0:
            print("⏳ Waiting before sending the next prompt...", flush=True) 
            time.sleep(20)  # ⏱️ Delay only after the first prompt
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

    time.sleep(30)
    print("\n👁 Triggering U4 upscaled images is in progress...", flush=True)

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
        time.sleep(5)


    time.sleep(10)
    print("\n✅ Upscaled images triggered, waiting for images to save...", flush=True)
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
                                print(f"💾 Saved {os.path.basename(download_image(url, q["prompt_index"]))} for prompt {q['prompt_index']}", flush=True)
        
        saved_count = sum(1 for q in queue if q["image_saved"])
        remaining = len(queue) - saved_count
        print(f"⏳ Waiting... {saved_count}/{len(queue)} images saved so far. Remaining: {remaining}", flush=True)                        
        if all(q["image_saved"] for q in queue):
            break
        time.sleep(5)

    print("\nGetting the saved images ready to download and logging any failed prompts...", flush=True)     

    failed = [
        {"index": q["prompt_index"], "prompt": q["prompt"], "cdn_url": q.get("cdn_url")}
        for q in queue if not q["image_saved"]
    ]


    if failed:
        existing = []
        if os.path.exists(FAILED_PROMPTS_PATH):
            try:
                with open(FAILED_PROMPTS_PATH, "r") as f:
                    content = f.read().strip()
                    if content:
                        existing = json.loads(content)
            except Exception as e:
                print(f"⚠️ Failed to load {FAILED_PROMPTS_PATH}: {e}", flush=True)

        existing.extend(failed)
        with open(FAILED_PROMPTS_PATH, "w") as f:
            json.dump(existing, f, indent=2)

        print(f"{len(failed)} failed prompts have been saved to the failed prompts file.", flush=True)
    else:
        print("✅ All images saved successfully.", flush=True)

def main():
    start = time.time()
    for i in range(0, len(prompts), 10):
        batch = prompts[i:i+10]
        print(f"\n🚀 Processing Batch {i//10 + 1} - {len(batch)} prompts...", flush=True)
        time.sleep(2)
        try:
            clear_discord_channel()
        except Exception as e:
            print(f"⚠️ Clear failed: {e}", flush=True)
        time.sleep(2)    
        print("\n ↓↓↓ Starting to send prompts:", flush=True)
        process_batch(batch, i + 1)
        try:
            clear_discord_channel()
        except Exception as e:
            print(f"⚠️ Clear after batch failed: {e}", flush=True)
    total = time.time() - start
    print(f"\n⏱️ The run took {int(total // 60)} min {int(total % 60)} sec to complete.", flush=True)

if __name__ == "__main__":
    main()
    print("✅ Execution completed. Images saved in a ZIP folder under Downloads. If there were failed prompts, an Excel file has also been downloaded.", flush=True)


