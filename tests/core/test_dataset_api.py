"""Real CPU tensor tests, optional until the research dependencies are installed."""
import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

AVAILABLE = all(importlib.util.find_spec(name) for name in ('torch', 'numpy', 'pandas', 'sklearn', 'yaml', 'torch_geometric'))
ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(AVAILABLE, 'Requires the isolated CPU PyTorch/PyG research environment.')
class DatasetAPIRealTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import numpy as np
        import torch
        from sklearn.preprocessing import PowerTransformer
        from sklearn.metrics import r2_score
        spec = importlib.util.spec_from_file_location('graphland_dataset_api_test', ROOT / 'dataset.py')
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.np, cls.torch, cls.PowerTransformer, cls.r2_score = np, torch, PowerTransformer, staticmethod(r2_score)

    def test_self_loops_include_isolated_nodes_and_return_tensor(self):
        edges = self.np.array([[0, 1], [0, 1]], dtype=self.np.int64)
        result = self.module.Dataset.get_pyg_edge_index(edges, num_nodes=3, add_self_loops=True)
        self.assertTrue(self.torch.is_tensor(result))
        self.assertEqual(result.ndim, 2)
        pairs = result.T.tolist()
        for node in range(3): self.assertEqual(pairs.count([node, node]), 1)
        self.assertEqual(pairs.count([0, 1]), 1)
        self.assertEqual(pairs.count([1, 0]), 1)
        result.to('cpu')

    def test_empty_induced_edges_keep_integer_2d_shape(self):
        edges = self.module.Dataset.get_induced_subgraph_edges(self.np.array([[0, 1]]), [2])
        self.assertEqual(edges.shape, (0, 2))
        self.assertTrue(self.np.issubdtype(edges.dtype, self.np.integer))
        graph = self.module.Dataset.get_pyg_edge_index(edges, 1, add_self_loops=True)
        self.assertEqual(graph.tolist(), [[0], [0]])

    def test_wrapper_exposes_raw_targets_and_inverse_metric_for_nonlinear_transform(self):
        raw = self.np.array([1., 4., 9.])
        transform = self.PowerTransformer().fit(raw[:, None])
        torch = self.torch
        underlying = SimpleNamespace(name='fixture', split='RL', transductive=True, task='regression',
            features=torch.zeros(3, 2), targets=torch.tensor(transform.transform(raw[:, None]).ravel()),
            graph=torch.tensor([[0, 1], [1, 0]]), train_mask=torch.tensor([True, True, False]),
            val_mask=torch.tensor([False, False, True]), test_mask=torch.tensor([True, True, True]),
            numerical_features_mask=torch.tensor([True, False]), fraction_features_mask=torch.tensor([False, False]),
            categorical_features_mask=torch.tensor([False, True]), regression_targets_transform=transform, targets_orig=raw)
        with patch.object(self.module, 'Dataset', return_value=underlying):
            wrapper = self.module.PyGDataset('fixture')
        predictions_raw = self.np.array([1., 4., 4.])
        predictions = torch.tensor(transform.transform(predictions_raw[:, None]).ravel())
        self.np.testing.assert_allclose(wrapper.inverse_predictions(predictions), predictions_raw)
        self.np.testing.assert_array_equal(wrapper[0].y_raw.numpy(), raw)
        self.assertAlmostEqual(wrapper.compute_regression_metric(predictions), self.r2_score(raw, predictions_raw))
        self.assertIs(wrapper.to('cpu'), wrapper)


if __name__ == '__main__': unittest.main()
