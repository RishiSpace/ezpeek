"""Tests for STUN/TURN client functionality."""
import socket
import struct
from ezpeek.core.stun_turn import (
    StunMessage,
    StunMessageType,
    StunAttributeType,
    StunAttribute,
    StunClient,
    StunServerConfig,
    NatInfo,
    STUN_MAGIC_COOKIE,
)


class TestStunMessage:
    """Tests for STUN message encoding/decoding."""

    def test_create_binding_request(self):
        """Test creating a binding request."""
        msg = StunMessage.create_binding_request()
        assert msg.message_type == StunMessageType.BINDING_REQUEST
        assert len(msg.transaction_id) == 12

    def test_encode_decode_roundtrip(self):
        """Test that encoding and decoding produces equivalent message."""
        original = StunMessage.create_binding_request()
        encoded = original.encode()
        decoded = StunMessage.decode(encoded)

        assert decoded is not None
        assert decoded.message_type == original.message_type
        assert decoded.transaction_id == original.transaction_id

    def test_decode_invalid_data(self):
        """Test that invalid data returns None."""
        assert StunMessage.decode(b"") is None
        assert StunMessage.decode(b"short") is None
        # Wrong magic cookie
        bad_cookie = struct.pack(">HHI", 0x0001, 0, 0x12345678) + b"\x00" * 12
        assert StunMessage.decode(bad_cookie) is None

    def test_xor_mapped_address_parsing(self):
        """Test parsing XOR-MAPPED-ADDRESS attribute."""
        # Create a response with XOR-MAPPED-ADDRESS
        # IP: 192.168.1.100 (0xC0A80164), Port: 12345
        ip_int = 0xC0A80164
        port = 12345
        
        xor_ip = ip_int ^ STUN_MAGIC_COOKIE
        xor_port = port ^ (STUN_MAGIC_COOKIE >> 16)
        
        xor_addr_value = struct.pack(">xBHI", 0x01, xor_port, xor_ip)
        
        msg = StunMessage(
            message_type=StunMessageType.BINDING_RESPONSE,
            transaction_id=b"\x00" * 12,
            attributes=[
                StunAttribute(StunAttributeType.XOR_MAPPED_ADDRESS, xor_addr_value)
            ]
        )

        result = msg.get_xor_mapped_address()
        assert result is not None
        assert result[0] == "192.168.1.100"
        assert result[1] == 12345


class TestStunClient:
    """Tests for STUN client."""

    def test_client_init_default_servers(self):
        """Test client initializes with default STUN servers."""
        client = StunClient()
        assert len(client.servers) > 0
        assert any("google" in s.host for s in client.servers)

    def test_client_init_custom_servers(self):
        """Test client accepts custom server list."""
        servers = [StunServerConfig("custom.stun.server", 3478)]
        client = StunClient(servers=servers)
        assert len(client.servers) == 1
        assert client.servers[0].host == "custom.stun.server"

    def test_nat_info_dataclass(self):
        """Test NatInfo dataclass."""
        info = NatInfo(
            public_ip="203.0.113.50",
            public_port=54321,
            local_ip="192.168.1.100",
            local_port=12345,
            nat_type="full_cone"
        )
        assert info.public_ip == "203.0.113.50"
        assert info.nat_type == "full_cone"


class TestStunServerConfig:
    """Tests for server configuration."""

    def test_default_port(self):
        """Test default port is 3478."""
        config = StunServerConfig(host="stun.example.com")
        assert config.port == 3478

    def test_turn_credentials(self):
        """Test TURN configuration with credentials."""
        config = StunServerConfig(
            host="turn.example.com",
            port=3478,
            username="user",
            password="pass",
            use_turn=True
        )
        assert config.use_turn is True
        assert config.username == "user"
        assert config.password == "pass"
