### Model name

Fixture Model

### Model variant or version

test-only

### GitHub username

fixture-user

### Paper URL

https://example.test/paper

### Code availability

available

### Training code URL

https://example.test/code

### GraphLand data release

v1

### Evaluator code reference

https://github.com/yandex-research/graphland/tree/7246fe3

### Method type

trained

### Tuning protocol

Test fixture: validation selection with disclosed per-result counts.

### External data or pretraining

None. Synthetic metadata.

### Results

```csv
setting,dataset,value,std,num_runs,hparam_trials
RL,hm-categories,0.8123,0.0041,10,20
RH,web-fraud,0.5942,0.006,5,
THI,web-topics,0.774,,1,
RL,hm-prices,-0.125,0.02,5,10
```

### Additional notes

Synthetic fixture, never publish as benchmark results.

### Confirmations

- [x] I used only the official GraphLand datasets and splits.
- [x] I did not use test labels for training or hyperparameter tuning.
- [x] I followed the information-access protocol for every reported setting.
- [x] I confirm that these submission details and results may be published.
- [x] I understand that the results will be marked as self-reported unless independently reproduced.
- [x] I have not included secrets or confidential data in this public issue.
