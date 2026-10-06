# GitFlow contributor guide

`main` is the release branch and remains the default for users installing the
toolkit. `develop` integrates the next release. Both are protected. This project
is still an experimental alpha; merging a release does not establish production
or autonomous-driving safety.

## Choose a branch and target

| Change | Branch from | PR head | PR base |
| --- | --- | --- | --- |
| New behavior | `develop` | `feature/<issue>-<description>` | `develop` |
| Ordinary bug fix | `develop` | `fix/<issue>-<description>` | `develop` |
| Documentation or maintenance | `develop` | `docs/*`, `chore/*`, `refactor/*`, `test/*`, `perf/*`, `ci/*`, `build/*` | `develop` |
| Automated dependency update | `develop` | `dependabot/*` | `develop` |
| Release stabilization | `develop` | `release/<version>` | `main` |
| Urgent fix for released code | `main` | `hotfix/<issue>-<description>` | `main` |
| Release/hotfix back-merge | Upstream `main` | Upstream `main` | `develop` |

Every prefix needs a descriptive suffix. Existing and agent-created `WIP/*`
branches may target `develop`; `WIP/release/*` and `WIP/hotfix/*` may also target
`main`. A fork's `main` or `develop` is not a contribution branch: create a topic
branch. Release/hotfix branches may also target `develop` for integration work.
The **Branch policy** check rejects unsupported targets and gives next steps.

Scheduled Dependabot version updates target `develop`. GitHub sends automatic
security updates only to the default branch, so upstream PRs authored by
`dependabot[bot]` on `dependabot/*` branches may also target `main`. Review them
as hotfixes and back-merge after merging. The exception checks both the PR author
and source repository; naming a fork branch `dependabot/*` does not qualify.
See [GitHub's Dependabot guidance](https://docs.github.com/en/code-security/tutorials/secure-your-dependencies/customizing-dependabot-prs).

## Submit from a fork

Fork the repository on GitHub, substitute your username below, and run these
commands in Linux or WSL2. No GitFlow extension is required.

```bash
git clone https://github.com/YOUR_USERNAME/carla-agentic-toolkit.git
cd carla-agentic-toolkit
git remote add upstream https://github.com/sidkolapalli/carla-agentic-toolkit.git
git fetch upstream
git switch -c feature/123-describe-change upstream/develop
uv sync --locked --all-extras
cargo build --locked --manifest-path sandbox-runner/Cargo.toml --release
# Add a failing regression test, implement the change, then run:
make check
uv run ruff format --check .
git add <changed-files>
git commit -m "Describe the change"
git push -u origin feature/123-describe-change
gh pr create --repo sidkolapalli/carla-agentic-toolkit --base develop \
  --head YOUR_USERNAME:feature/123-describe-change
```

You can use GitHub's web PR form instead of `gh`. Explicitly choose base
**develop**. Link the issue, show the regression test's before/after result and
describe any live CARLA testing. Never supply a Jev key to CI. Ordinary tests
need neither a running simulator nor provider credentials.

When your PR falls behind its target, update your topic branch and rerun checks:

```bash
git fetch upstream
git merge upstream/develop
git push
```

Use `upstream/main` instead for a hotfix or release PR targeting `main`. Resolve
conflicts on the topic branch. Do not force-push either long-lived branch.

## What GitHub enforces

- All changes to `main` and `develop` go through a pull request.
- **Linux quality gate** and **Branch policy** must pass from GitHub Actions.
  The PR must be up to date with its target and review conversations resolved.
- One maintainer approval is required through [CODEOWNERS](../.github/CODEOWNERS).
  New reviewable commits dismiss stale approvals.
- Merge commits are the enabled merge method, preserving release/back-merge
  ancestry. Squash and rebase merging are disabled.
- Force pushes and deletion of `main` and `develop` are blocked. Existing `v*`
  release tags cannot be rewritten or deleted.

The sole-maintainer exception applies **only to the review ruleset**: repository
administrators may bypass approval through a PR for their own work, recording why
in that PR. GitHub cannot restrict that exception to self-authored PRs, so this is
a maintainer responsibility. The separate core ruleset has no bypass actors;
CI, PRs, resolved conversations, force-push blocking and deletion protection still
apply. Remove the review exception when independent maintainers can review each
other. Administrators can edit repository settings; these rules do not remove
that administrative power.

First-time external contributors may need a maintainer to approve the Actions
run. PR code runs on GitHub-hosted runners with no provider secrets; fork tokens
are read-only. The Rust audit action uses its stdout fallback for fork reports.
Do not change this to `pull_request_target` to run untrusted contributor code.

The checked-in [ruleset definitions](../.github/rulesets/) describe the intended
settings. GitHub's [active rules](https://github.com/sidkolapalli/carla-agentic-toolkit/rules)
are authoritative; editing a JSON file alone does not update those settings.
GitHub documents [how rulesets and bypasses work](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/creating-rulesets-for-a-repository).

## Maintainer release and hotfix steps

1. Cut `release/<version>` from current upstream `develop`, or `hotfix/<issue>`
   from current upstream `main`. Agree on the release version before changing it.
2. Stabilize the branch, update release notes and open a PR into `main`. Keep
   feature work on `develop`. Confidential security fixes follow
   [SECURITY.md](../SECURITY.md) before any public PR is opened.
3. Wait for required checks and review, then use **Create a merge commit**.
   For a self-authored PR, document use of the review exception after checks pass.
4. For a release, verify CI on the merged `main` commit. Tag that exact commit and
   publish the release notes. Alpha releases remain prereleases and source-only;
   do not publish a wheel until Rust runner packaging is solved and tested.
5. Open `main` → `develop` in the upstream repository and merge after checks pass.
   If there is an active release branch, merge the hotfix into it as well before
   completing that release. Record the back-merge PR in the release/hotfix PR.
6. Delete a completed topic/release/hotfix branch only after its work is preserved
   in both long-lived branches. Automatic branch deletion is disabled so a
   release branch is retained until this is verified.

Do not use `git flow ... finish` commands that push directly to protected
branches. Perform release and hotfix integration through GitHub PRs instead.
