# Repo Permissions Policy

Use `config.json` to define default and repo-specific access rules.

## Supported permissions

- `pull` (`read`)
- `push` (`write`)
- `triage`
- `maintain` (`maintainer`)
- `admin`

Aliases in parentheses are accepted by the sync script.

## Example: repo-specific rules

```json
{
  "exclude_repos": [],
  "defaults": {
    "teams": [],
    "users": []
  },
  "repos": {
    "my-repo": {
      "inherit_defaults": true,
      "teams": [
        { "slug": "argo", "permission": "maintainer" }
      ],
      "users": [
        { "username": "user1", "permission": "admin" },
        { "username": "user2", "permission": "maintain" }
      ]
    }
  },
  "prune_unmanaged": {
    "teams": false,
    "users": false
  }
}
```

## Notes

- `teams[].slug` is the team slug, not display name.
- `inherit_defaults: true` merges defaults with repo-specific rules.
- Set `inherit_defaults: false` to use only repo-specific rules for that repo.
- Enable `prune_unmanaged` only when you want strict enforcement that removes unmanaged access.
