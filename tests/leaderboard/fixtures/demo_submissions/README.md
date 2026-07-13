# Demo submissions

Every JSON file in this directory is synthetic test data for local UI and Browser QA.
The values are not GraphLand benchmark claims and must never be copied into
`leaderboard/submissions/` or included in a production deployment.

Build an opt-in demo artifact from the repository root:

```bash
python3 scripts/leaderboard/build.py \
  --allow-pending \
  --output _site \
  --submissions-dir tests/leaderboard/fixtures/demo_submissions
```

Running the ordinary build without `--submissions-dir` continues to use only
reviewed production submissions.
