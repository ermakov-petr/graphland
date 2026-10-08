"""Protocol/control-flow tests run without a GPU or optional research dependencies."""
import ast
import argparse
import math
import os
import tempfile
import sys
import unittest
from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from statistics import mean, stdev
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def load_source(filename, injected):
    tree = ast.parse((ROOT / filename).read_text())
    tree.body = [node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom))]
    namespace = dict(injected)
    exec(compile(tree, filename, 'exec'), namespace)
    return namespace


class Progress:
    def __init__(self, **kwargs): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def update(self): pass
    def set_postfix(self, value): pass


class TrainingProtocolTests(unittest.TestCase):
    def run_sequence(self, inductive, sequence, max_steps=None):
        from traceback import format_exc
        namespace = load_source('train_loops.py', {'math': math, 'format_exc': format_exc, 'tqdm': Progress,
            'torch': SimpleNamespace(no_grad=lambda: lambda function: function)})
        namespace['prepare_for_training'] = lambda **kwargs: (None, None, None)
        namespace['train_step_full_graph_transductive'] = lambda **kwargs: None
        namespace['train_step_full_graph_inductive'] = lambda **kwargs: None
        iterator = iter(sequence)
        previous_bests = []
        def evaluate(**kwargs):
            if inductive: previous_bests.append(kwargs['best_prev_val_metric'])
            value = next(iterator)
            if isinstance(value, Exception): raise value
            return value
        namespace['evaluate_full_graph_transductive'] = evaluate
        namespace['evaluate_full_graph_inductive'] = evaluate
        model = SimpleNamespace(cpu=lambda: None)
        args = SimpleNamespace(max_steps=len(sequence) if max_steps is None else max_steps, amp=False, early_stopping=-1)
        results = namespace['_train_full_graph'](model, SimpleNamespace(metric_name='R2'), args, 1, inductive)
        return results, previous_bests

    def test_negative_scores_select_validation_max_in_both_protocols(self):
        sequence = [{'val R2': -.5, 'test R2': -.7}, {'val R2': -.2, 'test R2': -.9}, {'val R2': -.4, 'test R2': .8}]
        for inductive in (False, True):
            with self.subTest(inductive=inductive):
                result, previous = self.run_sequence(inductive, sequence)
                self.assertTrue(result['successful'])
                self.assertEqual((result['val R2'], result['test R2'], result['step']), (-.2, -.9, 2))
                if inductive: self.assertEqual(previous, [-math.inf, -.5, -.2])

    def test_failure_after_checkpoint_is_excluded_and_retains_diagnostics(self):
        result, _ = self.run_sequence(False, [{'val R2': -.2, 'test R2': -.3}, RuntimeError('fixture failure')])
        self.assertFalse(result['successful'])
        self.assertEqual(result['step'], 1)
        self.assertIn('fixture failure', result['failure reason'])

    def test_nonfinite_or_no_checkpoint_never_fabricates_zero(self):
        for sequence in ([{'val R2': math.nan, 'test R2': .4}], [{'val R2': .2, 'test R2': math.inf}], []):
            result, _ = self.run_sequence(True, sequence)
            self.assertFalse(result['successful'])
            self.assertIsNone(result['val R2'])
            self.assertIsNone(result['test R2'])
            self.assertIsNone(result['step'])


class AggregationTests(unittest.TestCase):
    def make_logger(self, minimum=1):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.saved = []
        namespace = load_source('logger.py', {'os': os, 'deepcopy': deepcopy, 'math': math, 'mean': mean, 'stdev': stdev,
            'write_yaml': lambda data, path: self.saved.append(deepcopy(data))})
        args = SimpleNamespace(save_dir=self.directory.name, dataset='fixture', name='audit', num_runs_with_best_hparams=3,
                               num_runs_with_each_hparams=1, num_hparam_search_trials=2, min_successful_runs=minimum)
        return namespace['Logger'](args, 'R2')

    @staticmethod
    def result(val, test, successful=True):
        return {'val R2': val, 'test R2': test, 'step': 1 if val is not None else None, 'successful': successful}

    def test_failed_attempts_retained_but_not_averaged(self):
        logger = self.make_logger()
        logger.start_main_trial()
        logger.finish_main_run(self.result(.2, .3))
        logger.finish_main_run(self.result(.9, .99, False))
        logger.finish_main_run(self.result(.4, .5))
        result = logger.finish_main_trial()
        self.assertEqual((result['num runs'], result['num successful runs'], result['num failed runs']), (3, 2, 1))
        self.assertAlmostEqual(result['val R2 mean'], .3)
        self.assertAlmostEqual(result['test R2 mean'], .4)
        self.assertAlmostEqual(result['val R2 std'], math.sqrt(.02))
        self.assertEqual(result['successful'], [True, False, True])
        self.assertEqual(len(result['failure reasons']), 3)
        self.assertEqual(self.saved[-1]['main results']['num failed runs'], 1)

    def test_single_success_has_mean_without_std(self):
        logger = self.make_logger()
        logger.start_main_trial()
        logger.finish_main_run(self.result(-.2, -.3))
        result = logger.finish_main_trial()
        self.assertEqual(result['test R2 mean'], -.3)
        self.assertIsNone(result['test R2 std'])

    def test_no_eligible_runs_fail_without_numeric_summary(self):
        logger = self.make_logger(2)
        logger.start_main_trial()
        logger.finish_main_run(self.result(.2, .3))
        logger.finish_main_run(self.result(math.nan, .4))
        with self.assertRaises(RuntimeError): logger.finish_main_trial()
        self.assertIsNone(logger.results['main results']['val R2 mean'])

    def test_search_never_selects_failed_trial(self):
        logger = self.make_logger()
        logger.start_search_phase()
        logger.start_search_trial({'lr': .1}, 1)
        logger.finish_search_run(self.result(.99, .99, False))
        logger.start_search_trial({'lr': .2}, 2)
        logger.finish_search_run(self.result(-.3, -.2))
        self.assertEqual(logger.finish_search_phase(), {'lr': .2})


class OptunaContractTests(unittest.TestCase):
    def test_current_argument_names_and_failed_trial_state(self):
        class Distribution: pass
        class IntDistribution(Distribution): pass
        told = []
        study = SimpleNamespace(enqueue_trial=lambda value: None, ask=lambda distributions: SimpleNamespace(params={'lr': .1}),
                                tell=lambda **kwargs: told.append(kwargs))
        optuna = SimpleNamespace(distributions=SimpleNamespace(BaseDistribution=Distribution, IntDistribution=IntDistribution),
                                 samplers=SimpleNamespace(TPESampler=lambda **kwargs: None),
                                 create_study=lambda **kwargs: study, trial=SimpleNamespace(TrialState=SimpleNamespace(FAIL='FAIL')))
        namespace = load_source('hparam_generators.py', {'ABC': ABC, 'abstractmethod': abstractmethod, 'deepcopy': deepcopy,
                                                        'product': product, 'math': math, 'optuna': optuna})
        generator = namespace['OptunaHparamGenerator'](SimpleNamespace(num_optuna_trials=3, lr=Distribution()))
        self.assertEqual(len(generator), 3)
        self.assertEqual(generator.start_trial(), {'lr': .1})
        generator.finish_trial(None)
        self.assertEqual(told[-1]['state'], 'FAIL')
        generator.start_trial()
        generator.finish_trial(-.2)
        self.assertEqual(told[-1]['values'], -.2)


class ArgumentPolicyTests(unittest.TestCase):
    def parse(self, argv):
        namespace = load_source('args.py', {'os': os, 'argparse': argparse, 'dataclass': dataclass,
                                          'BaseDistribution': type('Distribution', (), {}),
                                          'read_yaml': lambda path: {}})
        with patch.object(sys, 'argv', ['main.py', *argv]):
            return namespace['get_args']()

    def test_default_policy_and_public_optuna_trial_name(self):
        args = self.parse([])
        self.assertEqual(args.min_successful_runs, 1)
        self.assertEqual(args.num_optuna_trials, 100)

    def test_policy_cannot_exceed_attempt_budget(self):
        for argv in (['--min_successful_runs', '0'], ['--num_runs_with_best_hparams', '0'],
                     ['--num_runs_with_best_hparams', '1', '--min_successful_runs', '2'],
                     ['--lr', '.1', '.2', '--num_runs_with_each_hparams', '1', '--min_successful_runs', '2']):
            with self.subTest(argv=argv), self.assertRaises(SystemExit): self.parse(argv)

    def test_fixed_main_can_require_two_successes(self):
        self.assertEqual(self.parse(['--min_successful_runs', '2', '--num_runs_with_best_hparams', '3']).min_successful_runs, 2)


if __name__ == '__main__': unittest.main()
