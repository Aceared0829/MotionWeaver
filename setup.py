"""Install MotionWeaver and the data, training and inference packages."""
from pathlib import Path

from setuptools import find_packages, setup

ROOT = Path(__file__).parent
METADATA = ROOT / '.build/python'
METADATA.mkdir(parents=True, exist_ok=True)
package_roots = {
    'motionweaver': 'model/MotionWeaver',
    'motionbricks': 'tools/compat/motionbricks',
    'data': 'data',
    'training': 'training',
    'inference': 'inference',
    'benchmark': 'benchmark',
}
packages = {}
for name, folder in package_roots.items():
    packages[name] = folder
    for child in find_packages(str(ROOT / folder)):
        packages[f'{name}.{child}'] = f'{folder}/{child.replace(".", "/")}'

setup(
    name='motionweaver', version='0.1.0', python_requires='>=3.10',
    options={'egg_info': {'egg_base': str(METADATA)}},
    packages=list(packages), package_dir=packages,
    package_data={'data': ['skeletons/*.json', 'skeletons/mappings/*.json', 'annotations/*.json'],
                  'data.tools.review': ['review_web/*'],
                  'training': ['configs/pretrain/*.yaml', 'configs/unreal/*.json'],
                  'benchmark': ['manifest/*.json']},
    install_requires=['torch>=2.0', 'numpy', 'einops', 'scipy', 'hydra-core', 'omegaconf',
                      'vector-quantize-pytorch', 'colorlog'],
    entry_points={'console_scripts': [
        'mw-benchmark = benchmark.run:main',
        'mw-review = data.tools.review.motion_review_server:main',
        'mw-train-refpose = training.pretrain.train_conditioned_pose:main',
        'mw-export-unreal = inference.export.export_unreal_inference:main',
    ]},
    extras_require={
        'training': ['pytorch-lightning', 'transformers<5', 'adam-atan2-pytorch', 'matplotlib', 'imageio', 'pillow'],
        'demo': ['pytorch-lightning', 'transformers<5', 'adam-atan2-pytorch', 'mujoco>=3.0', 'pynput',
                 "keyboard; platform_system == 'Windows'", "python-xlib; platform_system == 'Linux'"],
        'export': ['onnx>=1.16', 'onnxruntime>=1.18'],
        'dev': ['pytest>=8', 'ruff>=0.17,<0.18', 'pre-commit>=3'],
    },
)
