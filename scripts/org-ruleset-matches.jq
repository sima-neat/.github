# Compare every managed value, allowing API-added object fields/defaults.
# Arrays are sets in these policies, but must have exactly the managed members.
def canonical:
  if type == "array" then map(canonical) | sort
  elif type == "object" then with_entries(.value |= canonical)
  else . end;
def contains_managed($expected):
  . as $actual |
  if ($expected | type) == "object" then
    ($actual | type) == "object" and
    all($expected | keys[]; . as $key |
      ($actual | has($key)) and
      ($actual[$key] | contains_managed($expected[$key])))
  elif ($expected | type) == "array" then
    ($actual | canonical) == ($expected | canonical)
  else $actual == $expected end;

. as $actual |
all(["name", "target", "enforcement"][]; . as $key | $actual[$key] == $expected[$key]) and
((.bypass_actors // [] | canonical) == ($expected.bypass_actors // [] | canonical)) and
(.conditions | contains_managed($expected.conditions)) and
(([.rules[].type] | sort) == ([$expected.rules[].type] | sort)) and
all($expected.rules[]; . as $rule |
  [$actual.rules[] | select(.type == $rule.type)] as $matches |
  ($matches | length) == 1 and
  ($matches[0] | contains_managed($rule)))
