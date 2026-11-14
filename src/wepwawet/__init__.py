"""Wepwawet: measurement-based IP geolocation."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("wepwawet")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
