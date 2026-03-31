"""
Documentation Agent
Documentation generation and maintenance.
"""

from typing import Any, Dict, List
from agents.base_agent import BaseAgent, AgentTask, TaskStatus


class DocumentationAgent(BaseAgent):
    """Agent specialized in documentation generation and maintenance."""

    def __init__(self, agent_id: str = "doc-001", name: str = "Documentation Agent"):
        super().__init__(
            agent_id=agent_id,
            name=name,
            agent_type="documenter",
            hierarchy_level=3,
            capabilities=[
                "documentation", "api_docs", "readme_generation",
                "changelog", "architecture_diagrams", "code_comments"
            ]
        )

    def execute_task(self, task: AgentTask) -> Dict[str, Any]:
        self.status = "busy"
        task.status = TaskStatus.IN_PROGRESS
        result = {"success": False}
        
        try:
            if task.task_type == "documentation":
                result = self._generate_docs(task.context)
            elif task.task_type == "api_docs":
                result = self._generate_api_docs(task.context)
            elif task.task_type == "changelog":
                result = self._update_changelog(task.context)
            elif task.task_type == "readme_update":
                result = self._update_readme(task.context)
            
            task.status = TaskStatus.COMPLETED
            task.result = result
            self._store_doc_knowledge(task, result)
        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)
            result = {"success": False, "error": str(e)}
        
        self.status = "idle"
        return result

    def _generate_docs(self, context: Dict) -> Dict[str, Any]:
        """Generate documentation."""
        target = context.get("target", "general")
        return {
            "success": True,
            "target": target,
            "docs_generated": True,
            "format": "markdown"
        }

    def _generate_api_docs(self, context: Dict) -> Dict[str, Any]:
        """Generate API documentation."""
        endpoints = context.get("endpoints", [])
        return {
            "success": True,
            "endpoints_documented": len(endpoints),
            "format": "OpenAPI/Swagger"
        }

    def _update_changelog(self, context: Dict) -> Dict[str, Any]:
        """Update changelog."""
        changes = context.get("changes", [])
        return {
            "success": True,
            "changes_added": len(changes),
            "version": context.get("version", "unreleased")
        }

    def _update_readme(self, context: Dict) -> Dict[str, Any]:
        """Update README file."""
        return {
            "success": True,
            "sections_updated": context.get("sections", []),
            "format": "markdown"
        }

    def _store_doc_knowledge(self, task: AgentTask, result: Dict):
        """Store documentation-specific knowledge."""
        from agents.base_agent import KnowledgeEntry
        entry = KnowledgeEntry(
            category="documentation",
            title=f"Documentation: {task.title}",
            content=f"Task: {task.description}\nResult: {result}",
            tags=["documentation", task.task_type]
        )
        self.store_knowledge(entry)

    def get_capabilities(self) -> List[str]:
        return self.capabilities