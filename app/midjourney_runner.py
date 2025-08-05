import os
import json
import time
import uuid
import difflib
import zipfile
from io import BytesIO
from urllib.parse import urlparse

import pandas as pd
import requests
from redis import Redis
from rq import get_current_job

from .cancel_job_error import CancelJobError
from .tigris_utils import download_file_obj, upload_file_path
from .user_utils import (
    get_user_failed_prompts_path,
    get_user_log_key,
    get_user_images_dir,
)


def update_prompts_today(email, key, prompts_this_job):
    """
    Call the Apps Script endpoint to update PromptsToday for the user.
    """
    import os, requests
    endpoint = os.getenv("LICENSE_VALIDATION_URL")
    payload = {
        "email": email,
        "key": key,
        "promptsThisJob": prompts_this_job
    }
    try:
        r = requests.post(endpoint, json=payload, timeout=10)
        r.raise_for_status()
        data = r.json()
        print("✅ PromptsToday updated:", data, flush=True)
        return data
    except Exception as e:
        print("⚠️ Failed to update PromptsToday:", e, flush=True)
        return None


class MidjourneyRunner:
    """Context object for running a single Midjourney job.

    Stores all state that was previously kept in module level variables.
    Each instance is used for exactly one job run so no state leaks between
    jobs.
    """

    def __init__(self, button_label: str):
        self.button_label = button_label
        self.redis_conn = Redis.from_url(
            os.getenv("REDIS_URL", "redis://redis:6379/0")
        )

        # These are populated during ``run``
        self.LOG_KEY = ""
        self.HEADERS = {}
        self.OUTPUT_DIR = ""
        self.FAILED_PROMPTS_PATH = ""

        self.CHANNEL_ID = ""
        self.GUILD_ID = ""
        self.MIDJOURNEY_APP_ID = ""
        self.MIDJOURNEY_COMMAND_ID = ""
        self.COMMAND_VERSION = ""

    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------
    def log(self, *args):
        msg = " ".join(str(a) for a in args)
        print(msg, flush=True)
        if self.LOG_KEY:
            try:
                self.redis_conn.rpush(self.LOG_KEY, msg)
            except Exception as e:  # pragma: no cover - defensive
                print(f"❌ Failed to write log: {e}", flush=True)

    def check_cancel(self):
        job = get_current_job()
        if job and (job.is_canceled or job.meta.get("cancel_requested")):
            print("❌ Job was canceled – exiting early", flush=True)
            raise CancelJobError("Job canceled")

    def get_user_id(self):
        res = requests.get(
            "https://discord.com/api/v9/users/@me", headers=self.HEADERS
        )
        return res.json().get("id") if res.status_code == 200 else None

    def get_messages(self, limit: int = 100):
        url = (
            f"https://discord.com/api/v9/channels/{self.CHANNEL_ID}/messages?limit={limit}"
        )
        return requests.get(url, headers=self.HEADERS).json()

    def delete_message(self, msg_id):
        url = (
            f"https://discord.com/api/v9/channels/{self.CHANNEL_ID}/messages/{msg_id}"
        )
        requests.delete(url, headers=self.HEADERS)

    def clear_discord_channel(self):
        self.log("\n🧹 Clearing the memory from the previous run...")
        user_id = self.get_user_id()
        for _ in range(5):
            self.check_cancel()
            messages = self.get_messages()
            for msg in messages:
                author_id = msg.get("author", {}).get("id")
                if (
                    (author_id == self.MIDJOURNEY_APP_ID and "components" in msg)
                    or msg.get("message_reference")
                    or author_id == user_id
                ):
                    self.delete_message(msg["id"])
                    time.sleep(1)
            time.sleep(1.5)
        self.log("✅ Memory cleared and environment ready to process prompts.")

    def send_prompt(self, prompt):
        session_id = str(uuid.uuid4())
        payload = {
            "type": 2,
            "application_id": self.MIDJOURNEY_APP_ID,
            "guild_id": self.GUILD_ID,
            "channel_id": self.CHANNEL_ID,
            "session_id": session_id,
            "data": {
                "version": self.COMMAND_VERSION,
                "id": self.MIDJOURNEY_COMMAND_ID,
                "name": "imagine",
                "type": 1,
                "options": [{"type": 3, "name": "prompt", "value": prompt}],
            },
        }
        res = requests.post(
            "https://discord.com/api/v9/interactions", headers=self.HEADERS, json=payload
        )
        if res.status_code == 204:
            self.log(f"✅ Prompt sent: {prompt[:60]}...")
            return session_id
        else:
            self.log(
                f"❌ Failed to send prompt: {res.status_code} | {res.text}"
            )
            return None

    def trigger_button(self, custom_id, message_id):
        payload = {
            "type": 3,
            "guild_id": self.GUILD_ID,
            "channel_id": self.CHANNEL_ID,
            "message_id": message_id,
            "application_id": self.MIDJOURNEY_APP_ID,
            "session_id": "a" + str(int(time.time() * 1000)),
            "data": {"component_type": 2, "custom_id": custom_id},
        }
        requests.post(
            "https://discord.com/api/v9/interactions", headers=self.HEADERS, json=payload
        )

    def download_image(self, url, index):
        ext = os.path.splitext(urlparse(url).path)[1]
        res = requests.get(url)
        if res.status_code == 200:
            filepath = os.path.join(
                self.OUTPUT_DIR, f"{index}_{self.button_label}{ext}"
            )
            with open(filepath, "wb") as f:
                f.write(res.content)
            return filepath
        return None

    def process_batch(self, batch, start_index):
        queue = []
        for i, prompt in enumerate(batch):
            self.check_cancel()
            if i > 0:
                self.log("⏳ Waiting before sending the next prompt...")
                time.sleep(20)
            session_id = self.send_prompt(prompt)
            if session_id:
                queue.append(
                    {
                        "prompt": prompt,
                        "session_id": session_id,
                        "message_id": None,
                        "clicked": False,
                        "image_saved": False,
                        "cdn_url": None,
                        "prompt_index": start_index + i,
                    }
                )

        time.sleep(30)
        self.log(
            f"\n👁 Triggering {self.button_label} upscaled images is in progress..."
        )
        for _ in range(len(queue)):
            self.check_cancel()
            messages = self.get_messages()
            for msg in reversed(messages):
                if msg.get("author", {}).get("id") != self.MIDJOURNEY_APP_ID:
                    continue
                components = msg.get("components", [])
                if not components:
                    continue
                for button in components[0].get("components", []):
                    if button.get("label") == self.button_label:
                        for q in queue:
                            if q["clicked"]:
                                continue
                            sim = difflib.SequenceMatcher(
                                None,
                                msg.get("content", "").lower(),
                                q["prompt"].lower(),
                            ).ratio()
                            if sim > 0.7:
                                self.trigger_button(button["custom_id"], msg["id"])
                                q["message_id"] = msg["id"]
                                q["clicked"] = True
                                time.sleep(1)
                                break
            if all(q["clicked"] for q in queue):
                break
            time.sleep(5)

        time.sleep(10)
        self.log("\n✅ Upscaled images triggered, waiting for images to save...")
        for _ in range(len(queue)):
            self.check_cancel()
            messages = self.get_messages()
            for msg in reversed(messages):
                if msg.get("author", {}).get("id") != self.MIDJOURNEY_APP_ID:
                    continue
                for q in queue:
                    if (
                        q["clicked"]
                        and not q["image_saved"]
                        and msg.get("message_reference", {}).get("message_id")
                        == q["message_id"]
                    ):
                        attachments = msg.get("attachments", [])
                        if attachments:
                            url = attachments[0]["url"]
                            filepath = self.download_image(url, q["prompt_index"])
                            if filepath:
                                q["cdn_url"] = url
                                q["image_saved"] = True
                                self.log(
                                    f"💾 Saved {os.path.basename(filepath)} for prompt {q['prompt_index']}"
                                )

            saved_count = sum(1 for q in queue if q["image_saved"])
            remaining = len(queue) - saved_count
            self.log(
                f"⏳ Waiting... {saved_count}/{len(queue)} images saved so far. Remaining: {remaining}"
            )
            if all(q["image_saved"] for q in queue):
                break
            time.sleep(5)

        self.log(
            "\nGetting the saved images ready to download and logging any failed prompts..."
        )
        failed = [
            {
                "index": q["prompt_index"],
                "prompt": q["prompt"],
                "cdn_url": q.get("cdn_url"),
            }
            for q in queue
            if not q["image_saved"]
        ]

        if failed:
            existing = []
            if os.path.exists(self.FAILED_PROMPTS_PATH):
                try:
                    with open(self.FAILED_PROMPTS_PATH, "r") as f:
                        existing = json.load(f)
                except Exception:  # pragma: no cover - defensive
                    self.log("⚠️ Failed to load failed prompts")
            existing.extend(failed)

            failed_prompts_dir = os.path.dirname(self.FAILED_PROMPTS_PATH)
            os.makedirs(failed_prompts_dir, exist_ok=True)

            with open(self.FAILED_PROMPTS_PATH, "w") as f:
                json.dump(existing, f, indent=2)
            self.log(
                f"{len(failed)} failed prompts have been saved to the failed prompts file."
            )
        else:
            self.log("✅ All images saved successfully.")

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------
    # def run(self, user_email: str, prompts_file: str):
    def run(self, user_email: str, prompts_file: str, key: str):    
        self.OUTPUT_DIR = get_user_images_dir(user_email)
        self.FAILED_PROMPTS_PATH = get_user_failed_prompts_path(user_email)
        self.LOG_KEY = get_user_log_key(user_email)

        self.log(f"🟢 Midjourney{self.button_label} mode started running ...")
        self.check_cancel()

        settings_stream = download_file_obj(f"Users/{user_email}/settings.json")
        if not settings_stream:
            self.log("❌ Could not load settings file from storage. Exiting job.")
            return
        config = json.load(settings_stream)

        USER_TOKEN = config["USER TOKEN"]
        self.CHANNEL_ID = config["CHANNEL ID"]
        self.GUILD_ID = config["GUILD ID"]
        self.MIDJOURNEY_APP_ID = config["MIDJOURNEY APP ID"]
        self.MIDJOURNEY_COMMAND_ID = config["MIDJOURNEY COMMAND ID"]
        self.COMMAND_VERSION = config["COMMAND VERSION"]

        self.HEADERS = {
            "Authorization": USER_TOKEN,
            "Content-Type": "application/json",
        }

        response = requests.get(prompts_file)
        if response.status_code != 200:
            self.log(
                f"❌ Failed to download prompts file: {response.status_code}"
            )
            return
        prompts = pd.read_excel(BytesIO(response.content))["prompt"].dropna().tolist()

        os.makedirs(self.OUTPUT_DIR, exist_ok=True)

        start = time.time()
        for i in range(0, len(prompts), 10):
            batch = prompts[i : i + 10]
            self.log(
                f"\n🚀 Processing Batch {i//10 + 1} - {len(batch)} prompts..."
            )
            self.check_cancel()
            time.sleep(2)
            try:
                self.clear_discord_channel()
            except Exception as e:  # pragma: no cover - defensive
                self.log("⚠️ Clear failed:", e)
            time.sleep(1)
            self.log("\n↓↓↓ Starting to send prompts:")
            self.process_batch(batch, i + 1)
            try:
                self.clear_discord_channel()
            except Exception as e:  # pragma: no cover - defensive
                self.log("⚠️ Clear after batch failed:", e)

            job = get_current_job()
            if job:
                completed = min(i + len(batch), len(prompts))
                job.meta["completed_prompts"] = completed
                job.meta["total_prompts"] = len(prompts)
                job.save_meta()
                # self.log(
                #     f"🔄 Progress updated: {completed} / {len(prompts)} prompts completed"
                # )

        total = time.time() - start

        zip_path = os.path.join(os.path.dirname(self.OUTPUT_DIR), "images.zip")
        try:
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for fname in os.listdir(self.OUTPUT_DIR):
                    fpath = os.path.join(self.OUTPUT_DIR, fname)
                    if os.path.isfile(fpath):
                        zf.write(fpath, arcname=fname)
        except Exception as e:  # pragma: no cover - defensive
            self.log(f"⚠️ Failed to create ZIP: {e}")
            zip_path = None

        if zip_path and os.path.exists(zip_path):
            if upload_file_path(zip_path, f"Users/{user_email}/images.zip"):
                self.log(
                    "✅ Execution completed. Images saved in a ZIP folder under downloads."
                )
                try:
                    for fname in os.listdir(self.OUTPUT_DIR):
                        os.remove(os.path.join(self.OUTPUT_DIR, fname))
                    os.remove(zip_path)
                except Exception as e:  # pragma: no cover - defensive
                    self.log(f"⚠️ Cleanup error: {e}")
            else:
                self.log("❌ Failed to upload ZIP archive.")

        if os.path.exists(self.FAILED_PROMPTS_PATH):
            if upload_file_path(
                self.FAILED_PROMPTS_PATH, f"Users/{user_email}/failed_prompts.json"
            ):
                self.log(" Failed prompts Excel file has also been downloaded.")
                try:
                    os.remove(self.FAILED_PROMPTS_PATH)
                except Exception as e:  # pragma: no cover - defensive
                    self.log(f"⚠️ Failed to delete local failed_prompts.json: {e}")
            else:
                self.log("❌ Failed to upload failed_prompts.json.")

        self.log(
            f"\n⏱️ The run took {int(total // 60)} min {int(total % 60)} sec to complete."
        )
        update_prompts_today(user_email, key, len(prompts))


class MidjourneyRunnerAll(MidjourneyRunner):
    """Runner that triggers and saves all U1–U4 variants in one pass."""

    def __init__(self):
        super().__init__("All")

    def download_variant_image(self, url, index, variant):
        """Download an image for a specific upscaled variant."""
        ext = os.path.splitext(urlparse(url).path)[1]
        res = requests.get(url)
        if res.status_code == 200:
            filepath = os.path.join(self.OUTPUT_DIR, f"{index}_{variant}{ext}")
            with open(filepath, "wb") as f:
                f.write(res.content)
            return filepath
        return None

    def process_batch(self, batch, start_index):
        queue = []
        for i, prompt in enumerate(batch):
            self.check_cancel()
            if i > 0:
                self.log("⏳ Waiting before sending the next prompt...")
                time.sleep(20)
            session_id = self.send_prompt(prompt)
            if session_id:
                queue.append(
                    {
                        "prompt": prompt,
                        "session_id": session_id,
                        "message_ids": {},
                        "clicked": {"U1": False, "U2": False, "U3": False, "U4": False},
                        "saved": {"U1": False, "U2": False, "U3": False, "U4": False},
                        "cdn_urls": {},
                        "prompt_index": start_index + i,
                    }
                )

        time.sleep(30)
        self.log("\n👁 Triggering upscaled images is in progress...")
        for _ in range(len(queue)):
            self.check_cancel()
            messages = self.get_messages(100)
            for msg in reversed(messages):
                if msg.get("author", {}).get("id") != self.MIDJOURNEY_APP_ID:
                    continue
                comps = msg.get("components", [])
                if not comps:
                    continue
                content = msg.get("content", "").lower()
                for btn in comps[0].get("components", []):
                    label = btn.get("label", "")
                    if label not in {"U1", "U2", "U3", "U4"}:
                        continue
                    for q in queue:
                        if q["clicked"][label]:
                            continue
                        sim = difflib.SequenceMatcher(
                            None, content, q["prompt"].lower()
                        ).ratio()
                        if sim > 0.7:
                            self.trigger_button(btn["custom_id"], msg["id"])
                            q["message_ids"][label] = msg["id"]
                            q["clicked"][label] = True
                            time.sleep(1)
                            break
            if all(
                all(q["clicked"][u] for u in ("U1", "U2", "U3", "U4"))
                for q in queue
            ):
                break
            time.sleep(10)

        self.log("\n✅ Upscaled images triggered, waiting for images to save...")
        time.sleep(30)
        for _ in range(len(queue)):
            self.check_cancel()
            messages = self.get_messages()
            for msg in reversed(messages):
                if msg.get("author", {}).get("id") != self.MIDJOURNEY_APP_ID:
                    continue
                attachments = msg.get("attachments", [])
                if not attachments:
                    continue
                content = msg.get("content", "").lower()
                for q in queue:
                    if all(q["saved"].values()):
                        continue
                    if (
                        difflib.SequenceMatcher(
                            None, content, q["prompt"].lower()
                        ).ratio()
                        < 0.7
                    ):
                        continue
                    for i in range(1, 5):
                        if f"image #{i}" in content:
                            label = f"U{i}"
                            if q["saved"][label]:
                                continue
                            url = attachments[0]["url"]
                            filepath = self.download_variant_image(
                                url, q["prompt_index"], label
                            )
                            if filepath:
                                q["cdn_urls"][label] = url
                                q["saved"][label] = True
                                self.log(
                                    f"💾 Saved {os.path.basename(filepath)} for prompt {q['prompt_index']}"
                                )
                            break
            if all(
                all(q["saved"][u] for u in ("U1", "U2", "U3", "U4"))
                for q in queue
            ):
                break
            time.sleep(8)

        self.log(
            "\nGetting the saved images ready to download and logging any failed prompts..."
        )
        failed = [
            {
                "index": q["prompt_index"],
                "prompt": q["prompt"],
                "variant_u1": q["cdn_urls"].get("U1") if not q["saved"]["U1"] else None,
                "variant_u2": q["cdn_urls"].get("U2") if not q["saved"]["U2"] else None,
                "variant_u3": q["cdn_urls"].get("U3") if not q["saved"]["U3"] else None,
                "variant_u4": q["cdn_urls"].get("U4") if not q["saved"]["U4"] else None,
            }
            for q in queue
            if not all(q["saved"].values())
        ]

        if failed:
            existing = []
            if os.path.exists(self.FAILED_PROMPTS_PATH):
                try:
                    with open(self.FAILED_PROMPTS_PATH, "r") as f:
                        existing = json.load(f)
                except Exception:  # pragma: no cover - defensive
                    self.log("⚠️ Failed to load failed prompts")
            existing.extend(failed)

            failed_prompts_dir = os.path.dirname(self.FAILED_PROMPTS_PATH)
            os.makedirs(failed_prompts_dir, exist_ok=True)

            with open(self.FAILED_PROMPTS_PATH, "w") as f:
                json.dump(existing, f, indent=2)
            self.log(
                f"{len(failed)} failed prompts have been saved to the failed prompts file."
            )
        else:
            self.log("✅ All images saved successfully.")

