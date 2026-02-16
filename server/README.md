# EzPeek Server

STUN/TURN server for NAT traversal, enabling ezpeek connections over the internet.

## Quick Start

### STUN Only (No authentication required)

```bash
python -m server.stun_turn_server --host 0.0.0.0 --port 3478
```

### TURN with Relay (Requires authentication)

```bash
python -m server.stun_turn_server \
    --host 0.0.0.0 \
    --port 3478 \
    --turn \
    --realm ezpeek.io \
    --credentials credentials.json \
    --relay-ip YOUR_PUBLIC_IP
```

## Configuration

### Credentials File (credentials.json)

```json
{
    "user1": "password1",
    "user2": "password2"
}
```

### Environment Variables

- `PUBLIC_IP`: Override auto-detected public IP for relay addresses

## Docker Deployment

```bash
# Build
docker build -t ezpeek-stun-turn .

# Run STUN only
docker run -d -p 3478:3478/udp ezpeek-stun-turn

# Run with TURN
docker run -d \
    -p 3478:3478/udp \
    -p 49152-49252:49152-49252/udp \
    -e PUBLIC_IP=your.server.ip \
    -v /path/to/credentials.json:/app/credentials.json \
    ezpeek-stun-turn \
    --turn --credentials /app/credentials.json
```

## Cloud Deployment

### AWS EC2

1. Launch an EC2 instance (t3.micro is sufficient for small deployments)
2. Configure Security Group:
   - UDP 3478 (STUN/TURN)
   - UDP 49152-65535 (TURN relay ports)
3. Install Python 3.9+
4. Clone repository and run server

### Google Cloud / Azure

Similar setup - ensure UDP ports are open in firewall rules.

## Protocol Support

- **STUN (RFC 5389)**: Binding requests for NAT discovery
- **TURN (RFC 5766)**: UDP relay for symmetric NAT traversal
- Long-term credentials (RFC 5389)
- XOR-MAPPED-ADDRESS
- Channel bindings for efficient relay

## Integration with EzPeek

The client automatically uses STUN for NAT discovery. Configure the server URL in the client:

```python
from ezpeek.core.stun_turn import StunClient, StunServerConfig

# Custom server
client = StunClient(servers=[
    StunServerConfig("your-server.com", 3478)
])

# Discover public address
info = client.get_public_address()
print(f"Public: {info.public_ip}:{info.public_port}")
```

## Monitoring

Enable debug logging:

```bash
python -m server.stun_turn_server --debug
```

Logs include:
- Binding requests/responses
- Allocation creation/deletion
- Permission grants
- Relay traffic statistics
