"""Real Linux DGL CPU smoke; independent of the full CUDA benchmark.

Run in the dgl-cpu CI environment. Missing DGL is a failure, never a skip.
"""
import unittest

import dgl
import torch

from model import Model


class ModelSmokeTests(unittest.TestCase):
    def test_all_model_variants_and_plr_modes_forward_backward(self):
        # A green smoke must represent the declared environment, not a silent
        # resolver downgrade or a CUDA wheel incidentally executed on CPU.
        self.assertEqual(torch.__version__.split("+")[0], "2.4.0")
        self.assertIsNone(torch.version.cuda)
        self.assertEqual(dgl.__version__, "2.4.0")
        torch.set_num_threads(1)
        torch.manual_seed(0)
        # Node 3 has no neighbors before self-loops; the node count is explicit.
        graph = dgl.graph(([0, 1, 1, 2], [1, 0, 2, 1]), num_nodes=4)
        graph = dgl.add_self_loop(graph)
        features = torch.randn(4, 4)
        self.assertEqual(graph.device.type, "cpu")
        self.assertEqual(features.device.type, "cpu")
        for name in ("ResMLP", "GCN", "GraphSAGE", "GAT", "GAT-sep", "GT", "GT-sep"):
            for use_plr, lite in ((False, False), (True, False), (True, True)):
                with self.subTest(model=name, plr=use_plr, lite=lite):
                    model = Model(
                        model_name=name, num_layers=2, features_dim=4,
                        hidden_dim=16, output_dim=2, num_heads=4,
                        hidden_dim_multiplier=1, normalization="layernorm",
                        dropout=0, use_plr=use_plr,
                        numerical_features_mask=torch.tensor([True, True, False, False]),
                        plr_frequencies_dim=4, plr_frequencies_scale=0.1,
                        plr_embedding_dim=3, use_plr_lite=lite,
                    )
                    optimizer = torch.optim.Adam(model.parameters(), lr=0.001)
                    predictions = model(graph, features)
                    self.assertEqual(tuple(predictions.shape), (4, 2))
                    self.assertTrue(torch.isfinite(predictions).all())
                    loss = torch.nn.functional.cross_entropy(predictions, torch.tensor([0, 1, 0, 1]))
                    loss.backward()
                    gradients = [p.grad for p in model.parameters() if p.requires_grad]
                    self.assertTrue(gradients)
                    self.assertTrue(all(g is not None and torch.isfinite(g).all() for g in gradients))
                    optimizer.step()
        print("DGL CPU smoke: torch=" + torch.__version__ + "; dgl=" + dgl.__version__ + "; 21 model/PLR combinations")
