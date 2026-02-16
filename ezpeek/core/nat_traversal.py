"""
NAT traversal integration for ezpeek.

Provides high-level functions to establish connections through NAT
using STUN for discovery and TURN for relay when needed.
"""
from __future__ import annotations

import asyncio
import logging
import socket
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional, Tuple, List

from .stun_turn import (
    StunClient,
    TurnClient,
    StunServerConfig,
    NatInfo,
    DEFAULT_STUN_SERVERS,
)

logger = logging.getLogger(__name__)


@dataclass
class ConnectionInfo:
    """Information about an established NAT-traversed connection."""
    local_ip: str
    local_port: int
    public_ip: str
    public_port: int
    relay_ip: Optional[str] = None
    relay_port: Optional[int] = None
    connection_type: str = "direct"  # "direct", "stun", "turn"


@dataclass
class NatTraversalConfig:
    """Configuration for NAT traversal."""
    stun_servers: List[StunServerConfig]
    turn_server: Optional[StunServerConfig] = None
    prefer_relay: bool = False  # Use TURN even if direct connection possible
    timeout: float = 10.0


# Default configuration using public STUN servers
DEFAULT_CONFIG = NatTraversalConfig(
    stun_servers=DEFAULT_STUN_SERVERS,
    turn_server=None,
    prefer_relay=False,
)


class NatTraversalService:
    """
    Service for establishing NAT-traversed connections.
    
    Usage:
        service = NatTraversalService()
        info = service.discover()
        print(f"Public address: {info.public_ip}:{info.public_port}")
    """

    def __init__(self, config: Optional[NatTraversalConfig] = None):
        self.config = config or DEFAULT_CONFIG
        self._stun_client = StunClient(
            servers=self.config.stun_servers,
            timeout=self.config.timeout / 3,
        )
        self._turn_client: Optional[TurnClient] = None
        self._nat_info: Optional[NatInfo] = None

    def discover(self, local_port: int = 0) -> Optional[ConnectionInfo]:
        """
        Discover public address through STUN.
        
        Args:
            local_port: Local port to bind (0 for random)
            
        Returns:
            ConnectionInfo with discovered addresses
        """
        self._nat_info = self._stun_client.get_public_address(local_port)
        
        if not self._nat_info:
            logger.warning("STUN discovery failed")
            return None

        return ConnectionInfo(
            local_ip=self._nat_info.local_ip,
            local_port=self._nat_info.local_port,
            public_ip=self._nat_info.public_ip,
            public_port=self._nat_info.public_port,
            connection_type="stun" if self._nat_info.nat_type != "none" else "direct",
        )

    def allocate_relay(self) -> Optional[ConnectionInfo]:
        """
        Allocate a TURN relay address.
        
        Returns:
            ConnectionInfo with relay address, or None if unavailable
        """
        if not self.config.turn_server:
            logger.warning("No TURN server configured")
            return None

        try:
            self._turn_client = TurnClient(
                self.config.turn_server,
                timeout=self.config.timeout,
            )
            relay_addr = self._turn_client.allocate()
            
            if not relay_addr:
                logger.warning("TURN allocation failed")
                return None

            # Get STUN info if we don't have it
            if not self._nat_info:
                self.discover()

            return ConnectionInfo(
                local_ip=self._nat_info.local_ip if self._nat_info else "0.0.0.0",
                local_port=self._nat_info.local_port if self._nat_info else 0,
                public_ip=self._nat_info.public_ip if self._nat_info else "",
                public_port=self._nat_info.public_port if self._nat_info else 0,
                relay_ip=relay_addr[0],
                relay_port=relay_addr[1],
                connection_type="turn",
            )

        except Exception as e:
            logger.error(f"TURN allocation error: {e}")
            return None

    def create_permission(self, peer_ip: str, peer_port: int) -> bool:
        """Create TURN permission for a peer."""
        if not self._turn_client:
            return False
        return self._turn_client.create_permission(peer_ip, peer_port)

    def close(self):
        """Clean up resources."""
        if self._turn_client:
            self._turn_client.close()
            self._turn_client = None


def get_best_connection_address(
    local_port: int = 0,
    config: Optional[NatTraversalConfig] = None,
) -> Tuple[str, int, str]:
    """
    Get the best address for accepting connections.
    
    Returns:
        (ip, port, connection_type) tuple
        - For no NAT: returns local IP/port, type="direct"
        - For NAT: returns public IP/port, type="stun"
        - For symmetric NAT with TURN: returns relay IP/port, type="turn"
    """
    service = NatTraversalService(config)
    
    try:
        info = service.discover(local_port)
        
        if not info:
            # Fallback to local
            from ezpeek.utils import get_local_ip
            return (get_local_ip(), local_port or 17000, "local")

        # Check if we need TURN (symmetric NAT)
        if config and config.turn_server and info.connection_type == "stun":
            # For symmetric NAT, try TURN
            nat_info = service._nat_info
            if nat_info and nat_info.nat_type == "symmetric":
                relay_info = service.allocate_relay()
                if relay_info and relay_info.relay_ip:
                    return (relay_info.relay_ip, relay_info.relay_port, "turn")

        return (info.public_ip, info.public_port, info.connection_type)

    finally:
        service.close()


class IceCandidate:
    """ICE candidate for connection establishment."""
    
    def __init__(self, ip: str, port: int, candidate_type: str, priority: int = 0):
        self.ip = ip
        self.port = port
        self.candidate_type = candidate_type  # "host", "srflx", "relay"
        self.priority = priority

    def __repr__(self):
        return f"IceCandidate({self.ip}:{self.port}, {self.candidate_type})"


def gather_ice_candidates(
    local_port: int = 0,
    config: Optional[NatTraversalConfig] = None,
) -> List[IceCandidate]:
    """
    Gather ICE candidates for connection establishment.
    
    Returns list of candidates in priority order:
    1. Host candidates (local addresses)
    2. Server reflexive (STUN discovered)
    3. Relay (TURN allocated)
    """
    candidates = []
    service = NatTraversalService(config)

    try:
        # 1. Host candidate
        from ezpeek.utils import get_local_ip
        local_ip = get_local_ip()
        if local_ip != "0.0.0.0":
            candidates.append(IceCandidate(
                ip=local_ip,
                port=local_port or 17000,
                candidate_type="host",
                priority=100,
            ))

        # 2. Server reflexive (STUN)
        info = service.discover(local_port)
        if info and info.public_ip:
            candidates.append(IceCandidate(
                ip=info.public_ip,
                port=info.public_port,
                candidate_type="srflx",
                priority=80,
            ))

        # 3. Relay (TURN)
        if config and config.turn_server:
            relay_info = service.allocate_relay()
            if relay_info and relay_info.relay_ip:
                candidates.append(IceCandidate(
                    ip=relay_info.relay_ip,
                    port=relay_info.relay_port,
                    candidate_type="relay",
                    priority=50,
                ))

    except Exception as e:
        logger.warning(f"ICE gathering error: {e}")
    finally:
        service.close()

    # Sort by priority (highest first)
    candidates.sort(key=lambda c: c.priority, reverse=True)
    return candidates
