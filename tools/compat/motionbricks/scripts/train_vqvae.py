"""Compatibility entry; use `python -m training.pretrain.train_unreal --model vqvae`."""
import runpy
import sys

if __name__ == "__main__":
    sys.argv[1:1] = ["--model", "vqvae"]
    runpy.run_module("training.pretrain.train_unreal", run_name="__main__")
else:
    from training.pretrain.train_unreal import *
