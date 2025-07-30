# print("✅ tasks.py loaded successfully!")

# from rq import get_current_job
# from app.MidjourneyAll import main as midjourney_all_main   # or refactor code here

# def midjourney_all(user_email, prompts_file):
#     # optional: job = get_current_job()
#     return midjourney_all_main(user_email, prompts_file)


# app/tasks.py

print("✅ tasks.py loaded successfully!")

from app.MidjourneyU1 import main as run_u1
from app.MidjourneyU2 import main as run_u2
from app.MidjourneyU3 import main as run_u3
from app.MidjourneyU4 import main as run_u4
from app.MidjourneyAll import main as run_all

# Optional: for debugging or status tracking later
from rq import get_current_job

mode_map = {
    "U1": run_u1,
    "U2": run_u2,
    "U3": run_u3,
    "U4": run_u4,
    "All": run_all
}

def run_mode(mode, user_email, prompts_file):
    print(f"🚀 Running mode: {mode} for user: {user_email}")
    if mode not in mode_map:
        raise ValueError(f"❌ Invalid mode: {mode}")
    return mode_map[mode](user_email, prompts_file)

