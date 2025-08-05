# from .midjourney_runner import MidjourneyRunner


# def main(user_email: str, prompts_file: str):
#     """Entry point for U1 jobs.

#     Each call creates a fresh :class:`MidjourneyRunner` instance so that no
#     state persists between jobs.
#     """

#     runner = MidjourneyRunner("U1")
#     runner.run(user_email, prompts_file)


from .midjourney_runner import MidjourneyRunner

def main(user_email: str, prompts_file: str, key: str):
    runner = MidjourneyRunner("U1")
    runner.run(user_email, prompts_file, key)