import unittest
from unittest.mock import Mock
from unittest.mock import patch

import mail_idle


class MailIdleTests(unittest.TestCase):
    def test_listener_is_enabled_by_default_on_render(self):
        with patch.dict("os.environ", {"RENDER": "true"}, clear=True):
            self.assertTrue(mail_idle._enabled())
        with patch.dict("os.environ", {"RENDER_EXTERNAL_HOSTNAME": "hsc-app-3.onrender.com"}, clear=True):
            self.assertTrue(mail_idle._enabled())

    def test_listener_status_reports_retry_phase(self):
        with patch.object(mail_idle, "_STATE", {
            "enabled": True, "running": True, "connected": False,
            "last_uid": 0, "last_event_at": "", "last_connected_at": "",
            "last_error": "temporary failure", "stage": "retry_wait",
        }):
            self.assertEqual(mail_idle.listener_status()["phase"], "retrying")

    def test_initial_uid_uses_uidnext_without_searching_mailbox(self):
        mailbox = Mock()
        mailbox.response.return_value = ("UIDNEXT", [b"431"])

        self.assertEqual(mail_idle._initial_uid(mailbox), 430)
        mailbox.uid.assert_not_called()

    def test_suspend_listener_closes_active_idle_socket(self):
        mailbox = Mock()
        with patch.object(mail_idle, "_MAILBOX", mailbox):
            mail_idle.suspend_listener(seconds=5)
            mailbox.shutdown.assert_called_once()
            mail_idle.resume_listener()

    def test_idle_detects_exists_and_closes_command(self):
        mailbox = Mock()
        mailbox._new_tag.return_value = b"ABCD1"
        mailbox.readline.side_effect = [b"+ idling\r\n", b"* 14 EXISTS\r\n", b"ABCD1 OK IDLE done\r\n"]
        mailbox.sock.gettimeout.return_value = 30
        mailbox.tagged_commands = {b"ABCD1": None}

        self.assertTrue(mail_idle._idle_once(mailbox, timeout=60))
        self.assertEqual(mailbox.send.call_args_list[0].args[0], b"ABCD1 IDLE\r\n")
        self.assertEqual(mailbox.send.call_args_list[1].args[0], b"DONE\r\n")
        self.assertNotIn(b"ABCD1", mailbox.tagged_commands)

    def test_new_uids_only_returns_messages_after_checkpoint(self):
        mailbox = Mock()
        mailbox.uid.return_value = ("OK", [b"100 101 102"])

        self.assertEqual(mail_idle._new_uids(mailbox, 100), [101, 102])
        mailbox.uid.assert_called_once_with("search", None, "UID 101:*")

    def test_latest_uid_handles_empty_inbox(self):
        mailbox = Mock()
        mailbox.uid.return_value = ("OK", [b""])

        self.assertEqual(mail_idle._latest_uid(mailbox), 0)


if __name__ == "__main__":
    unittest.main()
