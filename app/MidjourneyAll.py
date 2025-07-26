# app/MidjourneyAll.py
def main(user_email: str | None = None, prompts_file: str | None = None):
    """
    RQ calls this.  Runs the full Mid‑journey batch flow for one user.

    Parameters
    ----------
    user_email : str   – required
    prompts_file : str – absolute path to the uploaded XLSX
    """
    # ── std‑lib & third‑party ────────────────────────────────────────────────
    import os, json, time, uuid, difflib
    from urllib.parse import urlparse

    import pandas as pd
    import requests

    # ── project helpers ────────────────────────────────────────────────────
    from .user_utils import (
        get_user_settings_path,
        get_user_failed_prompts_path,
        get_user_images_dir,
    )

    # ── guards ─────────────────────────────────────────────────────────────
    if not user_email or not prompts_file:
        raise ValueError("user_email and prompts_file are required")

    print("🟢 MidjourneyAll started …", flush=True)

    # ── constants / config ────────────────────────────────────────────────
    OUTPUT_DIR          = get_user_images_dir(user_email)
    FAILED_PROMPTS_PATH = get_user_failed_prompts_path(user_email)
    BATCH_SIZE          = 10

    prompts = pd.read_excel(prompts_file)["prompt"].dropna().tolist()

    with open(get_user_settings_path(user_email)) as f:
        cfg = json.load(f)

    USER_TOKEN            = cfg["USER TOKEN"]
    CHANNEL_ID            = cfg["CHANNEL ID"]
    GUILD_ID              = cfg["GUILD ID"]
    MIDJOURNEY_APP_ID     = cfg["MIDJOURNEY APP ID"]
    MIDJOURNEY_COMMAND_ID = cfg["MIDJOURNEY COMMAND ID"]
    COMMAND_VERSION       = cfg["COMMAND VERSION"]

    HEADERS = {"Authorization": USER_TOKEN, "Content-Type": "application/json"}
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # ── helper fns (unchanged) ─────────────────────────────────────────────
    def get_user_id():
        r = requests.get("https://discord.com/api/v9/users/@me", headers=HEADERS)
        if r.ok:
            return r.json()["id"]
        raise RuntimeError("Failed to get user ID")

    def get_messages(limit=100):
        url = f"https://discord.com/api/v9/channels/{CHANNEL_ID}/messages?limit={limit}"
        r = requests.get(url, headers=HEADERS)
        return r.json() if r.ok else []

    def delete_message(msg_id):
        url = f"https://discord.com/api/v9/channels/{CHANNEL_ID}/messages/{msg_id}"
        return requests.delete(url, headers=HEADERS).status_code in (200, 204)

    def clear_discord_channel():
        print("🧹 Clearing Discord channel …", flush=True)
        uid = get_user_id()
        for _ in range(5):
            msgs = get_messages()
            if not msgs:
                break
            for m in msgs:
                aid = m["author"]["id"]
                is_grid     = aid == MIDJOURNEY_APP_ID and "components" in m
                is_upscaled = aid == MIDJOURNEY_APP_ID and m.get("message_reference")
                is_user     = aid == uid
                if is_grid or is_upscaled or is_user:
                    delete_message(m["id"])
                    time.sleep(1)
            time.sleep(1.5)
        print("✅ Channel clear.", flush=True)

    def send_prompt(prompt):
        sid = str(uuid.uuid4())
        payload = {
            "type": 2, "application_id": MIDJOURNEY_APP_ID, "guild_id": GUILD_ID,
            "channel_id": CHANNEL_ID, "session_id": sid,
            "data": {
                "version": COMMAND_VERSION,
                "id": MIDJOURNEY_COMMAND_ID,
                "name": "imagine", "type": 1,
                "options": [{"type": 3, "name": "prompt", "value": prompt}],
            },
        }
        r = requests.post("https://discord.com/api/v9/interactions",
                          headers=HEADERS, json=payload)
        if r.status_code == 204:
            print(f"✅ Prompt sent: {prompt[:60]}…", flush=True)
            return sid
        print(f"❌ Prompt failed ({r.status_code})", flush=True)
        return None

    def trigger_button(custom_id, msg_id):
        payload = {
            "type": 3, "guild_id": GUILD_ID, "channel_id": CHANNEL_ID,
            "message_id": msg_id, "application_id": MIDJOURNEY_APP_ID,
            "session_id": "a" + str(int(time.time() * 1000)),
            "data": {"component_type": 2, "custom_id": custom_id},
        }
        requests.post("https://discord.com/api/v9/interactions",
                      headers=HEADERS, json=payload)

    def download_image(url, fname):
        ext = os.path.splitext(urlparse(url).path)[1]
        r = requests.get(url)
        if r.ok:
            path = os.path.join(OUTPUT_DIR, f"{fname}{ext}")
            with open(path, "wb") as f:
                f.write(r.content)
            return True
        return False

    # ── batch processing (unchanged) ──────────────────────────────────────
    def process_batch(batch, start_idx):
        # … (keep your full existing body here unchanged)
        ...

    # ── main loop (your original batch loop) ─────────────────────────────
    start = time.time()
    for i in range(0, len(prompts), BATCH_SIZE):
        batch = prompts[i : i + BATCH_SIZE]
        clear_discord_channel()
        print(f"🚀 Batch {i // BATCH_SIZE + 1} – {len(batch)} prompts", flush=True)
        time.sleep(2)
        process_batch(batch, i + 1)
        clear_discord_channel()

    mins, secs = divmod(int(time.time() - start), 60)
    print(f"⏱️ Run finished in {mins} min {secs} sec", flush=True)
