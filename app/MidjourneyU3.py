from .midjourney_runner import MidjourneyRunner


def main(user_email: str, prompts_file: str):
    """Entry point for U3 jobs using a dedicated runner context."""

    runner = MidjourneyRunner("U3")
    runner.run(user_email, prompts_file)

