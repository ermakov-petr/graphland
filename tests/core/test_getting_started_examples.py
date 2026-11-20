"""Run the published training examples on small CPU graphs without downloads."""

import importlib.util
import io
import math
import re
import unittest
from contextlib import redirect_stdout
from html import unescape
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
AVAILABLE = all(importlib.util.find_spec(name) for name in ('torch', 'torch_geometric', 'sklearn'))


@unittest.skipUnless(AVAILABLE, 'Requires the isolated CPU PyTorch/PyG research environment.')
class GettingStartedExamplesTests(unittest.TestCase):
    def test_training_examples_use_the_right_graphs_masks_and_checkpoint(self):
        page = (ROOT / 'site/getting-started.html').read_text(encoding='utf-8')
        blocks = re.findall(r'<code class="language-python">(.*?)</code>', page, re.DOTALL)
        self.assertEqual(len(blocks), 2)
        for split, block in zip(('RL', 'THI'), blocks):
            with self.subTest(split=split):
                source = unescape(re.sub(r'<[^>]*>', '', block))
                self.check_example(split, source)

    def check_example(self, split, source):
        import sklearn.metrics as metrics
        import torch
        import torch.nn.functional as functional
        import torch_geometric.datasets as datasets
        from torch_geometric.data import Data
        from torch_geometric.nn.models import GCN

        def graph(size):
            node_ids = torch.arange(size)
            return Data(
                x=torch.stack((node_ids / size, torch.ones(size)), dim=1),
                edge_index=torch.stack((node_ids[:-1], node_ids[1:])),
                y=node_ids.float() / 3,
            )

        stages = ('train', 'val', 'test')
        graphs = [graph(4), graph(7), graph(10)] if split == 'THI' else [graph(10)] * 3
        snapshots = dict(zip(stages, graphs))
        masks = {}
        for stage, indices in zip(stages, ([0, 2], [4, 6], [7, 9])):
            snapshot = snapshots[stage]
            mask = torch.zeros(snapshot.num_nodes, dtype=torch.bool)
            mask[indices] = True
            snapshot['mask' if split == 'THI' else f'{stage}_mask'] = mask
            masks[stage] = mask

        expected_forward_stages = ['train', 'val'] * 10 + ['test']
        seen_forward_stages = []
        seen_losses = []
        seen_scores = []
        first_validation_state = None
        prediction = None
        real_forward = GCN.forward
        real_loss = functional.mse_loss
        real_score = metrics.r2_score

        def forward(model, x, edge_index):
            nonlocal first_validation_state, prediction
            stage = expected_forward_stages[len(seen_forward_stages)]
            snapshot = snapshots[stage]
            self.assertIs(x, snapshot.x)
            self.assertIs(edge_index, snapshot.edge_index)
            self.assertEqual(model.training, stage == 'train')
            self.assertEqual(torch.is_grad_enabled(), stage == 'train')
            if stage == 'val' and first_validation_state is None:
                first_validation_state = {name: value.clone() for name, value in model.state_dict().items()}
            if stage == 'test':
                for name, value in model.state_dict().items():
                    torch.testing.assert_close(value, first_validation_state[name])
            output = real_forward(model, x, edge_index)
            prediction = output.detach().squeeze(-1)
            seen_forward_stages.append(stage)
            return output

        def check_selected_nodes(targets, predictions):
            stage = seen_forward_stages[-1]
            mask = masks[stage]
            torch.testing.assert_close(targets, snapshots[stage].y[mask])
            torch.testing.assert_close(predictions.detach(), prediction[mask])
            return stage

        def loss(predictions, targets):
            seen_losses.append(check_selected_nodes(targets, predictions))
            self.assertTrue(predictions.requires_grad)
            return real_loss(predictions, targets)

        def score(targets, predictions):
            stage = check_selected_nodes(targets, predictions)
            seen_scores.append(stage)
            value = real_score(targets, predictions)
            self.assertTrue(math.isfinite(value))
            # Make all validation scores negative and the first epoch the best.
            return -len(seen_scores) if stage == 'val' else value

        loaded = graphs if split == 'THI' else graphs[:1]
        with (
            patch.object(datasets, 'GraphLandDataset', return_value=loaded, create=True) as load,
            patch.object(GCN, 'forward', new=forward),
            patch.object(functional, 'mse_loss', new=loss),
            patch.object(metrics, 'r2_score', new=score),
            redirect_stdout(io.StringIO()),
        ):
            exec(compile(source, f'getting-started.html ({split})', 'exec'), {})

        load.assert_called_once_with(root='data/graphland', name='artnet-views', split=split)
        self.assertEqual(seen_forward_stages, expected_forward_stages)
        self.assertEqual(seen_losses, ['train'] * 10)
        self.assertEqual(seen_scores, ['val'] * 10 + ['test'])
