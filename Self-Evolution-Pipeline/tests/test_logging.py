"""日志系统测试 — 验证 Logger 单例、级别控制、文件缓存。

=== Logger 设计 ===

  Logger 是一个线程安全的单例门面:
  - 第一次调用 get_logger() 时自动 configure()
  - 所有后续 logger 共享同一套 handlers
  - _log_file_path 缓存: 同一次运行只生成一个日志文件

=== 关键行为 ===

  1. get_logger("name") 返回同一个 logger 实例 (单例)
  2. set_debug(2) = DEBUG, set_debug(1) = INFO, set_debug(0) = WARNING
  3. reset_configuration() 清除所有 handlers 和缓存 (用于测试隔离)
  4. _default_log_file() 缓存路径，避免创建多个空文件
"""
from __future__ import annotations

import logging

import pytest

from skill_evolution.utils.logging import Logger


# ═══════════════════════════════════════════════════════════════════════════════
# 单例行为
# ═══════════════════════════════════════════════════════════════════════════════

class TestLogger:
    """验证 Logger 的单例和配置行为。"""

    def test_get_logger_returns_same_instance(self):
        """相同 name 的 get_logger() 应该返回同一个 logger 实例。"""
        Logger.reset_configuration()
        logger1 = Logger.get_logger("test_singleton")
        logger2 = Logger.get_logger("test_singleton")
        assert logger1 is logger2

    def test_get_logger_default_name(self):
        """不传 name 时应该返回根 logger (skill_evolution)。"""
        Logger.reset_configuration()
        logger = Logger.get_logger()
        assert logger.name == "skill_evolution"

    def test_set_debug_levels(self):
        """set_debug() 应该正确设置日志级别。

        - set_debug(2) → DEBUG (最详细)
        - set_debug(1) → INFO
        - set_debug(0) → WARNING (最少)

        注意: Logger.configure(attach_to_root=True) 时，handler 级别被设置，
        但 logger 自身的 level 可能不变。我们检查 handler 的 level。
        """
        Logger.reset_configuration()
        Logger.set_debug(2)
        logger = Logger.get_logger()
        # 检查 handler 的 level (attach_to_root 时 handler 在 root logger 上)
        root = logging.getLogger()
        handler_levels = [h.level for h in root.handlers]
        assert any(level == logging.DEBUG for level in handler_levels)

        Logger.reset_configuration()
        Logger.set_debug(1)
        logger = Logger.get_logger()
        root = logging.getLogger()
        handler_levels = [h.level for h in root.handlers]
        assert any(level == logging.INFO for level in handler_levels)

    def test_reset_configuration(self):
        """reset_configuration() 应该清除所有状态。

        这是测试隔离的关键 — 每个测试前后的 autouse fixture 调用它。
        """
        Logger.configure(level=logging.DEBUG)
        Logger.reset_configuration()
        assert Logger._configured is False
        assert Logger._log_file_path is None
