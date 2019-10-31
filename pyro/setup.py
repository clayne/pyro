from setuptools import setup
import os

with open(os.path.join(os.path.dirname(__file__),'..','VERSION'), 'r') as f:
    version = f.read().trim()

setup(
    name='Pyro API',
    version=version,
    description='An incremental build system for Skyrim Classic (TESV), Skyrim Special Edition (SSE), and Fallout 4 (FO4) projects',
    author='fireundubh',
    author_email='fireundubh@gmail.com',
    license='MIT License',
    packages=['pyro'],
    zip_safe=False,
    classifiers=[
        'Programming Language :: Python :: 3',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
    ],
)
