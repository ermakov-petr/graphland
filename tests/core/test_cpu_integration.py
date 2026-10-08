"""Actual PyTorch CPU optimization and Optuna ask/tell integration; no DGL/GPU required."""
import importlib.util
import math
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
TORCH_AVAILABLE = all(importlib.util.find_spec(name) for name in ('torch', 'yaml', 'tqdm'))
OPTUNA_AVAILABLE = importlib.util.find_spec('optuna') is not None


def load_module(filename, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(TORCH_AVAILABLE, 'Requires the isolated CPU PyTorch research environment.')
class TorchCPUIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import torch
        cls.torch = torch
        utils = load_module('utils.py', 'graphland_cpu_test_utils')
        with patch.dict(sys.modules, {'utils': utils}):
            cls.loops = load_module('train_loops.py', 'graphland_cpu_test_loops')

    def toy(self, inductive):
        torch = self.torch
        class Model(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.linear = torch.nn.Linear(1, 1)
                with torch.no_grad():
                    self.linear.weight.zero_()
                    self.linear.bias.fill_(8.)
            def forward(self, graph, x): return self.linear(x).squeeze(1)
        x = torch.tensor([[-3.], [-2.], [-1.], [1.], [2.], [3.]])
        targets = x.squeeze(1).clone()
        graph = SimpleNamespace(device=torch.device('cpu'))
        def r2(predictions, target, mask):
            y, p = target[mask], predictions[mask]
            return float(1 - ((y - p) ** 2).sum() / ((y - y.mean()) ** 2).sum())
        dataset = SimpleNamespace(metric_name='R2', loss_fn=torch.nn.functional.mse_loss)
        if inductive:
            dataset.train_graph = dataset.val_graph = dataset.test_graph = graph
            dataset.train_features, dataset.val_features, dataset.test_features = x[:2], x[:4], x
            dataset.train_targets, dataset.val_targets, dataset.test_targets = targets[:2], targets[:4], targets
            dataset.train_mask = torch.tensor([True, True])
            dataset.val_mask = torch.tensor([False, False, True, True])
            dataset.test_mask = torch.tensor([False, False, False, False, True, True])
            dataset.compute_val_metric_inductive = lambda p: r2(p, dataset.val_targets, dataset.val_mask)
            dataset.compute_test_metric_inductive = lambda p: r2(p, dataset.test_targets, dataset.test_mask)
        else:
            dataset.graph, dataset.features, dataset.targets = graph, x, targets
            dataset.train_mask = torch.tensor([True, True, False, False, False, False])
            dataset.val_mask = torch.tensor([False, False, True, True, False, False])
            dataset.test_mask = torch.tensor([False, False, False, False, True, True])
            dataset.compute_metrics_transductive = lambda p: {
                f'{part} R2': r2(p, targets, getattr(dataset, f'{part}_mask')) for part in ('train', 'val', 'test')}
        args = SimpleNamespace(lr=1e-4, weight_decay=0., amp=False, max_steps=3, early_stopping=-1,
                               num_warmup_steps=0, warmup_proportion=0.)
        return Model(), dataset, args

    def test_real_forward_backward_negative_r2_both_protocols(self):
        for inductive in (False, True):
            with self.subTest(inductive=inductive):
                model, dataset, args = self.toy(inductive)
                previous = model.linear.weight.detach().clone()
                function = self.loops.train_full_graph_inductive if inductive else self.loops.train_full_graph_transductive
                result = function(model, dataset, args, 1)
                self.assertTrue(result['successful'], result['failure reason'])
                self.assertTrue(math.isfinite(result['val R2']))
                self.assertLess(result['val R2'], 0.)
                self.assertLess(result['test R2'], 0.)
                self.assertIsNotNone(result['step'])
                self.assertFalse(self.torch.equal(previous, model.linear.weight))

    def test_real_optimization_nonfinite_evaluation_is_failed(self):
        model, dataset, args = self.toy(False)
        with patch.object(self.loops, 'evaluate_full_graph_transductive', return_value={'val R2': math.nan, 'test R2': .2}):
            result = self.loops.train_full_graph_transductive(model, dataset, args, 1)
        self.assertFalse(result['successful'])
        self.assertIsNone(result['val R2'])


@unittest.skipUnless(OPTUNA_AVAILABLE, 'Requires Optuna in the isolated research environment.')
class RealOptunaIntegrationTests(unittest.TestCase):
    def test_integer_lr_distribution_rejects_map_bounds_and_predefined_indices(self):
        import optuna
        module = load_module('hparam_generators.py', 'graphland_actual_optuna_lr_bounds')
        for low, high in ((-1, 15), (0, 20)):
            with self.subTest(low=low, high=high), self.assertRaisesRegex(ValueError, 'lr.*indices'):
                module.OptunaHparamGenerator(SimpleNamespace(
                    num_optuna_trials=1, lr=optuna.distributions.IntDistribution(low, high)))
        distribution = optuna.distributions.IntDistribution(0, 14, step=2)
        for index in (-1, 16, .001, True, 3):
            with self.subTest(index=index), self.assertRaisesRegex(ValueError, 'predefined.*lr'):
                module.OptunaHparamGenerator(SimpleNamespace(
                    num_optuna_trials=1, lr=distribution, predefined_hparam_combs=[{'lr': index}]))

    def test_integer_lr_distribution_maps_valid_boundary_indices(self):
        import optuna
        module = load_module('hparam_generators.py', 'graphland_actual_optuna_lr_valid')
        generator = module.OptunaHparamGenerator(SimpleNamespace(
            num_optuna_trials=2, lr=optuna.distributions.IntDistribution(0, 15),
            predefined_hparam_combs=[{'lr': 0}, {'lr': 15}]))
        for expected in (1e-5, 1e-2):
            self.assertEqual(generator.start_trial()['lr'], expected)
            generator.finish_trial(-.2)
        self.assertEqual([trial.params['lr'] for trial in generator.study.trials], [0, 15])

    def test_three_actual_trials_complete_and_fail(self):
        import optuna
        module = load_module('hparam_generators.py', 'graphland_actual_optuna_test')
        generator = module.OptunaHparamGenerator(SimpleNamespace(
            num_optuna_trials=3, lr=optuna.distributions.FloatDistribution(1e-4, 1e-2, log=True)))
        self.assertEqual(len(generator), 3)
        for metric in (None, -.3, math.nan):
            params = generator.start_trial()
            self.assertTrue(1e-4 <= params['lr'] <= 1e-2)
            generator.finish_trial(metric)
        self.assertEqual([trial.state for trial in generator.study.trials],
                         [optuna.trial.TrialState.FAIL, optuna.trial.TrialState.COMPLETE, optuna.trial.TrialState.FAIL])
        self.assertEqual(generator.study.best_value, -.3)

    @unittest.skipUnless(TORCH_AVAILABLE, 'Actual Logger imports PyTorch utilities.')
    def test_actual_logger_serializes_distribution_valued_args(self):
        import optuna
        utils = load_module('utils.py', 'graphland_optuna_distribution_yaml_utils')
        with patch.dict(sys.modules, {'utils': utils}):
            logger_module = load_module('logger.py', 'graphland_optuna_distribution_yaml_logger')
        with tempfile.TemporaryDirectory() as directory:
            args = SimpleNamespace(save_dir=directory, dataset='fixture', name='distribution',
                num_runs_with_best_hparams=1, num_runs_with_each_hparams=1, num_hparam_search_trials=3,
                lr=optuna.distributions.FloatDistribution(1e-4, 1e-2, log=True))
            logger = logger_module.Logger(args, 'R2')
            saved = utils.read_yaml(Path(logger.save_dir) / 'args.yaml')
            self.assertEqual(saved['lr'], args.lr)

    @unittest.skipUnless(TORCH_AVAILABLE, 'Actual Logger imports PyTorch utilities.')
    def test_all_failed_search_retains_attempts_and_rejects_best_hparams(self):
        import optuna
        utils = load_module('utils.py', 'graphland_optuna_logger_utils')
        with patch.dict(sys.modules, {'utils': utils}):
            logger_module = load_module('logger.py', 'graphland_optuna_actual_logger')
        module = load_module('hparam_generators.py', 'graphland_optuna_failed_trials')
        generator = module.OptunaHparamGenerator(SimpleNamespace(
            num_optuna_trials=3, lr=optuna.distributions.FloatDistribution(1e-4, 1e-2, log=True)))
        with tempfile.TemporaryDirectory() as directory:
            args = SimpleNamespace(save_dir=directory, dataset='fixture', name='allfailed',
                num_runs_with_best_hparams=1, num_runs_with_each_hparams=1, num_hparam_search_trials=3, min_successful_runs=1)
            logger = logger_module.Logger(args, 'R2')
            logger.start_search_phase()
            for index in range(1, 4):
                logger.start_search_trial(generator.start_trial(), index)
                logger.finish_search_run({'val R2': None, 'test R2': None, 'step': None, 'successful': False,
                                          'failure reason': 'intentional fixture failure'})
                generator.finish_trial(logger.finish_search_trial()['val R2 mean'])
            with self.assertRaisesRegex(RuntimeError, 'No hyperparameter trial'): logger.finish_search_phase()
            self.assertEqual(len(generator.study.trials), 3)
            self.assertTrue(all(trial.state == optuna.trial.TrialState.FAIL for trial in generator.study.trials))
            self.assertTrue((Path(logger.save_dir) / 'results.yaml').exists())
            self.assertEqual(sum(trial['results']['num failed runs'] for trial in logger.results['hparam search results']), 3)


if __name__ == '__main__': unittest.main()
