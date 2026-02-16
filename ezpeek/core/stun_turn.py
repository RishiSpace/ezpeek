"""
STUN/TURN client implementation for NAT traversal.

This module provides:
- STUN binding requests to discover public IP/port
- TURN allocation and relay support for symmetric NAT
- Integration with ezpeek's transport layer
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import socket
import struct
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional, Tuple, List


# STUN Message Types (RFC 5389)
class StunMessageType(IntEnum):
    BINDING_REQUEST = 0x0001
    BINDING_RESPONSE = 0x0101
    BINDING_ERROR_RESPONSE = 0x0111
    # TURN Message Types (RFC 5766)
    ALLOCATE_REQUEST = 0x0003
    ALLOCATE_RESPONSE = 0x0103
    ALLOCATE_ERROR_RESPONSE = 0x0113
    REFRESH_REQUEST = 0x0004
    REFRESH_RESPONSE = 0x0104
    SEND_INDICATION = 0x0016
    DATA_INDICATION = 0x0017
    CREATE_PERMISSION_REQUEST = 0x0008
    CREATE_PERMISSION_RESPONSE = 0x0108
    CHANNEL_BIND_REQUEST = 0x0009
    CHANNEL_BIND_RESPONSE = 0x0109


# STUN Attribute Types (RFC 5389 & 5766)
class StunAttributeType(IntEnum):
    MAPPED_ADDRESS = 0x0001
    USERNAME = 0x0006
    MESSAGE_INTEGRITY = 0x0008
    ERROR_CODE = 0x0009
    UNKNOWN_ATTRIBUTES = 0x000A
    REALM = 0x0014
    NONCE = 0x0015
    XOR_MAPPED_ADDRESS = 0x0020
    SOFTWARE = 0x8022
    FINGERPRINT = 0x8028
    # TURN specific
    CHANNEL_NUMBER = 0x000C
    LIFETIME = 0x000D
    XOR_PEER_ADDRESS = 0x0012
    DATA = 0x0013
    XOR_RELAYED_ADDRESS = 0x0016
    REQUESTED_TRANSPORT = 0x0019


STUN_MAGIC_COOKIE = 0x2112A442
STUN_HEADER_SIZE = 20


@dataclass
class StunAttribute:
    type: int
    value: bytes


@dataclass
class StunMessage:
    message_type: int
    transaction_id: bytes
    attributes: List[StunAttribute] = field(default_factory=list)

    @classmethod
    def create_binding_request(cls) -> "StunMessage":
        """Create a STUN Binding Request message."""
        transaction_id = secrets.token_bytes(12)
        return cls(
            message_type=StunMessageType.BINDING_REQUEST,
            transaction_id=transaction_id,
            attributes=[
                StunAttribute(StunAttributeType.SOFTWARE, b"ezpeek/1.0")
            ]
        )

    @classmethod
    def create_allocate_request(cls, username: str, realm: str, nonce: bytes, 
                                  password: str) -> "StunMessage":
        """Create a TURN Allocate Request message."""
        transaction_id = secrets.token_bytes(12)
        msg = cls(
            message_type=StunMessageType.ALLOCATE_REQUEST,
            transaction_id=transaction_id,
            attributes=[
                StunAttribute(StunAttributeType.REQUESTED_TRANSPORT, 
                            struct.pack(">I", 17)),  # UDP (17)
                StunAttribute(StunAttributeType.USERNAME, username.encode()),
                StunAttribute(StunAttributeType.REALM, realm.encode()),
                StunAttribute(StunAttributeType.NONCE, nonce),
            ]
        )
        return msg

    def encode(self, key: Optional[bytes] = None) -> bytes:
        """Encode the STUN message to bytes."""
        # Encode attributes first
        attrs_data = b""
        for attr in self.attributes:
            # Skip MESSAGE_INTEGRITY and FINGERPRINT for now
            if attr.type in (StunAttributeType.MESSAGE_INTEGRITY, StunAttributeType.FINGERPRINT):
                continue
            value = attr.value
            # Pad to 4-byte boundary
            padding = (4 - len(value) % 4) % 4
            attr_header = struct.pack(">HH", attr.type, len(value))
            attrs_data += attr_header + value + (b"\x00" * padding)

        # Build header
        message_length = len(attrs_data)
        if key:
            message_length += 24  # MESSAGE_INTEGRITY (4 + 20)
        
        header = struct.pack(
            ">HHI",
            self.message_type,
            message_length,
            STUN_MAGIC_COOKIE
        ) + self.transaction_id

        message = header + attrs_data

        # Add MESSAGE_INTEGRITY if key provided
        if key:
            integrity = hmac.new(key, message, hashlib.sha1).digest()
            integrity_attr = struct.pack(">HH", StunAttributeType.MESSAGE_INTEGRITY, 20) + integrity
            message += integrity_attr

        return message

    @classmethod
    def decode(cls, data: bytes) -> Optional["StunMessage"]:
        """Decode bytes into a STUN message."""
        if len(data) < STUN_HEADER_SIZE:
            return None

        message_type, message_length, magic_cookie = struct.unpack(">HHI", data[:8])
        
        if magic_cookie != STUN_MAGIC_COOKIE:
            return None

        transaction_id = data[8:20]
        
        # Parse attributes
        attributes = []
        offset = STUN_HEADER_SIZE
        while offset < STUN_HEADER_SIZE + message_length:
            if offset + 4 > len(data):
                break
            attr_type, attr_length = struct.unpack(">HH", data[offset:offset+4])
            offset += 4
            
            if offset + attr_length > len(data):
                break
            attr_value = data[offset:offset+attr_length]
            attributes.append(StunAttribute(attr_type, attr_value))
            
            # Skip padding
            offset += attr_length
            offset += (4 - attr_length % 4) % 4

        return cls(message_type, transaction_id, attributes)

    def get_attribute(self, attr_type: int) -> Optional[StunAttribute]:
        """Get an attribute by type."""
        for attr in self.attributes:
            if attr.type == attr_type:
                return attr
        return None

    def get_xor_mapped_address(self) -> Optional[Tuple[str, int]]:
        """Extract XOR-MAPPED-ADDRESS from response."""
        attr = self.get_attribute(StunAttributeType.XOR_MAPPED_ADDRESS)
        if not attr:
            return None
        
        if len(attr.value) < 8:
            return None

        # Parse XOR-MAPPED-ADDRESS
        family = attr.value[1]
        xor_port = struct.unpack(">H", attr.value[2:4])[0]
        port = xor_port ^ (STUN_MAGIC_COOKIE >> 16)

        if family == 0x01:  # IPv4
            xor_ip = struct.unpack(">I", attr.value[4:8])[0]
            ip_int = xor_ip ^ STUN_MAGIC_COOKIE
            ip = socket.inet_ntoa(struct.pack(">I", ip_int))
            return (ip, port)
        elif family == 0x02:  # IPv6
            # IPv6 XOR with magic cookie + transaction ID
            # Not implemented for simplicity
            pass

        return None

    def get_xor_relayed_address(self) -> Optional[Tuple[str, int]]:
        """Extract XOR-RELAYED-ADDRESS from TURN response."""
        attr = self.get_attribute(StunAttributeType.XOR_RELAYED_ADDRESS)
        if not attr:
            return None
        
        if len(attr.value) < 8:
            return None

        family = attr.value[1]
        xor_port = struct.unpack(">H", attr.value[2:4])[0]
        port = xor_port ^ (STUN_MAGIC_COOKIE >> 16)

        if family == 0x01:  # IPv4
            xor_ip = struct.unpack(">I", attr.value[4:8])[0]
            ip_int = xor_ip ^ STUN_MAGIC_COOKIE
            ip = socket.inet_ntoa(struct.pack(">I", ip_int))
            return (ip, port)

        return None

    def get_error_code(self) -> Optional[Tuple[int, str]]:
        """Extract ERROR-CODE from response."""
        attr = self.get_attribute(StunAttributeType.ERROR_CODE)
        if not attr or len(attr.value) < 4:
            return None
        
        error_class = attr.value[2] & 0x07
        error_number = attr.value[3]
        error_code = error_class * 100 + error_number
        reason = attr.value[4:].decode("utf-8", errors="ignore")
        return (error_code, reason)


@dataclass
class StunServerConfig:
    """Configuration for STUN/TURN server."""
    host: str
    port: int = 3478
    username: Optional[str] = None
    password: Optional[str] = None
    use_turn: bool = False


@dataclass
class NatInfo:
    """Information about NAT mapping discovered via STUN."""
    public_ip: str
    public_port: int
    local_ip: str
    local_port: int
    nat_type: str = "unknown"  # "none", "full_cone", "restricted", "symmetric"


# Default public STUN servers
DEFAULT_STUN_SERVERS = [
    StunServerConfig("stun.l.google.com", 19302),
    StunServerConfig("stun1.l.google.com", 19302),
    StunServerConfig("stun.cloudflare.com", 3478),
    StunServerConfig("stun.stunprotocol.org", 3478),
]


class StunClient:
    """STUN client for NAT traversal."""

    def __init__(self, servers: Optional[List[StunServerConfig]] = None,
                 timeout: float = 3.0, retries: int = 2):
        self.servers = servers or DEFAULT_STUN_SERVERS
        self.timeout = timeout
        self.retries = retries

    def get_public_address(self, local_port: int = 0) -> Optional[NatInfo]:
        """
        Discover public IP and port via STUN.
        
        Args:
            local_port: Local port to bind (0 for random)
            
        Returns:
            NatInfo with public address, or None if discovery failed
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(self.timeout)
        
        try:
            sock.bind(("0.0.0.0", local_port))
            local_addr = sock.getsockname()
            
            for server in self.servers:
                for attempt in range(self.retries):
                    try:
                        result = self._stun_request(sock, server)
                        if result:
                            from ezpeek.utils import get_local_ip
                            return NatInfo(
                                public_ip=result[0],
                                public_port=result[1],
                                local_ip=get_local_ip(),
                                local_port=local_addr[1],
                                nat_type=self._detect_nat_type(result, local_addr)
                            )
                    except (socket.timeout, OSError):
                        continue
        finally:
            sock.close()

        return None

    def _stun_request(self, sock: socket.socket, 
                      server: StunServerConfig) -> Optional[Tuple[str, int]]:
        """Send STUN binding request and get mapped address."""
        request = StunMessage.create_binding_request()
        data = request.encode()

        # Resolve server address
        try:
            addr = socket.getaddrinfo(server.host, server.port, socket.AF_INET)[0][4]
        except socket.gaierror:
            return None

        sock.sendto(data, addr)
        
        try:
            response_data, _ = sock.recvfrom(1024)
        except socket.timeout:
            return None

        response = StunMessage.decode(response_data)
        if not response:
            return None

        if response.message_type != StunMessageType.BINDING_RESPONSE:
            return None

        if response.transaction_id != request.transaction_id:
            return None

        return response.get_xor_mapped_address()

    def _detect_nat_type(self, public: Tuple[str, int], 
                         local: Tuple[str, int]) -> str:
        """Basic NAT type detection."""
        if public[0] == local[0] and public[1] == local[1]:
            return "none"
        elif public[1] == local[1]:
            return "full_cone"  # Port preserved
        else:
            return "symmetric"  # Port changed


class TurnClient:
    """TURN client for relay-based NAT traversal."""

    def __init__(self, server: StunServerConfig, timeout: float = 5.0):
        if not server.username or not server.password:
            raise ValueError("TURN requires username and password")
        
        self.server = server
        self.timeout = timeout
        self.sock: Optional[socket.socket] = None
        self.relayed_address: Optional[Tuple[str, int]] = None
        self.realm: str = ""
        self.nonce: bytes = b""
        self._allocation_lifetime = 600  # seconds

    def allocate(self) -> Optional[Tuple[str, int]]:
        """
        Request a TURN allocation (relayed transport address).
        
        Returns:
            (relayed_ip, relayed_port) tuple, or None on failure
        """
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(self.timeout)
        
        try:
            self.sock.bind(("0.0.0.0", 0))
            
            # Resolve server
            addr = socket.getaddrinfo(
                self.server.host, self.server.port, socket.AF_INET
            )[0][4]

            # First request without auth to get nonce/realm
            request = StunMessage(
                message_type=StunMessageType.ALLOCATE_REQUEST,
                transaction_id=secrets.token_bytes(12),
                attributes=[
                    StunAttribute(StunAttributeType.REQUESTED_TRANSPORT,
                                struct.pack(">I", 17))  # UDP
                ]
            )
            
            self.sock.sendto(request.encode(), addr)
            response_data, _ = self.sock.recvfrom(2048)
            response = StunMessage.decode(response_data)

            if not response:
                return None

            # Check for 401 Unauthorized (expected - need auth)
            error = response.get_error_code()
            if error and error[0] == 401:
                # Extract realm and nonce
                realm_attr = response.get_attribute(StunAttributeType.REALM)
                nonce_attr = response.get_attribute(StunAttributeType.NONCE)
                
                if realm_attr and nonce_attr:
                    self.realm = realm_attr.value.decode()
                    self.nonce = nonce_attr.value
                else:
                    return None

                # Compute long-term credential key
                key = self._compute_key()

                # Send authenticated request
                auth_request = StunMessage.create_allocate_request(
                    self.server.username,
                    self.realm,
                    self.nonce,
                    self.server.password
                )
                
                self.sock.sendto(auth_request.encode(key), addr)
                response_data, _ = self.sock.recvfrom(2048)
                response = StunMessage.decode(response_data)

            if not response:
                return None

            if response.message_type == StunMessageType.ALLOCATE_RESPONSE:
                self.relayed_address = response.get_xor_relayed_address()
                
                # Get lifetime
                lifetime_attr = response.get_attribute(StunAttributeType.LIFETIME)
                if lifetime_attr and len(lifetime_attr.value) >= 4:
                    self._allocation_lifetime = struct.unpack(">I", lifetime_attr.value[:4])[0]
                
                return self.relayed_address

            return None

        except (socket.timeout, OSError) as e:
            print(f"[TURN] Allocation failed: {e}")
            return None

    def _compute_key(self) -> bytes:
        """Compute long-term credential key: MD5(username:realm:password)."""
        credential = f"{self.server.username}:{self.realm}:{self.server.password}"
        return hashlib.md5(credential.encode()).digest()

    def refresh(self, lifetime: int = 600) -> bool:
        """Refresh the TURN allocation."""
        if not self.sock or not self.nonce:
            return False

        try:
            addr = socket.getaddrinfo(
                self.server.host, self.server.port, socket.AF_INET
            )[0][4]

            request = StunMessage(
                message_type=StunMessageType.REFRESH_REQUEST,
                transaction_id=secrets.token_bytes(12),
                attributes=[
                    StunAttribute(StunAttributeType.LIFETIME, 
                                struct.pack(">I", lifetime)),
                    StunAttribute(StunAttributeType.USERNAME, 
                                self.server.username.encode()),
                    StunAttribute(StunAttributeType.REALM, self.realm.encode()),
                    StunAttribute(StunAttributeType.NONCE, self.nonce),
                ]
            )

            key = self._compute_key()
            self.sock.sendto(request.encode(key), addr)
            response_data, _ = self.sock.recvfrom(2048)
            response = StunMessage.decode(response_data)

            return response and response.message_type == StunMessageType.REFRESH_RESPONSE

        except (socket.timeout, OSError):
            return False

    def create_permission(self, peer_ip: str, peer_port: int) -> bool:
        """Create permission for a peer to send data through relay."""
        if not self.sock or not self.nonce:
            return False

        try:
            addr = socket.getaddrinfo(
                self.server.host, self.server.port, socket.AF_INET
            )[0][4]

            # XOR the peer address
            ip_int = struct.unpack(">I", socket.inet_aton(peer_ip))[0]
            xor_ip = ip_int ^ STUN_MAGIC_COOKIE
            xor_port = peer_port ^ (STUN_MAGIC_COOKIE >> 16)
            
            xor_peer_addr = struct.pack(">xBHI", 0x01, xor_port, xor_ip)

            request = StunMessage(
                message_type=StunMessageType.CREATE_PERMISSION_REQUEST,
                transaction_id=secrets.token_bytes(12),
                attributes=[
                    StunAttribute(StunAttributeType.XOR_PEER_ADDRESS, xor_peer_addr),
                    StunAttribute(StunAttributeType.USERNAME,
                                self.server.username.encode()),
                    StunAttribute(StunAttributeType.REALM, self.realm.encode()),
                    StunAttribute(StunAttributeType.NONCE, self.nonce),
                ]
            )

            key = self._compute_key()
            self.sock.sendto(request.encode(key), addr)
            response_data, _ = self.sock.recvfrom(2048)
            response = StunMessage.decode(response_data)

            return response and response.message_type == StunMessageType.CREATE_PERMISSION_RESPONSE

        except (socket.timeout, OSError):
            return False

    def close(self):
        """Close the TURN client and deallocate."""
        if self.sock:
            # Send refresh with lifetime=0 to deallocate
            self.refresh(0)
            self.sock.close()
            self.sock = None


def discover_nat_info(local_port: int = 0) -> Optional[NatInfo]:
    """
    Convenience function to discover NAT information.
    
    Args:
        local_port: Local port to use for discovery
        
    Returns:
        NatInfo object with public/local addresses and NAT type
    """
    client = StunClient()
    return client.get_public_address(local_port)


def get_public_ip() -> Optional[str]:
    """Get public IP address using STUN."""
    info = discover_nat_info()
    return info.public_ip if info else None
