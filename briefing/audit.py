"""Tamper-evident execution receipt: no secrets or request headers are serialized."""
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

CRONS = {'premarket': '0 12 * * 1-5', 'close': '0 22 * * 1-5'}


def write_receipt(report, directory):
    directory = Path(directory)
    mode = report['mode']
    event = os.environ.get('GITHUB_EVENT_NAME', 'local')
    cron = os.environ.get('BRIEFING_SCHEDULE', '')
    generated = datetime.fromisoformat(report['generated_at_utc'])
    hour = 12 if mode == 'premarket' else 22
    nominal = generated.astimezone(timezone.utc).replace(hour=hour, minute=0, second=0, microsecond=0)
    # A delayed run cannot silently validate a future or different-day slot.
    delay = (generated-nominal).total_seconds()/60
    expected_day = nominal.weekday() < 5
    receipt = {'schema_version': '1.0', 'event': event, 'cron': cron or None, 'mode': mode,
        'run_id': os.environ.get('GITHUB_RUN_ID'), 'attempt': os.environ.get('GITHUB_RUN_ATTEMPT'),
        'commit': os.environ.get('GITHUB_SHA'), 'ref': os.environ.get('GITHUB_REF'),
        'generated_at_utc': report['generated_at_utc'], 'generated_at_kst': report['generated_at_kst'],
        'nominal_slot_utc': nominal.isoformat(), 'delay_minutes': round(delay, 2),
        'scheduled_slot_valid': bool(event == 'schedule' and cron == CRONS[mode] and expected_day
                                     and 0 <= delay <= 180 and report['context']['scheduled_session_valid']),
        'report_status': report['status'], 'files': {}}
    for name in ['briefing.md', 'briefing.json']:
        raw = (directory/name).read_bytes()
        receipt['files'][name] = {'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
    (directory/'receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    return receipt


def verify_receipt(directory):
    directory = Path(directory)
    receipt = json.loads((directory/'receipt.json').read_text())
    for name, expected in receipt['files'].items():
        if name not in ['briefing.md', 'briefing.json']:
            raise ValueError('unexpected receipt file')
        raw = (directory/name).read_bytes()
        if len(raw) != expected['bytes'] or hashlib.sha256(raw).hexdigest() != expected['sha256']:
            raise ValueError('report checksum mismatch')
    if set(receipt['files']) != {'briefing.md', 'briefing.json'}:
        raise ValueError('receipt missing report files')
    return receipt
