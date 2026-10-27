"""Keep the reusable workflow's package helper tied to its workflow revision."""
import argparse
from pathlib import Path
import textwrap

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/vulcan-build.yml"
START = "          # BEGIN BUNDLED SDK PACKAGE HELPER\n"
END = "          # END BUNDLED SDK PACKAGE HELPER"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    current = WORKFLOW.read_text()
    before, rest = current.split(START, 1)
    _, after = rest.split(END, 1)
    shell = ('helper_dir=$(mktemp -d "${RUNNER_TEMP}/sdk-package.XXXXXX")\n'
             'cat > "$helper_dir/sdk-package.sh" <<\'SDK_PACKAGE_SCRIPT\'\n'
             + (ROOT / "scripts/sdk-package.sh").read_text()
             + 'SDK_PACKAGE_SCRIPT\nchmod 0755 "$helper_dir/sdk-package.sh"\n'
             + 'echo "SDK_PACKAGE_HELPER=$helper_dir/sdk-package.sh" >> "$GITHUB_ENV"\n')
    expected = before + START + textwrap.indent(shell, "          ") + END + after
    if args.check:
        if expected != current:
            raise SystemExit("Stale helper bundle: run python3 scripts/bundle_sdk_package_helper.py")
    else:
        WORKFLOW.write_text(expected)


if __name__ == "__main__":
    main()
