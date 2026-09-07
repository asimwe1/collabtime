#!/usr/bin/env python3
"""
Setup script for Warehouse Distributed Shared Memory System
"""

from setuptools import setup, find_packages
from pathlib import Path

# Read README file
this_directory = Path(__file__).parent
long_description = (this_directory / "README.md").read_text(encoding='utf-8')

# Read requirements
requirements = []
with open('requirements.txt', 'r') as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith('#'):
            # Remove version constraints and comments for basic requirements
            req = line.split('>=')[0].split('==')[0].split('#')[0].strip()
            if req and not any(req.startswith(skip) for skip in ['asyncio', 'concurrent.futures', 'multiprocessing']):
                requirements.append(line)

setup(
    name="warehouse-dsm",
    version="0.1.0",
    author="Warehouse DSM Team",
    author_email="warehouse-dsm@example.com",
    description="Distributed Shared Memory System for Warehouse Robotics",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/warehouse-dsm/warehouse-dsm",
    packages=find_packages(),
    py_modules=["app", "config"],
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Science/Research",
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
        "Topic :: System :: Distributed Computing",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
    ],
    python_requires=">=3.8",
    install_requires=requirements,
    extras_require={
        "dev": [
            "pytest>=7.4.0",
            "pytest-cov>=4.1.0",
            "pytest-asyncio>=0.21.0",
            "black>=23.7.0",
            "flake8>=6.0.0",
            "mypy>=1.5.0",
        ],
        "docs": [
            "sphinx>=7.1.0",
            "sphinx-rtd-theme>=1.3.0",
            "sphinx-autodoc-typehints>=1.24.0",
        ],
        "performance": [
            "numba>=0.57.0",
            "cython>=3.0.0",
        ],
        "distributed": [
            "redis>=4.6.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "warehouse-dsm-run=experiments.run:main",
            "warehouse-dsm-viz=app:main",
        ],
    },
    package_data={
        "experiments": [
            "*.yaml",
            "README.md",
        ],
        "lf": [
            "*.lf",
        ],
    },
    include_package_data=True,
    zip_safe=False,
    keywords="warehouse robotics distributed-systems shared-memory mesa lingua-franca",
    project_urls={
        "Bug Reports": "https://github.com/warehouse-dsm/warehouse-dsm/issues",
        "Source": "https://github.com/warehouse-dsm/warehouse-dsm",
        "Documentation": "https://warehouse-dsm.readthedocs.io/",
    },
)
