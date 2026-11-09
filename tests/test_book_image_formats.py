"""Book artwork and screenshot formats follow their source images."""

import asyncio
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

import pytest
from PIL import Image

from src.meta import Meta
from src.screenshot_manifest import files as manifest_files
from src.screenshot_manifest import register
from src.uploadscreens import _upload_screens


