"""Structured logging system — adapted from OpenSpace utils/logging.py.

Thread-safe singleton Logger facade with:
- Colored console output (ANSI)
- Real-time file flush
- 3-tier debug levels (0=WARNING, 1=INFO, 2=DEBUG)
- Per-script log file organization
"""
from __future__ import annotations

import logging
import os
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional


def _supports_color() -> bool:
    """Check if the terminal supports ANSI colors."""
    return (
        hasattr(sys.stdout, "isatty")
        and sys.stdout.isatty()
        and not os.getenv("NO_COLOR")
    )


class ColoredFormatter(logging.Formatter):
    """Formatter that adds ANSI color codes per log level."""

    COLORS = {
        "DEBUG": "\033[1;36m",     # Bold cyan
        "INFO": "\033[1;32m",      # Bold green
        "WARNING": "\033[1;33m",   # Bold yellow
        "ERROR": "\033[1;31m",     # Bold red
        "CRITICAL": "\033[1;35m",  # Bold magenta
        "RESET": "\033[0m",
    }

    def format(self, record: logging.LogRecord) -> str:
        formatted = super().format(record)
        level_color = self.COLORS.get(record.levelname, self.COLORS["RESET"])
        return f"{level_color}{formatted}{self.COLORS['RESET']}"


class FlushFileHandler(logging.FileHandler):
    """File handler that flushes after each emit for real-time logging."""

    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        self.flush()


class Logger:
    """Thread-safe logger facade with lazy initialization.

    First call to ``get_logger()`` triggers ``configure()`` automatically.
    All subsequent loggers inherit the configured handlers and level.
    """

    _ROOT_NAME = "skill_evolution"
    _LOG_FORMAT = (
        "%(asctime)s.%(msecs)03d [%(levelname)-8s] %(filename)s:%(lineno)d - %(message)s"
    )
    _DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

    _lock = threading.Lock()
    _configured = False
    _registered: dict[str, logging.Logger] = {}
    _log_file_path: Optional[str] = None  # Cache the log file path

    @classmethod
    def get_logger(cls, name: Optional[str] = None) -> logging.Logger:
        """Return a logger with *name*. First call triggers configure()."""
        if name is None:
            name = cls._ROOT_NAME

        need_config = False
        with cls._lock:
            logger = cls._registered.get(name)
            if logger is None:
                logger = logging.getLogger(name)
                logger.propagate = True
                cls._registered[name] = logger
            if not cls._configured:
                need_config = True

        if need_config:
            cls.configure()
        return logger

    @classmethod
    def configure(
        cls,
        *,
        level: Optional[int] = None,
        fmt: Optional[str] = None,
        log_to_console: bool = True,
        log_to_file: Optional[str] = "auto",
        use_colors: bool = True,
        force_color: bool = False,
        force: bool = False,
        attach_to_root: bool = False,
    ) -> None:
        """Configure the logging system.

        Args:
            level: Log level (logging.DEBUG, logging.INFO, etc.)
            fmt: Custom format string
            log_to_console: Whether to output to console
            log_to_file: "auto" for auto-generated path, None to disable, or explicit path
            use_colors: Whether to use ANSI colors on console
            force_color: Force colors even if terminal doesn't support them
            force: Force reconfiguration
            attach_to_root: Attach handlers to root logger
        """
        with cls._lock:
            if cls._configured and not force:
                if level is not None:
                    cls._update_level(level)
                return

            resolved_level = level if level is not None else cls._get_env_level()
            fmt_str = fmt or cls._LOG_FORMAT

            actual_log_file = None
            if log_to_file == "auto":
                actual_log_file = cls._default_log_file()
            elif log_to_file is not None:
                actual_log_file = log_to_file

            target = (
                logging.getLogger() if attach_to_root
                else logging.getLogger(cls._ROOT_NAME)
            )
            target.setLevel(resolved_level)

            # Close old handlers before removing to avoid file handle leaks
            for h in target.handlers[:]:
                target.removeHandler(h)
                h.close()

            color_ok = force_color or (use_colors and _supports_color())
            console_fmt = (
                ColoredFormatter(fmt_str, datefmt=cls._DATE_FORMAT) if color_ok
                else logging.Formatter(fmt_str, datefmt=cls._DATE_FORMAT)
            )
            file_fmt = logging.Formatter(fmt_str, datefmt=cls._DATE_FORMAT)

            if log_to_console:
                ch = logging.StreamHandler(sys.stdout)
                ch.setLevel(resolved_level)
                ch.setFormatter(console_fmt)
                target.addHandler(ch)

            if actual_log_file:
                dir_path = os.path.dirname(actual_log_file)
                if dir_path:
                    os.makedirs(dir_path, exist_ok=True)
                fh = FlushFileHandler(actual_log_file, encoding="utf-8")
                fh.setLevel(resolved_level)
                fh.setFormatter(file_fmt)
                target.addHandler(fh)

            cls._configured = True

    @classmethod
    def set_level(cls, level: str) -> None:
        """Set log level by name (e.g. 'DEBUG', 'INFO')."""
        resolved = getattr(logging, level.upper(), None)
        if resolved is None or not isinstance(resolved, int):
            raise ValueError(f"Unknown log level: {level!r}")
        if not cls._configured:
            cls.configure(level=resolved, attach_to_root=True)
            return
        cls._update_level(resolved)

    @classmethod
    def set_debug(cls, debug_level: int = 2) -> None:
        """Switch debug level: 0=WARNING, 1=INFO, 2=DEBUG."""
        level = {2: logging.DEBUG, 1: logging.INFO}.get(max(0, min(debug_level, 2)), logging.WARNING)
        if not cls._configured:
            cls.configure(level=level, attach_to_root=True)
            return
        cls._update_level(level)

    @classmethod
    def reset_configuration(cls) -> None:
        """Remove all handlers and clear registered loggers (for testing)."""
        with cls._lock:
            for lg in cls._registered.values():
                for h in lg.handlers[:]:
                    lg.removeHandler(h)
                    h.close()  # Close handler to release file handles
            # Also close root logger handlers
            root = logging.getLogger()
            for h in root.handlers[:]:
                root.removeHandler(h)
                h.close()
            cls._registered.clear()
            cls._configured = False
            cls._log_file_path = None  # Clear cached path

    @classmethod
    def _default_log_file(cls) -> str:
        """Generate default log file path organized by script name.

        Returns a cached path after the first call to avoid creating
        multiple empty log files.
        Skips file creation when running under pytest.
        """
        if cls._log_file_path:
            return cls._log_file_path

        # Skip log file creation during tests (pytest sets __main__.__file__ to its own path)
        try:
            import __main__
            main_file = getattr(__main__, "__file__", "") or ""
            if "pytest" in main_file or "pytest" in sys.modules:
                return ""
        except Exception:
            pass

        script_name = "skill_evolution"
        try:
            import __main__
            if hasattr(__main__, "__file__") and __main__.__file__:
                script_name = Path(__main__.__file__).stem
        except Exception:
            pass

        log_dir = Path(__file__).parent.parent.parent / "logs" / script_name
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        cls._log_file_path = str(log_dir / f"pipeline_{timestamp}.log")
        return cls._log_file_path

    @classmethod
    def _get_env_level(cls) -> int:
        """Resolve log level from environment variables."""
        env = os.getenv("SKILL_EVOLUTION_DEBUG") or os.getenv("DEBUG")
        if env is not None:
            try:
                val = int(env)
                return {2: logging.DEBUG, 1: logging.INFO}.get(val, logging.WARNING)
            except ValueError:
                if env.strip().lower() in ("1", "true", "yes"):
                    return logging.DEBUG
        return logging.INFO

    @classmethod
    def _update_level(cls, level: int) -> None:
        for lg in cls._registered.values():
            lg.setLevel(level)
            for h in lg.handlers:
                h.setLevel(level)


# Auto-configure from environment
_env_debug = os.getenv("SKILL_EVOLUTION_DEBUG") or os.getenv("DEBUG")
if _env_debug is not None:
    try:
        Logger.set_debug(int(_env_debug))
    except ValueError:
        Logger.set_debug(2 if _env_debug.strip().lower() in ("1", "true", "yes") else 0)

Logger.configure(attach_to_root=True)
logger = Logger.get_logger()
logger.debug("Skill Evolution Pipeline logging initialized")
