# Org Default Repository Permission Policy

Use `config.json` to define the organization-wide default repository permission.

## Supported values

- `none`
- `read`
- `write`
- `admin`

## Example

```json
{
  "default_repository_permission": "none"
}
```

## Notes

- This controls baseline access for organization members on repositories where they are not explicitly granted access by team/user policy.
