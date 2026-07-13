# GraphLand Leaderboard

The GraphLand Leaderboard is a fully static, version-controlled leaderboard for the 14 official GraphLand datasets. It has no backend, database, CMS, runtime GitHub API calls, analytics, or cookies. Researchers submit results through a public GitHub Issue Form; maintainers review the public issue and explicitly opt it into automation; an approved and merged JSON submission is then included in the next GitHub Pages deployment.

Review verifies the submitted format and declared protocol. It is not an independent reproduction unless the entry is explicitly marked `reproduced`. GraphLand has no hidden test set: targets and fixed split masks are part of the public dataset release. Issues, draft pull requests, and their discussions are public, so submissions must not contain secrets or confidential data.

The production data directory intentionally starts empty. Test numbers live only under `tests/leaderboard/fixtures/`; they are not published benchmark results.

## Architecture

The repository has one source of truth for each kind of data:

- `leaderboard/config.json` defines site configuration, task families, display labels, and the four settings.
- `leaderboard/datasets.json` defines the fixed 14-dataset catalog, canonical metric for each dataset, setting availability, display formatting, source, release, and license.
- `leaderboard/schema/submission.schema.json` defines the portable JSON structure for one model submission.
- `leaderboard/submissions/*.json` stores one reviewed submission per file. CSV is never edited as source data.
- `site/` contains the static HTML, CSS, JavaScript, and favicon.
- `scripts/leaderboard/validate.py` performs JSON Schema and semantic validation.
- `scripts/leaderboard/build.py` validates the repository and creates the complete `_site/` artifact, including frontend data, canonical long-form CSV, the public schema, and `.nojekyll`.
- `scripts/leaderboard/issue_to_submission.py` converts the constrained Issue Form body into one submission object. It treats all issue text as untrusted data.
- `tests/leaderboard/` contains standard-library `unittest` coverage and fixtures.
- `.github/workflows/leaderboard-validate.yml` validates pull requests and relevant pushes.
- `.github/workflows/leaderboard-issue-to-pr.yml` creates or updates one label-gated draft pull request per issue.
- `.github/workflows/deploy-pages.yml` builds and deploys the Pages artifact from `main`.

Generated output belongs in `_site/` and is ignored by Git. Every public asset and data reference is relative so the site works at the project Pages base path `/graphland/`.

## Local setup and checks

Python 3.9 or newer is recommended.

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements-leaderboard.txt
```

Validate metadata and every committed submission:

```bash
python3 scripts/leaderboard/validate.py
```

Run the complete test suite:

```bash
python3 -m unittest discover -s tests/leaderboard -p 'test_*.py' -v
```

Build the deterministic Pages artifact:

```bash
python3 scripts/leaderboard/build.py --output _site
```

### Demo submission preview

The repository includes four explicitly synthetic submissions for local UI and
Browser QA. They exercise all settings and task families, missing and unavailable
cells, negative R², code filtering, and the main provenance and verification
badges. They are not benchmark claims and are never read by the production build.

Build the opt-in demo artifact:

```bash
python3 scripts/leaderboard/build.py \
  --allow-pending \
  --output _site \
  --submissions-dir tests/leaderboard/fixtures/demo_submissions
```

Run the ordinary build without `--submissions-dir` before preparing a production
artifact. Never copy demo JSON files into `leaderboard/submissions/`.

For a quick local preview, serve `_site/` directly and open `http://localhost:8000/`:

```bash
python3 -m http.server 8000 --directory _site
```

To exercise the exact production base path, serve a temporary parent directory and open `http://localhost:8000/graphland/`:

```bash
preview_root="$(mktemp -d)"
cp -R _site "$preview_root/graphland"
python3 -m http.server 8000 --directory "$preview_root"
```

Stop the server with `Ctrl-C` and remove the temporary preview directory afterward.

## Dataset catalog and canonical metrics

The benchmark is fixed at 14 datasets in three task families:

| Task family | Canonical metric | Dataset IDs |
| --- | --- | --- |
| Multiclass node classification | Accuracy | `hm-categories`, `pokec-regions`, `web-topics` |
| Binary node classification | AP (Average Precision) | `tolokers-2`, `city-reviews`, `artnet-exp`, `web-fraud` |
| Node regression | R² (coefficient of determination) | `hm-prices`, `avazu-ctr`, `city-roads-M`, `city-roads-L`, `twitch-views`, `artnet-views`, `web-traffic` |

These are described as recommended metrics in the dataset release and are canonical for this leaderboard. Higher is better for all three metrics. There is no aggregate score, overall rank, or `All` task table; results with different metrics are never combined.

Accuracy and AP are stored on their canonical `[0, 1]` scale and formatted as percentages only for display. R² is stored as the raw coefficient and is not multiplied by 100; valid R² values may be negative. The reference metric accepts raw negative R². However, the current reference training loops initialize the best validation score to zero, so they may fail to retain a checkpoint if every validation R² is negative. This leaderboard follows the metric definition and accepts every finite R² value; submitters must ensure their own checkpoint selection handles negative validation scores correctly.

When results from multiple runs or seeds are summarized, `std` means the sample standard deviation used by the official logger (`ddof=1`, with an `n-1` denominator). It is stored in the same raw scale as the corresponding value and must be finite and non-negative. Omit the `std` field when a standard deviation is unavailable; do not invent one or convert classification deviations to percentage points in the JSON.

For example, this schema-only fragment demonstrates the optional standard-deviation field; it is not a claimed GraphLand result:

```json
{
  "setting": "RL",
  "dataset": "hm-categories",
  "value": 0.8123,
  "std": 0.0041
}
```

The UI would display that Accuracy using the dataset's percentage formatting. Omitting `std` displays only the mean.

The dataset release is Zenodo version `v1`, DOI [`10.5281/zenodo.16895532`](https://doi.org/10.5281/zenodo.16895532), licensed under Apache-2.0. That is the dataset license recorded in `datasets.json`; the research code repository itself uses the MIT license.

## Experimental settings

| Setting | Split file | Information access | Train / validation / test |
| --- | --- | --- | --- |
| RL — Random Low | `split_masks_RL.csv` | Transductive | Random stratified 10% / 10% / 80% |
| RH — Random High | `split_masks_RH.csv` | Transductive | Random stratified 50% / 25% / 25% |
| TH — Temporal High | `split_masks_TH.csv` | Transductive | Temporal 50% / 25% / 25% |
| THI — Temporal High, inductive | `split_masks_TH.csv` | Inductive | The same temporal 50% / 25% / 25% split as TH |

In the transductive settings, the full graph is available and train, validation, and test masks select the labeled nodes used at each stage.

THI is a leaderboard setting and information-access protocol, not a fourth split file. The official evaluator handles `THI` by selecting the TH masks and switching to inductive preprocessing. Do not create, document, or read `split_masks_THI.csv`.

The evaluator constructs THI snapshots as follows:

1. The training graph is the subgraph induced by nodes in the TH train mask. Validation and test nodes and their incident edges are absent.
2. The validation graph is the subgraph induced by nodes in the TH train or validation masks. Test nodes and their incident edges are absent.
3. The test graph is the full graph.
4. Categorical encoding, regression-target transforms, numerical/fraction feature transforms, and imputers in the inductive preprocessing path are fitted on training data and then applied to later snapshots.
5. Labels are intersected with the labeled-node mask before loss or metric computation.

Thus TH and THI use identical temporal membership but different graph information. Models must not use validation or test information earlier than the selected protocol permits, and test labels must not be used for training, tuning, checkpoint selection, or model selection.

Every dataset supports RL and RH. TH and THI are both unavailable for `city-reviews`, `city-roads-M`, `city-roads-L`, and `web-traffic`; the UI shows these combinations as `N/A`, and validation rejects them. The remaining ten datasets support all four settings. A missing result in an otherwise supported combination is displayed as `—`.

## Submission JSON

Each submission is a separate UTF-8 JSON file under `leaderboard/submissions/`. Its safe filename must equal `<id>.json`. Use a lowercase, hyphenated stable ID, or the automation-owned `issue-<number>` form. Do not derive paths from model names, variants, URLs, or other user-provided text.

The JSON Schema is the structural contract. Semantic validation additionally checks canonical datasets/settings, metric ranges, finite numbers, result uniqueness, URL policy, dates, review state, code availability, and cross-field rules. A submission may contain any non-empty subset of supported dataset/setting results; partial submissions are expected.

Important fields include:

- Model name and variant/version.
- Paper URL and training-code availability/URL.
- Submitter, provenance (`author_submission` or `maintainer_seeded`), and source issue.
- GraphLand release, tag, or commit used.
- Method type (`trained` or `in_context`), tuning protocol, hyperparameter-trial count, and run/seed count.
- External data or pretraining disclosure.
- Submission date, verification state, review metadata, and notes.
- Results containing only `setting`, `dataset`, `value`, and optional `std`; task and metric are derived from `datasets.json`.

For in-context learning, `hparam_trials` must be `0`. Closed models and models without published training code are permitted, but must use `code_availability: "unavailable"` and `training_code_url: null` so that the UI can label them accurately.

To add a reviewed submission manually:

1. Create `leaderboard/submissions/<id>.json` using the public schema.
2. Include only real, attributable results; do not add demonstrations or estimated numbers.
3. Run validation, tests, and a full build.
4. Inspect `_site/data/leaderboard.json`, `_site/leaderboard.csv`, and the local site.
5. Open a pull request for manual review.

## CSV generation

`leaderboard.csv` is generated during every build from the validated submission JSON files. It is a canonical long-form artifact, not a second editable data source. Task and metric values are joined from `datasets.json`, preventing submitters from relabeling results.

The stable column order is:

```text
submission_id,model_name,model_variant,setting,task,dataset,metric,value,std,num_runs,method_type,hparam_trials,code_availability,paper_url,code_url,provenance,verification,submitted_at,source_issue
```

An omitted standard deviation or unavailable code URL is represented by an empty CSV field. Builds sort submissions and results deterministically.

## Public Issue to draft pull request

The public workflow is deliberately label-gated:

1. A researcher opens the `leaderboard-submission.yml` Issue Form.
2. The researcher supplies model metadata and rows in `setting,dataset,value,std` format and accepts all protocol/publication confirmations.
3. A maintainer reviews the issue and adds the `leaderboard-ready` label only when it is ready for conversion.
4. The issue workflow parses and validates the body without executing submitted code or downloading submitted URLs.
5. It writes only `leaderboard/submissions/issue-<number>.json` on the stable branch `leaderboard/issue-<number>` and creates one draft pull request containing `Closes #<number>`.
6. Later issue edits update the same branch and draft pull request while the gate label remains present.
7. Discussion and manual review continue in the pull request. A maintainer records `review.status: approved`, their GitHub login, and the ISO review date in the JSON before merge; ordinary pull-request validation rejects `pending` records. Automation never approves or merges the pull request.
8. After approval and merge, validation runs again on `main`, Pages is rebuilt, and the merged result appears on the site. The pull request closes the source issue.

Merely opening an issue cannot create a branch or pull request. The conversion workflow uses the issue number for branch and file paths, bounds input sizes, uses minimal permissions, and never interpolates untrusted text into shell commands. A pull request created with `GITHUB_TOKEN` may not start ordinary pull-request workflows, so the issue workflow performs validation and a full build itself; `main` is validated again after merge.

The expected manual-review service level is approximately one week. This is a target, not a guarantee.

## Review and provenance statuses

`provenance` describes who introduced the entry:

- `author_submission`: submitted by a model author through the public form.
- `maintainer_seeded`: a standard open baseline added by a maintainer from an attributable source.

`verification` describes evidentiary status:

- `self_reported`: reviewed for format and declared protocol compliance, but not independently reproduced.
- `reproduced`: independently rerun by the GraphML team. Use this only after an actual reproduction.

The nested review status records repository review state:

- `pending`: no reviewer or review date may be recorded, and production validation refuses to publish it.
- `approved`: requires a reviewer and ISO review date.

These dimensions are intentionally separate. Approval does not imply reproduction, and neither status should be described as verification of state of the art.

## Manual GitHub repository settings

After merging the implementation, a repository administrator must configure GitHub:

1. Enable GitHub Actions for the repository or fork.
2. In `Settings → Pages`, select `GitHub Actions` as the source.
3. In `Settings → Actions → General`, allow GitHub Actions to create pull requests.
4. Create the `leaderboard-submission` label used by the Issue Form.
5. Create the maintainer-only gate label `leaderboard-ready`.
6. Configure branch protection for `main`.
7. Make the leaderboard validation workflow a required status check.
8. Require manual pull-request review before merge.
9. Later, add GraphML reviewers or `CODEOWNERS` once the real GitHub users/team are known; do not invent a team slug.

Also verify that the Pages environment is named `github-pages` and that deployment approvals, if enabled, name the intended maintainers.

## Troubleshooting

### Validation reports an unknown or unsupported result

Use the exact case-sensitive dataset IDs from `datasets.json`. Only RL, RH, TH, and THI are valid settings. Remove TH/THI rows for the four non-temporal datasets. Do not add datasets or metrics to a submission.

### A classification value is rejected

Accuracy and AP must be submitted on `[0, 1]`, not as percentages. Submit `0.8123`, not `81.23`. R² remains in its raw scale and may be negative. `std` uses the same scale as its value.

### An in-context submission is rejected

Set `method_type` to `in_context` and `hparam_trials` to `0`. Describe any other adaptation or inference procedure in `tuning_protocol`.

### Validation mentions the filename

The filename must be exactly `<submission-id>.json`, use the safe ID grammar, be a regular file, and not be a symlink. Automated submissions always use `issue-<number>.json`.

### Someone expects `split_masks_THI.csv`

That file does not exist. Both TH and THI read `split_masks_TH.csv`; THI changes graph visibility and preprocessing.

### The site works at `/` but not `/graphland/`

Run the base-path preview above. Keep HTML, CSS, JavaScript, JSON, CSV, schema, and favicon references relative; a leading `/` targets the domain root and breaks project Pages.

### The issue does not create a pull request

Confirm that both labels exist, the issue has `leaderboard-ready`, Actions may create pull requests, and the parser comment explains no validation error. Removing the label closes the automation gate; adding it again retriggers review.

### A generated pull request does not run normal PR checks

This can be expected for pull requests created with `GITHUB_TOKEN`. The issue-to-PR workflow must run schema validation, semantic validation, unit tests, and the full build before pushing. Required checks run again on the merge commit or another trusted event.

### Pages does not deploy

Confirm that Pages uses GitHub Actions, the workflow has `contents: read`, `pages: write`, and `id-token: write`, the environment is `github-pages`, and the uploaded artifact contains `_site/index.html` plus `.nojekyll`.

## Official resources

- [GraphLand dataset on Zenodo](https://zenodo.org/records/16895532)
- [GraphLand dataset on Kaggle](https://www.kaggle.com/datasets/bazhenovgleb/graphland)
- [Official GraphLand repository](https://github.com/yandex-research/graphland)
- [GraphLand paper](https://arxiv.org/abs/2409.14500)
- [PyTorch Geometric `GraphLandDataset`](https://pytorch-geometric.readthedocs.io/en/latest/generated/torch_geometric.datasets.GraphLandDataset.html)
