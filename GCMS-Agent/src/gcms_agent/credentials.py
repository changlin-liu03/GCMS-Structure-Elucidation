"""Load the one permitted project credential without adding it to config."""
import os
from pathlib import Path

from gcms_agent.config import PROJECT_ROOT


def load_project_credentials(project_root=None, environ=None):
    """Process environment wins; .env loads only DEEPSEEK_API_KEY.

    No shell evaluation, variable interpolation, parent-directory search or
    general dotenv import. The value is never returned or logged.
    """
    environment = os.environ if environ is None else environ
    if environment.get("DEEPSEEK_API_KEY", "").strip():
        return True
    path = Path(project_root or PROJECT_ROOT) / ".env"
    if not path.is_file():
        return False
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError):
        raise ValueError("Unable to read project credential file") from None
    matches = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, separator, value = line.partition("=")
        if not separator or name.strip() != "DEEPSEEK_API_KEY":
            continue
        value = value.strip()
        if value.startswith(("'", '"')):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError("Malformed project credential entry")
            value = value[1:-1]
        if not value or any(char.isspace() for char in value) or value == "replace-with-your-key":
            raise ValueError("Project credential entry is empty or invalid")
        matches.append(value)
    if len(matches) > 1:
        raise ValueError("Duplicate project credential entries")
    if not matches:
        return False
    environment["DEEPSEEK_API_KEY"] = matches[0]
    return True
