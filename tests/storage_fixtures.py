"""Read persisted snapshots in tests without depending on one aggregate file."""

import json
from pathlib import Path


def task_path(state_file, task_id):
    return Path(state_file).with_suffix('') / (task_id + '.json')


def saved_tasks_text(state_file):
    state_file = Path(state_file)
    if state_file.exists():
        return state_file.read_text(encoding='utf-8')
    tasks = {path.stem: json.loads(path.read_text(encoding='utf-8'))['task']
             for path in state_file.with_suffix('').glob('*.json')}
    return json.dumps({'version': 1, 'tasks': tasks}, ensure_ascii=False)
