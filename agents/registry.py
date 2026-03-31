"""
Agent Registry
Manages all agents and provides a unified interface for the Spinr development system.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from agents.base_agent import AgentTask, TaskPriority
from agents.orchestrator import OrchestratorAgent
from agents.knowledge_base import KnowledgeBaseAgent
from agents.code_reviewer import CodeReviewerAgent
from agents.tester import TestingAgent
from agents.backend_agent import BackendAgent
from agents.frontend_agent import FrontendAgent
from agents.deployer import DeploymentAgent
from agents.security_agent import SecurityAgent
from agents.documenter import DocumentationAgent

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("AgentRegistry")


class AgentRegistry:
    """
    Central registry for managing all agents in the Spinr development system.
    
    Provides:
    - Agent initialization and registration
    - Task execution interface
    - System status monitoring
    - Knowledge base access
    """

    def __init__(self, knowledge_base_path: Optional[Path] = None):
        """
        Initialize the Agent Registry.
        
        Args:
            knowledge_base_path: Path to knowledge base directory
        """
        self.knowledge_base_path = knowledge_base_path or Path("agents/knowledge")
        self.knowledge_base_path.mkdir(parents=True, exist_ok=True)
        
        # Initialize orchestrator
        self.orchestrator = OrchestratorAgent(knowledge_base_path=self.knowledge_base_path)
        
        # Initialize specialized agents
        self.agents = {
            "knowledge_base": KnowledgeBaseAgent(),
            "code_reviewer": CodeReviewerAgent(),
            "tester": TestingAgent(),
            "backend": BackendAgent(),
            "frontend": FrontendAgent(),
            "deployer": DeploymentAgent(),
            "security": SecurityAgent(),
            "documenter": DocumentationAgent()
        }
        
        # Register all agents with orchestrator
        self._register_agents()
        
        logger.info("Agent Registry initialized with all agents")

    def _register_agents(self):
        """Register all specialized agents with the orchestrator."""
        agent_list = list(self.agents.values())
        self.orchestrator.register_agents(agent_list)
        
        # Load existing knowledge
        for agent in self.agents.values():
            agent.load_knowledge_from_disk()

    def execute_task(self, task_type: str, title: str, description: str,
                     priority: str = "medium", context: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Execute a task through the orchestrator.
        
        Args:
            task_type: Type of task (e.g., 'feature_development', 'bug_fix')
            title: Task title
            description: Task description
            priority: Task priority ('critical', 'high', 'medium', 'low')
            context: Additional context for the task
            
        Returns:
            Execution result
        """
        priority_map = {
            "critical": TaskPriority.CRITICAL,
            "high": TaskPriority.HIGH,
            "medium": TaskPriority.MEDIUM,
            "low": TaskPriority.LOW
        }
        
        task = AgentTask(
            title=title,
            description=description,
            task_type=task_type,
            priority=priority_map.get(priority, TaskPriority.MEDIUM),
            context=context or {},
            created_by="user"
        )
        
        logger.info(f"Executing task: {title}")
        return self.orchestrator.execute_task(task)

    def get_agent(self, agent_type: str):
        """
        Get a specific agent by type.
        
        Args:
            agent_type: Type of agent to retrieve
            
        Returns:
            Agent instance or None
        """
        return self.agents.get(agent_type)

    def get_system_status(self) -> Dict[str, Any]:
        """
        Get overall system status.
        
        Returns:
            System status information
        """
        return self.orchestrator.get_system_status()

    def generate_report(self) -> Dict[str, Any]:
        """
        Generate a comprehensive system report.
        
        Returns:
            Report data
        """
        return self.orchestrator.generate_report()

    def query_knowledge(self, query: str, category: Optional[str] = None) -> List[Dict]:
        """
        Query the knowledge base.
        
        Args:
            query: Search query
            category: Optional category filter
            
        Returns:
            List of matching knowledge entries
        """
        return self.agents["knowledge_base"].query_knowledge(query, category)

    def list_agents(self) -> List[Dict[str, Any]]:
        """
        List all registered agents.
        
        Returns:
            List of agent information
        """
        return [
            {
                "id": agent.agent_id,
                "name": agent.name,
                "type": agent.agent_type,
                "status": agent.status.value,
                "capabilities": agent.capabilities
            }
            for agent in self.agents.values()
        ]


# Global registry instance
_registry: Optional[AgentRegistry] = None


def get_registry() -> AgentRegistry:
    """
    Get or create the global agent registry.
    
    Returns:
        AgentRegistry instance
    """
    global _registry
    if _registry is None:
        _registry = AgentRegistry()
    return _registry