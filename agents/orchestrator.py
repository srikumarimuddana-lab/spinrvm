"""
Orchestrator Agent
Top-level coordinator for all agent activities in the Spinr development system.
"""

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from agents.base_agent import (
    BaseAgent,
    AgentMessage,
    AgentTask,
    AgentStatus,
    TaskPriority,
    TaskStatus,
    KnowledgeEntry
)


class OrchestratorAgent(BaseAgent):
    """
    Orchestrator Agent - The supreme coordinator of the agent hierarchy.
    
    Responsibilities:
    - Receives and decomposes development tasks
    - Assigns subtasks to appropriate specialized agents
    - Monitors overall progress and handles escalations
    - Maintains task dependencies and execution order
    - Coordinates multi-agent collaborations
    - Provides high-level reporting and analytics
    """

    def __init__(
        self,
        agent_id: str = "orchestrator-001",
        name: str = "Orchestrator",
        knowledge_base_path=None
    ):
        """
        Initialize the Orchestrator Agent.
        
        Args:
            agent_id: Unique identifier for the agent
            name: Human-readable name
            knowledge_base_path: Path to knowledge base directory
        """
        super().__init__(
            agent_id=agent_id,
            name=name,
            agent_type="orchestrator",
            hierarchy_level=0,  # Highest level
            capabilities=[
                "task_decomposition",
                "task_assignment",
                "progress_monitoring",
                "escalation_handling",
                "collaboration_coordination",
                "reporting",
                "resource_allocation"
            ],
            knowledge_base_path=knowledge_base_path
        )
        
        # Task management
        self.task_queue: List[AgentTask] = []
        self.active_tasks: Dict[str, AgentTask] = {}
        self.completed_tasks_history: List[Dict[str, Any]] = []
        
        # Agent registry
        self.registered_agents: Dict[str, Dict[str, Any]] = {}
        self.agent_workload: Dict[str, int] = {}
        
        # Collaboration tracking
        self.active_collaborations: Dict[str, Dict[str, Any]] = {}
        
        # Performance metrics
        self.metrics = {
            "tasks_processed": 0,
            "tasks_completed": 0,
            "tasks_failed": 0,
            "average_completion_time": 0,
            "escalations_handled": 0
        }
        
        # Register orchestrator-specific handlers
        self._register_orchestrator_handlers()
        
        self.logger.info("Orchestrator Agent initialized as supreme coordinator")

    def _register_orchestrator_handlers(self):
        """Register orchestrator-specific message handlers."""
        self.register_handler("task_completed", self._handle_task_completed)
        self.register_handler("task_failed", self._handle_task_failed)
        self.register_handler("agent_registration", self._handle_agent_registration)
        self.register_handler("progress_update", self._handle_progress_update)
        self.register_handler("collaboration_request", self._handle_collaboration_request)
        self.register_handler("resource_request", self._handle_resource_request)

    def register_agent(self, agent: BaseAgent):
        """
        Register a specialized agent with the orchestrator.
        
        Args:
            agent: Agent to register
        """
        self.registered_agents[agent.agent_id] = {
            "agent": agent,
            "name": agent.name,
            "type": agent.agent_type,
            "capabilities": agent.capabilities,
            "hierarchy_level": agent.hierarchy_level,
            "status": agent.status.value,
            "registered_at": datetime.now().isoformat()
        }
        
        # Register peer relationship
        self.register_peer(agent)
        agent.register_peer(self)
        
        # Initialize workload tracking
        self.agent_workload[agent.agent_id] = 0
        
        self.logger.info(f"Registered agent: {agent.name} ({agent.agent_type})")

    def register_agents(self, agents: List[BaseAgent]):
        """
        Register multiple agents at once.
        
        Args:
            agents: List of agents to register
        """
        for agent in agents:
            self.register_agent(agent)
        
        # Establish peer relationships between all agents
        for i, agent1 in enumerate(agents):
            for agent2 in agents[i+1:]:
                agent1.register_peer(agent2)
                agent2.register_peer(agent1)
        
        self.logger.info(f"Registered {len(agents)} agents with full peer connectivity")

    def execute_task(self, task: AgentTask) -> Dict[str, Any]:
        """
        Execute a top-level task by decomposing and delegating.
        
        Args:
            task: Task to execute
            
        Returns:
            Execution result
        """
        self.status = AgentStatus.BUSY
        task.status = TaskStatus.IN_PROGRESS
        task.updated_at = datetime.now().isoformat()
        
        self.active_tasks[task.id] = task
        self.metrics["tasks_processed"] += 1
        
        try:
            # Decompose task into subtasks
            subtasks = self._decompose_task(task)
            
            # Assign subtasks to appropriate agents
            assignments = self._assign_subtasks(task, subtasks)
            
            # Monitor execution
            result = self._monitor_task_execution(task, assignments)
            
            # Update metrics
            self.metrics["tasks_completed"] += 1
            
            return result
            
        except Exception as e:
            self.logger.error(f"Task execution failed: {e}")
            task.status = TaskStatus.FAILED
            task.error = str(e)
            self.metrics["tasks_failed"] += 1
            raise
            
        finally:
            self.status = AgentStatus.IDLE
            if task.id in self.active_tasks:
                del self.active_tasks[task.id]

    def _decompose_task(self, task: AgentTask) -> List[AgentTask]:
        """
        Decompose a high-level task into subtasks.
        
        Args:
            task: Parent task to decompose
            
        Returns:
            List of subtasks
        """
        subtasks = []
        
        # Analyze task type and create appropriate subtasks
        task_type = task.task_type.lower()
        
        if task_type == "feature_development":
            subtasks = self._decompose_feature_task(task)
        elif task_type == "bug_fix":
            subtasks = self._decompose_bugfix_task(task)
        elif task_type == "code_review":
            subtasks = self._decompose_code_review_task(task)
        elif task_type == "deployment":
            subtasks = self._decompose_deployment_task(task)
        elif task_type == "testing":
            subtasks = self._decompose_testing_task(task)
        else:
            # Generic decomposition
            subtasks = self._decompose_generic_task(task)
        
        # Link subtasks to parent
        for subtask in subtasks:
            subtask.parent_task = task.id
            task.subtasks.append(subtask.id)
        
        self.logger.info(f"Decomposed task {task.id} into {len(subtasks)} subtasks")
        return subtasks

    def _decompose_feature_task(self, task: AgentTask) -> List[AgentTask]:
        """Decompose a feature development task."""
        subtasks = []
        
        # Determine if backend or frontend feature
        context = task.context or {}
        feature_area = context.get("area", "full_stack")
        
        if feature_area in ["backend", "full_stack"]:
            subtasks.append(AgentTask(
                title=f"Backend: {task.title}",
                description=f"Implement backend for: {task.description}",
                task_type="backend_implementation",
                priority=task.priority,
                created_by=self.agent_id,
                context={**context, "parent_task": task.id}
            ))
        
        if feature_area in ["frontend", "full_stack", "rider_app", "driver_app"]:
            subtasks.append(AgentTask(
                title=f"Frontend: {task.title}",
                description=f"Implement frontend for: {task.description}",
                task_type="frontend_implementation",
                priority=task.priority,
                created_by=self.agent_id,
                context={**context, "parent_task": task.id}
            ))
        
        # Always add testing
        subtasks.append(AgentTask(
            title=f"Testing: {task.title}",
            description=f"Create tests for: {task.description}",
            task_type="test_creation",
            priority=task.priority,
            created_by=self.agent_id,
            context={**context, "parent_task": task.id}
        ))
        
        # Add code review
        subtasks.append(AgentTask(
            title=f"Code Review: {task.title}",
            description=f"Review implementation of: {task.description}",
            task_type="code_review",
            priority=TaskPriority.MEDIUM,
            created_by=self.agent_id,
            context={**context, "parent_task": task.id}
        ))
        
        # Add documentation
        subtasks.append(AgentTask(
            title=f"Documentation: {task.title}",
            description=f"Document: {task.description}",
            task_type="documentation",
            priority=TaskPriority.LOW,
            created_by=self.agent_id,
            context={**context, "parent_task": task.id}
        ))
        
        return subtasks

    def _decompose_bugfix_task(self, task: AgentTask) -> List[AgentTask]:
        """Decompose a bug fix task."""
        return [
            AgentTask(
                title=f"Analysis: {task.title}",
                description=f"Analyze root cause: {task.description}",
                task_type="bug_analysis",
                priority=task.priority,
                created_by=self.agent_id,
                context=task.context
            ),
            AgentTask(
                title=f"Fix: {task.title}",
                description=f"Implement fix: {task.description}",
                task_type="bug_fix_implementation",
                priority=task.priority,
                created_by=self.agent_id,
                context=task.context
            ),
            AgentTask(
                title=f"Testing: {task.title}",
                description=f"Test fix: {task.description}",
                task_type="regression_testing",
                priority=task.priority,
                created_by=self.agent_id,
                context=task.context
            )
        ]

    def _decompose_code_review_task(self, task: AgentTask) -> List[AgentTask]:
        """Decompose a code review task."""
        return [
            AgentTask(
                title=f"Quality Review: {task.title}",
                description=f"Review code quality: {task.description}",
                task_type="quality_review",
                priority=task.priority,
                created_by=self.agent_id,
                context=task.context
            ),
            AgentTask(
                title=f"Security Review: {task.title}",
                description=f"Review security: {task.description}",
                task_type="security_review",
                priority=TaskPriority.HIGH,
                created_by=self.agent_id,
                context=task.context
            )
        ]

    def _decompose_deployment_task(self, task: AgentTask) -> List[AgentTask]:
        """Decompose a deployment task."""
        return [
            AgentTask(
                title=f"Pre-deployment: {task.title}",
                description=f"Prepare deployment: {task.description}",
                task_type="deployment_preparation",
                priority=task.priority,
                created_by=self.agent_id,
                context=task.context
            ),
            AgentTask(
                title=f"Execute Deployment: {task.title}",
                description=f"Deploy: {task.description}",
                task_type="deployment_execution",
                priority=task.priority,
                created_by=self.agent_id,
                context=task.context
            ),
            AgentTask(
                title=f"Post-deployment: {task.title}",
                description=f"Verify deployment: {task.description}",
                task_type="deployment_verification",
                priority=task.priority,
                created_by=self.agent_id,
                context=task.context
            )
        ]

    def _decompose_testing_task(self, task: AgentTask) -> List[AgentTask]:
        """Decompose a testing task."""
        return [
            AgentTask(
                title=f"Unit Tests: {task.title}",
                description=f"Create unit tests: {task.description}",
                task_type="unit_testing",
                priority=task.priority,
                created_by=self.agent_id,
                context=task.context
            ),
            AgentTask(
                title=f"Integration Tests: {task.title}",
                description=f"Create integration tests: {task.description}",
                task_type="integration_testing",
                priority=task.priority,
                created_by=self.agent_id,
                context=task.context
            )
        ]

    def _decompose_generic_task(self, task: AgentTask) -> List[AgentTask]:
        """Decompose a generic task."""
        return [AgentTask(
            title=task.title,
            description=task.description,
            task_type=task.task_type,
            priority=task.priority,
            created_by=self.agent_id,
            context=task.context
        )]

    def _assign_subtasks(self, parent_task: AgentTask, subtasks: List[AgentTask]) -> Dict[str, str]:
        """
        Assign subtasks to appropriate agents.
        
        Args:
            parent_task: Parent task
            subtasks: List of subtasks to assign
            
        Returns:
            Dictionary mapping subtask IDs to agent IDs
        """
        assignments = {}
        
        for subtask in subtasks:
            best_agent = self._find_best_agent(subtask)
            
            if best_agent:
                subtask.assigned_to = best_agent.agent_id
                subtask.status = TaskStatus.ASSIGNED if hasattr(TaskStatus, 'ASSIGNED') else TaskStatus.PENDING
                
                # Send task to agent
                self.send_message(
                    receiver_id=best_agent.agent_id,
                    message_type="task",
                    content={"task": subtask.to_dict()},
                    priority=subtask.priority
                )
                
                assignments[subtask.id] = best_agent.agent_id
                self.agent_workload[best_agent.agent_id] = self.agent_workload.get(best_agent.agent_id, 0) + 1
                
                self.logger.info(f"Assigned subtask '{subtask.title}' to {best_agent.name}")
            else:
                self.logger.warning(f"No suitable agent found for subtask: {subtask.title}")
                # Escalate if no agent found
                self.escalate_to_higher_level(
                    f"No agent available for task type: {subtask.task_type}",
                    {"subtask": subtask.to_dict(), "parent_task": parent_task.id}
                )
        
        return assignments

    def _find_best_agent(self, task: AgentTask) -> Optional[BaseAgent]:
        """
        Find the best agent for a given task.
        
        Args:
            task: Task to find agent for
            
        Returns:
            Best matching agent or None
        """
        task_type = task.task_type.lower()
        
        # Map task types to agent types
        task_agent_mapping = {
            "backend_implementation": "backend",
            "frontend_implementation": "frontend",
            "test_creation": "tester",
            "unit_testing": "tester",
            "integration_testing": "tester",
            "regression_testing": "tester",
            "code_review": "code_reviewer",
            "quality_review": "code_reviewer",
            "security_review": "security",
            "deployment_preparation": "deployer",
            "deployment_execution": "deployer",
            "deployment_verification": "deployer",
            "documentation": "documenter",
            "bug_analysis": "code_reviewer",
            "bug_fix_implementation": "backend",  # or frontend based on context
        }
        
        target_agent_type = task_agent_mapping.get(task_type)
        
        # Find agents of the target type
        candidates = []
        for agent_info in self.registered_agents.values():
            agent = agent_info["agent"]
            
            # Check if agent type matches
            if target_agent_type and agent.agent_type == target_agent_type:
                candidates.append(agent)
            # Or if agent has the capability
            elif task_type in agent.capabilities:
                candidates.append(agent)
            # Or if agent can handle general tasks
            elif "general" in agent.capabilities:
                candidates.append(agent)
        
        if not candidates:
            return None
        
        # Sort by workload (prefer less busy agents)
        candidates.sort(key=lambda a: self.agent_workload.get(a.agent_id, 0))
        
        # Prefer agents that are idle
        for agent in candidates:
            if agent.status == AgentStatus.IDLE:
                return agent
        
        # Return least busy agent
        return candidates[0]

    def _monitor_task_execution(self, parent_task: AgentTask, assignments: Dict[str, str]) -> Dict[str, Any]:
        """
        Monitor the execution of a task and its subtasks.
        
        Args:
            parent_task: Parent task
            assignments: Subtask to agent assignments
            
        Returns:
            Execution result
        """
        self.logger.info(f"Monitoring execution of task {parent_task.id}")
        
        # Store task in active tasks
        self.active_tasks[parent_task.id] = parent_task
        
        # Return monitoring context
        return {
            "task_id": parent_task.id,
            "status": "in_progress",
            "subtasks": assignments,
            "started_at": datetime.now().isoformat()
        }

    def _handle_task_completed(self, message: AgentMessage):
        """Handle task completion notifications."""
        task_data = message.content.get("task")
        if task_data:
            task = AgentTask.from_dict(task_data)
            
            self.logger.info(f"Task completed: {task.title} by {message.sender}")
            
            # Update workload
            if message.sender in self.agent_workload:
                self.agent_workload[message.sender] = max(0, self.agent_workload[message.sender] - 1)
            
            # Store in history
            self.completed_tasks_history.append({
                "task": task.to_dict(),
                "completed_by": message.sender,
                "completed_at": datetime.now().isoformat()
            })
            
            # Check if parent task is complete
            if task.parent_task and task.parent_task in self.active_tasks:
                self._check_parent_task_completion(task.parent_task)

    def _handle_task_failed(self, message: AgentMessage):
        """Handle task failure notifications."""
        task_data = message.content.get("task")
        error = message.content.get("error", "Unknown error")
        
        if task_data:
            task = AgentTask.from_dict(task_data)
            
            self.logger.error(f"Task failed: {task.title} - {error}")
            
            # Update workload
            if message.sender in self.agent_workload:
                self.agent_workload[message.sender] = max(0, self.agent_workload[message.sender] - 1)
            
            # Handle failure - potentially retry or escalate
            self._handle_task_failure_internal(task, error, message.sender)

    def _handle_agent_registration(self, message: AgentMessage):
        """Handle agent registration requests."""
        agent_info = message.content.get("agent_info")
        if agent_info:
            self.logger.info(f"Agent registration request from {agent_info.get('name')}")

    def _handle_progress_update(self, message: AgentMessage):
        """Handle progress update messages."""
        task_id = message.content.get("task_id")
        progress = message.content.get("progress")
        
        self.logger.info(f"Progress update for task {task_id}: {progress}%")

    def _handle_collaboration_request(self, message: AgentMessage):
        """Handle collaboration requests between agents."""
        collaboration_id = message.content.get("collaboration_id")
        task_data = message.content.get("task")
        collaborators = message.content.get("collaborators", [])
        
        self.active_collaborations[collaboration_id] = {
            "task": task_data,
            "coordinator": message.sender,
            "collaborators": collaborators,
            "status": "active",
            "started_at": datetime.now().isoformat()
        }
        
        self.logger.info(f"Collaboration {collaboration_id} initiated")

    def _handle_resource_request(self, message: AgentMessage):
        """Handle resource allocation requests."""
        resource_type = message.content.get("resource_type")
        self.logger.info(f"Resource request for {resource_type} from {message.sender}")

    def _check_parent_task_completion(self, parent_task_id: str):
        """Check if all subtasks of a parent task are complete."""
        # This would check all subtasks and mark parent as complete if all done
        pass

    def _handle_task_failure_internal(self, task: AgentTask, error: str, agent_id: str):
        """Internal task failure handling."""
        # Implement retry logic or escalation
        pass

    def get_capabilities(self) -> List[str]:
        """Get orchestrator capabilities."""
        return self.capabilities

    def get_system_status(self) -> Dict[str, Any]:
        """
        Get the overall system status.
        
        Returns:
            System status information
        """
        return {
            "orchestrator": self.get_status(),
            "registered_agents": len(self.registered_agents),
            "active_tasks": len(self.active_tasks),
            "task_queue": len(self.task_queue),
            "active_collaborations": len(self.active_collaborations),
            "agent_workload": self.agent_workload,
            "metrics": self.metrics,
            "agents": {
                agent_id: {
                    "name": info["name"],
                    "type": info["type"],
                    "status": info["agent"].status.value
                }
                for agent_id, info in self.registered_agents.items()
            }
        }

    def generate_report(self) -> Dict[str, Any]:
        """
        Generate a comprehensive system report.
        
        Returns:
            Report data
        """
        return {
            "generated_at": datetime.now().isoformat(),
            "system_status": self.get_system_status(),
            "metrics": self.metrics,
            "completed_tasks": len(self.completed_tasks_history),
            "recent_completions": self.completed_tasks_history[-10:] if self.completed_tasks_history else [],
            "knowledge_base_size": len(self.local_knowledge)
        }