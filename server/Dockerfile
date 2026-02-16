FROM python:3.11-slim

WORKDIR /app

# Copy server code
COPY server/ ./server/

# No dependencies needed - pure Python implementation

EXPOSE 3478/udp
EXPOSE 49152-49252/udp

# Default: STUN only mode
ENTRYPOINT ["python", "-m", "server.stun_turn_server"]
CMD ["--host", "0.0.0.0", "--port", "3478"]
