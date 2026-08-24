## What this changes

<!-- One paragraph. What did you make possible, or what was wrong before? -->

## Why

<!-- The reasoning a reviewer can't reconstruct from the diff. If a real
     statement forced a decision, say what the document does — not what it
     says. -->

## Checks

- [ ] `make lint && make typecheck && make test` pass locally
- [ ] Monetary values are `Decimal` — never `float`, including on the wire
- [ ] Code, comments and identifiers are in **English** (Italian only in
      string literals that must match real document text)
- [ ] **No bank data added**: no statements, real IBANs, real names, real
      amounts — fixtures use `MARIO ROSSI` and fake IBANs
- [ ] Nothing new sends data off-machine
- [ ] `CLAUDE.md` / the relevant `README` updated if this changes behavior,
      a schema, an endpoint or the shape of the platform

## If this adds or changes a parser

- [ ] I inspected the real document (position-aware), I did not guess a layout
- [ ] `DETECTION` markers appear in *every* document of the format and collide
      with no other source (`demo/DETECTION_COLLISIONS.md`)
- [ ] Signs are reconstructed so that **negative = outflow**
- [ ] A test covers it with a synthetic or redacted fixture

## If this touches the DB

- [ ] An Alembic migration is included, and it is reversible or says why not
- [ ] `natural_key` inputs are unchanged (account id, value date, amount,
      occurrence) — changing them re-keys every existing row
