"""Unit tests for pydantic-settings env parsing (assemblage.settings)."""

import unittest
from unittest import mock

from assemblage.enums import RuntimeEnv, ScraperOutputPolicy, SupportedCompiler
from assemblage.settings import (
    BuilderSettings,
    DatabaseSettings,
    MQSettings,
    S3Settings,
    ScraperSettings,
)
from pydantic import ValidationError


class TestMQSettings(unittest.TestCase):
    def test_env_overrides(self):
        env = {
            "MQ_HOST": "broker",
            "MQ_PORT": "5673",
            "RABBITMQ_USER": "u",
            "RABBITMQ_PASS": "secret",
        }
        with mock.patch.dict("os.environ", env, clear=False):
            settings = MQSettings()
        self.assertEqual(settings.host, "broker")
        self.assertEqual(settings.port, 5673)
        self.assertEqual(settings.user, "u")
        self.assertEqual(settings.password.get_secret_value(), "secret")

    def test_guest_defaults(self):
        drop = dict.fromkeys(("MQ_HOST", "MQ_PORT", "RABBITMQ_USER", "RABBITMQ_PASS"), "")
        with mock.patch.dict("os.environ", drop, clear=False):
            for key in drop:
                import os

                os.environ.pop(key, None)
            settings = MQSettings()
        self.assertEqual(settings.host, "rabbitmq")
        self.assertEqual(settings.user, "guest")
        self.assertEqual(settings.password.get_secret_value(), "guest")

    def test_password_is_masked_in_repr(self):
        settings = MQSettings()
        self.assertNotIn("guest", repr(settings.password))


class TestDatabaseSettings(unittest.TestCase):
    def test_url_property(self):
        env = {
            "DB_HOST": "db",
            "DB_PORT": "5432",
            "POSTGRES_DATABASE": "assemblage",
            "POSTGRES_USER": "user",
            "POSTGRES_PASSWORD": "pw",
        }
        with mock.patch.dict("os.environ", env, clear=False):
            settings = DatabaseSettings()
        self.assertEqual(
            settings.url,
            "postgresql+psycopg2://user:pw@db:5432/assemblage",
        )


class TestS3Settings(unittest.TestCase):
    def test_disabled_without_host(self):
        with mock.patch.dict("os.environ", {}, clear=False):
            import os

            for key in ("S3_HOST", "S3_ACCESS_KEY", "S3_SECRET_ACCESS_KEY"):
                os.environ.pop(key, None)
            settings = S3Settings()
        self.assertFalse(settings.enabled)

    def test_enabled_requires_credentials(self):
        import os

        with mock.patch.dict("os.environ", {"S3_HOST": "minio"}, clear=False):
            for key in ("S3_ACCESS_KEY", "S3_SECRET_ACCESS_KEY"):
                os.environ.pop(key, None)
            with self.assertRaises(ValidationError):
                S3Settings()

    def test_enabled_with_credentials(self):
        env = {
            "S3_HOST": "minio",
            "S3_ACCESS_KEY": "ak",
            "S3_SECRET_ACCESS_KEY": "sk",
        }
        with mock.patch.dict("os.environ", env, clear=False):
            settings = S3Settings()
        self.assertTrue(settings.enabled)


class TestBuilderSettings(unittest.TestCase):
    def test_lowercase_and_upper_aliases(self):
        for name in ("compiler", "COMPILER"):
            env = {name: "gcc", "language": "c++", "COMPILER_FLAG": "-O3"}
            with mock.patch.dict("os.environ", env, clear=False):
                import os

                os.environ.pop("compiler" if name == "COMPILER" else "COMPILER", None)
                settings = BuilderSettings()
            self.assertEqual(settings.compiler, SupportedCompiler.GCC)
            self.assertEqual(settings.compiler_flag, "-O3")

    def test_defaults(self):
        env = {"compiler": "clang", "language": "c++"}
        with mock.patch.dict("os.environ", env, clear=False):
            import os

            os.environ.pop("COMPILER_FLAG", None)
            os.environ.pop("SAVE_ASSEMBLY", None)
            settings = BuilderSettings()
        self.assertEqual(settings.compiler_flag, "")
        self.assertTrue(settings.save_assembly)
        self.assertEqual(settings.wait_for_build_opt_minutes, 5)
        self.assertEqual(settings.binaries_root, "/binaries")


class TestScraperSettings(unittest.TestCase):
    def test_defaults(self):
        with mock.patch.dict("os.environ", {}, clear=False):
            import os

            for key in ("SCRAPER_POLICY", "GITHUB_TOKEN", "SCRAPE_DATASOURCE", "SCRAPE_QUALIFIERS"):
                os.environ.pop(key, None)
            settings = ScraperSettings()
        self.assertEqual(settings.qualifiers, {"language:c++"})
        self.assertEqual(settings.default_policy, ScraperOutputPolicy.ON_REQUEST)

    def test_scrape_qualifiers_single(self):
        env = {"SCRAPE_QUALIFIERS": "language:rust"}
        with mock.patch.dict("os.environ", env, clear=False):
            settings = ScraperSettings()
        self.assertEqual(settings.qualifiers, {"language:rust"})

    def test_scrape_qualifiers_comma_separated(self):
        env = {"SCRAPE_QUALIFIERS": "language:rust, stars:>10"}
        with mock.patch.dict("os.environ", env, clear=False):
            settings = ScraperSettings()
        self.assertEqual(settings.qualifiers, {"language:rust", "stars:>10"})

    def test_runtime_env_alias(self):
        with mock.patch.dict("os.environ", {"RUNTIME_ENV": "development"}, clear=False):
            settings = ScraperSettings()
        self.assertEqual(settings.runtime_env, RuntimeEnv.dev)
        self.assertEqual(settings.log_level, "DEBUG")


if __name__ == "__main__":
    unittest.main()
