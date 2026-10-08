import os
from copy import deepcopy
import math
from statistics import mean, stdev
from utils import write_yaml


class Logger:
    def __init__(self, args, metric_name):
        self.save_dir = self._get_save_dir(base_dir=args.save_dir, dataset=args.dataset, name=args.name)
        self.metric_name = metric_name
        self.num_runs_with_best_hparams = args.num_runs_with_best_hparams
        self.num_runs_with_each_hparams = args.num_runs_with_each_hparams
        self.num_hparam_search_trials = args.num_hparam_search_trials
        self.min_successful_runs = getattr(args, 'min_successful_runs', 1)

        self.results = {
            'main results': None,
            'best hparams': None,
            'hparam search results': None
        }

        print(f'Info will be saved to {self.save_dir}.')
        write_yaml(vars(args), os.path.join(self.save_dir, 'args.yaml'))

    def start_main_trial(self):
        print('Starting the main trial...\n')

        if self.results['main results'] is None:
            self.results['main results'] = self._get_empty_trial_results_dict()
            num_finished_runs_with_best_hparams = 0
        else:
            num_finished_runs_with_best_hparams = self.results['main results']['num runs']
            for run_id, (val_metric, test_metric, step, successful) in enumerate(
                    zip(
                        self.results['main results'][f'val {self.metric_name} values'],
                        self.results['main results'][f'test {self.metric_name} values'],
                        self.results['main results']['best steps'],
                        self.results['main results']['successful']
                    ),
                    start=1
            ):
                print(f'Run {run_id} finished during hparam search phase. '
                      f'Best val {self.metric_name}: {self._format_metric(val_metric)}, '
                      f'corresponding test {self.metric_name}: {self._format_metric(test_metric)} '
                      f'(step {step}).')

                if not successful:
                    print('An error occured during the run!')

                print()

        return num_finished_runs_with_best_hparams

    def start_main_run(self, run_id):
        print(f'Starting run {run_id}/{self.num_runs_with_best_hparams}...')

    def finish_main_run(self, run_results):
        self._finish_run(run_results=run_results, trial_results=self.results['main results'])

    def finish_main_trial(self):
        self._print_results_summary(results=self.results['main results'])
        if not self.results['main results']['eligible for summary']:
            raise RuntimeError(f'Only {self.results["main results"]["num successful runs"]} successful finite runs; '
                               f'{self.min_successful_runs} required. Attempts and failures were saved.')

        return deepcopy(self.results['main results'])

    def start_search_phase(self):
        self.results['hparam search results'] = []

        print('\n' * 3)
        print(f'Starting hparam search ({self.num_hparam_search_trials} trials)...')
        print('\n' * 3)

    def start_search_trial(self, hparams, trial_id):
        self.results['hparam search results'].append(
            {'hparams': hparams, 'results': self._get_empty_trial_results_dict()}
        )

        print(f'Starting hparam search trial {trial_id}/{self.num_hparam_search_trials} using the following hparams:')
        print(', '.join(f'{key}={value}' for key, value in hparams.items()))
        print()

    def start_search_run(self, run_id):
        print(f'Starting run {run_id}/{self.num_runs_with_each_hparams}...')

    def finish_search_run(self, run_results):
        self._finish_run(run_results=run_results, trial_results=self.results['hparam search results'][-1]['results'])

    def finish_search_trial(self):
        self._print_results_summary(results=self.results['hparam search results'][-1]['results'])

        return deepcopy(self.results['hparam search results'][-1]['results'])

    def finish_search_phase(self):
        eligible_trials = [trial for trial in self.results['hparam search results']
                           if trial['results']['eligible for summary']]
        if not eligible_trials:
            write_yaml(self.results, os.path.join(self.save_dir, 'results.yaml'))
            raise RuntimeError('No hyperparameter trial met the minimum successful-run policy.')
        best_search_trial_info = max(
            eligible_trials, key=lambda x: x['results'][f'val {self.metric_name} mean']
        )

        self.results['best hparams'] = deepcopy(best_search_trial_info['hparams'])
        self.results['main results'] = deepcopy(best_search_trial_info['results'])

        write_yaml(self.results, os.path.join(self.save_dir, 'results.yaml'))

        print('\n' * 3)
        print('Finished hparam search.')
        print('The best hparams found are:')
        print(', '.join(f'{key}={value}' for key, value in self.results['best hparams'].items()))
        print('\n' * 10)

        return deepcopy(self.results['best hparams'])

    def _finish_run(self, run_results, trial_results):
        values = [run_results.get(f'{part} {self.metric_name}') for part in ('val', 'test')]
        finite = all(value is not None and math.isfinite(value) for value in values)
        successful = bool(run_results.get('successful') and finite and run_results.get('step') is not None)
        trial_results['num runs'] += 1
        trial_results[f'val {self.metric_name} values'].append(float(values[0]) if values[0] is not None and math.isfinite(values[0]) else None)
        trial_results[f'test {self.metric_name} values'].append(float(values[1]) if values[1] is not None and math.isfinite(values[1]) else None)
        trial_results['best steps'].append(run_results['step'])
        trial_results['successful'].append(successful)
        trial_results['failure reasons'].append(None if successful else run_results.get('failure reason') or 'Run failed or no finite checkpoint.')
        trial_results['num successful runs'] = sum(trial_results['successful'])
        trial_results['num failed runs'] = trial_results['num runs'] - trial_results['num successful runs']
        trial_results['eligible for summary'] = trial_results['num successful runs'] >= self.min_successful_runs
        for part in ('val', 'test'):
            valid_values = [value for value, valid in zip(trial_results[f'{part} {self.metric_name} values'],
                                                        trial_results['successful']) if valid]
            trial_results[f'{part} {self.metric_name} mean'] = mean(valid_values) if trial_results['eligible for summary'] else None
            trial_results[f'{part} {self.metric_name} std'] = stdev(valid_values) if trial_results['eligible for summary'] and len(valid_values) >= 2 else None

        write_yaml(self.results, os.path.join(self.save_dir, 'results.yaml'))

        print(f'Finished run {trial_results["num runs"]}. '
              f'Best val {self.metric_name}: {self._format_metric(values[0])}, '
              f'corresponding test {self.metric_name}: {self._format_metric(values[1])} '
              f'(step {run_results["step"]}).')

        if not successful:
            print('An error occured during the run!')

        print()

    def _print_results_summary(self, results):
        print(f'Finished {results["num runs"]} attempts: {results["num successful runs"]} successful, '
              f'{results["num failed runs"]} failed. Minimum required: {self.min_successful_runs}.')
        for part in ('val', 'test'):
            for statistic in ('mean', 'std'):
                print(f'{part.title()} {self.metric_name} {statistic}: '
                      f'{self._format_metric(results[f"{part} {self.metric_name} {statistic}"])}')
        print('\n' * 5)

    @staticmethod
    def _get_save_dir(base_dir, dataset, name):
        idx = 1
        save_dir = os.path.join(base_dir, dataset, f'{name}_{idx:02d}')
        while os.path.exists(save_dir):
            idx += 1
            save_dir = os.path.join(base_dir, dataset, f'{name}_{idx:02d}')

        os.makedirs(save_dir)

        return save_dir

    def _get_empty_trial_results_dict(self):
        return {
            'num runs': 0,
            'num successful runs': 0,
            'num failed runs': 0,
            'minimum successful runs': self.min_successful_runs,
            'eligible for summary': False,
            f'val {self.metric_name} mean': None,
            f'val {self.metric_name} std': None,
            f'test {self.metric_name} mean': None,
            f'test {self.metric_name} std': None,
            f'val {self.metric_name} values': [],
            f'test {self.metric_name} values': [],
            'best steps': [],
            'successful': [],
            'failure reasons': []
        }

    @staticmethod
    def _format_metric(value):
        return f'{value:.4f}' if value is not None and math.isfinite(value) else 'Not available'
