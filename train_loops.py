from traceback import format_exc
import math
from tqdm import tqdm
import torch
from utils import get_parameter_groups, get_lr_scheduler_with_warmup


def prepare_for_training(model, args):
    parameter_groups = get_parameter_groups(model)
    optimizer = torch.optim.AdamW(parameter_groups, lr=args.lr, weight_decay=args.weight_decay)
    gradscaler = torch.amp.GradScaler(enabled=args.amp)
    scheduler = get_lr_scheduler_with_warmup(optimizer=optimizer, num_warmup_steps=args.num_warmup_steps,
                                             num_steps=args.max_steps, warmup_proportion=args.warmup_proportion)

    return optimizer, gradscaler, scheduler


def train_step_full_graph_transductive(model, dataset, optimizer, scheduler, gradscaler, amp=False):
    model.train()

    with torch.autocast(enabled=amp, device_type=dataset.graph.device.type):
        preds = model(graph=dataset.graph, x=dataset.features)
        loss = dataset.loss_fn(input=preds[dataset.train_mask], target=dataset.targets[dataset.train_mask])

    gradscaler.scale(loss).backward()
    gradscaler.step(optimizer)
    gradscaler.update()
    optimizer.zero_grad()
    scheduler.step()


def train_step_full_graph_inductive(model, dataset, optimizer, scheduler, gradscaler, amp=False):
    model.train()

    with torch.autocast(enabled=amp, device_type=dataset.train_graph.device.type):
        preds = model(graph=dataset.train_graph, x=dataset.train_features)
        loss = dataset.loss_fn(input=preds[dataset.train_mask], target=dataset.train_targets[dataset.train_mask])

    gradscaler.scale(loss).backward()
    gradscaler.step(optimizer)
    gradscaler.update()
    optimizer.zero_grad()
    scheduler.step()


@torch.no_grad()
def evaluate_full_graph_transductive(model, dataset, amp=False):
    model.eval()

    with torch.autocast(enabled=amp, device_type=dataset.graph.device.type):
        preds = model(graph=dataset.graph, x=dataset.features)

    metrics = dataset.compute_metrics_transductive(preds)

    return metrics


@torch.no_grad()
def evaluate_full_graph_inductive(model, dataset, best_prev_val_metric, amp=False):
    model.eval()
    metrics = {}

    with torch.autocast(enabled=amp, device_type=dataset.val_graph.device.type):
        preds = model(graph=dataset.val_graph, x=dataset.val_features)

    metrics[f'val {dataset.metric_name}'] = dataset.compute_val_metric_inductive(preds)

    if math.isfinite(metrics[f'val {dataset.metric_name}']) and metrics[f'val {dataset.metric_name}'] > best_prev_val_metric:
        with torch.autocast(enabled=amp, device_type=dataset.test_graph.device.type):
            preds = model(graph=dataset.test_graph, x=dataset.test_features)

        metrics[f'test {dataset.metric_name}'] = dataset.compute_test_metric_inductive(preds)

    return metrics


def update_results(results, new_metrics, metric_name, step):
    if not all(math.isfinite(new_metrics[f'{part} {metric_name}']) for part in ('val', 'test')):
        raise ValueError('The selected checkpoint must have finite validation and test metrics.')
    results[f'val {metric_name}'] = new_metrics[f'val {metric_name}']
    results[f'test {metric_name}'] = new_metrics[f'test {metric_name}']
    results['step'] = step

    return results


def _train_full_graph(model, dataset, args, run_id, inductive):
    results = {f'val {dataset.metric_name}': None, f'test {dataset.metric_name}': None,
               'step': None, 'successful': False, 'failure reason': None}
    best_val_metric = -math.inf
    num_steps_without_val_improvement = 0
    try:
        optimizer, gradscaler, scheduler = prepare_for_training(model=model, args=args)
        with tqdm(total=args.max_steps, desc=f'Run {run_id}') as progress_bar:
            for step in range(1, args.max_steps + 1):
                train_step = train_step_full_graph_inductive if inductive else train_step_full_graph_transductive
                train_step(model=model, dataset=dataset, optimizer=optimizer, scheduler=scheduler,
                           gradscaler=gradscaler, amp=args.amp)
                if inductive:
                    metrics = evaluate_full_graph_inductive(model=model, dataset=dataset,
                                                            best_prev_val_metric=best_val_metric, amp=args.amp)
                else:
                    metrics = evaluate_full_graph_transductive(model=model, dataset=dataset, amp=args.amp)
                val_metric = metrics[f'val {dataset.metric_name}']
                if not math.isfinite(val_metric):
                    raise ValueError('Validation metric is not finite.')
                if val_metric > best_val_metric:
                    update_results(results, metrics, dataset.metric_name, step)
                    best_val_metric = val_metric
                    num_steps_without_val_improvement = 0
                else:
                    num_steps_without_val_improvement += 1
                progress_bar.update()
                progress_bar.set_postfix({metric: f'{value:.2f}' for metric, value in metrics.items()})
                if num_steps_without_val_improvement == args.early_stopping:
                    break
        if results['step'] is None:
            raise ValueError('No finite checkpoint was evaluated during this run.')
        results['successful'] = True
    except Exception:
        results['failure reason'] = format_exc()
        print(results['failure reason'])
    finally:
        model.cpu()
    return results


def train_full_graph_transductive(model, dataset, args, run_id):
    return _train_full_graph(model, dataset, args, run_id, inductive=False)


def train_full_graph_inductive(model, dataset, args, run_id):
    return _train_full_graph(model, dataset, args, run_id, inductive=True)


def train_minibatch(model, dataset, args, run_id):
    raise NotImplementedError


def get_train_fn(train_regime, transductive):
    if train_regime == 'full-graph':
        if transductive:
            return train_full_graph_transductive
        else:
            return train_full_graph_inductive

    elif train_regime == 'minibatch':
        return train_minibatch

    else:
        raise ValueError(f'Unknown train_regime: {train_regime}. Supported values are: "full-graph", "minibatch".')
