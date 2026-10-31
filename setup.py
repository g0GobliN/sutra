"""Keep setuptools' generated egg-info out of the source distribution.

``sdist.run()`` appends ``<egg_info>/SOURCES.txt`` after MANIFEST.in has
already been processed, so no MANIFEST rule can remove it. This hook filters
the release tree at the last point setuptools lets a project control it.

The repository root is the ``sutra`` package, so the build script itself would
otherwise be copied into the wheel as ``sutra/setup.py``; ``CleanBuildPy``
keeps it out while the sdist still carries it for rebuilding.
"""

from __future__ import annotations

import os

from setuptools import setup
from setuptools.command.build_py import build_py
from setuptools.command.sdist import sdist


class CleanSdist(sdist):
    def make_release_tree(self, base_dir, files):
        egg_info = self.get_finalized_command("egg_info").egg_info
        prefix = egg_info + os.sep
        files = [f for f in files if f != egg_info and not f.startswith(prefix)]
        super().make_release_tree(base_dir, files)


class CleanBuildPy(build_py):
    """The repository root is the package, so this build script must not ship."""

    def find_package_modules(self, package, package_dir):
        modules = super().find_package_modules(package, package_dir)
        return [module for module in modules if module[1] != "setup"]


setup(cmdclass={"sdist": CleanSdist, "build_py": CleanBuildPy})
