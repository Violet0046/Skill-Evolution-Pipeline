"""Tests for the logging system."""
from __future__ import annotations

import logging

from skill_evolution.utils.logging import Logger


class TestLogger:
    def test_get_logger_returns_same_instance(self):
        logger1 = Logger.get_logger("test.module")
        logger2 = Logger.get_logger("test.module")
        assert logger1 is logger2

    def test_get_logger_default_name(self):
        logger = Logger.get_logger()
        assert logger.name == "skill_evolution"

    def test_set_debug_levels(self):
        Logger.set_debug(0)
        Logger.set_debug(1)
        Logger.set_debug(2)

    def test_reset_configuration(self):
        Logger.get_logger("test.reset")
        Logger.reset_configuration()
        # After reset, should be able to reconfigure
        Logger.configure(level=logging.WARNING)
