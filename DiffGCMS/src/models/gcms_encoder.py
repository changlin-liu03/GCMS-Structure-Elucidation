"""SpecTUS-style spectrum encoder for GC-EI-MS peaks.

Architecture matches SpecTUS Base (12 x BartEncoderLayer, d_model=1024)
so pretrained `model.encoder.*` weights can be loaded strictly.
Mean pooling after the encoder has no SpecTUS parameters.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

import torch
import torch.nn as nn
import torch.nn.functional as F
from transformers.models.bart.configuration_bart import BartConfig
from transformers.models.bart.modeling_bart import BartEncoderLayer

SPECTUS_ENCODER_PREFIX = "model.encoder."


class SpectusLearnedPositionalEmbedding(nn.Embedding):
    """Intensity log-bin positional embedding (from SpecTUS)."""

    def __init__(self, num_embeddings: int, embedding_dim: int):
        self.offset = 2
        super().__init__(num_embeddings + self.offset, embedding_dim)

    def forward(self, positions: torch.Tensor) -> torch.Tensor:
        return super().forward(positions + self.offset)


def _expand_mask(mask: torch.Tensor, dtype: torch.dtype) -> torch.Tensor:
    bsz, src_len = mask.size()
    tgt_len = src_len
    expanded = mask[:, None, None, :].expand(bsz, 1, tgt_len, src_len).to(dtype)
    inverted = 1.0 - expanded
    return inverted.masked_fill(inverted.bool(), torch.finfo(dtype).min)


def remap_spectus_encoder_state_dict(state_dict: Dict[str, torch.Tensor]) -> Dict[str, torch.Tensor]:
    """Strip HuggingFace SpecTUS prefixes so keys match GCMSpectrumEncoder."""
    remapped = {}
    for key, value in state_dict.items():
        new_key = key
        if new_key.startswith("module."):
            new_key = new_key[len("module.") :]
        if new_key.startswith(SPECTUS_ENCODER_PREFIX):
            new_key = new_key[len(SPECTUS_ENCODER_PREFIX) :]
        elif new_key.startswith("encoder."):
            new_key = new_key[len("encoder.") :]
        remapped[new_key] = value
    return remapped


def load_encoder_state_dict(path: str) -> Dict[str, torch.Tensor]:
    """Load a single .pt state_dict or a sharded encoder directory."""
    path_obj = Path(path)
    if path_obj.is_dir():
        state: Dict[str, torch.Tensor] = {}
        shards = sorted(path_obj.glob("*.pt"))
        if not shards:
            raise FileNotFoundError(f"No *.pt shards in {path}")
        for shard in shards:
            key_file = shard.with_suffix(".key")
            if key_file.exists():
                key = key_file.read_text().strip()
            else:
                key = shard.stem
            state[key] = torch.load(str(shard), map_location="cpu", weights_only=False)
        return remap_spectus_encoder_state_dict(state)

    payload = torch.load(str(path_obj), map_location="cpu", weights_only=False)
    if isinstance(payload, dict) and "state_dict" in payload:
        payload = payload["state_dict"]
    return remap_spectus_encoder_state_dict(payload)


@dataclass
class GCMSEncoderConfig:
    d_model: int = 1024
    encoder_layers: int = 12
    encoder_heads: int = 16
    encoder_ffn: int = 4096
    dropout: float = 0.2
    attention_dropout: float = 0.0
    max_mz: int = 500
    max_log_id: int = 29


class GCMSpectrumEncoder(nn.Module):
    """Dual-channel encoder: m/z token embed + intensity position embed."""

    def __init__(self, cfg: GCMSEncoderConfig):
        super().__init__()
        self.cfg = cfg
        bart_cfg = BartConfig(
            d_model=cfg.d_model,
            encoder_layers=cfg.encoder_layers,
            encoder_attention_heads=cfg.encoder_heads,
            encoder_ffn_dim=cfg.encoder_ffn,
            dropout=cfg.dropout,
            attention_dropout=cfg.attention_dropout,
            activation_dropout=0.0,
        )
        self.embed_tokens = nn.Embedding(cfg.max_mz + 1, cfg.d_model)
        self.embed_positions = SpectusLearnedPositionalEmbedding(cfg.max_log_id + 1, cfg.d_model)
        self.layers = nn.ModuleList([BartEncoderLayer(bart_cfg) for _ in range(cfg.encoder_layers)])
        self.layernorm_embedding = nn.LayerNorm(cfg.d_model)
        self.gradient_checkpointing = False
        # Number of trailing BartEncoderLayer modules that receive gradients.
        # 0 = follow the caller's freeze / no_grad; 4 = last-4 finetune.
        self.trainable_last_n = 0

    def _run_layer(self, layer: nn.Module, hidden_states: torch.Tensor, attn_mask):
        if self.gradient_checkpointing and self.training:
            def create_custom_forward(module):
                def custom_forward(h, mask):
                    return module(h, mask, layer_head_mask=None)
                return custom_forward

            return torch.utils.checkpoint.checkpoint(
                create_custom_forward(layer),
                hidden_states,
                attn_mask,
            )
        return layer(hidden_states, attn_mask, layer_head_mask=None)[0]

    def forward(
        self,
        input_ids: torch.Tensor,
        position_ids: torch.Tensor,
        attention_mask: Optional[torch.Tensor] = None,
    ) -> Tuple[torch.Tensor, Dict[str, torch.Tensor]]:
        n_layers = len(self.layers)
        last_n = int(getattr(self, "trainable_last_n", 0) or 0)
        freeze_n = n_layers - last_n if 0 < last_n < n_layers else 0

        def stem(training: bool) -> torch.Tensor:
            hidden = self.embed_tokens(input_ids) + self.embed_positions(position_ids)
            hidden = self.layernorm_embedding(hidden)
            return F.dropout(hidden, p=self.cfg.dropout, training=training)

        if freeze_n > 0:
            with torch.no_grad():
                hidden_states = stem(training=False)
                if attention_mask is not None:
                    attn_mask = _expand_mask(attention_mask, hidden_states.dtype)
                else:
                    attn_mask = None
                for layer in self.layers[:freeze_n]:
                    hidden_states = layer(hidden_states, attn_mask, layer_head_mask=None)[0]
            hidden_states = hidden_states.detach()
            if attention_mask is not None:
                attn_mask = _expand_mask(attention_mask, hidden_states.dtype)
            else:
                attn_mask = None
            for layer in self.layers[freeze_n:]:
                hidden_states = self._run_layer(layer, hidden_states, attn_mask)
        else:
            hidden_states = stem(training=self.training)
            if attention_mask is not None:
                attn_mask = _expand_mask(attention_mask, hidden_states.dtype)
            else:
                attn_mask = None
            for layer in self.layers:
                hidden_states = self._run_layer(layer, hidden_states, attn_mask)

        if attention_mask is None:
            attention_mask = torch.ones_like(input_ids, dtype=torch.long)

        mask = attention_mask.unsqueeze(-1).float()
        pooled = (hidden_states * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1.0)
        return pooled, {"h0": pooled, "hidden_states": hidden_states}


class GCMSConditionProjector(nn.Module):
    """Map encoder output to GraphTransformer condition vector y.

    Default: Linear(d_model → hidden) → SiLU → Linear(hidden → y_dim),
    i.e. 1024 → 4096 → 1024 for SpecTUS Base.
    """

    def __init__(self, input_dim: int, output_dim: int, hidden_dim: int = 4096):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.SiLU(),
            nn.Linear(hidden_dim, output_dim),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)
