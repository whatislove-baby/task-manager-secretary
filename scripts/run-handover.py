"""Offline handover evidence: selected genuine workflows, no real accounts."""
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import sys
import unittest

root = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(root/'backend'), str(root/'backend/tests')]
checks = [
    ('Canvas: import, update, failure preserves local work',
     'test_canvas.CanvasTests.test_handover_fixtures_import_update_and_failure_keep_local_work'),
    ('Mail: distinct tasks, confirmation and repeated import',
     'test_mail_handover.MailHandoverTests.test_two_courses_same_title_and_date_survive_review_and_confirmation'),
    ('Mail: follow-up includes original evidence',
     'test_mail_handover.MailHandoverTests.test_follow_up_contains_original_mail_instructions_and_full_draft'),
    ('Failure: truncated model response saves no tasks',
     'test_mail_handover.MailHandoverTests.test_truncated_valid_json_is_a_failure_without_saving_or_marking_analyzed'),
    ('Compose: meeting draft, preview, confirmed SMTP and duplicate protection',
     'test_mail_compose.MailComposeTests.test_new_meeting_requires_confirm_sends_utf8_mime_and_repeated_confirm_is_idempotent'),
    ('Compose: reply uses original mail without changing calendar or sending',
     'test_mail_compose.MailComposeTests.test_reply_uses_untrusted_original_and_does_not_mutate_calendar_or_send'),
    ('Compose failure: truncated model output keeps original draft',
     'test_mail_compose.MailComposeTests.test_truncated_json_is_rejected_even_if_json_is_parseable'),
    ('Compose failure: uncertain SMTP result survives restart with no retry',
     'test_mail_compose.MailComposeTests.test_ambiguous_data_timeout_is_not_retried_and_survives_reopen'),
]
results = []
for label, name in checks:
    suite = unittest.defaultTestLoader.loadTestsFromName(name)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    results.append(dict(name=label, test=name, passed=result.wasSuccessful(),
                        tests=result.testsRun, failures=len(result.failures), errors=len(result.errors)))
report = dict(timestamp=datetime.now(timezone.utc).isoformat(), platform=platform.platform(),
              python=platform.python_version(), baseline='5e583cd171c3a71c6678348e8a690e4654d58cfc',
              scope='Synthetic inputs and controlled HTTP/model/SMTP responses; real SQLite, MIME, job and compose logic.',
              real_canvas='not tested', real_microsoft='not tested', real_model='not tested',
              real_smtp='not tested; fake SMTP only, no external email sent',
              human_handover='pending teammate execution and signatures', results=results)
destination = root/'handover/results.json'
destination.parent.mkdir(exist_ok=True)
destination.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
print('Handover evidence:', destination)
raise SystemExit(0 if all(r['passed'] for r in results) else 1)
