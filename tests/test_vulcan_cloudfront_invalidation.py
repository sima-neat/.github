import re
import textwrap
from pathlib import Path
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "vulcan-publish-artifacts.yml"


def workflow() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def path_helpers() -> dict:
    content = workflow()
    match = re.search(
        r"^          def branch_key\(.*?(?=^          def run\()",
        content,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None, "Unable to locate embedded artifact path helpers"
    namespace = {"quote": quote}
    exec(textwrap.dedent(match.group()), namespace)
    return namespace


def test_encoded_branch_s3_key_is_encoded_again_for_cloudfront_viewer_path() -> None:
    helpers = path_helpers()

    branch = helpers["branch_key"]("integration/yolox-seg-pose-detessdequant")
    assert branch == "integration%2Fyolox-seg-pose-detessdequant"

    viewer_path = helpers["cloudfront_viewer_path"](f"core/{branch}/abc123")
    assert viewer_path == "/core/integration%252Fyolox-seg-pose-detessdequant/abc123"


def test_publish_waits_for_invalidation_and_verifies_cloudfront_content() -> None:
    content = workflow()

    assert '"--query", "Invalidation.Id"' in content
    assert '"aws", "cloudfront", "wait", "invalidation-completed"' in content
    assert "verify_cloudfront_file(" in content
    assert '("metadata-all.json", "metadata.json", "manifest.json")' in content

