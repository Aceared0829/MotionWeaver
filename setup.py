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
                  'data.tools': ['review_web/*'], 'training': ['configs/pretrain/*.yaml']},
    install_requires=['torch>=2.0', 'numpy', 'einops', 'scipy', 'hydra-core', 'omegaconf',
                      'vector-quantize-pytorch', 'colorlog'],
    extras_require={
        'training': ['pytorch-lightning', 'transformers<5', 'adam-atan2-pytorch', 'matplotlib', 'imageio', 'pillow'],
        'demo': ['pytorch-lightning', 'transformers<5', 'adam-atan2-pytorch', 'mujoco>=3.0', 'pynput',
                 "keyboard; platform_system == 'Windows'", "python-xlib; platform_system == 'Linux'"],
    },
)
