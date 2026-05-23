# Repo CODEOWNERS Policy

Use `config.json` to define the default code owner team and managed branches.

The sync script creates the team when missing, gives it write access to each
target repository, and writes `.github/CODEOWNERS` only on branches that exist.
Existing CODEOWNERS files are preserved with a managed default block prepended.
