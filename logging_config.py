"""
Production-safe logging configuration for Flask application.

This module provides logging setup that:
- Uses RotatingFileHandler to bound log file size
- Falls back to StreamHandler if logs/ directory is unwritable
- Sets logging.raiseExceptions = False in production to prevent handler failures
- Configures appropriate log levels for production vs development
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler


def setup_logging(is_production=True):
    """
    Configure production-safe logging with rotation and fallback.
    
    Features:
    - Uses RotatingFileHandler to bound log file size (5MB per file, 5 backups)
    - Falls back to StreamHandler if logs/ directory is unwritable
    - Sets logging.raiseExceptions = False in production to prevent handler failures from crashing app
    - Uses INFO level in production, DEBUG in development
    
    Args:
        is_production: Boolean indicating if running in production mode
        
    Returns:
        Configured logger instance
    """
    # Determine logging level
    level = logging.INFO if is_production else logging.DEBUG
    
    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Get root logger
    logger = logging.getLogger()
    logger.setLevel(level)
    
    # Set raiseExceptions = False in production to prevent handler failures from propagating
    if is_production:
        logging.raiseExceptions = False
    
    # Try to setup file-based logging with rotation
    file_handler_added = False
    logs_dir = 'logs'
    
    try:
        # Create logs directory if it doesn't exist
        if not os.path.exists(logs_dir):
            os.makedirs(logs_dir, exist_ok=True)
        
        # Check if directory is writable
        if os.access(logs_dir, os.W_OK):
            # Setup RotatingFileHandler with conservative limits
            # 5MB per file, 5 backups = max 30MB total
            handler = RotatingFileHandler(
                'logs/app.log',
                maxBytes=5 * 1024 * 1024,  # 5 MB
                backupCount=5
            )
            handler.setFormatter(formatter)
            handler.setLevel(level)
            logger.addHandler(handler)
            file_handler_added = True
        else:
            # Directory exists but not writable - fall back to stderr
            pass
    except (OSError, IOError, PermissionError) as e:
        # If we can't write to logs directory, continue with fallback
        # Don't crash the app due to logging issues
        pass
    
    # If file handler couldn't be added, fall back to StreamHandler (stderr)
    if not file_handler_added:
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(formatter)
        stream_handler.setLevel(level)
        logger.addHandler(stream_handler)
    
    return logger
