import json
from pathlib import Path
from gcms_agent.config import write_json

class Checkpoints:
    def __init__(self, root):
        self.root = Path(root)
    def load(self, key):
        path = self.root / f"{key}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    def save(self, key, state):
        write_json(self.root / f"{key}.json", state)
