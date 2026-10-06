# Pull Request

## What and Why

Describe the problem and the smallest correct change. Link related issues.

## Branches

- [ ] This PR follows [GitFlow](https://github.com/sidkolapalli/carla-agentic-toolkit/blob/main/docs/gitflow.md):
      normal work targets `develop`; a `release/*` or `hotfix/*` PR targets `main`.
- [ ] For a release/hotfix, I linked the planned or completed back-merge into `develop`.
      Otherwise: not applicable.

## Verification

- [ ] `make check` passes.
- [ ] A regression test failed before the behavioral fix and passes now, or the
      change is documentation/configuration-only.
- [ ] I documented whether live CARLA was tested and which version was used.
- [ ] Security-boundary changes include a rejection/failure test.

## Notes

List compatibility concerns, deferred work, or reviewer setup instructions.
