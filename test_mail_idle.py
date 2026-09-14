import unittest
from unittest.mock import Mock

import mail_idle


class MailIdleTests(unittest.TestCase):
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
