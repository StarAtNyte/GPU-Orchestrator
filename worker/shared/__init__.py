"""
Shared utilities for all worker types.

This module provides common functionality:
- Redis connection and consumer group management
- Protobuf message handling
- Configuration management
"""

__version__ = "1.0.0"
__all__ = ['worker_pb2']

# Import after defining package metadata
from . import worker_pb2
