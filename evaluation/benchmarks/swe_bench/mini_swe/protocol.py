"""Explicit completion markers shared by native messages and command outputs."""

SUBMISSION_MARKER = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"


def submission_text(text):
    """Return text after a standalone marker, or None when no marker is present."""
    if not isinstance(text, str):
        return None
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.strip() == SUBMISSION_MARKER:
            return "".join(lines[index + 1:])
    return None
