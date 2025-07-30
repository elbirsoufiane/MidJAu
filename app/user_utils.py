import os
import json

Users_DIR = "Users"

def get_user_dir(email):
    return os.path.join(Users_DIR, email)

def get_user_settings_path(email):
    return os.path.join(get_user_dir(email), "settings.json")

def get_user_prompts_path(email):
    return os.path.join(get_user_dir(email), "prompts.xlsx")

def get_user_images_dir(email):
    return os.path.join(get_user_dir(email), "images")

def list_user_image_urls(email):
    image_dir = os.path.join("Users", email, "images")
    if not os.path.exists(image_dir):
        return []
    
    return [f"/Users/{email}/images/{f}" for f in os.listdir(image_dir) if f.lower().endswith(".png")]

def get_user_logs_dir(email):
    return os.path.join(get_user_dir(email), "logs")

# Redis list key used for storing live log messages
def get_user_log_key(email):
    return f"user_log:{email}"

def get_user_failed_prompts_path(email):
    return os.path.join(get_user_logs_dir(email), "failed_prompts.json")

def init_user_if_missing(email):
    user_dir = get_user_dir(email)
    os.makedirs(get_user_images_dir(email), exist_ok=True)
    os.makedirs(get_user_logs_dir(email), exist_ok=True)
    
    # Create empty settings file if not exists
    settings_path = get_user_settings_path(email)
    if not os.path.exists(settings_path):
        with open(settings_path, "w") as f:
            json.dump({}, f)

    # Create empty failed prompts log if not exists
    failed_path = get_user_failed_prompts_path(email)
    if not os.path.exists(failed_path):
        with open(failed_path, "w") as f:
            json.dump([], f)
