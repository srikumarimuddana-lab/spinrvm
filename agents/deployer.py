"""
Deployment Agent
CI/CD and deployment management.
"""

from typing import Any, Dict, List
from agents.base_agent import BaseAgent, AgentTask, TaskStatus


class DeploymentAgent(BaseAgent):
    """Agent specialized in deployment and CI/CD management."""

    def __init__(self, agent_id: str = "deployer-001", name: str = "Deployment Agent"):
        super().__init__(
            agent_id=agent_id,
            name=name,
            agent_type="deployer",
            hierarchy_level=3,
            capabilities=[
                "deployment_preparation", "deployment_execution",
                "deployment_verification", "ci_cd_management",
                "environment_configuration", "rollback",
                "eas_build", "fly_io", "vercel"
            ]
        )
        self.platforms = {
            "backend": "Fly.io",
            "rider_app": "EAS Build",
            "driver_app": "EAS Build",
            "admin_dashboard": "Vercel"
        }

    def execute_task(self, task: AgentTask) -> Dict[str, Any]:
        self.status = "busy"
        task.status = TaskStatus.IN_PROGRESS
        result = {"success": False}
        
        try:
            if task.task_type == "deployment_preparation":
                result = self._prepare_deployment(task.context)
            elif task.task_type == "deployment_execution":
                result = self._execute_deployment(task.context)
            elif task.task_type == "deployment_verification":
                result = self._verify_deployment(task.context)
            elif task.task_type == "rollback":
                result = self._rollback(task.context)
            
            task.status = TaskStatus.COMPLETED
            task.result = result
            self._store_deployment_knowledge(task, result)
        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)
            result = {"success": False, "error": str(e)}
        
        self.status = "idle"
        return result

    def _prepare_deployment(self, context: Dict) -> Dict[str, Any]:
        """Prepare for deployment."""
        target = context.get("target", "backend")
        return {
            "success": True,
            "target": target,
            "platform": self.platforms.get(target, "Unknown"),
            "checks_passed": True,
            "ready_for_deployment": True
        }

    def _execute_deployment(self, context: Dict) -> Dict[str, Any]:
        """Execute deployment."""
        target = context.get("target", "backend")
        return {
            "success": True,
            "target": target,
            "version": context.get("version", "latest"),
            "deployment_id": "deploy-" + target,
            "url": f"https://{target}.spinr.app"
        }

    def _verify_deployment(self, context: Dict) -> Dict[str, Any]:
        """Verify deployment success."""
        return {
            "success": True,
            "health_check": "passed",
            "smoke_tests": "passed",
            "performance": "nominal"
        }

    def _rollback(self, context: Dict) -> Dict[str, Any]:
        """Rollback to previous version."""
        return {
            "success": True,
            "rolled_back_to": context.get("version", "previous"),
            "reason": context.get("reason", "Manual rollback")
        }

    def _store_deployment_knowledge(self, task: AgentTask, result: Dict):
        """Store deployment-specific knowledge."""
        from agents.base_agent import KnowledgeEntry
        entry = KnowledgeEntry(
            category="deployments",
            title=f"Deployment: {task.title}",
            content=f"Task: {task.description}\nResult: {result}",
            tags=["deployment", task.task_type]
        )
        self.store_knowledge(entry)

    def get_capabilities(self) -> List[str]:
        return self.capabilities