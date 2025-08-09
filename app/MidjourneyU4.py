from .midjourney_runner import MidjourneyRunner
def main(user_email: str, prompts_file: str, key: str):
    runner = MidjourneyRunner("U4")
    runner.run(user_email, prompts_file, key)
