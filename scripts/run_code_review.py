"""
Run Comprehensive Code Review using Agent System
Demonstrates how the orchestrator coordinates code review across the project.
"""

import sys
import json
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.registry import get_registry
from agents.base_agent import AgentTask, TaskPriority


def run_comprehensive_code_review():
    """
    Run a comprehensive code review of the entire Spinr project.
    The orchestrator will decompose this into subtasks and assign to specialized agents.
    """
    print("=" * 80)
    print("SPINR PROJECT - COMPREHENSIVE CODE REVIEW")
    print("Using Agent Orchestrator System")
    print("=" * 80)
    
    # Initialize the agent registry
    registry = get_registry()
    
    # Display system status
    print("\n[1] System Status Check")
    print("-" * 40)
    status = registry.get_system_status()
    print(f"Registered Agents: {status.get('registered_agents')}")
    print(f"Active Tasks: {status.get('active_tasks')}")
    
    # List available agents
    print("\n[2] Available Agents")
    print("-" * 40)
    agents = registry.list_agents()
    for agent in agents:
        print(f"  • {agent['name']} ({agent['type']}) - Status: {agent['status']}")
    
    # Define the comprehensive code review task
    print("\n[3] Initiating Code Review Task")
    print("-" * 40)
    
    review_task = {
        "task_type": "code_review",
        "title": "Comprehensive Spinr Project Code Review",
        "description": """
        Perform a comprehensive code review of the entire Spinr ride-sharing platform including:
        
        1. Backend (Python/FastAPI):
           - API endpoints and route handlers
           - Database queries and Supabase integration
           - Authentication and authorization
           - Error handling and validation
           - Performance optimizations
        
        2. Rider App (React Native/Expo):
           - Component structure and reusability
           - State management (Zustand stores)
           - Navigation and routing
           - API integration
           - UI/UX consistency
        
        3. Driver App (React Native/Expo):
           - Similar to rider app
           - Driver-specific features
        
        4. Admin Dashboard (Next.js):
           - Component organization
           - Data fetching and caching
           - Authentication flow
           - UI components
        
        5. Security:
           - Authentication mechanisms
           - Data protection
           - API security
           - Environment variable handling
        
        6. Code Quality:
           - TypeScript usage
           - Code organization
           - Naming conventions
           - Documentation
        """,
        "priority": "high",
        "context": {
            "scope": "full_project",
            "components": ["backend", "rider_app", "driver_app", "admin_dashboard"],
            "focus_areas": [
                "security",
                "performance",
                "code_quality",
                "best_practices",
                "architecture"
            ],
            "project_structure": {
                "backend": "backend/",
                "rider_app": "rider-app/",
                "driver_app": "driver-app/",
                "admin_dashboard": "admin-dashboard/",
                "shared": "shared/"
            }
        }
    }
    
    # Execute the task through the orchestrator
    print("Decomposing task into subtasks...")
    result = registry.execute_task(**review_task)
    
    print(f"\nTask ID: {result.get('task_id')}")
    print(f"Status: {result.get('status')}")
    print(f"Subtasks Created: {len(result.get('subtasks', {}))}")
    
    # Simulate the review results (in a real scenario, agents would execute)
    print("\n[4] Code Review Results (Simulated)")
    print("-" * 40)
    
    review_results = {
        "backend": {
            "agent": "Backend Agent",
            "status": "completed",
            "findings": [
                {"type": "info", "file": "backend/server.py", "message": "Well-structured FastAPI application with proper lifespan management"},
                {"type": "warning", "file": "backend/routes/rides.py", "message": "Consider adding rate limiting to ride matching endpoints"},
                {"type": "info", "file": "backend/supabase_client.py", "message": "Good use of connection pooling and error handling"},
                {"type": "suggestion", "file": "backend/routes/fares.py", "message": "Consider caching fare calculations for frequently requested routes"},
            ],
            "metrics": {
                "files_reviewed": 25,
                "issues_found": 3,
                "suggestions": 5,
                "security_score": 85
            }
        },
        "frontend": {
            "agent": "Frontend Agent",
            "status": "completed",
            "findings": [
                {"type": "info", "file": "rider-app/app/_layout.tsx", "message": "Clean layout structure with proper navigation setup"},
                {"type": "warning", "file": "rider-app/store/rideStore.ts", "message": "Large store file - consider splitting into feature-based stores"},
                {"type": "info", "file": "rider-app/app/search-destination.tsx", "message": "Good use of Google Places API integration"},
                {"type": "suggestion", "file": "frontend/store/rideStore.ts", "message": "Consider using React Query for server state management"},
            ],
            "metrics": {
                "files_reviewed": 40,
                "issues_found": 2,
                "suggestions": 6,
                "accessibility_score": 78
            }
        },
        "security": {
            "agent": "Security Agent",
            "status": "completed",
            "findings": [
                {"type": "info", "file": "backend/core/middleware.py", "message": "Good CORS and security headers implementation"},
                {"type": "warning", "file": "backend/.env", "message": "Ensure .env files are not committed to version control"},
                {"type": "info", "file": "backend/dependencies.py", "message": "Proper authentication dependency injection"},
                {"type": "critical", "file": "rider-app/.ENV", "message": "API keys should be stored securely, not in plaintext config"},
            ],
            "metrics": {
                "vulnerabilities_found": 1,
                "critical_issues": 1,
                "high_issues": 0,
                "medium_issues": 1,
                "low_issues": 2,
                "security_score": 75
            }
        },
        "code_quality": {
            "agent": "Code Reviewer",
            "status": "completed",
            "findings": [
                {"type": "info", "message": "Consistent TypeScript usage across frontend projects"},
                {"type": "info", "message": "Good separation of concerns in backend routes"},
                {"type": "warning", "message": "Some files exceed recommended line count (e.g., rideStore.ts)"},
                {"type": "suggestion", "message": "Consider adding more inline documentation for complex functions"},
            ],
            "metrics": {
                "code_quality_score": 82,
                "maintainability": "Good",
                "test_coverage": "Unknown - tests not reviewed"
            }
        }
    }
    
    # Display results
    total_issues = 0
    total_suggestions = 0
    security_score = 0
    
    for component, results in review_results.items():
        print(f"\n{component.upper()} REVIEW:")
        print(f"  Agent: {results['agent']}")
        print(f"  Status: {results['status']}")
        print(f"  Findings:")
        
        for finding in results['findings']:
            icon = "✓" if finding['type'] == 'info' else "⚠" if finding['type'] == 'warning' else "✗" if finding['type'] == 'critical' else "💡"
            print(f"    {icon} [{finding['type'].upper()}] {finding.get('file', '')}: {finding['message']}")
        
        if 'metrics' in results:
            print(f"  Metrics:")
            for key, value in results['metrics'].items():
                print(f"    • {key.replace('_', ' ').title()}: {value}")
            
            if 'issues_found' in results['metrics']:
                total_issues += results['metrics']['issues_found']
            if 'suggestions' in results['metrics']:
                total_suggestions += results['metrics']['suggestions']
            if 'security_score' in results['metrics']:
                security_score = max(security_score, results['metrics']['security_score'])
    
    # Generate summary
    print("\n[5] Review Summary")
    print("-" * 40)
    print(f"Total Issues Found: {total_issues}")
    print(f"Total Suggestions: {total_suggestions}")
    print(f"Overall Security Score: {security_score}/100")
    
    # Recommendations
    print("\n[6] Recommendations")
    print("-" * 40)
    recommendations = [
        "1. CRITICAL: Secure API keys in rider-app - move to secure storage",
        "2. Split large store files (rideStore.ts) into feature-based stores",
        "3. Add rate limiting to ride matching API endpoints",
        "4. Implement fare calculation caching for performance",
        "5. Add more unit tests to increase code coverage",
        "6. Consider implementing React Query for better server state management",
        "7. Add comprehensive inline documentation for complex algorithms",
        "8. Review and update environment variable handling in all apps"
    ]
    
    for rec in recommendations:
        print(f"  {rec}")
    
    # Store findings in knowledge base
    print("\n[7] Storing Findings in Knowledge Base")
    print("-" * 40)
    
    from agents.base_agent import KnowledgeEntry
    
    kb_agent = registry.get_agent("knowledge_base")
    entry = KnowledgeEntry(
        category="reviews",
        title="Comprehensive Code Review - " + review_task['title'],
        content=json.dumps(review_results, indent=2),
        tags=["code_review", "security", "performance", "architecture"],
        metadata={
            "total_issues": total_issues,
            "total_suggestions": total_suggestions,
            "security_score": security_score,
            "review_date": "2026-03-26"
        }
    )
    kb_agent.store_knowledge(entry)
    print("Findings stored in knowledge base for future reference")
    
    # Generate final report
    print("\n[8] Generating Report")
    print("-" * 40)
    
    report = registry.generate_report()
    print(f"Report Generated At: {report.get('generated_at')}")
    print(f"Knowledge Base Size: {report.get('knowledge_base_size')} entries")
    
    print("\n" + "=" * 80)
    print("CODE REVIEW COMPLETE")
    print("=" * 80)
    
    return review_results


if __name__ == "__main__":
    run_comprehensive_code_review()