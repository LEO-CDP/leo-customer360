"""Production customer engagement feature: persist, load, and execute workflow JSON.

Flows are stored in the ``customer360`` schema (crm_flow, crm_flow_version, crm_flow_node, crm_flow_edge).
"""

from .engine import DEFAULT_MAX_STEPS, FlowEngine
from .models import FlowDefinition, JourneyResult, NodeDef
from .nodes import NODE_REGISTRY, BaseNode, register_node
from .repository import FlowNotFound, SavedFlow, WorkflowRepository, read_workflow_file
from .services import ChannelGateway, EventSource, ProfileWriter, Services
from leo_customer360_dao.models.identity import CdpMasterProfile

__all__ = [
    "DEFAULT_MAX_STEPS",
    "ChannelGateway",
    "EventSource",
    "FlowDefinition",
    "FlowEngine",
    "FlowNotFound",
    "JourneyResult",
    "NODE_REGISTRY",
    "BaseNode",
    "CdpMasterProfile",
    "NodeDef",
    "ProfileWriter",
    "SavedFlow",
    "Services",
    "WorkflowRepository",
    "read_workflow_file",
    "register_node",
]
