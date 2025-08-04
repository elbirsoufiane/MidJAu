from .midjourney_runner import MidjourneyRunner


def main(user_email: str, prompts_file: str):
    """Entry point for U4 jobs using a dedicated runner context."""

    runner = MidjourneyRunner("U4")
    runner.run(user_email, prompts_file)

