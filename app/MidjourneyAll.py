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
    from io import BytesIO

    import pandas as pd
    import requests
    from rq import get_current_job
    from rq.exceptions import CancelJobError

    # def check_cancel():
    #     job = get_current_job()
    #     if job and job.is_canceled:
    #         print("❌ Job was canceled – exiting early", flush=True)
    #         exit(0)  # Or return if inside a loop

    def check_cancel():
        job = get_current_job()
        if job:
            if job.is_canceled or job.meta.get("cancel_requested"):
                print("❌ Job was canceled – exiting early", flush=True)
                raise CancelJobError("Job canceled")

    # ── project helpers ────────────────────────────────────────────────────
    from .user_utils import (
        get_user_settings_path,
        get_user_failed_prompts_path,
        get_user_images_dir,
        get_user_log_key,
    )

    from .tigris_utils import download_file_obj, upload_file_path
    import zipfile

    from redis import Redis
    redis_conn = Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))
    LOG_KEY = get_user_log_key(user_email)
    redis_conn.delete(LOG_KEY)


    def log(msg: str):
        print(msg, flush=True)
        try:
            redis_conn.rpush(LOG_KEY, msg)
        except Exception as e:
            print(f"❌ Failed to write log: {e}", flush=True)

    # ── guards ─────────────────────────────────────────────────────────────
    if not user_email or not prompts_file:
        raise ValueError("user_email and prompts_file are required")

    # print("🟢 MidjourneyAll started …", flush=True)
    log("🟢 MidjourneyAll mode started running ...")
    check_cancel()

    # ── constants / config ────────────────────────────────────────────────
    OUTPUT_DIR          = get_user_images_dir(user_email)
    FAILED_PROMPTS_PATH = get_user_failed_prompts_path(user_email)
    BATCH_SIZE          = 10

    # prompts = pd.read_excel(prompts_file)["prompt"].dropna().tolist()

    response = requests.get(prompts_file)
    if response.status_code != 200:
        raise RuntimeError(f"Failed to download prompts file: {response.status_code}")

    prompts = pd.read_excel(BytesIO(response.content))["prompt"].dropna().tolist()


    # with open(get_user_settings_path(user_email)) as f:
    #     cfg = json.load(f)

    settings_stream = download_file_obj(f"Users/{user_email}/settings.json")
    if not settings_stream:
        log("❌ Could not load settings file from storage. Exiting job.")
        return
    cfg = json.load(settings_stream)

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
        # print("🧹 Clearing Discord channel …", flush=True)
        log("\n🧹 Clearing the memory from the previous run...")
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
        # print("✅ Channel clear.", flush=True)
        log("✅ Memory cleared and environment ready to process prompts.")
        check_cancel()

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
            # print(f"✅ Prompt sent: {prompt[:60]}…", flush=True)
            log(f"✅ Prompt sent: {prompt[:60]}...")
            return sid
        else:
            # print(f"❌ Prompt failed ({r.status_code})", flush=True)
            log(f"❌ Failed to send prompt: {r.status_code} | {r.text}")
            # log(f"❌ Prompt failed ({r.status_code})")
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
        queue = []
        # 1) send prompts
        for i, prompt in enumerate(batch):
            check_cancel()
            if i:  # delay between prompts except first
                # print("⏳ Waiting before next prompt …", flush=True)
                log("⏳ Waiting before sending the next prompt...")
                time.sleep(20)
            check_cancel()    
            sid = send_prompt(prompt)
            if sid:
                queue.append({
                    "prompt": prompt,
                    "session_id": sid,
                    "message_ids": {},
                    "clicked": {"u1": False, "u2": False, "u3": False, "u4": False},
                    "saved":   {"u1": False, "u2": False, "u3": False, "u4": False},
                    "cdn_urls": {},
                    "prompt_index": start_idx + i,
                })

        # 2) click U‑buttons
        time.sleep(30)
        # print("\n👁 Triggering U‑buttons …", flush=True)
        log("\n👁 Triggering U1 upscaled images is in progress...")

        for _ in range(len(queue)):
            check_cancel()
            msgs = get_messages(100)
            for msg in reversed(msgs):
                if msg.get("author", {}).get("id") != MIDJOURNEY_APP_ID:
                    continue
                comps = msg.get("components", [])
                if not comps:
                    continue
                content = msg.get("content", "").lower()

                for btn in comps[0].get("components", []):
                    label = btn.get("label", "")
                    if label not in {"U1", "U2", "U3", "U4"}:
                        continue
                    ukey = label.lower()
                    for q in queue:
                        if q["clicked"][ukey]:
                            continue
                        sim = difflib.SequenceMatcher(
                            None, content, q["prompt"].lower()).ratio()
                        if sim > 0.7:
                            check_cancel()
                            trigger_button(btn["custom_id"], msg["id"])
                            q["message_ids"][ukey] = msg["id"]
                            q["clicked"][ukey] = True
                            time.sleep(1)
                            break
            if all(all(q["clicked"][u] for u in ("u1", "u2", "u3", "u4"))
                   for q in queue):
                break
            time.sleep(10)

        # print("\n✅ Upscales triggered – waiting for images …", flush=True)
        log("\n✅ Upscaled images triggered, waiting for images to save...")
        time.sleep(30)

        # 3) download images
        for _ in range(len(queue)):
            msgs = get_messages()
            for msg in reversed(msgs):
                if msg.get("author", {}).get("id") != MIDJOURNEY_APP_ID:
                    continue
                atts = msg.get("attachments", [])
                if not atts:
                    continue
                content = msg.get("content", "").lower()

                for q in queue:
                    if all(q["saved"].values()):
                        continue
                    if difflib.SequenceMatcher(
                            None, content, q["prompt"].lower()).ratio() < 0.7:
                        continue
                    for i in range(1, 5):
                        if f"image #{i}" in content:
                            ukey = f"u{i}"
                            if q["saved"][ukey]:
                                continue
                            url = atts[0]["url"]
                            fname = f"{q['prompt_index']}_{ukey.upper()}"
                            check_cancel()
                            if download_image(url, fname):
                                q["cdn_urls"][ukey] = url
                                q["saved"][ukey] = True
                                # print(f"💾 Saved {fname}", flush=True)
                                # log(f"💾 Saved {fname}")
                                log(f"💾 Saved {fname} for prompt {q['prompt_index']}")
                            break
            if all(all(q["saved"][u] for u in ("u1", "u2", "u3", "u4"))
                   for q in queue):
                break
            time.sleep(8)

        log("\nGetting the saved images ready to download and logging any failed prompts...")

        # 4) log failures
        failed = [
            {
                "index": q["prompt_index"],
                "prompt": q["prompt"],
                "variant_u1": q["cdn_urls"].get("u1") if not q["saved"]["u1"] else None,
                "variant_u2": q["cdn_urls"].get("u2") if not q["saved"]["u2"] else None,
                "variant_u3": q["cdn_urls"].get("u3") if not q["saved"]["u3"] else None,
                "variant_u4": q["cdn_urls"].get("u4") if not q["saved"]["u4"] else None,
            }
            for q in queue if not all(q["saved"].values())
        ]

        check_cancel()
        if failed:
            try:
                existing = []
                if os.path.exists(FAILED_PROMPTS_PATH):
                    try:
                        with open(FAILED_PROMPTS_PATH) as f:
                            content = f.read().strip()
                            if content:
                                existing = json.loads(content)
                    except Exception as e:
                        # log(f"⚠️ Failed to read existing failed prompts: {e}")
                        log(f"⚠️ Failed to load {FAILED_PROMPTS_PATH}: {e}")

                existing.extend(failed)

                with open(FAILED_PROMPTS_PATH, "w") as f:
                    json.dump(existing, f, indent=2)

                if not existing:
                    os.remove(FAILED_PROMPTS_PATH)  # optional cleanup

                log(f" {len(failed)} failed prompts have been saved to the failed prompts file.")
            except Exception as e:
                log(f"⚠️  Could not write failures: {e}")
        else:
            log("✅ All images saved successfully.")
    

    # ─────────────────────────── main loop ────────────────────────────
    start = time.time()
    for i in range(0, len(prompts), BATCH_SIZE):
        batch = prompts[i : i + BATCH_SIZE]
        clear_discord_channel()
        # print(f"🚀 Batch {i // BATCH_SIZE + 1} – {len(batch)} prompts", flush=True)
        # log(f"🚀 Batch {i // BATCH_SIZE + 1} – {len(batch)} prompts")
        log(f"\n🚀 Processing batch {i // BATCH_SIZE + 1} ({len(batch)} prompts)...")
        time.sleep(2)
        log("\n↓↓↓ Starting to send prompts:")
        process_batch(batch, i + 1)
        clear_discord_channel()

    mins, secs = divmod(int(time.time() - start), 60)
    # print(f"⏱️ Run finished in {mins} min {secs} sec", flush=True)
    # log(f"⏱️ Run finished in {mins} min {secs} sec")
    log(f"\n⏱️ The run took {mins} min {secs} sec to complete.")
    log("✅ Execution completed. Images saved in a ZIP file under Downloads. If there were failed prompts, an Excel file has also been downloaded.")

    # ── Zip images and upload to Tigris ───────────────────────────────
    zip_path = os.path.join(os.path.dirname(OUTPUT_DIR), "images.zip")
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for fname in os.listdir(OUTPUT_DIR):
                fpath = os.path.join(OUTPUT_DIR, fname)
                if os.path.isfile(fpath):
                    zf.write(fpath, arcname=fname)
    except Exception as e:
        log(f"⚠️ Failed to create ZIP: {e}")
        zip_path = None

    if zip_path and os.path.exists(zip_path):
        if upload_file_path(zip_path, f"Users/{user_email}/images.zip"):
            log("✅ Uploaded ZIP archive to cloud storage.")
            try:
                for fname in os.listdir(OUTPUT_DIR):
                    os.remove(os.path.join(OUTPUT_DIR, fname))
                os.remove(zip_path)
            except Exception as e:
                log(f"⚠️ Cleanup error: {e}")
        else:
            log("❌ Failed to upload ZIP archive.")

    # ── Upload failed prompts ───────────────────────────────────────
    if os.path.exists(FAILED_PROMPTS_PATH):
        if upload_file_path(FAILED_PROMPTS_PATH, f"Users/{user_email}/failed_prompts.json"):
            log("✅ Uploaded failed_prompts.json to cloud storage.")
            try:
                os.remove(FAILED_PROMPTS_PATH)
            except Exception as e:
                log(f"⚠️ Failed to delete local failed_prompts.json: {e}")
        else:
            log("❌ Failed to upload failed_prompts.json.")

