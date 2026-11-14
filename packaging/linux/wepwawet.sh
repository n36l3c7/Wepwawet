#!/bin/sh
# Wepwawet launcher. -I isolates the bundled Python from the user's site-packages and PYTHON* variables.
exec /opt/wepwawet/python/bin/python3 -I -m wepwawet "$@"
