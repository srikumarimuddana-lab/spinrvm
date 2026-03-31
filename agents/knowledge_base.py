"""
Knowledge Base Agent
Manages the shared knowledge repository for all agents.
"""

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from agents.base_agent import (
    BaseAgent, AgentTask, TaskStatus, KnowledgeEntry
)


class KnowledgeBaseAgent(BaseAgent):
    """Agent specialized in managing project knowledge."""

    def __init__(self, agent_id: str = "kb-001", name: str = "Knowledge Base Agent"):
        super().__init__(
            agent_id=agent_id,
            name=name,
            agent_type="knowledge_base",
            hierarchy_level=1,
            capabilities=[
                "knowledge_storage", "knowledge_retrieval",
                "pattern_matching", "decision_tracking",
                "technical_debt_analysis", "code_evolution_tracking"
            ]
        )
        self.decisions: Dict[str, Dict] = {}
        self.patterns: Dict[str, Dict] = {}
        self.issues_history: List[Dict] = []

    def execute_task(self, task: AgentTask) -> Dict[str, Any]:
        self.status = "busy"
        task.status = TaskStatus.IN_PROGRESS
        result = {"success": False}
        
        try:
            if task.task_type == "store_knowledge":
                entry = KnowledgeEntry(**task.context.get("entry", {}))
                self.store_knowledge(entry)
                result = {"success": True, "entry_id": entry.id}
            elif task.task_type == "query_knowledge":
                results = self.query_knowledge(task.context.get("query", ""))
                result = {"success": True, "results": results}
            elif task.task_type == "record_decision":
                self.record_decision(task.context)
                result = {"success": True}
            
            task.status = TaskStatus.COMPLETED
            task.result = result
        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)
            result = {"success": False, "error": str(e)}
        
        self.status = "idle"
        return result

    def record_decision(self, decision: Dict[str, Any]):
        """Record an architecture decision."""
        decision_id = decision.get("id", str(len(self.decisions)))
        self.decisions[decision_id] = {
            **decision,
            "recorded_at": datetime.now().isoformat()
        }
        entry = KnowledgeEntry(
            category="decisions",
            title=decision.get("title", "Decision"),
            content=json.dumps(decision, indent=2),
            tags=decision.get("tags", [])
        )
        self.store_knowledge(entry)

    def record_pattern(self, pattern: Dict[str, Any]):
        """Record a code pattern."""
        pattern_id = pattern.get("id", str(len(self.patterns)))
        self.patterns[pattern_id] = pattern
        entry = KnowledgeEntry(
            category="patterns",
            title=pattern.get("name", "Pattern"),
            content=json.dumps(pattern, indent=2),
            tags=pattern.get("tags", [])
        )
        self.store_knowledge(entry)

    def get_capabilities(self) -> List[str]:
        return self.capabilities