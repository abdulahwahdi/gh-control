import unittest

from gh_control.plugins.tray import gh_control_tray as tray_mod


class AddGitIdentityTest(unittest.TestCase):
    def _items(self, active):
        tray = tray_mod.Tray.__new__(tray_mod.Tray)
        items = []
        tray._add = lambda menu, text, callback=None, sensitive=True: items.append(text)
        tray._add_git_identity(None, {}, active)
        return items

    def test_edit_item_with_email(self):
        items = self._items({"login": "alice", "git_name": "Al", "git_email": "a@b.c"})
        self.assertIn("Git: Al <a@b.c>", items)
        self.assertIn("Edit git identity…", items)

    def test_edit_item_without_email(self):
        items = self._items({"login": "alice"})
        self.assertEqual(len(items), 2)
        self.assertEqual(items[-1], "Edit git identity…")


if __name__ == "__main__":
    unittest.main()
