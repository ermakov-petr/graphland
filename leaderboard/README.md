# GraphLand Leaderboard

A static, version-controlled leaderboard for the 14 official GraphLand datasets. Submission JSON is the source of truth; CSV, XLSX and browser data are generated. The site has no runtime GitHub API calls, database, analytics or cookies.

Production seeds are published ResMLP, GraphSAGE and CatBoost results from [arXiv v5](https://arxiv.org/abs/2409.14500v5): 130 numerical cells from Tables 2, 5, 6 and 7. They are source transcriptions, not independent reruns. [sources/paper-v5.json](sources/paper-v5.json) retains the exact source table, original cell, canonical value, seed count and source SHA-256. TLE/MLE/RTE cells are omitted; they are never scored as zero. The original experiment commit is not reported and is explicitly described as unknown in evaluator_ref. Synthetic examples live only under tests/leaderboard/fixtures/demo_submissions.

## Local build and checks

Python 3.9+ works for the static leaderboard. Use Python 3.11 for the CPU research-code environment.

~~~sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-leaderboard.txt
python scripts/leaderboard/validate.py
python -m unittest discover -s tests/leaderboard -p 'test_*.py' -v
python scripts/leaderboard/build.py --output _site
python -m http.server 8000 --directory _site
~~~

The portable wheel lock includes every allowed wheel SHA-256 and enables --require-hashes and --only-binary=:all:. To review dependency updates, edit requirements-leaderboard.in and run scripts/leaderboard/lock_requirements.py with network access; commit the reviewed input and generated lock together. CI installs the checked-in lock, never regenerates it.

The output must be absent, empty, or carry the builder-owned .graphland-build.json marker. Source directories, their ancestors, .git, agent directories and an explicitly selected submissions directory cannot be output targets. A legacy nonempty _site without the marker is refused: use a fresh output path and deliberately remove the old artifact after inspection. A sibling staging directory is built before replacement; write or swap failure preserves/restores the prior good artifact.

For opt-in local synthetic UI QA:

~~~sh
python scripts/leaderboard/build.py --allow-pending --output _site-demo \
  --submissions-dir tests/leaderboard/fixtures/demo_submissions
~~~

Demo records retain their explicit synthetic/non-benchmark notices and display a local Synthetic demo badge. They may exercise review/reproduction UI states but are never scientific evidence.

For browser behavior and scientific CPU checks use a separate environment:

~~~sh
python3.11 -m venv .venv-qa
. .venv-qa/bin/activate
python -m pip install -r requirements-leaderboard.txt
python -m pip install -r requirements-core-test.txt -r requirements-browser-test.txt
python -m playwright install chromium
python -m unittest discover -s tests/core -v
python -m unittest discover -s tests/browser -v
~~~

The browser suite builds fixture data and starts its own local server. It checks real Chromium interactions, keyboard focus, dialogs, URL/history, filters, loading recovery and viewport overflow. The CPU suite tests tiny fixtures; neither suite reproduces the full GPU benchmark.

## Files and contracts

- config.json: site labels, task families and settings, validated against schema/config.schema.json.
- datasets.json: the fixed dataset catalog, canonical metric, release, availability and formatting, validated against schema/datasets.schema.json.
- schema/submission.schema.json: public v1/v2 submission structure.
- submissions/*.json: one reviewed model record per file, safe filename exactly matching its id.
- sources/paper-v5.json: attributable paper seed evidence.
- scripts/leaderboard/common.py: strict JSON, finite numbers, URL and display-text policy.
- scripts/leaderboard/validate.py: structural and semantic validation.
- scripts/leaderboard/build.py: staged static build, normalized per-result metadata and deterministic exports.
- scripts/leaderboard/issue_to_submission.py: constrained form parsing; all issue text is untrusted data.
- scripts/leaderboard/automation.py: trusted lifecycle, ownership, handover and deployment freshness checks.
- site/: relative-path HTML/CSS/JavaScript assets for project Pages /graphland/.

## Canonical metrics and information access

| Task | Metric | Dataset IDs |
| --- | --- | --- |
| Multiclass classification | Accuracy | hm-categories, pokec-regions, web-topics |
| Binary classification | AP | tolokers-2, city-reviews, artnet-exp, web-fraud |
| Regression | R² | hm-prices, avazu-ctr, city-roads-M, city-roads-L, twitch-views, artnet-views, web-traffic |

Higher is better. There is no aggregate rank across different metrics. Accuracy/AP are raw [0,1] fractions, displayed as percentages; R² is a finite raw coefficient at most 1 and has no arbitrary lower bound. Paper tables display all metrics at 100 times their canonical scale, including R²: 62.66 becomes 0.6266. Deviations use the same conversion.

std is sample standard deviation (ddof=1); it needs at least two successful runs. Omit it when unavailable. Counts describe successful contributing runs, while research logs retain attempted/failed run details. The training code starts checkpoint selection at negative infinity, checks finite metrics and excludes failed runs/trials. --min_successful_runs declares the eligibility threshold before execution; it does not increase the attempt budget or manufacture a score.

| Setting | Split masks | Visibility | Train / validation / test |
| --- | --- | --- | --- |
| RL | split_masks_RL.csv | transductive | stratified 10% / 10% / 80% |
| RH | split_masks_RH.csv | transductive | stratified 50% / 25% / 25% |
| TH | split_masks_TH.csv | transductive | temporal 50% / 25% / 25% |
| THI | split_masks_TH.csv | inductive | same temporal membership as TH |

THI is an information-access setting, not a fourth split file. Training sees only TH train nodes and edges; validation sees train+validation; test sees the full graph. Every fitted encoder, imputer, feature/target transform uses training data only in THI and is then applied without refitting to later snapshots. Intersect masks with labeled nodes. Test labels never select checkpoints, hyperparameters or models. Regression evaluation must invert the fitted target transform to the original scale; PyGDataset exposes y_raw, inverse_predictions and compute_regression_metric for this purpose.

TH/THI are unavailable for city-reviews, city-roads-M, city-roads-L and web-traffic. They display N/A and validation rejects submitted values. Supported but missing cells display —. A table slice with no submitted values explains its coverage rather than showing an all-dash ranking.

The dataset release is v1, [Zenodo DOI 10.5281/zenodo.16895532](https://doi.org/10.5281/zenodo.16895532), Apache-2.0. The repository code uses MIT.

## Submission schema v2

The new Issue Form emits schema_version 2.0. Existing valid v1 records and legacy Issue bodies remain supported. v1 retains graphland_ref and global counts; the builder copies those effective counts into generated rows without rewriting source JSON. v2 separates data_release (currently v1) from evaluator_ref, an exact evaluator commit/tag or an honest attributable protocol reference when the original commit is unreported.

Each v2 result contains setting,dataset,value,num_runs with optional std,hparam_trials. Budget is per result because preprocessing/model grids vary by dataset and setting. An omitted hparam_trials means unknown, not zero. For in_context, any supplied budget must be zero. Example (schema illustration, not a benchmark claim):

~~~json
{"setting":"RL","dataset":"hm-categories","value":0.8123,"std":0.0041,"num_runs":10,"hparam_trials":30}
~~~

Model, variant, paper/code availability, submitter, provenance, source issue, method/tuning, external-data disclosure, submission date, review and notes remain required. Closed or unavailable training code is allowed: use code_availability unavailable and training_code_url null. The public schema rejects unknown properties; task/metric are derived from the catalog.

source_issue is a positive integer for author_submission and v1. An approved v2 maintainer_seeded paper record can explicitly use null when no source issue exists. Never invent an issue or experiment commit.

provenance describes introduction (author_submission or maintainer_seeded). verification describes evidence (self_reported or reproduced). approved review records format/declared-protocol review and requires an actual repository reviewer handle/date; it does not imply an independent reproduction. A real reproduced record additionally needs reproduction with HTTPS evidence_url, evaluator_ref, reproduced_by and reproduced_at. Link actual run logs/configuration/results. Validation checks this declared evidence contract; it cannot prove the experiment occurred.

JSON rejects duplicate keys at any depth, nonstandard/overflow/nonfinite numbers, illegal URL ports/credentials and invalid UTF-8 surrogate codepoints. Display text is NFC; controls, zero-width space, BOM, bidi controls, word joiner and soft hyphen are rejected. ZWNJ/ZWJ remain available for legitimate language joining and emoji sequences. Names/variants cannot contain line/tab separators.

## Downloads and browser state

leaderboard.xlsx is the primary spreadsheet download: text uses explicit OOXML string cells, numerical metrics remain numerical and no formula XML is generated. It safely preserves names beginning with formula characters. leaderboard.csv is the canonical programmatic export; do not treat arbitrary CSV text as safe for spreadsheet formula evaluation. Both exports use effective per-result counts and leave unknown budgets empty.

The stable CSV column order is submission_id,model_name,model_variant,setting,task,dataset,metric,value,std,num_runs,method_type,hparam_trials,code_availability,paper_url,code_url,provenance,verification,submitted_at,source_issue.

The URL carries setting,task,q,code,sort,order,submission so a filtered view or result dialog can be shared. Back/Forward restores state. Failed data loads offer Retry and a canonical CSV fallback. Mobile menu and dialogs contain keyboard focus; resize closes the menu and restores the page at the same 768px breakpoint used in CSS.

## Public Issue lifecycle and manual handover

1. A researcher opens the public Issue Form and accepts the protocol confirmations. Rows use setting,dataset,value,std,num_runs,hparam_trials; an empty std or budget is allowed.
2. A maintainer with current write/maintain/admin permission reviews the exact current body/title and freshly adds leaderboard-ready. Opening an issue or editing it does not generate a candidate.
3. The read-only job checks current source, permission and snapshot, parses bounded input and produces a manifest plus JSON artifact. Pending candidates are accepted for these checks; they remain unpublishable.
4. The mutation job checks the bounded artifact and re-fetches the issue, trusted label actor, current main and remote branch. Only after revalidation does it update leaderboard/issue-<number> and its one draft PR. It writes no unrelated files, never executes submitted code and never fetches submitted URLs.
5. A trusted bot marker binds issue snapshot, generated JSON hash and branch head. Exact bot ownership plus a force-with-lease guard is required for updates. Prepared/generated states allow recovery when push succeeds but PR creation fails.
6. Successful conversion consumes leaderboard-ready. Any revision requires a fresh maintainer review and removal/re-addition of the label. Closing an issue, obsolete events and ordinary races terminate neutrally. Repeated equivalent conversion errors use deduplicated notices.
7. Any manual JSON/branch change, approved/reproduced record or PR made ready for review freezes automation. Human review metadata and changed scientific results are never overwritten or carried forward. Continue edits in the PR; use a new submission/review if its results change.
8. A human reviewer records approved metadata, checks declared evidence and merges through required CI/review. Automation never approves or merges. Strict main validation refuses pending records.

A PR generated with GITHUB_TOKEN can create approval-required workflow runs on opened/synchronize/reopened events. Check the actual Actions run and approve it when GitHub requires a trusted maintainer; do not assume that the absence of a check is success. Candidate CI accepts pending records to test the draft. Publication CI remains strict. A trusted manual event can rerun validation if a generated event is suppressed.

## GitHub enforcement and deployment

CODEOWNERS names the confirmed owner @ermakov-petr for leaderboard data, tools, UI and workflows. Repository settings must enforce required PR review, stale-review dismissal, conversation resolution and the validate status check on main, including administrators; no force push or deletion. A CODEOWNERS file alone does not enable these controls. The bootstrap implementation and enforcement evidence are retained in outputs/implementation-2026-10-08.

Actions needs permission to create draft PRs; the shared GitHub setting also controls whether Actions may approve PRs. Do not disable it blindly and break generation. Branch rules and human review provide the enforcement boundary. Pages uses GitHub Actions and the github-pages environment restricted to main; add required environment reviewers when the real deployment-review team is established.

Deploy builds run without write access. The separate deployment job verifies freshness against current main before publishing. data/build-info.json reports the source commit SHA and explicit rollback status. An obsolete rerun is refused. An intentional rollback uses workflow_dispatch with rollback=true and rollback_sha set to an exact 40-character ancestor commit; it is validated and flagged publicly, not silently inferred from an old run. Ordinary push/manual deployment selects latest main. Published assets remain relative to /graphland/.

## Source maintenance

To reimport the fixed paper seeds, obtain the exact v5 HTML, inspect its SHA/version and run:

~~~sh
python scripts/leaderboard/import_paper_baselines.py --source paper-v5.html
python scripts/leaderboard/validate.py
python -m unittest discover -s tests/leaderboard
~~~

The importer fails on missing/ambiguous model rows or unrecognized cells and writes the source ledger with the file SHA. Review both JSON and original tables before merging. Different paper versions are a new review, not an implicit refresh.

Official resources: [paper](https://arxiv.org/abs/2409.14500), [official code](https://github.com/yandex-research/graphland), [non-GNN baselines](https://github.com/gvbazhenov/graphland-baselines), [Zenodo](https://zenodo.org/records/16895532), [Kaggle](https://www.kaggle.com/datasets/bazhenovgleb/graphland), [PyG GraphLandDataset](https://pytorch-geometric.readthedocs.io/en/latest/generated/torch_geometric.datasets.GraphLandDataset.html).
