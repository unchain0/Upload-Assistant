"""Deliver confirmation sounds without passing control characters through logging."""

import sys


def play_prompt_sound() -> None:
    """Ring the terminal bell after a confirmation prompt."""
    sys.stdout.write("\x07")
    sys.stdout.flush()
