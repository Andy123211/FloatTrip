from __future__ import annotations

import tempfile
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch

from app.core import auth


class AuthSecretTests(unittest.TestCase):
    def test_local_development_secret_survives_process_restart(self):
        original_secret = auth._SECRET
        original_path = auth._LOCAL_SECRET_PATH
        try:
            with tempfile.TemporaryDirectory() as directory, patch.dict(
                "os.environ", {"JWT_SECRET": ""}
            ):
                auth._LOCAL_SECRET_PATH = Path(directory) / ".jwt-secret"
                auth._SECRET = None
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    first = auth._get_secret()
                auth._SECRET = None
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    second = auth._get_secret()

                self.assertEqual(first, second)
                self.assertEqual(auth._LOCAL_SECRET_PATH.stat().st_mode & 0o777, 0o600)
        finally:
            auth._SECRET = original_secret
            auth._LOCAL_SECRET_PATH = original_path
