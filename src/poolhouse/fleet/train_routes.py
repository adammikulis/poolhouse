"""The job command that runs ``python -m poolhouse.train run`` for the Fine-tune page."""

from __future__ import annotations

import sys


class TrainRoutes:
    """A job may name ``poolhouse.train`` and run the training recipes."""

    def job_commands(self) -> tuple[str, ...]:
        return (*super().job_commands(), "poolhouse.train")

    def job_launch(self, command, args, root):
        if command != "poolhouse.train" or args[:1] != ["run"]:
            return super().job_launch(command, args, root)
        return [sys.executable, "-m", "poolhouse.train", *args], None
