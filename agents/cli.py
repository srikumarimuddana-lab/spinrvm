"""
CLI Interface for Spinr Development Agents
Allows Cline and developers to interact with the agent system via command line.
"""

import argparse
import json
import sys
from pathlib import Path

from agents.registry import get_registry


def execute_task(args):
    """Execute a task through the agent system."""
    registry = get_registry()
    
    context = {}
    if args.context:
        try:
            context = json.loads(args.context)
        except json.JSONDecodeError:
            print("Error: Invalid JSON in context parameter")
            sys.exit(1)
    
    result = registry.execute_task(
        task_type=args.type,
        title=args.title,
        description=args.description or args.title,
        priority=args.priority,
        context=context
    )
    
    print(json.dumps(result, indent=2))


def list_agents(args):
    """List all registered agents."""
    registry = get_registry()
    agents = registry.list_agents()
    
    print("\nRegistered Agents:")
    print("-" * 60)
    for agent in agents:
        print(f"ID: {agent['id']}")
        print(f"Name: {agent['name']}")
        print(f"Type: {agent['type']}")
        print(f"Status: {agent['status']}")
        print(f"Capabilities: {', '.join(agent['capabilities'][:3])}...")
        print("-" * 60)


def system_status(args):
    """Get system status."""
    registry = get_registry()
    status = registry.get_system_status()
    
    print("\nSystem Status:")
    print(f"  Registered Agents: {status.get('registered_agents')}")
    print(f"  Active Tasks: {status.get('active_tasks')}")
    print(f"  Task Queue: {status.get('task_queue')}")
    print(f"  Active Collaborations: {status.get('active_collaborations')}")
    
    print("\nAgent Workload:")
    for agent_id, workload in status.get('agent_workload', {}).items():
        print(f"  {agent_id}: {workload} tasks")


def query_knowledge(args):
    """Query the knowledge base."""
    registry = get_registry()
    results = registry.query_knowledge(args.query, category=args.category)
    
    print(f"\nKnowledge Query Results for '{args.query}':")
    print("-" * 60)
    for entry in results[:args.limit]:
        print(f"Title: {entry.get('title')}")
        print(f"Category: {entry.get('category')}")
        print(f"Tags: {', '.join(entry.get('tags', []))}")
        print(f"Content: {entry.get('content')[:200]}...")
        print("-" * 60)


def generate_report(args):
    """Generate system report."""
    registry = get_registry()
    report = registry.generate_report()
    
    print("\nSystem Report:")
    print(f"Generated At: {report.get('generated_at')}")
    print(f"Completed Tasks: {report.get('completed_tasks')}")
    print(f"Knowledge Base Size: {report.get('knowledge_base_size')}")
    
    if args.output:
        with open(args.output, 'w') as f:
            json.dump(report, f, indent=2)
        print(f"\nReport saved to: {args.output}")


def main():
    parser = argparse.ArgumentParser(
        description="Spinr Development Agents CLI"
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")
    
    # Execute task command
    task_parser = subparsers.add_parser("execute", help="Execute a task")
    task_parser.add_argument("--type", required=True, help="Task type")
    task_parser.add_argument("--title", required=True, help="Task title")
    task_parser.add_argument("--description", help="Task description")
    task_parser.add_argument("--priority", default="medium", 
                           choices=["critical", "high", "medium", "low"])
    task_parser.add_argument("--context", help="JSON context for the task")
    task_parser.set_defaults(func=execute_task)
    
    # List agents command
    agents_parser = subparsers.add_parser("agents", help="List all agents")
    agents_parser.set_defaults(func=list_agents)
    
    # System status command
    status_parser = subparsers.add_parser("status", help="Get system status")
    status_parser.set_defaults(func=system_status)
    
    # Query knowledge command
    query_parser = subparsers.add_parser("query", help="Query knowledge base")
    query_parser.add_argument("query", help="Search query")
    query_parser.add_argument("--category", help="Category filter")
    query_parser.add_argument("--limit", type=int, default=10, help="Result limit")
    query_parser.set_defaults(func=query_knowledge)
    
    # Generate report command
    report_parser = subparsers.add_parser("report", help="Generate system report")
    report_parser.add_argument("--output", "-o", help="Output file path")
    report_parser.set_defaults(func=generate_report)
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    args.func(args)


if __name__ == "__main__":
    main()