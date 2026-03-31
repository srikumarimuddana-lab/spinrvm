"""
Base Agent Module
Provides the foundation for all agents in the Spinr development system.
"""

import json
import uuid
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)


class AgentStatus(Enum):
    """Agent operational status."""
    IDLE = "idle"
    BUSY = "busy"
    WAITING = "waiting"
    ERROR = "error"
    OFFLINE = "offline"


class TaskPriority(Enum):
    """Task priority levels."""
    CRITICAL = 1
    HIGH = 2
    MEDIUM = 3
    LOW = 4


class TaskStatus(Enum):
    """Task execution status."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    DELEGATED = "delegated"


@dataclass
class AgentMessage:
    """Message structure for inter-agent communication."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    sender: str = ""
    receiver: str = ""
    message_type: str = "task"
    content: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    priority: TaskPriority = TaskPriority.MEDIUM
    requires_response: bool = False
    correlation_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert message to dictionary."""
        data = asdict(self)
        data['priority'] = self.priority.value
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AgentMessage':
        """Create message from dictionary."""
        if 'priority' in data:
            data['priority'] = TaskPriority(data['priority'])
        return cls(**data)


@dataclass
class AgentTask:
    """Task structure for agent execution."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    title: str = ""
    description: str = ""
    task_type: str = "general"
    status: TaskStatus = TaskStatus.PENDING
    priority: TaskPriority = TaskPriority.MEDIUM
    assigned_to: Optional[str] = None
    created_by: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    completed_at: Optional[str] = None
    context: Dict[str, Any] = field(default_factory=dict)
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    dependencies: List[str] = field(default_factory=list)
    subtasks: List[str] = field(default_factory=list)
    parent_task: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert task to dictionary."""
        data = asdict(self)
        data['status'] = self.status.value
        data['priority'] = self.priority.value
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AgentTask':
        """Create task from dictionary."""
        if 'status' in data:
            data['status'] = TaskStatus(data['status'])
        if 'priority' in data:
            data['priority'] = TaskPriority(data['priority'])
        return cls(**data)


@dataclass
class KnowledgeEntry:
    """Entry structure for knowledge base."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    category: str = "general"
    title: str = ""
    content: str = ""
    tags: List[str] = field(default_factory=list)
    source: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    metadata: Dict[str, Any] = field(default_factory=dict)
    relevance_score: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert knowledge entry to dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'KnowledgeEntry':
        """Create knowledge entry from dictionary."""
        return cls(**data)


class BaseAgent(ABC):
    """
    Base class for all agents in the Spinr development system.
    
    Provides core functionality for:
    - Task management
    - Inter-agent communication
    - Knowledge base interaction
    - Status tracking
    - Error handling
    """

    def __init__(
        self,
        agent_id: str,
        name: str,
        agent_type: str,
        hierarchy_level: int = 1,
        capabilities: Optional[List[str]] = None,
        knowledge_base_path: Optional[Path] = None
    ):
        """
        Initialize the base agent.
        
        Args:
            agent_id: Unique identifier for the agent
            name: Human-readable name
            agent_type: Type of agent (e.g., 'orchestrator', 'backend')
            hierarchy_level: Level in agent hierarchy (0=highest)
            capabilities: List of agent capabilities
            knowledge_base_path: Path to knowledge base directory
        """
        self.agent_id = agent_id
        self.name = name
        self.agent_type = agent_type
        self.hierarchy_level = hierarchy_level
        self.capabilities = capabilities or []
        self.status = AgentStatus.IDLE
        self.knowledge_base_path = knowledge_base_path or Path("agents/knowledge")
        
        # Task management
        self.tasks: Dict[str, AgentTask] = {}
        self.completed_tasks: List[str] = []
        
        # Communication
        self.message_queue: List[AgentMessage] = []
        self.message_handlers: Dict[str, Callable] = {}
        self.peer_agents: Dict[str, 'BaseAgent'] = {}
        
        # Knowledge
        self.local_knowledge: Dict[str, KnowledgeEntry] = {}
        
        # Logging
        self.logger = logging.getLogger(f"Agent.{self.name}")
        
        # Initialize knowledge base directory
        self._init_knowledge_base()
        
        # Register default message handlers
        self._register_default_handlers()
        
        self.logger.info(f"Agent {self.name} initialized (ID: {self.agent_id})")

    def _init_knowledge_base(self):
        """Initialize knowledge base directory structure."""
        categories = ["patterns", "decisions", "issues", "configurations", "metrics"]
        for category in categories:
            category_path = self.knowledge_base_path / category
            category_path.mkdir(parents=True, exist_ok=True)

    def _register_default_handlers(self):
        """Register default message handlers."""
        self.register_handler("task", self._handle_task_message)
        self.register_handler("query", self._handle_query_message)
        self.register_handler("knowledge", self._handle_knowledge_message)
        self.register_handler("status", self._handle_status_message)
        self.register_handler("escalation", self._handle_escalation_message)

    def register_handler(self, message_type: str, handler: Callable):
        """
        Register a handler for a specific message type.
        
        Args:
            message_type: Type of message to handle
            handler: Handler function
        """
        self.message_handlers[message_type] = handler

    def send_message(
        self,
        receiver_id: str,
        message_type: str,
        content: Dict[str, Any],
        priority: TaskPriority = TaskPriority.MEDIUM,
        requires_response: bool = False
    ) -> AgentMessage:
        """
        Send a message to another agent.
        
        Args:
            receiver_id: ID of the receiving agent
            message_type: Type of message
            content: Message content
            priority: Message priority
            requires_response: Whether a response is required
            
        Returns:
            The sent message
        """
        message = AgentMessage(
            sender=self.agent_id,
            receiver=receiver_id,
            message_type=message_type,
            content=content,
            priority=priority,
            requires_response=requires_response
        )
        
        # If receiver is a known peer, deliver directly
        if receiver_id in self.peer_agents:
            self.peer_agents[receiver_id].receive_message(message)
        else:
            # Store in queue for later delivery
            self.message_queue.append(message)
        
        self.logger.info(f"Sent {message_type} message to {receiver_id}")
        return message

    def receive_message(self, message: AgentMessage):
        """
        Receive and process a message.
        
        Args:
            message: The received message
        """
        self.logger.info(f"Received {message.message_type} message from {message.sender}")
        
        handler = self.message_handlers.get(message.message_type)
        if handler:
            try:
                handler(message)
            except Exception as e:
                self.logger.error(f"Error handling message: {e}")
                self._send_error_response(message, str(e))
        else:
            self.logger.warning(f"No handler for message type: {message.message_type}")

    def _handle_task_message(self, message: AgentMessage):
        """Handle incoming task messages."""
        task_data = message.content.get("task")
        if task_data:
            task = AgentTask.from_dict(task_data)
            self.add_task(task)

    def _handle_query_message(self, message: AgentMessage):
        """Handle incoming query messages."""
        query = message.content.get("query")
        query_type = message.content.get("query_type", "general")
        
        result = self.query_knowledge(query, query_type)
        
        if message.requires_response:
            self.send_message(
                receiver_id=message.sender,
                message_type="query_response",
                content={"result": result, "query": query},
                correlation_id=message.id
            )

    def _handle_knowledge_message(self, message: AgentMessage):
        """Handle incoming knowledge updates."""
        knowledge_data = message.content.get("knowledge")
        if knowledge_data:
            entry = KnowledgeEntry.from_dict(knowledge_data)
            self.store_knowledge(entry)

    def _handle_status_message(self, message: AgentMessage):
        """Handle status request messages."""
        if message.requires_response:
            self.send_message(
                receiver_id=message.sender,
                message_type="status_response",
                content=self.get_status(),
                correlation_id=message.id
            )

    def _handle_escalation_message(self, message: AgentMessage):
        """Handle escalation messages."""
        self.logger.warning(f"Escalation received: {message.content}")
        # Override in subclasses for specific escalation handling

    def _send_error_response(self, original_message: AgentMessage, error: str):
        """Send error response for a failed message handling."""
        self.send_message(
            receiver_id=original_message.sender,
            message_type="error",
            content={
                "error": error,
                "original_message_id": original_message.id
            },
            priority=TaskPriority.HIGH
        )

    def add_task(self, task: AgentTask):
        """
        Add a task to the agent's task list.
        
        Args:
            task: Task to add
        """
        self.tasks[task.id] = task
        self.logger.info(f"Added task: {task.title} (ID: {task.id})")
        
        # Update knowledge with task info
        self._update_task_knowledge(task)

    def update_task_status(self, task_id: str, status: TaskStatus, result: Optional[Dict] = None):
        """
        Update the status of a task.
        
        Args:
            task_id: ID of the task
            status: New status
            result: Optional result data
        """
        if task_id in self.tasks:
            task = self.tasks[task_id]
            task.status = status
            task.updated_at = datetime.now().isoformat()
            
            if status == TaskStatus.COMPLETED:
                task.completed_at = datetime.now().isoformat()
                task.result = result
                self.completed_tasks.append(task_id)
                
                # Notify orchestrator if exists
                self._notify_task_completion(task)
            
            elif status == TaskStatus.FAILED:
                task.error = result.get("error") if result else "Unknown error"
                self._handle_task_failure(task)

    def _update_task_knowledge(self, task: AgentTask):
        """Update knowledge base with task information."""
        entry = KnowledgeEntry(
            category="tasks",
            title=f"Task: {task.title}",
            content=json.dumps(task.to_dict(), indent=2),
            tags=[task.task_type, task.status.value],
            source=self.agent_id,
            metadata={"task_id": task.id, "priority": task.priority.value}
        )
        self.store_knowledge(entry)

    def _notify_task_completion(self, task: AgentTask):
        """Notify relevant agents about task completion."""
        # Notify orchestrator
        orchestrator = self._get_orchestrator()
        if orchestrator:
            self.send_message(
                receiver_id=orchestrator.agent_id,
                message_type="task_completed",
                content={"task": task.to_dict()},
                priority=TaskPriority.MEDIUM
            )

    def _handle_task_failure(self, task: AgentTask):
        """Handle task failure."""
        self.logger.error(f"Task failed: {task.title} - {task.error}")
        
        # Store failure in knowledge base
        entry = KnowledgeEntry(
            category="issues",
            title=f"Task Failure: {task.title}",
            content=f"Error: {task.error}\nTask: {json.dumps(task.to_dict(), indent=2)}",
            tags=["failure", task.task_type],
            source=self.agent_id
        )
        self.store_knowledge(entry)

    def _get_orchestrator(self) -> Optional['BaseAgent']:
        """Get the orchestrator agent if known."""
        for agent in self.peer_agents.values():
            if agent.agent_type == "orchestrator":
                return agent
        return None

    def register_peer(self, agent: 'BaseAgent'):
        """
        Register a peer agent for direct communication.
        
        Args:
            agent: Peer agent to register
        """
        self.peer_agents[agent.agent_id] = agent
        self.logger.info(f"Registered peer agent: {agent.name} ({agent.agent_id})")

    def store_knowledge(self, entry: KnowledgeEntry):
        """
        Store a knowledge entry.
        
        Args:
            entry: Knowledge entry to store
        """
        # Store locally
        self.local_knowledge[entry.id] = entry
        
        # Persist to disk
        try:
            category_path = self.knowledge_base_path / entry.category
            category_path.mkdir(parents=True, exist_ok=True)
            
            file_path = category_path / f"{entry.id}.json"
            with open(file_path, 'w') as f:
                json.dump(entry.to_dict(), f, indent=2)
            
            self.logger.debug(f"Stored knowledge entry: {entry.title}")
        except Exception as e:
            self.logger.error(f"Failed to persist knowledge: {e}")

    def query_knowledge(self, query: str, category: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Query the knowledge base.
        
        Args:
            query: Search query
            category: Optional category filter
            
        Returns:
            List of matching knowledge entries
        """
        results = []
        query_lower = query.lower()
        
        for entry in self.local_knowledge.values():
            # Filter by category if specified
            if category and entry.category != category:
                continue
            
            # Simple text matching
            if (query_lower in entry.title.lower() or 
                query_lower in entry.content.lower() or
                any(query_lower in tag.lower() for tag in entry.tags)):
                results.append(entry.to_dict())
        
        # Sort by relevance score
        results.sort(key=lambda x: x.get('relevance_score', 0), reverse=True)
        
        return results

    def load_knowledge_from_disk(self):
        """Load knowledge entries from disk."""
        try:
            for category_path in self.knowledge_base_path.iterdir():
                if category_path.is_dir():
                    for file_path in category_path.glob("*.json"):
                        try:
                            with open(file_path, 'r') as f:
                                data = json.load(f)
                                entry = KnowledgeEntry.from_dict(data)
                                self.local_knowledge[entry.id] = entry
                        except Exception as e:
                            self.logger.error(f"Failed to load {file_path}: {e}")
            
            self.logger.info(f"Loaded {len(self.local_knowledge)} knowledge entries")
        except Exception as e:
            self.logger.error(f"Failed to load knowledge base: {e}")

    def get_status(self) -> Dict[str, Any]:
        """
        Get the current status of the agent.
        
        Returns:
            Dictionary containing agent status information
        """
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "agent_type": self.agent_type,
            "status": self.status.value,
            "hierarchy_level": self.hierarchy_level,
            "capabilities": self.capabilities,
            "active_tasks": len([t for t in self.tasks.values() if t.status == TaskStatus.IN_PROGRESS]),
            "pending_tasks": len([t for t in self.tasks.values() if t.status == TaskStatus.PENDING]),
            "completed_tasks": len(self.completed_tasks),
            "knowledge_entries": len(self.local_knowledge),
            "peer_agents": list(self.peer_agents.keys())
        }

    def escalate_to_higher_level(self, issue: str, context: Dict[str, Any]):
        """
        Escalate an issue to a higher-level agent.
        
        Args:
            issue: Description of the issue
            context: Context information
        """
        higher_level_agents = [
            agent for agent in self.peer_agents.values()
            if agent.hierarchy_level < self.hierarchy_level
        ]
        
        if higher_level_agents:
            # Sort by hierarchy level (lowest first = highest priority)
            higher_level_agents.sort(key=lambda a: a.hierarchy_level)
            target = higher_level_agents[0]
            
            self.send_message(
                receiver_id=target.agent_id,
                message_type="escalation",
                content={
                    "issue": issue,
                    "context": context,
                    "escalated_by": self.agent_id,
                    "timestamp": datetime.now().isoformat()
                },
                priority=TaskPriority.HIGH,
                requires_response=True
            )
            
            self.logger.info(f"Escalated issue to {target.name}: {issue}")
        else:
            self.logger.warning(f"No higher-level agent to escalate to: {issue}")

    def delegate_task(self, task: AgentTask, target_agent_id: str) -> bool:
        """
        Delegate a task to another agent.
        
        Args:
            task: Task to delegate
            target_agent_id: ID of the target agent
            
        Returns:
            True if delegation was successful
        """
        if target_agent_id in self.peer_agents:
            target_agent = self.peer_agents[target_agent_id]
            
            # Check if target can handle this task type
            if task.task_type in target_agent.capabilities or "general" in target_agent.capabilities:
                task.status = TaskStatus.DELEGATED
                task.assigned_to = target_agent_id
                task.updated_at = datetime.now().isoformat()
                
                self.send_message(
                    receiver_id=target_agent_id,
                    message_type="task",
                    content={"task": task.to_dict()},
                    priority=task.priority
                )
                
                self.logger.info(f"Delegated task {task.id} to {target_agent.name}")
                return True
            else:
                self.logger.warning(f"Target agent {target_agent.name} cannot handle task type {task.task_type}")
                return False
        else:
            self.logger.error(f"Unknown target agent: {target_agent_id}")
            return False

    def collaborate(self, task: AgentTask, collaborator_ids: List[str]) -> Dict[str, Any]:
        """
        Initiate collaboration with other agents on a task.
        
        Args:
            task: Task to collaborate on
            collaborator_ids: List of agent IDs to collaborate with
            
        Returns:
            Collaboration context
        """
        collaboration_id = str(uuid.uuid4())
        
        for agent_id in collaborator_ids:
            if agent_id in self.peer_agents:
                self.send_message(
                    receiver_id=agent_id,
                    message_type="collaboration_invite",
                    content={
                        "collaboration_id": collaboration_id,
                        "task": task.to_dict(),
                        "coordinator": self.agent_id
                    },
                    priority=task.priority,
                    requires_response=True
                )
        
        return {
            "collaboration_id": collaboration_id,
            "task_id": task.id,
            "coordinator": self.agent_id,
            "collaborators": collaborator_ids,
            "status": "initiated"
        }

    @abstractmethod
    def execute_task(self, task: AgentTask) -> Dict[str, Any]:
        """
        Execute a task. Must be implemented by subclasses.
        
        Args:
            task: Task to execute
            
        Returns:
            Task execution result
        """
        pass

    @abstractmethod
    def get_capabilities(self) -> List[str]:
        """
        Get the capabilities of this agent.
        
        Returns:
            List of capability strings
        """
        pass

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(id={self.agent_id}, name={self.name}, status={self.status.value})>"