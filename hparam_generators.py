from abc import ABC, abstractmethod
from copy import deepcopy
from itertools import product
import math
import optuna


class BaseHparamGenerator(ABC):
    @abstractmethod
    def __init__(self, args):
        raise NotImplementedError

    @abstractmethod
    def __len__(self):
        raise NotImplementedError

    @abstractmethod
    def start_trial(self):
        raise NotImplementedError

    @abstractmethod
    def finish_trial(self, val_metric):
        raise NotImplementedError


class GridSearchHparamGenerator(BaseHparamGenerator):
    def __init__(self, args):
        # Get hparams that have multiple values.
        hparam_lists = {key: value for key, value in vars(args).items() if isinstance(value, (list, tuple))}

        # Dict of lists to list of dicts.
        hparam_values = product(*hparam_lists.values())
        hparam_dicts = [dict(zip(hparam_lists.keys(), values)) for values in hparam_values]

        self.hparam_dicts = hparam_dicts
        self.hparam_id = -1

    def __len__(self):
        return len(self.hparam_dicts)

    def start_trial(self):
        self.hparam_id += 1
        hparams = deepcopy(self.hparam_dicts[self.hparam_id])

        return hparams

    def finish_trial(self, val_metric):
        pass


class OptunaHparamGenerator(BaseHparamGenerator):
    """Ask/tell search; integer lr distributions use indices into lr_map.

    With an integer lr distribution, predefined lr values must also be integer
    indices in its range/step. Float lr distributions use learning rates directly.
    """
    lr_map = [1e-5, 2e-5, 3e-5, 5e-5, 7e-5, 1e-4, 2e-4, 3e-4, 5e-4, 7e-4, 1e-3, 2e-3, 3e-3, 5e-3, 7e-3, 1e-2]

    def __init__(self, args):
        distributions = {
            key: value for key, value in vars(args).items() if isinstance(value, optuna.distributions.BaseDistribution)
        }
        self.use_lr_map = (
                'lr' in distributions and isinstance(distributions['lr'], optuna.distributions.IntDistribution)
        )
        if self.use_lr_map:
            lr_distribution = distributions['lr']
            if lr_distribution.low < 0 or lr_distribution.high >= len(self.lr_map):
                raise ValueError(f'Integer lr distributions must use indices 0..{len(self.lr_map) - 1} into lr_map.')

        sampler = optuna.samplers.TPESampler(seed=0, n_startup_trials=10)
        study = optuna.create_study(sampler=sampler, direction='maximize')

        predefined = getattr(args, 'predefined_hparam_combs', None)
        if predefined is not None:
            for hparams in predefined:
                if hparams.keys() != distributions.keys():
                    raise ValueError(
                        f'The set of predefined hparams {set(hparams.keys())} does not match the set of hparams to be '
                        f'searched {set(distributions.keys())}.'
                    )

                if self.use_lr_map:
                    index = hparams['lr']
                    if (not isinstance(index, int) or isinstance(index, bool) or
                            not lr_distribution.low <= index <= lr_distribution.high or
                            (index - lr_distribution.low) % lr_distribution.step != 0):
                        raise ValueError('A predefined integer-distribution lr must be an integer index '
                                         'within the distribution bounds and step, not a learning rate.')

                study.enqueue_trial(hparams)

        self.study = study
        self.distributions = distributions
        self.num_trials = args.num_optuna_trials
        self.cur_trial = None

    def __len__(self):
        return self.num_trials

    def start_trial(self):
        self.cur_trial = self.study.ask(self.distributions)
        hparams = deepcopy(self.cur_trial.params)

        if self.use_lr_map:
            hparams['lr'] = self.lr_map[hparams['lr']]

        return hparams

    def finish_trial(self, val_metric):
        if val_metric is None or not math.isfinite(val_metric):
            self.study.tell(trial=self.cur_trial, state=optuna.trial.TrialState.FAIL)
        else:
            self.study.tell(trial=self.cur_trial, values=val_metric)


def get_hparam_generator(args):
    if args.hparam_search_strategy == 'grid-search':
        return GridSearchHparamGenerator(args)
    elif args.hparam_search_strategy == 'optuna':
        return OptunaHparamGenerator(args)
    else:
        raise ValueError(
            f'Hparam generator can only be provided if hparam_search_strategy is "grid-search" or "optuna", '
            f'but hparam_search_strategy is {args.hparam_search_strategy}.'
        )
