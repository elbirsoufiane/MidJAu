from .midjourney_runner import MidjourneyRunner


def main(user_email: str, prompts_file: str):
    """Entry point for U2 jobs using a dedicated runner context."""

    runner = MidjourneyRunner("U2")
    runner.run(user_email, prompts_file)

