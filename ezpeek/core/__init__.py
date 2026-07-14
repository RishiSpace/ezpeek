"""Core ezpeek functionality."""
from .capture import CaptureSpec, build_capture_input_args
from .discovery import DiscoveryService
from .encoder import EncodeSpec, build_video_encode_args, pick_hw_encoder, pick_encoder, describe_encode_choice
from .host import HostService, HostState
from .transport import TransportSpec, build_sender_cmd, build_receiver_cmd
from .viewer import ViewerService, ViewerState
from .input_controller import InputController
from .control import ControlServer, ControlClient
from .stun_turn import (
    StunClient,
    TurnClient,
    StunServerConfig,
    NatInfo,
    discover_nat_info,
    get_public_ip,
)
from .nat_traversal import (
    NatTraversalService,
    NatTraversalConfig,
    ConnectionInfo,
    get_best_connection_address,
    gather_ice_candidates,
    IceCandidate,
)

__all__ = [
    # Capture
    "CaptureSpec",
    "build_capture_input_args",
    # Discovery
    "DiscoveryService",
    # Encoder
    "EncodeSpec",
    "build_video_encode_args",
    "pick_hw_encoder",
    "pick_encoder",
    "describe_encode_choice",
    # Host
    "HostService",
    "HostState",
    # Transport
    "TransportSpec",
    "build_sender_cmd",
    "build_receiver_cmd",
    # Viewer
    "ViewerService",
    "ViewerState",
    # Input / Control (for remoting)
    "InputController",
    "ControlServer",
    "ControlClient",
    # STUN/TURN
    "StunClient",
    "TurnClient",
    "StunServerConfig",
    "NatInfo",
    "discover_nat_info",
    "get_public_ip",
    # NAT Traversal
    "NatTraversalService",
    "NatTraversalConfig",
    "ConnectionInfo",
    "get_best_connection_address",
    "gather_ice_candidates",
    "IceCandidate",
]
