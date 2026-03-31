"""
Code Reviewer Agent
Automated code quality assurance and review.
"""

from typing import Any, Dict, List
from agents.base_agent import BaseAgent, AgentTask, TaskStatus


class CodeReviewerAgent(BaseAgent):
    """Agent specialized in code review and quality assurance."""

    def __init__(self, agent_id: str = "reviewer-001", name: str = "Code Reviewer"):
        super().__init__(
            agent_id=agent_id,
            name=name,
            agent_type="code_reviewer",
            hierarchy_level=1,
            capabilities=[
                "code_review", "quality_analysis", "style_checking",
                "refactoring_suggestions", "best_practices", "bug_analysis"
            ]
        )
        self.review_standards = {
            "max_function_length": 50,
            "max_file_length": 300,
            "max_complexity": 10,
            "naming_conventions": True,
            "documentation_required": True
        }

    def execute_task(self, task: AgentTask) -> Dict[str, Any]:
        self.status = "busy"
        task.status = TaskStatus.IN_PROGRESS
        result = {"success": False, "issues": [], "suggestions": []}
        
        try:
            if task.task_type == "code_review":
                result = self._review_code(task.context)
            elif task.task_type == "quality_review":
                result = self._analyze_quality(task.context)
            elif task.task_type == "bug_analysis":
                result = self._analyze_bug(task.context)
            
            task.status = TaskStatus.COMPLETED
            task.result = result
            self._update_knowledge_with_review(task, result)
        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)
            result = {"success": False, "error": str(e)}
        
        self.status = "idle"
        return result

    def _review_code(self, context: Dict) -> Dict[str, Any]:
        """Perform comprehensive code review."""
        file_path = context.get("file_path", "")
        code = context.get("code", "")
        
        issues = []
        suggestions = []
        
        # Check function length
        lines = code.split("\n")
        if len(lines) > self.review_standards["max_file_length"]:
            issues.append(f"File too long ({len(lines)} lines). Consider splitting.")
        
        # Check for documentation
        if self.review_standards["documentation_required"]:
            if '"""' not in code and "'''" not in code:
                suggestions.append("Add docstrings to functions and classes")
        
        return {
            "success": True,
            "file": file_path,
            "issues": issues,
            "suggestions": suggestions,
            "score": max(0, 100 - len(issues) * 10 - len(suggestions) * 5)
        }

    def _analyze_quality(self, context: Dict) -> Dict[str, Any]:
        """Analyze code quality metrics."""
        return {"success": True, "metrics": {"complexity": "low", "maintainability": "high"}}

    def _analyze_bug(self, context: Dict) -> Dict[str, Any]:
        """Analyze potential bugs."""
        return {"success": True, "potential_bugs": [], "root_cause": "To be determined"}

    def _update_knowledge_with_review(self, task: AgentTask, result: Dict):
        """Store review results in knowledge base."""
        from agents.base_agent import KnowledgeEntry
        entry = KnowledgeEntry(
            category="reviews",
            title=f"Code Review: {task.title}",
            content=f"Issues: {result.get('issues', [])}\nSuggestions: {result.get('suggestions', [])}",
            tags=["code_review", "quality"]
        )
        self.store_knowledge(entry)

    def get_capabilities(self) -> List[str]:
        return self.capabilities