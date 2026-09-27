# Contributing

Thanks for helping. A few rules keep dpdpkit trustworthy:

1. **Primitives, not promises.** Code, docs and messages never claim that using dpdpkit makes anyone compliant.
2. **Rules as data.** Every legal number (deadlines, thresholds, ages) comes from a policy pack in
   `dpdpkit-policies`. Do not hard-code them.
3. **Fail safe.** When delivery or a job fails, hold and alert. Never delete or file anything automatically
   on failure.
4. **One contract.** HTTP behaviour must match `openapi.yaml` in `dpdpkit-spec`; the conformance suite is the judge.
5. **Customer data stays put.** No network calls home. Telemetry is opt-in only.

## Developer Certificate of Origin

Every commit must be signed off (`git commit -s`), certifying the [DCO](https://developercertificate.org/).

## Workflow

- Open an issue first for anything larger than a bug fix.
- Rule changes use the `rule-change` label and must link the official notification.
- Add a `CHANGELOG.md` entry under "Unreleased".
- CI must pass (lint, types, tests, conformance where applicable).

## Multi-repo development

dpdpkit is split across several repositories. Clone them side by side and run
`scripts/dev-setup.sh` from the workspace to get one virtualenv with every package installed in editable mode.
