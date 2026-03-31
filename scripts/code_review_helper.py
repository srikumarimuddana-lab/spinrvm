#!/usr/bin/env python3
"""
Code Review Helper Tool for Spinr Platform

This script automates the code review workflow defined in .agents/workflows/code-review.md
It provides interactive checklists, automated security scans, and generates review reports.

Usage:
    python scripts/code_review_helper.py [commit_range]
    
Examples:
    python scripts/code_review_helper.py HEAD~1
    python scripts/code_review_helper.py main..feature-branch
    python scripts/code_review_helper.py --interactive
"""

import os
import sys
import io
import json
import subprocess
import argparse
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Set, Tuple, Optional
from dataclasses import dataclass, asdict
from enum import Enum
import re

# Fix Windows console encoding (cp1252 can't handle Unicode symbols)
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')


class Severity(Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


@dataclass
class ReviewFinding:
    severity: Severity
    category: str
    file: str
    line: Optional[int]
    description: str
    suggestion: Optional[str] = None


@dataclass
class ReviewReport:
    date: str
    reviewer: str
    commit_range: str
    files_reviewed: int
    lines_changed: int
    findings: List[ReviewFinding]
    security_score: int
    performance_score: int
    quality_score: int
    overall_status: str


class CodeReviewHelper:
    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.backend_dir = project_root / "backend"
        self.rider_app_dir = project_root / "rider-app"
        self.driver_app_dir = project_root / "driver-app"
        self.admin_dashboard_dir = project_root / "admin-dashboard"
        
        # Security patterns to detect
        self.secrets_patterns = [
            r"sk_live_[a-zA-Z0-9]+",  # Stripe live keys
            r"sk_test_[a-zA-Z0-9]+",  # Stripe test keys
            r"password\s*=\s*['\"][^'\"]+['\"]",  # Passwords
            r"secret\s*=\s*['\"][^'\"]+['\"]",  # Secrets
            r"api[_-]?key\s*=\s*['\"][^'\"]+['\"]",  # API keys
            r"token\s*=\s*['\"][^'\"]+['\"]",  # Tokens
        ]
        
        # Code quality patterns
        self.code_quality_patterns = {
            "long_functions": r"def\s+\w+.*?(?=\ndef|\Z)",
            "todo_comments": r"(TODO|FIXME|HACK|XXX)",
            "print_statements": r"print\s*\(",
            "console_logs": r"console\.(log|warn|error)\s*\(",
        }

    def get_changed_files(self, commit_range: str = "HEAD~1") -> List[str]:
        """Get list of changed files in the commit range."""
        try:
            result = subprocess.run(
                ["git", "diff", "--name-only", commit_range],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                check=True
            )
            files = [f.strip() for f in result.stdout.split('\n') if f.strip()]
            return files
        except subprocess.CalledProcessError as e:
            print(f"Error getting changed files: {e}")
            return []

    def categorize_files(self, files: List[str]) -> Dict[str, List[str]]:
        """Categorize changed files by component."""
        categories = {
            "backend": [],
            "rider_app": [],
            "driver_app": [],
            "admin_dashboard": [],
            "shared": [],
            "config": [],
            "other": []
        }
        
        for file in files:
            if file.startswith("backend/"):
                categories["backend"].append(file)
            elif file.startswith("rider-app/"):
                categories["rider_app"].append(file)
            elif file.startswith("driver-app/"):
                categories["driver_app"].append(file)
            elif file.startswith("admin-dashboard/"):
                categories["admin_dashboard"].append(file)
            elif file.startswith("shared/"):
                categories["shared"].append(file)
            elif file in ["package.json", "package-lock.json", "requirements.txt", 
                         "Dockerfile", "docker-compose.yml", ".env", ".env.example"]:
                categories["config"].append(file)
            else:
                categories["other"].append(file)
        
        return categories

    def scan_for_secrets(self, files: List[str]) -> List[ReviewFinding]:
        """Scan files for potential secrets and sensitive information."""
        findings = []
        
        for file in files:
            file_path = self.project_root / file
            if not file_path.exists():
                continue
                
            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                    lines = content.split('\n')
                
                for i, line in enumerate(lines, 1):
                    for pattern in self.secrets_patterns:
                        if re.search(pattern, line, re.IGNORECASE):
                            findings.append(ReviewFinding(
                                severity=Severity.CRITICAL,
                                category="security",
                                file=file,
                                line=i,
                                description=f"Potential secret detected: {line.strip()}",
                                suggestion="Remove hardcoded secrets and use environment variables"
                            ))
                            break
                
                # Check for sensitive data in logs
                if "logger" in content or "console.log" in content:
                    for i, line in enumerate(lines, 1):
                        if re.search(r"(logger\.(info|debug|warn|error)|console\.log).*\b(password|secret|token|key)\b", line, re.IGNORECASE):
                            findings.append(ReviewFinding(
                                severity=Severity.HIGH,
                                category="security",
                                file=file,
                                line=i,
                                description="Sensitive data in logs detected",
                                suggestion="Remove sensitive information from log statements"
                            ))
                
            except Exception as e:
                print(f"Error reading {file}: {e}")
        
        return findings

    def check_auth_dependencies(self, backend_files: List[str]) -> List[ReviewFinding]:
        """Check if protected endpoints have proper auth dependencies."""
        findings = []
        
        for file in backend_files:
            if not file.endswith('.py'):
                continue
                
            file_path = self.project_root / file
            if not file_path.exists():
                continue
                
            try:
                with open(file_path, 'r') as f:
                    content = f.read()
                
                # Find route decorators
                route_pattern = r'@(router\.(get|post|put|delete|patch))\s*\n\s*(async\s+def\s+\w+)'
                routes = re.findall(route_pattern, content, re.MULTILINE)
                
                for route_type, func_def in routes:
                    # Check if the function has auth dependency
                    func_name = func_def.split()[-1]
                    func_start = content.find(f"async def {func_name}")
                    if func_start == -1:
                        continue
                    
                    # Look for auth dependencies in the next few lines
                    func_lines = content[func_start:].split('\n')[:10]
                    has_auth = any("Depends(get_current_user)" in line or 
                                 "Depends(get_admin_user)" in line for line in func_lines)
                    
                    # Skip public endpoints (usually /health, /docs, etc.)
                    if not has_auth and not any(pattern in func_name for pattern in 
                                              ["health", "docs", "openapi", "favicon"]):
                        findings.append(ReviewFinding(
                            severity=Severity.HIGH,
                            category="security",
                            file=file,
                            line=content[:func_start].count('\n') + 1,
                            description=f"Protected endpoint '{func_name}' missing auth dependency",
                            suggestion="Add Depends(get_current_user) or Depends(get_admin_user)"
                        ))
                
            except Exception as e:
                print(f"Error checking auth in {file}: {e}")
        
        return findings

    def check_type_annotations(self, files: List[str]) -> List[ReviewFinding]:
        """Check for missing type annotations."""
        findings = []
        
        for file in files:
            if not file.endswith(('.py', '.ts', '.tsx')):
                continue
                
            file_path = self.project_root / file
            if not file_path.exists():
                continue
                
            try:
                with open(file_path, 'r') as f:
                    content = f.read()
                
                if file.endswith('.py'):
                    # Check Python functions for type annotations
                    func_pattern = r'def\s+(\w+)\s*\([^)]*\)\s*(?::|$)'
                    functions = re.findall(func_pattern, content)
                    
                    for func_name in functions:
                        if func_name.startswith('_'):  # Skip private functions
                            continue
                            
                        func_start = content.find(f"def {func_name}")
                        if func_start == -1:
                            continue
                            
                        func_line = content[:func_start].count('\n') + 1
                        func_def = content[func_start:func_start + 200]  # Get function definition
                        
                        if '->' not in func_def and '(' in func_def:
                            findings.append(ReviewFinding(
                                severity=Severity.MEDIUM,
                                category="code_quality",
                                file=file,
                                line=func_line,
                                description=f"Function '{func_name}' missing return type annotation",
                                suggestion="Add return type annotation (e.g., -> str)"
                            ))
                
                elif file.endswith(('.ts', '.tsx')):
                    # Check TypeScript interfaces and types
                    if "interface" not in content and "type " not in content:
                        findings.append(ReviewFinding(
                            severity=Severity.MEDIUM,
                            category="code_quality",
                            file=file,
                            line=1,
                            description="No TypeScript interfaces or types found",
                            suggestion="Define interfaces for props and data structures"
                        ))
                
            except Exception as e:
                print(f"Error checking type annotations in {file}: {e}")
        
        return findings

    def check_error_handling(self, backend_files: List[str]) -> List[ReviewFinding]:
        """Check for proper error handling in backend endpoints."""
        findings = []
        
        for file in backend_files:
            if not file.endswith('.py'):
                continue
                
            file_path = self.project_root / file
            if not file_path.exists():
                continue
                
            try:
                with open(file_path, 'r') as f:
                    content = f.read()
                
                # Find async functions that might be endpoints
                async_func_pattern = r'async def\s+(\w+)\s*\([^)]*\):'
                functions = re.findall(async_func_pattern, content)
                
                for func_name in functions:
                    if func_name.startswith('_'):
                        continue
                        
                    func_start = content.find(f"async def {func_name}")
                    if func_start == -1:
                        continue
                        
                    # Get function body (approximate)
                    func_lines = content[func_start:].split('\n')
                    func_body = []
                    indent_level = None
                    
                    for line in func_lines[1:]:  # Skip function definition
                        if line.strip() == '':
                            continue
                        if indent_level is None and line.strip():
                            indent_level = len(line) - len(line.lstrip())
                        
                        if line.strip() and (len(line) - len(line.lstrip())) <= indent_level:
                            break
                        func_body.append(line)
                    
                    func_body_text = '\n'.join(func_body)
                    
                    # Check for try-catch blocks
                    has_try_catch = 'try:' in func_body_text and 'except' in func_body_text
                    has_http_exception = 'HTTPException' in func_body_text
                    has_logging = 'logger' in func_body_text or 'loguru' in func_body_text
                    
                    if not has_try_catch and not has_http_exception:
                        findings.append(ReviewFinding(
                            severity=Severity.MEDIUM,
                            category="error_handling",
                            file=file,
                            line=content[:func_start].count('\n') + 1,
                            description=f"Function '{func_name}' missing error handling",
                            suggestion="Add try-catch blocks or HTTPException handling"
                        ))
                    
                    if not has_logging:
                        findings.append(ReviewFinding(
                            severity=Severity.LOW,
                            category="error_handling",
                            file=file,
                            line=content[:func_start].count('\n') + 1,
                            description=f"Function '{func_name}' missing error logging",
                            suggestion="Add proper error logging with context"
                        ))
                
            except Exception as e:
                print(f"Error checking error handling in {file}: {e}")
        
        return findings

    def generate_security_report(self, findings: List[ReviewFinding]) -> int:
        """Calculate security score based on findings."""
        critical_count = len([f for f in findings if f.severity == Severity.CRITICAL and f.category == "security"])
        high_count = len([f for f in findings if f.severity == Severity.HIGH and f.category == "security"])
        
        # Base score 100, subtract points for issues
        score = 100
        score -= critical_count * 25
        score -= high_count * 10
        
        return max(0, score)

    def generate_performance_report(self, files: List[str]) -> Tuple[int, List[str]]:
        """Analyze performance issues."""
        issues = []
        
        for file in files:
            file_path = self.project_root / file
            if not file_path.exists():
                continue
                
            try:
                with open(file_path, 'r') as f:
                    content = f.read()
                
                # Check for N+1 query patterns (simplified)
                if "for" in content and "find_one" in content:
                    issues.append(f"Potential N+1 query in {file}")
                
                # Check for large file operations
                if "open(" in content and "read()" in content:
                    issues.append(f"Large file read operation in {file}")
                
                # Check for missing pagination
                if "find_many" in content and "limit" not in content:
                    issues.append(f"Missing pagination in {file}")
                
            except Exception as e:
                print(f"Error analyzing performance in {file}: {e}")
        
        # Calculate score
        score = 100 - (len(issues) * 5)
        return max(0, score), issues

    def generate_quality_report(self, files: List[str]) -> Tuple[int, List[str]]:
        """Analyze code quality issues."""
        issues = []
        
        for file in files:
            file_path = self.project_root / file
            if not file_path.exists():
                continue
                
            try:
                with open(file_path, 'r') as f:
                    lines = f.readlines()
                
                # Check file length
                if len(lines) > 300:
                    issues.append(f"File too long ({len(lines)} lines): {file}")
                
                # Check for TODO comments
                for i, line in enumerate(lines, 1):
                    if re.search(r"(TODO|FIXME|HACK|XXX)", line, re.IGNORECASE):
                        issues.append(f"TODO comment at {file}:{i}")
                
                # Check for print statements
                for i, line in enumerate(lines, 1):
                    if "print(" in line and "def print" not in line:
                        issues.append(f"Print statement at {file}:{i}")
                
            except Exception as e:
                print(f"Error analyzing quality in {file}: {e}")
        
        # Calculate score
        score = 100 - (len(issues) * 2)
        return max(0, score), issues

    def interactive_review(self, files: List[str]) -> List[ReviewFinding]:
        """Interactive review process."""
        findings = []
        
        print("\n" + "="*60)
        print("INTERACTIVE CODE REVIEW")
        print("="*60)
        
        categories = self.categorize_files(files)
        
        for category, file_list in categories.items():
            if not file_list:
                continue
                
            print(f"\n{category.upper()} FILES ({len(file_list)} files):")
            for file in file_list:
                print(f"  - {file}")
        
        # Security review
        print("\n" + "-"*40)
        print("SECURITY REVIEW")
        print("-"*40)
        
        security_findings = self.scan_for_secrets(files)
        auth_findings = self.check_auth_dependencies(categories["backend"])
        
        for finding in security_findings + auth_findings:
            print(f"\n[!!] {finding.severity.value.upper()}: {finding.description}")
            if finding.suggestion:
                print(f"    Suggestion: {finding.suggestion}")
            
            response = input("Mark as issue? (y/n/skip): ").lower().strip()
            if response == 'y':
                findings.append(finding)
            elif response == 'skip':
                break
        
        # Code quality review
        print("\n" + "-"*40)
        print("CODE QUALITY REVIEW")
        print("-"*40)
        
        type_findings = self.check_type_annotations(files)
        error_findings = self.check_error_handling(categories["backend"])
        
        for finding in type_findings + error_findings:
            print(f"\n[!] {finding.severity.value.upper()}: {finding.description}")
            if finding.suggestion:
                print(f"    Suggestion: {finding.suggestion}")
            
            response = input("Mark as issue? (y/n/skip): ").lower().strip()
            if response == 'y':
                findings.append(finding)
            elif response == 'skip':
                break
        
        return findings

    def generate_report(self, commit_range: str, files: List[str], 
                       findings: List[ReviewFinding], reviewer: str) -> ReviewReport:
        """Generate final review report."""
        
        # Calculate metrics
        security_score = self.generate_security_report(findings)
        performance_score, perf_issues = self.generate_performance_report(files)
        quality_score, quality_issues = self.generate_quality_report(files)
        
        # Count findings by severity
        critical_issues = [f for f in findings if f.severity == Severity.CRITICAL]
        high_issues = [f for f in findings if f.severity == Severity.HIGH]
        medium_issues = [f for f in findings if f.severity == Severity.MEDIUM]
        low_issues = [f for f in findings if f.severity == Severity.LOW]
        
        # Determine overall status
        if critical_issues:
            overall_status = "FAIL"
        elif high_issues:
            overall_status = "CONDITIONAL_PASS"
        else:
            overall_status = "PASS"
        
        # Calculate lines changed (approximate)
        lines_changed = 0
        for file in files:
            file_path = self.project_root / file
            if file_path.exists():
                try:
                    with open(file_path, 'r') as f:
                        lines_changed += len(f.readlines())
                except:
                    pass
        
        report = ReviewReport(
            date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            reviewer=reviewer,
            commit_range=commit_range,
            files_reviewed=len(files),
            lines_changed=lines_changed,
            findings=findings,
            security_score=security_score,
            performance_score=performance_score,
            quality_score=quality_score,
            overall_status=overall_status
        )
        
        return report

    def save_report(self, report: ReviewReport, output_file: Optional[str] = None):
        """Save review report to file."""
        if not output_file:
            output_file = f"code_review_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        output_path = self.project_root / output_file
        
        # Convert to dict for JSON serialization, handling Enum values
        report_dict = asdict(report)
        
        # Convert Enum values to strings for JSON serialization
        def convert_enum(obj):
            if isinstance(obj, dict):
                return {k: convert_enum(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [convert_enum(item) for item in obj]
            elif isinstance(obj, Severity):
                return obj.value
            else:
                return obj
        
        report_dict = convert_enum(report_dict)
        
        with open(output_path, 'w') as f:
            json.dump(report_dict, f, indent=2)
        
        print(f"\nReport saved to: {output_path}")

    def print_summary(self, report: ReviewReport):
        """Print review summary."""
        print("\n" + "="*80)
        print("CODE REVIEW SUMMARY")
        print("="*80)
        
        print(f"Date: {report.date}")
        print(f"Reviewer: {report.reviewer}")
        print(f"Commit Range: {report.commit_range}")
        print(f"Files Reviewed: {report.files_reviewed}")
        print(f"Lines Changed: {report.lines_changed}")
        
        print(f"\nSCORES:")
        print(f"   Security: {report.security_score}/100")
        print(f"   Performance: {report.performance_score}/100")
        print(f"   Code Quality: {report.quality_score}/100")
        
        print(f"\nOVERALL STATUS: {report.overall_status}")
        
        # Group findings by severity
        findings_by_severity = {}
        for finding in report.findings:
            if finding.severity not in findings_by_severity:
                findings_by_severity[finding.severity] = []
            findings_by_severity[finding.severity].append(finding)
        
        for severity in [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW]:
            if severity in findings_by_severity:
                print(f"\n{severity.value.upper()} ISSUES ({len(findings_by_severity[severity])}):")
                for finding in findings_by_severity[severity]:
                    print(f"   - {finding.category}: {finding.description}")
                    if finding.suggestion:
                        print(f"     Suggestion: {finding.suggestion}")


def main():
    parser = argparse.ArgumentParser(description="Code Review Helper for Spinr Platform")
    parser.add_argument("commit_range", nargs="?", default="HEAD~1", 
                       help="Git commit range to review (default: HEAD~1)")
    parser.add_argument("--interactive", action="store_true",
                       help="Run interactive review mode")
    parser.add_argument("--automated", action="store_true",
                       help="Run automated review mode")
    parser.add_argument("--output", "-o", help="Output file for report")
    parser.add_argument("--reviewer", "-r", help="Reviewer name")
    
    args = parser.parse_args()
    
    # Check if we're in a git repository
    if not Path(".git").exists():
        print("Error: Not in a git repository")
        sys.exit(1)
    
    # Initialize helper
    helper = CodeReviewHelper(Path("."))
    
    # Get changed files
    print(f"Analyzing changes in commit range: {args.commit_range}")
    files = helper.get_changed_files(args.commit_range)
    
    if not files:
        print("No files changed in the specified commit range.")
        sys.exit(0)
    
    print(f"Found {len(files)} changed files")
    for f in files:
        print(f"  - {f}")
    
    # Run review
    if args.interactive:
        findings = helper.interactive_review(files)
    else:
        # Run automated checks (default behavior)
        findings = []
        findings.extend(helper.scan_for_secrets(files))
        findings.extend(helper.check_auth_dependencies(helper.categorize_files(files)["backend"]))
        findings.extend(helper.check_type_annotations(files))
        findings.extend(helper.check_error_handling(helper.categorize_files(files)["backend"]))
    
    # Generate report
    reviewer = args.reviewer or os.environ.get("GIT_AUTHOR_NAME", "Unknown")
    report = helper.generate_report(args.commit_range, files, findings, reviewer)
    
    # Print summary
    helper.print_summary(report)
    
    # Save report
    helper.save_report(report, args.output)
    
    # Exit with appropriate code
    if report.overall_status == "FAIL":
        sys.exit(1)
    elif report.overall_status == "CONDITIONAL_PASS":
        sys.exit(2)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()