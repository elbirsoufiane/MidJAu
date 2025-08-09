
print("✅ tasks.py loaded successfully!")

from app.MidjourneyU1 import main as run_u1
from app.MidjourneyU2 import main as run_u2
from app.MidjourneyU3 import main as run_u3
from app.MidjourneyU4 import main as run_u4
from app.MidjourneyAll import main as run_all

# Optional: for debugging or status tracking later
from rq import get_current_job
import os
import requests

mode_map = {
    "U1": run_u1,
    "U2": run_u2,
    "U3": run_u3,
    "U4": run_u4,
    "All": run_all
}

def run_mode(mode, user_email, prompts_file, key):
    print(f"🚀 Running mode: {mode} for user: {user_email}")
    if mode not in mode_map:
        raise ValueError(f"❌ Invalid mode: {mode}")
    return mode_map[mode](user_email, prompts_file, key)


def run_canva(rows, api_key, template_id):
    job = get_current_job()
    headers = {"Authorization": f"Bearer {api_key}"}
    results = []
    for idx, row in enumerate(rows):
        if job and (job.is_canceled or job.meta.get("cancel_requested")):
            break

        data = {"template_id": template_id}
        for k, v in row.items():
            if k not in {"image_path", "images"}:
                data[k] = v

        files = {}
        image_path = row.get("image_path")
        if image_path and os.path.exists(image_path):
            files["image"] = open(image_path, "rb")

        try:
            resp = requests.post(
                "https://api.canva.com/v1/designs",
                headers=headers,
                data=data,
                files=files or None,
                timeout=30,
            )
            resp.raise_for_status()
            results.append(resp.json())
        except Exception as e:
            results.append({"row": idx, "error": str(e)})
        finally:
            if files:
                files["image"].close()

        if job:
            job.meta["completed_prompts"] = idx + 1
            job.save_meta()

    return results
