from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def workflow(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def test_config_resolver_prefers_repository_role_over_owner_role() -> None:
    content = workflow("vulcan-resolve-config.yml")

    assert ".artifacts.publisher_repository_roles[$repository] // empty" in content
    assert 'artifact_publisher_role_arn="${repository_publisher_role_arn:-${owner_publisher_role_arn}}"' in content


def test_publish_workflows_enforce_registered_repository_role() -> None:
    for name in ("vulcan-publish-artifacts.yml", "vulcan-update-latest-artifacts.yml"):
        content = workflow(name)

        assert ".artifacts.publisher_repository_roles[$repository] // empty" in content
        assert 'registered_role_to_assume="${repository_role_to_assume:-${owner_role_to_assume}}"' in content
        assert '"${INPUT_ROLE_TO_ASSUME}" != "${registered_role_to_assume}"' in content
        assert 'role_to_assume="${registered_role_to_assume}"' in content
