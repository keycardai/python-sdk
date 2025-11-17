"""
Logging configuration for MCP client.

The library uses standard Python logging without forcing configuration.
Users can configure logging however they prefer, or use the environment
variables provided here for convenience.

Environment Variables:
    MCP_LOG_LEVEL: Set log level for all mcp.client loggers (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    MCP_LOG_FORMAT: Custom format string (optional)

Example usage in application code:
    import logging
    logging.basicConfig(level=logging.INFO)

    # Or with custom configuration:
    import logging
    logger = logging.getLogger('keycardai.mcp.client')
    logger.setLevel(logging.DEBUG)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    logger.addHandler(handler)
"""
import logging
import os


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger for the given module name.

    This function creates loggers that respect MCP_LOG_LEVEL environment variable
    if set, but doesn't force any configuration on the application.

    Args:
        name: Module name (usually __name__)

    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)

    # Only configure if MCP_LOG_LEVEL is explicitly set
    # This allows users to configure logging themselves if they prefer
    env_level = os.getenv("MCP_LOG_LEVEL")
    if env_level:
        try:
            level = getattr(logging, env_level.upper())
            logger.setLevel(level)
        except AttributeError:
            # Invalid log level in env var, ignore
            pass

    return logger


def configure_logging(
    level: int | str = logging.WARNING,
    format_string: str | None = None,
    handler: logging.Handler | None = None
) -> None:
    """
    Configure logging for all MCP client loggers.

    This is a convenience function for applications that want simple logging setup.
    Advanced users should configure logging directly using the logging module.

    Args:
        level: Log level (logging.DEBUG, logging.INFO, etc. or string like "DEBUG")
        format_string: Custom format string for log messages
        handler: Custom handler (if None, uses StreamHandler)

    Example:
        from keycardai.mcp.client.logging_config import configure_logging
        import logging

        # Simple setup
        configure_logging(logging.DEBUG)

        # Custom format
        configure_logging(
            level=logging.INFO,
            format_string='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
        )
    """
    # Convert string level to int if needed
    if isinstance(level, str):
        level = getattr(logging, level.upper())

    # Get the root logger for this package
    logger = logging.getLogger("keycardai.mcp.client")
    logger.setLevel(level)

    # Use provided handler or create default
    if handler is None:
        handler = logging.StreamHandler()

    # Set formatter
    if format_string is None:
        format_string = "[%(levelname)s:%(name)s:%(funcName)s:%(lineno)d] %(message)s"

    formatter = logging.Formatter(format_string)
    handler.setFormatter(formatter)

    # Clear existing handlers and add new one
    logger.handlers.clear()
    logger.addHandler(handler)

    # Prevent propagation to root logger to avoid duplicate messages
    logger.propagate = False

