import os
import sys
import pathlib
import warnings
import logging

import torch

try:
    torch.set_float32_matmul_precision("medium")
except Exception:
    pass

# PyTorch >=2.6 defaults torch.load(weights_only=True); PL/OmegaConf ckpts need weights_only=False.
_torch_load = torch.load

def _torch_load_compat(*args, **kwargs):
    kwargs.setdefault("weights_only", False)
    return _torch_load(*args, **kwargs)

torch.load = _torch_load_compat

import hydra
from omegaconf import DictConfig
from pytorch_lightning import Trainer
from pytorch_lightning.callbacks import LearningRateMonitor, ModelCheckpoint
from pytorch_lightning.loggers import CSVLogger, WandbLogger
from pytorch_lightning.utilities.warnings import PossibleUserWarning

from src import utils
from src.diffusion_model_gcms import GCMSDenoisingDiffusion
from src.diffusion.extra_features import DummyExtraFeatures, ExtraFeatures
from src.diffusion.extra_features_molecular import ExtraMolecularFeatures
from src.metrics.molecular_metrics_discrete import TrainMolecularMetricsDiscrete
from src.analysis.gcms_visualization import GCMSMolecularVisualization
from src.datasets import gcms_dataset

warnings.filterwarnings("ignore", category=PossibleUserWarning)


@hydra.main(version_base="1.3", config_path="../configs", config_name="config_train")
def main(cfg: DictConfig):
    from rdkit import RDLogger

    RDLogger.DisableLog("rdApp.*")

    logger = logging.getLogger("gcms_main")
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s.%(msecs)03d %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    ch = logging.StreamHandler(stream=sys.stdout)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    path = os.path.join("gcms_main.log")
    fh = logging.FileHandler(path)
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    logging.info(cfg)

    project_root = pathlib.Path(__file__).resolve().parent.parent
    gcms_root = project_root.parent
    cfg.general.parent_dir = str(gcms_root)

    def _resolve_project_path(path):
        if not path:
            return path
        path = os.path.expanduser(str(path))
        if os.path.isabs(path):
            return path
        candidate = project_root / path
        return str(candidate)

    cfg.general.encoder = _resolve_project_path(cfg.general.encoder)
    if cfg.general.decoder:
        cfg.general.decoder = _resolve_project_path(cfg.general.decoder)
    if getattr(cfg.general, "load_weights", None):
        cfg.general.load_weights = _resolve_project_path(cfg.general.load_weights)
    if getattr(cfg.general, "resume", None):
        cfg.general.resume = _resolve_project_path(cfg.general.resume)
    if getattr(cfg.general, "test_only", None):
        cfg.general.test_only = _resolve_project_path(cfg.general.test_only)
    if not cfg.general.encoder or not os.path.exists(str(cfg.general.encoder)):
        raise FileNotFoundError(
            f"SpecTUS encoder weights not found: {cfg.general.encoder}. "
            "Run: python scripts/extract_spectus_encoder.py"
        )

    if cfg.dataset.name != "gcms":
        raise NotImplementedError(f"Unknown dataset {cfg.dataset.name}")

    datamodule = gcms_dataset.GCMSDataModule(cfg)
    dataset_infos = gcms_dataset.GCMSDatasetInfos(
        datamodule,
        cfg,
        recompute_statistics=getattr(cfg.dataset, "recompute_statistics", False),
    )

    domain_features = ExtraMolecularFeatures(dataset_infos=dataset_infos)
    if cfg.model.extra_features is not None:
        extra_features = ExtraFeatures(cfg.model.extra_features, dataset_info=dataset_infos)
    else:
        extra_features = DummyExtraFeatures()

    dataset_infos.compute_input_output_dims(
        datamodule=datamodule,
        extra_features=extra_features,
        domain_features=domain_features,
    )

    logging.info(f"Output dims: {dataset_infos.output_dims}")
    train_metrics = TrainMolecularMetricsDiscrete(dataset_infos)
    visualization_tools = GCMSMolecularVisualization(
        cfg.dataset.remove_h, dataset_infos=dataset_infos
    )

    model_kwargs = {
        "dataset_infos": dataset_infos,
        "train_metrics": train_metrics,
        "visualization_tools": visualization_tools,
        "extra_features": extra_features,
        "domain_features": domain_features,
    }

    os.makedirs("preds/", exist_ok=True)
    os.makedirs("logs/", exist_ok=True)
    os.makedirs("logs/" + cfg.general.name, exist_ok=True)

    model = GCMSDenoisingDiffusion(cfg=cfg, **model_kwargs)

    load_weights = getattr(cfg.general, "load_weights", None)
    if load_weights:
        if not os.path.isfile(str(load_weights)):
            raise FileNotFoundError(f"load_weights checkpoint not found: {load_weights}")
        if cfg.general.resume:
            logging.warning(
                "Both general.load_weights and general.resume are set. "
                "Ignoring resume so the last-4 run does not restore the frozen-training optimizer."
            )
            cfg.general.resume = None
        logging.info("Loading weights from %s (optimizer / epoch not restored)", load_weights)
        payload = torch.load(load_weights, map_location="cpu")
        state = payload["state_dict"] if isinstance(payload, dict) and "state_dict" in payload else payload
        incompatible = model.load_state_dict(state, strict=True)
        logging.info(
            "Loaded weights: missing=%d unexpected=%d",
            len(incompatible.missing_keys),
            len(incompatible.unexpected_keys),
        )

    callbacks = [LearningRateMonitor(logging_interval="step")]
    if cfg.train.save_model and not cfg.general.test_only:
        save_top_k = getattr(cfg.general, "save_top_k", 2)
        checkpoint_callback = ModelCheckpoint(
            dirpath=f"checkpoints/{cfg.general.name}",
            filename="{epoch}",
            monitor="val/NLL",
            save_top_k=save_top_k,
            mode="min",
            every_n_epochs=1,
        )
        callbacks.append(checkpoint_callback)
        if getattr(cfg.general, "save_last", False):
            last_ckpt_save = ModelCheckpoint(
                dirpath=f"checkpoints/{cfg.general.name}",
                filename="last",
                every_n_epochs=1,
            )
            callbacks.append(last_ckpt_save)

    loggers = [
        CSVLogger(save_dir=f"logs/{cfg.general.name}", name=cfg.general.name),
    ]
    if cfg.general.wandb != "disabled":
        loggers.append(
            WandbLogger(
                name=cfg.general.name,
                save_dir=f"logs/{cfg.general.name}",
                project=cfg.general.wandb_name,
                log_model=False,
                config=utils.cfg_to_dict(cfg),
            )
        )

    use_gpu = cfg.general.gpus > 0 and torch.cuda.is_available()
    trainer = Trainer(
        gradient_clip_val=cfg.train.clip_grad,
        accelerator="gpu" if use_gpu else "cpu",
        devices=cfg.general.gpus if use_gpu else 1,
        max_epochs=cfg.train.n_epochs,
        limit_train_batches=getattr(cfg.train, "limit_train_batches", 1.0),
        limit_val_batches=getattr(cfg.train, "limit_val_batches", 1.0),
        check_val_every_n_epoch=cfg.general.check_val_every_n_epochs,
        fast_dev_run=cfg.general.name == "debug",
        callbacks=callbacks,
        log_every_n_steps=50 if cfg.general.name != "debug" else 1,
        logger=loggers,
        precision=getattr(cfg.train, "precision", "32"),
        accumulate_grad_batches=getattr(cfg.train, "gradient_accumulation", 1),
    )

    if getattr(cfg.general, "fit", True):
        trainer.fit(model, datamodule=datamodule, ckpt_path=cfg.general.resume)
        if getattr(cfg.general, "run_test_after_fit", False):
            trainer.test(model, datamodule=datamodule, ckpt_path="best")
    else:
        if not cfg.general.test_only:
            raise ValueError(
                "Test mode requires a checkpoint: general.test_only=path/to/model.ckpt"
            )
        logging.info(f"Test-only mode, checkpoint: {cfg.general.test_only}")
        trainer.test(model, datamodule=datamodule, ckpt_path=cfg.general.test_only)


if __name__ == "__main__":
    main()
