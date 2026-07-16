from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "pages.yml"
COMPANION_COMMIT = "c999965e5cc923281541409cda9502beb93b8a60"


class PagesWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_deploys_only_after_the_complete_book_check(self) -> None:
        self.assertIn("run: ./scripts/book-check", self.workflow)
        self.assertIn("needs: build", self.workflow)
        self.assertIn("path: book", self.workflow)
        self.assertNotIn("path: .\n", self.workflow)

    def test_companion_access_is_read_only_and_commit_scoped(self) -> None:
        self.assertIn("repository: kuochunchang/emmet-qt-bt1", self.workflow)
        self.assertIn(f"ref: {COMPANION_COMMIT}", self.workflow)
        self.assertIn("fetch-depth: 0", self.workflow)
        self.assertIn("EMMET_QT_BT1_READ_TOKEN", self.workflow)
        self.assertGreaterEqual(self.workflow.count("persist-credentials: false"), 2)

    def test_third_party_actions_are_pinned_to_full_commits(self) -> None:
        action_lines = [
            line.strip()
            for line in self.workflow.splitlines()
            if line.strip().startswith("uses:")
        ]
        self.assertGreaterEqual(len(action_lines), 5)
        for line in action_lines:
            reference = line.split("#", 1)[0].rsplit("@", 1)[-1].strip()
            self.assertRegex(reference, r"^[0-9a-f]{40}$")


if __name__ == "__main__":
    unittest.main()
