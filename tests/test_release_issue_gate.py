import unittest

from scripts.release_issue_gate import (
    repository_release_scope,
    release_target_candidates,
    resolve_release_issues,
)


def issue_item(target_release: str, number: int = 1, repository: str = "sima-neat/insight") -> dict:
    return {
        "id": f"item-{number}",
        "content": {
            "__typename": "Issue",
            "title": f"Issue {number}",
            "number": number,
            "url": f"https://github.com/{repository}/issues/{number}",
            "state": "OPEN",
            "repository": {"nameWithOwner": repository},
        },
        "fieldValues": {
            "nodes": [
                {
                    "__typename": "ProjectV2ItemFieldSingleSelectValue",
                    "name": target_release,
                    "field": {"name": "Target Release"},
                },
                {
                    "__typename": "ProjectV2ItemFieldSingleSelectValue",
                    "name": "Done",
                    "field": {"name": "Status"},
                },
                {
                    "__typename": "ProjectV2ItemFieldSingleSelectValue",
                    "name": None,
                    "field": {"name": "Release Status"},
                },
            ]
        },
    }


class ReleaseIssueGateTest(unittest.TestCase):
    def test_insight_candidates_prefer_repo_scoped_release(self):
        self.assertEqual(
            release_target_candidates("0.0.5", "sima-neat/insight"),
            ["insight-0.0.5", "0.0.5"],
        )

    def test_cali_candidates_prefer_cali_release(self):
        self.assertEqual(
            release_target_candidates("0.3.0", "sima-neat/core"),
            ["cali-0.3.0", "0.3.0"],
        )

    def test_candidates_deduplicate_already_prefixed_release(self):
        self.assertEqual(
            release_target_candidates("insight-0.0.5", "sima-neat/insight"),
            ["insight-0.0.5"],
        )

    def test_cali_repositories_apply_repository_scope(self):
        self.assertEqual(repository_release_scope("sima-neat/internals"), "sima-neat/internals")
        self.assertEqual(repository_release_scope("sima-neat/apps"), "sima-neat/apps")

    def test_non_cali_repositories_do_not_apply_repository_scope(self):
        self.assertIsNone(repository_release_scope("sima-neat/insight"))
        self.assertIsNone(repository_release_scope("sima-neat/sdk"))

    def test_resolves_repo_scoped_release_issue(self):
        target, issues, skipped = resolve_release_issues(
            items=[issue_item("insight-0.0.5")],
            release_targets=["insight-0.0.5", "0.0.5"],
            release_field_name="Target Release",
            status_field_name="Status",
            release_status_field_name="Release Status",
        )

        self.assertEqual(target, "insight-0.0.5")
        self.assertEqual(len(issues), 1)
        self.assertEqual(skipped, [])

    def test_resolves_plain_release_fallback(self):
        target, issues, skipped = resolve_release_issues(
            items=[issue_item("0.0.5")],
            release_targets=["insight-0.0.5", "0.0.5"],
            release_field_name="Target Release",
            status_field_name="Status",
            release_status_field_name="Release Status",
        )

        self.assertEqual(target, "0.0.5")
        self.assertEqual(len(issues), 1)
        self.assertEqual(skipped, [])

    def test_fails_when_multiple_release_candidates_match(self):
        with self.assertRaises(SystemExit):
            resolve_release_issues(
                items=[issue_item("insight-0.0.5", 1), issue_item("0.0.5", 2)],
                release_targets=["insight-0.0.5", "0.0.5"],
                release_field_name="Target Release",
                status_field_name="Status",
                release_status_field_name="Release Status",
            )

    def test_cali_release_scope_ignores_sibling_repository_issues(self):
        target, issues, skipped = resolve_release_issues(
            items=[
                issue_item("cali-0.3.0", 1, "sima-neat/internals"),
                issue_item("cali-0.3.0", 2, "sima-neat/apps"),
            ],
            release_targets=["cali-0.3.0", "0.3.0"],
            release_field_name="Target Release",
            status_field_name="Status",
            release_status_field_name="Release Status",
            repository_scope="sima-neat/internals",
        )

        self.assertEqual(target, "cali-0.3.0")
        self.assertEqual([issue.repository for issue in issues], ["sima-neat/internals"])
        self.assertEqual(skipped, [])

    def test_non_cali_release_keeps_cross_repository_issues(self):
        target, issues, skipped = resolve_release_issues(
            items=[
                issue_item("insight-0.0.5", 1, "sima-neat/insight"),
                issue_item("insight-0.0.5", 2, "sima-neat/vulcan"),
            ],
            release_targets=["insight-0.0.5", "0.0.5"],
            release_field_name="Target Release",
            status_field_name="Status",
            release_status_field_name="Release Status",
        )

        self.assertEqual(target, "insight-0.0.5")
        self.assertEqual(
            [issue.repository for issue in issues],
            ["sima-neat/insight", "sima-neat/vulcan"],
        )
        self.assertEqual(skipped, [])


if __name__ == "__main__":
    unittest.main()
