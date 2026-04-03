import sys
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.base_mailbox import MailboxAccount, YueMailSubdomainMailbox


class YueMailSubdomainMailboxTests(unittest.TestCase):
    def _build_mailbox(self):
        mailbox = YueMailSubdomainMailbox.__new__(YueMailSubdomainMailbox)
        mailbox.api = "https://example.invalid"
        mailbox.admin_token = "admin-token"
        mailbox.root_domain = "example.com"
        mailbox.subdomain_prefix = "mail"
        mailbox.custom_auth = ""
        mailbox.local_part_length = 10
        mailbox.subdomain_length = 12
        mailbox.proxy = None
        mailbox._health_state = {}
        mailbox._health_lock = None
        mailbox._health_enabled = True
        mailbox._health_suspend_failures = 3
        mailbox._health_suspend_seconds = 600
        mailbox._email_cache = {}
        mailbox._last_used_mail_ids = {}
        setattr(mailbox, "_log_fn", None)
        setattr(mailbox, "_task_control", None)
        return mailbox

    def test_fetch_mails_falls_back_to_unfiltered_list_and_matches_raw_content(self):
        mailbox = self._build_mailbox()
        target = "user@mail.example.com"
        mailbox._request_json = mock.Mock(
            side_effect=[
                [],
                {
                    "results": [
                        {
                            "id": "skip-1",
                            "subject": "unrelated",
                            "raw": "To: other@example.com\n\nhello",
                        },
                        {
                            "id": "keep-1",
                            "subject": "OpenAI verification",
                            "raw": f"To: {target}\n\nYour verification code is 123456",
                        },
                    ]
                },
            ]
        )

        mails = mailbox._fetch_mails(target)

        self.assertEqual([mail["id"] for mail in mails], ["keep-1"])

    def test_wait_for_code_prefers_recent_detail_candidate(self):
        mailbox = self._build_mailbox()
        now = time.time()
        account = MailboxAccount(email="user@mailsub.example.com")
        mailbox._fetch_mails = mock.Mock(
            return_value=[
                {
                    "id": "old-mail",
                    "subject": "OpenAI verification code",
                    "body": "Your verification code is 111111",
                    "created_at": now - 20,
                },
                {
                    "id": "new-mail",
                    "subject": "OpenAI",
                    "body": "",
                    "created_at": now + 1,
                },
            ]
        )
        mailbox._fetch_mail_detail = mock.Mock(
            side_effect=lambda mail_id: (
                {
                    "subject": "OpenAI verification code",
                    "body": "Your verification code is 222222",
                    "created_at": now + 2,
                }
                if mail_id == "new-mail"
                else {}
            )
        )

        def fake_run_polling_wait(
            *, timeout, poll_interval, poll_once, timeout_message=None
        ):
            code = poll_once()
            if code:
                return code
            raise TimeoutError(timeout_message or "timeout")

        mailbox._run_polling_wait = fake_run_polling_wait

        code = mailbox.wait_for_code(
            account, timeout=5, otp_sent_at=now, exclude_codes={"111111"}
        )

        self.assertEqual(code, "222222")
        self.assertEqual(mailbox._last_used_mail_ids[account.email], "new-mail")

    def test_wait_for_code_retries_same_mail_when_detail_arrives_late(self):
        mailbox = self._build_mailbox()
        account = MailboxAccount(email="user@mailsub.example.com")
        mailbox._fetch_mails = mock.Mock(
            side_effect=[
                [{"id": "mail-1", "subject": "OpenAI", "created_at": time.time()}],
                [{"id": "mail-1", "subject": "OpenAI", "created_at": time.time()}],
            ]
        )
        mailbox._fetch_mail_detail = mock.Mock(
            side_effect=[
                {},
                {
                    "subject": "OpenAI verification code",
                    "body": "Your verification code is 654321",
                    "created_at": time.time(),
                },
            ]
        )

        def fake_run_polling_wait(
            *, timeout, poll_interval, poll_once, timeout_message=None
        ):
            for _ in range(2):
                code = poll_once()
                if code:
                    return code
            raise TimeoutError(timeout_message or "timeout")

        mailbox._run_polling_wait = fake_run_polling_wait

        code = mailbox.wait_for_code(account, timeout=5)

        self.assertEqual(code, "654321")
        self.assertEqual(mailbox._fetch_mail_detail.call_count, 2)


if __name__ == "__main__":
    unittest.main()
