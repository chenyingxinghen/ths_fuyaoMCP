from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from fuyao_agent.config import ENV_FILE_VARIABLE, load_memory_db_path


class ConfigTests(unittest.TestCase):
    def test_environment_memory_path_overrides_env_file(self) -> None:
        clean_env = {
            key: value
            for key, value in os.environ.items()
            if key not in {ENV_FILE_VARIABLE, "FUYAO_MEMORY_DB"}
        }
        clean_env[ENV_FILE_VARIABLE] = "test.env"
        clean_env["FUYAO_MEMORY_DB"] = ":memory:"

        with patch.dict(os.environ, clean_env, clear=True):
            with patch("fuyao_agent.config.Path.is_file", return_value=True):
                with patch("fuyao_agent.config.load_dotenv") as load_dotenv_mock:
                    self.assertEqual(":memory:", load_memory_db_path())

        load_dotenv_mock.assert_called_once()
        self.assertFalse(load_dotenv_mock.call_args.kwargs["override"])


if __name__ == "__main__":
    unittest.main()
