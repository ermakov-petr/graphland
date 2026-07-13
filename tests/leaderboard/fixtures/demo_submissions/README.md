# Demo submissions

Every JSON file in this directory is synthetic test data for local UI and Browser QA.
The values are not GraphLand benchmark claims. This directory is the durable test
fixture catalog and is not read by an ordinary production build.

Build an opt-in demo artifact from the repository root:

```bash
python3 scripts/leaderboard/build.py \
  --allow-pending \
  --output _site \
  --submissions-dir tests/leaderboard/fixtures/demo_submissions
```

Running the ordinary build without `--submissions-dir` continues to use only
reviewed production submissions.

Reviewed derivatives of these fixtures may be committed temporarily as
`leaderboard/submissions/demo-*.json` for public GitHub Pages QA. Such production
copies must keep the `demo-` IDs and explicit synthetic/non-benchmark notices; they
may differ in review metadata because production validation accepts only approved
records.

The public demo is data-only and deliberately disposable. Remove its tracked
production copies with:

```bash
git rm -- 'leaderboard/submissions/demo-*.json'
```

After validation and deployment, the ordinary build is empty again when there are
no real submissions. Do not delete these fixtures or change build configuration as
part of demo removal; tests continue to use this directory after the public copies
are gone.
