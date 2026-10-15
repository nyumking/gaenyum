"""Copy reports and state into a separate Git worktree for durable storage."""
import argparse
import json
import shutil
from pathlib import Path
from .audit import verify_receipt


def stage_archive(output, state, destination):
    output, state, destination = Path(output), Path(state), Path(destination)
    receipt = verify_receipt(output)
    (destination/'state').mkdir(parents=True, exist_ok=True)
    (destination/'state'/'.keep').touch()
    run, attempt = receipt['run_id'], receipt['attempt']
    if not run or not attempt or not run.isdigit() or not attempt.isdigit():
        raise ValueError('archive requires GitHub numeric run and attempt IDs')
    target = destination/'reports'/f'{run}-{attempt}'
    target.mkdir(parents=True, exist_ok=True)
    for name in ['briefing.json', 'briefing.md', 'receipt.json']:
        source, dest = output/name, target/name
        if dest.exists() and source.read_bytes() != dest.read_bytes():
            raise ValueError('immutable report already exists with different bytes')
        shutil.copyfile(source, dest)
    for source in state.rglob('*.json'):
        relative = source.relative_to(state)
        if relative.parts[0] not in ['forecasts', 'calendar']:
            continue
        dest = destination/'state'/relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        if relative.parts[0] == 'forecasts' and dest.exists():
            if source.read_bytes() != dest.read_bytes(): raise ValueError('immutable forecast mismatch')
            continue
        shutil.copyfile(source, dest)
    verify_receipt(target)
    return target


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=Path('output'))
    parser.add_argument('--state', type=Path, default=Path('state'))
    parser.add_argument('--destination', type=Path, required=True)
    a = parser.parse_args()
    print(stage_archive(a.output, a.state, a.destination))

if __name__ == '__main__': main()
