"""Read only a bounded, non-sensitive backup policy report for public health."""
import json
import os
from pathlib import Path
import stat

POLICY = 'database_only_v1'
COUNTERS = ('archives_checked', 'archives_migrated', 'video_entries_removed', 'archives_retained', 'failed_archives')


def public_backup_status():
    result = dict(policy=POLICY, status='unavailable', **{key: 0 for key in COUNTERS})
    path = Path(os.getenv('PROCTOR_BACKUP_POLICY_FILE', '/var/backups/qorgau/backup-policy-status.json'))
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > 4096 or getattr(info, 'st_file_attributes', 0) & 0x400:
            return result
        with path.open('rb') as stream:
            raw = stream.read(4097)
        if len(raw) > 4096:
            return result
        report = json.loads(raw)
        if (report.get('policy') != POLICY or report.get('status') not in ('pending', 'error', 'complete')
                or any(type(report.get(key)) is not int or not 0 <= report[key] <= 10000000 for key in COUNTERS)):
            return result
        if report['status'] == 'complete' and (report['failed_archives'] or not 1 <= report['archives_retained'] <= 7):
            return result
        return {key: report[key] for key in ('policy', 'status', *COUNTERS)}
    except (OSError, ValueError, TypeError, AttributeError):
        return result
