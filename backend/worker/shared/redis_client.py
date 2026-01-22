"""
Redis connection and consumer group utilities.

Provides reusable functions for:
- Establishing Redis connections
- Creating/joining consumer groups
- Reading from streams with proper error handling
"""

import redis
from typing import Optional


class RedisStreamClient:
    """
    Wrapper around Redis client for stream operations.

    Handles consumer group creation, message reading, and acknowledgments.
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        stream_key: str = "",
        group_name: str = "gpu-workers",
        consumer_name: str = "worker-1"
    ):
        """
        Initialize Redis stream client.

        Args:
            host: Redis server hostname
            port: Redis server port
            stream_key: Stream to consume from (e.g., "jobs:sdxl")
            group_name: Consumer group name (shared across worker instances)
            consumer_name: Unique identifier for this worker instance
        """
        self.host = host
        self.port = port
        self.stream_key = stream_key
        self.group_name = group_name
        self.consumer_name = consumer_name

        # Connect with decode_responses=False to preserve binary data (protobuf)
        self.client = redis.Redis(host=host, port=port, decode_responses=False)

    def ensure_consumer_group(self) -> None:
        """
        Create consumer group if it doesn't exist.

        Consumer groups enable:
        - Load balancing across multiple workers
        - Message acknowledgment tracking
        - Automatic redelivery on worker failure

        Raises:
            redis.exceptions.ConnectionError: If Redis is unreachable
        """
        try:
            # id="0": Start reading from beginning of stream
            # mkstream=True: Create stream if it doesn't exist
            self.client.xgroup_create(
                self.stream_key,
                self.group_name,
                id="0",
                mkstream=True
            )
            print(f"✅ Consumer group '{self.group_name}' created for stream '{self.stream_key}'")
        except redis.exceptions.ResponseError as e:
            # BUSYGROUP means group already exists (expected for 2nd+ worker)
            if "BUSYGROUP" not in str(e):
                raise e

    def read_messages(self, count: int = 1, block_ms: int = 2000):
        """
        Read new messages from the stream (blocking call).

        Args:
            count: Maximum number of messages to read
            block_ms: Milliseconds to wait for new messages (0 = non-blocking)

        Returns:
            List of (stream_name, [(message_id, fields_dict)]) tuples
            Empty list if no messages available

        Example:
            entries = client.read_messages(count=5, block_ms=1000)
            for stream, messages in entries:
                for msg_id, fields in messages:
                    payload = fields[b'payload']
                    # process payload...
                    client.acknowledge(msg_id)
        """
        return self.client.xreadgroup(
            self.group_name,
            self.consumer_name,
            {self.stream_key: ">"},  # ">" means only NEW messages
            count=count,
            block=block_ms
        )

    def acknowledge(self, message_id: str) -> None:
        """
        Acknowledge successful message processing.

        CRITICAL: Without this, message stays in pending state.
        If worker crashes before ACK, Redis redelivers to another worker.

        Args:
            message_id: The ID returned from read_messages
        """
        self.client.xack(self.stream_key, self.group_name, message_id)

    def ping(self) -> bool:
        """
        Check if Redis is reachable.

        Returns:
            True if connection is healthy
        """
        try:
            return self.client.ping()
        except redis.exceptions.ConnectionError:
            return False
