from setuptools import setup, find_packages

setup(
    name="cxlagent",
    version="0.1.0",
    description="CXL Memory Snooping & Analysis Agent",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        "anthropic>=0.20.0",
    ],
    entry_points={
        "console_scripts": [
            "cxlagent=cxlagent.cli:main",
        ],
    },
)
