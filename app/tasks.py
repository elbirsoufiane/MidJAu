print("✅ tasks.py loaded successfully!")

from rq import get_current_job
from app.MidjourneyAll import main as midjourney_all_main   # or refactor code here

def midjourney_all(user_email, prompts_file):
    # optional: job = get_current_job()
    return midjourney_all_main(user_email, prompts_file)
