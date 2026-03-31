"""
Usage Examples for Spinr Development Agents System
Demonstrates how to use the agent hierarchy for autonomous development tasks.
"""

from agents.registry import get_registry
from agents.base_agent import AgentTask, TaskPriority


def example_feature_development():
    """
    Example: Develop a new feature using the agent hierarchy.
    The orchestrator decomposes the task and assigns to specialized agents.
    """
    registry = get_registry()
    
    # Execute a feature development task
    result = registry.execute_task(
        task_type="feature_development",
        title="Implement Real-time Ride Tracking",
        description="Add real-time location tracking for active rides",
        priority="high",
        context={
            "area": "full_stack",
            "components": ["backend", "rider_app", "driver_app"],
            "estimated_hours": 8
        }
    )
    
    print("Feature Development Result:")
    print(f"  Task ID: {result.get('task_id')}")
    print(f"  Status: {result.get('status')}")
    print(f"  Subtasks: {len(result.get('subtasks', {}))}")
    
    return result


def example_code_review():
    """
    Example: Perform automated code review.
    """
    registry = get_registry()
    
    # Execute a code review task
    result = registry.execute_task(
        task_type="code_review",
        title="Review Payment Module",
        description="Review the payment processing module for security issues",
        priority="high",
        context={
            "file_path": "backend/routes/fares.py",
            "focus_areas": ["security", "error_handling", "performance"]
        }
    )
    
    print("Code Review Result:")
    print(f"  Issues Found: {len(result.get('issues', []))}")
    print(f"  Suggestions: {len(result.get('suggestions', []))}")
    
    return result


def example_security_scan():
    """
    Example: Perform security vulnerability scan.
    """
    registry = get_registry()
    
    # Get the security agent directly
    security_agent = registry.get_agent("security")
    
    # Create a security review task
    task = AgentTask(
        title="Security Scan - API Endpoints",
        description="Scan all API endpoints for vulnerabilities",
        task_type="security_review",
        priority=TaskPriority.HIGH,
        context={"target": "backend"}
    )
    
    result = security_agent.execute_task(task)
    
    print("Security Scan Result:")
    print(f"  Risk Level: {result.get('risk_level')}")
    print(f"  Vulnerabilities: {result.get('vulnerabilities_found')}")
    
    return result


def example_knowledge_query():
    """
    Example: Query the knowledge base for patterns and decisions.
    """
    registry = get_registry()
    
    # Query for authentication patterns
    results = registry.query_knowledge("authentication", category="patterns")
    
    print("Knowledge Query Results:")
    for entry in results:
        print(f"  - {entry.get('title')}: {entry.get('content')[:100]}...")
    
    return results


def example_system_status():
    """
    Example: Get overall system status.
    """
    registry = get_registry()
    
    # Get system status
    status = registry.get_system_status()
    
    print("System Status:")
    print(f"  Registered Agents: {status.get('registered_agents')}")
    print(f"  Active Tasks: {status.get('active_tasks')}")
    print(f"  Knowledge Entries: {status.get('metrics', {}).get('tasks_processed', 0)}")
    
    # List all agents
    agents = registry.list_agents()
    print("\nRegistered Agents:")
    for agent in agents:
        print(f"  - {agent['name']} ({agent['type']}): {agent['status']}")
    
    return status


def example_deployment():
    """
    Example: Execute deployment task.
    """
    registry = get_registry()
    
    # Execute deployment task
    result = registry.execute_task(
        task_type="deployment",
        title="Deploy Backend v2.0",
        description="Deploy the latest backend changes to production",
        priority="critical",
        context={
            "target": "backend",
            "version": "2.0.0",
            "environment": "production"
        }
    )
    
    print("Deployment Result:")
    print(f"  Target: {result.get('target')}")
    print(f"  Status: {result.get('status')}")
    
    return result


def example_generate_report():
    """
    Example: Generate comprehensive system report.
    """
    registry = get_registry()
    
    # Generate report
    report = registry.generate_report()
    
    print("System Report:")
    print(f"  Generated At: {report.get('generated_at')}")
    print(f"  Completed Tasks: {report.get('completed_tasks')}")
    print(f"  Knowledge Base Size: {report.get('knowledge_base_size')}")
    
    return report


def run_all_examples():
    """Run all example functions."""
    print("=" * 60)
    print("Spinr Development Agents System - Usage Examples")
    print("=" * 60)
    
    examples = [
        ("System Status", example_system_status),
        ("Feature Development", example_feature_development),
        ("Code Review", example_code_review),
        ("Security Scan", example_security_scan),
        ("Knowledge Query", example_knowledge_query),
        ("Deployment", example_deployment),
        ("Generate Report", example_generate_report),
    ]
    
    for name, example_func in examples:
        print(f"\n{'=' * 60}")
        print(f"Example: {name}")
        print("=" * 60)
        try:
            example_func()
        except Exception as e:
            print(f"Error in {name}: {e}")
    
    print("\n" + "=" * 60)
    print("All examples completed!")
    print("=" * 60)


if __name__ == "__main__":
    run_all_examples()