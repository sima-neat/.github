import json
import os
from pathlib import Path
import re
import subprocess
import textwrap


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "vulcan-notify-slack.yml"
README = ROOT / ".github" / "workflows" / "vulcan" / "README.md"


def workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def embedded_composer() -> str:
    match = re.search(
        r"(?ms)^          script: \|\n(?P<script>.*?)(?=^      - name: Post failure notification)",
        workflow(),
    )
    assert match is not None, "Unable to locate embedded notification composer"
    return textwrap.dedent(match.group("script"))


def test_notifier_is_a_minimally_privileged_reusable_workflow() -> None:
    content = workflow()

    assert "workflow_call:" in content
    assert "actions: read" in content
    assert "contents: read" in content
    assert "slack_bot_token:" in content
    assert "required: true" in content


def test_notifier_fetches_failed_jobs_and_posts_with_pinned_actions() -> None:
    content = workflow()

    assert "github.rest.actions.listJobsForWorkflowRun" in content
    assert '"failure"' in content
    assert '"timed_out"' in content
    assert "actions/github-script@ed597411d8f924073f98dfc5c65a23a2325f34cd" in content
    assert "slackapi/slack-github-action@dcb1066f776dd043e64d0e8ba94ca15cc7e1875d" in content
    assert "errors: true" in content
    assert "method: chat.postMessage" in content
    assert "payload: ${{ steps.compose.outputs.result }}" in content


def test_notifier_escapes_untrusted_workflow_metadata() -> None:
    content = workflow()

    assert "const escapeMrkdwn" in content
    assert '.replaceAll("<", "&lt;")' in content
    assert "process.env.COMMIT_MESSAGE" in content
    assert "${{ inputs.commit_message }}" not in content.split("script: |", 1)[1]


def test_composer_renders_failed_jobs_and_prevents_slack_mentions() -> None:
    jobs = [
        {
            "name": "Build <unsafe>",
            "conclusion": "failure",
            "html_url": "https://github.com/sima-neat/core/actions/runs/123/job/1",
        },
        {
            "name": "Successful job",
            "conclusion": "success",
            "html_url": "https://github.com/sima-neat/core/actions/runs/123/job/2",
        },
    ]
    wrapper = f"""
    const core = {{
      setFailed(message) {{ throw new Error(message); }},
    }};
    const github = {{
      paginate: async () => {json.dumps(jobs)},
      rest: {{ actions: {{ listJobsForWorkflowRun() {{}} }} }},
    }};
    async function compose() {{
    {textwrap.indent(embedded_composer(), '  ')}
    }}
    process.stdout.write(await compose());
    """
    environment = {
        **os.environ,
        "CHANNEL_ID": "C123456",
        "RUN_ID": "123",
        "REPOSITORY": "sima-neat/core",
        "WORKFLOW_NAME": "Vulcan CI",
        "BRANCH": "develop",
        "HEAD_SHA": "0123456789abcdef",
        "CONCLUSION": "failure",
        "RUN_ATTEMPT": "2",
        "RUN_URL": "https://github.com/sima-neat/core/actions/runs/123",
        "ACTOR": "test-user",
        "COMMIT_MESSAGE": "Break it <!channel>\nbody",
    }

    completed = subprocess.run(
        ["node", "--input-type=module", "-e", textwrap.dedent(wrapper)],
        check=True,
        capture_output=True,
        env=environment,
        text=True,
    )
    payload = json.loads(completed.stdout)
    rendered = json.dumps(payload)

    assert payload["channel"] == "C123456"
    assert "Build &lt;unsafe&gt;" in rendered
    assert "Successful job" not in rendered
    assert "&lt;!channel&gt;" in rendered
    assert "<!channel>" not in rendered
    assert payload["blocks"][-1]["elements"][0]["url"] == environment["RUN_URL"]


def test_notifier_configuration_is_documented() -> None:
    content = README.read_text(encoding="utf-8")

    assert "vulcan-notify-slack.yml" in content
    assert "SLACK_BOT_TOKEN" in content
    assert "SLACK_VULCAN_EVENT_CHANNEL_ID" in content
    assert "workflow_run" in content
