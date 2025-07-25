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

print("🟢 MidjourneyAll mode started running ...", flush=True)

# === ENVIRONMENT SETUP ===
USER_EMAIL = os.environ.get("USER_EMAIL")
PROMPTS_FILE = os.environ.get("PROMPTS_FILE")

if not USER_EMAIL or not PROMPTS_FILE:
    raise Exception("❌ USER_EMAIL or PROMPTS_FILE not set in environment variables.")

OUTPUT_DIR = get_user_images_dir(USER_EMAIL)
FAILED_PROMPTS_PATH = get_user_failed_prompts_path(USER_EMAIL)
BATCH_SIZE = 10

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
    print("\n🧹 Clearing the memory from the previous run...", flush=True)
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

def download_image(url, filename):
    ext = os.path.splitext(urlparse(url).path)[1]
    res = requests.get(url)
    if res.status_code == 200:
        path = os.path.join(OUTPUT_DIR, f"{filename}{ext}")
        with open(path, "wb") as f:
            f.write(res.content)
        # print(f"✅ Saved: {path}", flush=True)
        return True
    return False

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
                "message_ids": {},
                "clicked": {"u1": False, "u2": False, "u3": False, "u4": False},
                "saved": {"u1": False, "u2": False, "u3": False, "u4": False},
                "cdn_urls": {},
                "prompt_index": start_index + i
            })

    time.sleep(30)
    print("\n👁 Triggering U1 upscaled images is in progress...", flush=True)

    for attempt in range(len(queue)):
        messages = get_messages(100)
        for msg in reversed(messages):
            if msg.get("author", {}).get("id") != MIDJOURNEY_APP_ID:
                continue
            components = msg.get("components", [])
            if not components:
                continue
            content = msg.get("content", "").lower()
            for button in components[0].get("components", []):
                label = button.get("label", "")
                if label in ["U1", "U2", "U3", "U4"]:
                    ukey = label.lower()
                    for q in queue:
                        if q["clicked"][ukey]:
                            continue
                        sim = difflib.SequenceMatcher(None, content, q["prompt"].lower()).ratio()
                        if sim > 0.7:
                            trigger_button(button["custom_id"], msg["id"])
                            q["message_ids"][ukey] = msg["id"]
                            q["clicked"][ukey] = True
                            # print(f"✅ Matched and clicked {label} for {q['prompt_index']}", flush=True)
                            time.sleep(1)
                            break
        if all(all(q["clicked"][u] for u in ["u1", "u2", "u3", "u4"]) for q in queue):
            break
        time.sleep(10)
    print("\n✅ Upscaled images triggered, waiting for images to save...", flush=True)
    time.sleep(30)

    for _ in range(len(queue)):
        messages = get_messages()
        for msg in reversed(messages):
            if msg.get("author", {}).get("id") != MIDJOURNEY_APP_ID:
                continue
            attachments = msg.get("attachments", [])
            if not attachments:
                continue
            content = msg.get("content", "").lower()
            for q in queue:
                if all(q["saved"].values()):
                    continue
                sim = difflib.SequenceMatcher(None, content, q["prompt"].lower()).ratio()
                if sim < 0.7:
                    continue
                for i in range(1, 5):
                    if f"image #{i}" in content:
                        ukey = f"u{i}"
                        if q["saved"][ukey]:
                            continue
                        url = attachments[0]["url"]
                        filename = f"{q['prompt_index']}_{ukey.upper()}"
                        if download_image(url, filename):
                            q["cdn_urls"][ukey] = url
                            q["saved"][ukey] = True
                            print(f"💾 Saved {filename} for prompt {q['prompt_index']}", flush=True)
                        break
        if all(all(q["saved"][u] for u in ["u1", "u2", "u3", "u4"]) for q in queue):
            break
        time.sleep(8)

    print("\nGetting the saved images ready to download and logging any failed prompts...", flush=True)
    failed = []
    for q in queue:
        if not all(q["saved"].values()):
            failed.append({
                "index": q["prompt_index"],
                "prompt": q["prompt"],
                "variant_u1": q["cdn_urls"].get("u1") if not q["saved"]["u1"] else None,
                "variant_u2": q["cdn_urls"].get("u2") if not q["saved"]["u2"] else None,
                "variant_u3": q["cdn_urls"].get("u3") if not q["saved"]["u3"] else None,
                "variant_u4": q["cdn_urls"].get("u4") if not q["saved"]["u4"] else None
            })


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

        print(f" {len(failed)} failed prompts have been saved to the failed prompts file.", flush=True)
    else:
        print("✅ All images saved successfully.", flush=True)


def main():
    start = time.time()  # 🕒 Start the timer
    for i in range(0, len(prompts), BATCH_SIZE):
        batch = prompts[i:i + BATCH_SIZE]
        clear_discord_channel()
        print(f"\n🚀 Processing batch {i // BATCH_SIZE + 1} ({len(batch)} prompts)...", flush=True)
        time.sleep(2)
        print("\n↓↓↓ Starting to send prompts:", flush=True)
        process_batch(batch, i + 1)
        clear_discord_channel()
    total = time.time() - start  # 🕒 End time after all batches
    print(f"\n⏱️ The run took {int(total // 60)} min {int(total % 60)} sec to complete.", flush=True)

if __name__ == "__main__":
    main()
    print("✅ Execution completed. Images saved in a ZIP file under Downloads. If there were failed prompts, an Excel file has also been downloaded.", flush=True)
