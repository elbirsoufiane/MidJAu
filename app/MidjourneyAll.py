# from .midjourney_runner import MidjourneyRunnerAll


# def main(user_email: str, prompts_file: str):
#     """Entry point for running all U1–U4 variants."""
#     runner = MidjourneyRunnerAll()
#     runner.run(user_email, prompts_file)

from .midjourney_runner import MidjourneyRunnerAll

def main(user_email: str, prompts_file: str, key: str):
    """Entry point for running all U1–U4 variants."""
    runner = MidjourneyRunnerAll()
    runner.run(user_email, prompts_file, key)
