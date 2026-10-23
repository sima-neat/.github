"""Bundle the resolver into its workflow so workflow pins also pin executed code."""
import argparse
from pathlib import Path
import textwrap

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/vulcan-resolve-modalix-runner.yml'
SOURCE = ROOT / 'scripts/resolve_modalix_runner.py'
START = '          # BEGIN BUNDLED MODALIX RESOLVER\n'
END = '          # END BUNDLED MODALIX RESOLVER'


def bundled_source(workflow):
    block = workflow.split(START, 1)[1].split(END, 1)[0]
    shell = textwrap.dedent(block)
    return shell.removeprefix("python3 - <<'PYTHON'\n").removesuffix('PYTHON\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    workflow = WORKFLOW.read_text()
    before, remainder = workflow.split(START, 1)
    _, after = remainder.split(END, 1)
    shell = "python3 - <<'PYTHON'\n" + SOURCE.read_text() + 'PYTHON\n'
    expected = before + START + textwrap.indent(shell, '          ') + END + after
    if args.check:
        if workflow != expected:
            raise SystemExit('Resolver bundle is stale; run python3 scripts/bundle_modalix_resolver.py')
    else:
        WORKFLOW.write_text(expected)


if __name__ == '__main__':
    main()
