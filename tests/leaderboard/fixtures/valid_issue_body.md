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

### GraphLand release, tag, or commit

v1

### Method type

trained

### Hyperparameter trials

4

### Tuning protocol

Test fixture only: select hyperparameters using validation data.

### Number of runs or seeds

3

### External data or pretraining

None. This is synthetic test metadata.

### Results

```csv
setting,dataset,value,std
RL,hm-categories,0.8123,0.0041
RH,web-fraud,0.5942,0.0060
THI,web-topics,0.7740,
RL,hm-prices,-0.125,0.02
```

### Additional notes

Synthetic parser fixture; never publish as a benchmark result.

### Confirmations

- [x] I used only the official GraphLand datasets and splits.
- [x] I did not use test labels for training or hyperparameter tuning.
- [x] I followed the information-access protocol for every reported setting.
- [x] I confirm that these submission details and results may be published.
- [x] I understand that the results will be marked as self-reported unless independently reproduced.
- [x] I have not included secrets or confidential data in this public issue.
