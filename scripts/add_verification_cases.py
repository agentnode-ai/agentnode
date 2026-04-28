"""Add verification.cases, tier, and llm.required to all agent manifests."""
import os
import yaml

BASE = os.path.join(os.path.dirname(__file__), "..", "starter-packs")

# Agent definitions: slug -> {tier, return_keys, cases}
# llm_only agents use call_llm_text, llm_plus_tools use run_tool
AGENTS = {
    "academic-research-agent": {
        "tier": "llm_plus_tools",
        "cases": [
            {"name": "research_ml", "goal": "Research recent advances in machine learning for drug discovery",
             "expected": {"required_keys": ["review", "done"], "done": True, "min_lengths": {"review": 30}}},
            {"name": "research_physics", "goal": "Review academic literature on gravitational wave detection methods",
             "expected": {"required_keys": ["review", "done"], "done": True, "min_lengths": {"review": 30}}},
        ],
    },
    "api-design-agent": {
        "tier": "llm_plus_tools",
        "cases": [
            {"name": "rest_api", "goal": "Design a REST API for a task management application",
             "expected": {"required_keys": ["requirements", "done"], "done": True}},
            {"name": "ecommerce_api", "goal": "Design an API for an e-commerce product catalog",
             "expected": {"required_keys": ["requirements", "done"], "done": True}},
        ],
    },
    "ci-cd-agent": {
        "tier": "llm_plus_tools",
        "cases": [
            {"name": "python_pipeline", "goal": "Create a CI/CD pipeline for a Python web application",
             "expected": {"required_keys": ["pipeline_steps", "done"], "done": True}},
            {"name": "node_pipeline", "goal": "Set up continuous integration for a Node.js microservice",
             "expected": {"required_keys": ["pipeline_steps", "done"], "done": True}},
        ],
    },
    "cloud-cost-agent": {
        "tier": "llm_plus_tools",
        "cases": [
            {"name": "aws_costs", "goal": "Analyze AWS cloud spending and identify cost optimization opportunities",
             "expected": {"required_keys": ["cost_analysis", "done"], "done": True}},
            {"name": "azure_costs", "goal": "Review Azure resource costs and suggest budget reductions",
             "expected": {"required_keys": ["cost_analysis", "done"], "done": True}},
        ],
    },
    "code-review-agent": {
        "tier": "llm_plus_tools",
        "cases": [
            {"name": "python_review", "goal": "Review a Python REST API codebase for quality and security issues",
             "expected": {"required_keys": ["review", "done"], "done": True, "min_lengths": {"review": 30}}},
            {"name": "js_review", "goal": "Review a JavaScript frontend application for code quality",
             "expected": {"required_keys": ["review", "done"], "done": True, "min_lengths": {"review": 30}}},
        ],
    },
    "competitive-intel-agent": {
        "tier": "llm_plus_tools",
        "cases": [
            {"name": "tech_company", "goal": "Analyze competitive landscape for a cloud computing startup",
             "expected": {"required_keys": ["analysis", "done"], "done": True, "min_lengths": {"analysis": 30}}},
            {"name": "saas_market", "goal": "Research competitors in the project management SaaS market",
             "expected": {"required_keys": ["analysis", "done"], "done": True, "min_lengths": {"analysis": 30}}},
        ],
    },
    "contract-review-agent": {
        "tier": "llm_plus_tools",
        "cases": [
            {"name": "nda_review", "goal": "Review a non-disclosure agreement for potential risks",
             "expected": {"required_keys": ["summary", "done"], "done": True}},
            {"name": "saas_contract", "goal": "Analyze a SaaS subscription agreement for unfavorable terms",
             "expected": {"required_keys": ["summary", "done"], "done": True}},
        ],
    },
    "crm-enrichment-agent": {
        "tier": "llm_plus_tools",
        "cases": [
            {"name": "enrich_contact", "goal": "Enrich CRM data for a software engineering lead at a tech company",
             "expected": {"required_keys": ["contact", "done"], "done": True}},
            {"name": "enrich_company", "goal": "Research and enrich company profile for a fintech startup",
             "expected": {"required_keys": ["contact", "done"], "done": True}},
        ],
    },
    "csv-analyst-agent": {
        "tier": "llm_plus_tools",
        "cases": [
            {"name": "sales_data", "goal": "Analyze a CSV file of quarterly sales data and identify trends",
             "expected": {"required_keys": ["analysis", "done"], "done": True}},
            {"name": "survey_data", "goal": "Analyze survey response data from a CSV file",
             "expected": {"required_keys": ["analysis", "done"], "done": True}},
        ],
    },
    "data-pipeline-agent": {
        "tier": "llm_plus_tools",
        "cases": [
            {"name": "etl_pipeline", "goal": "Build a data pipeline to process customer transaction logs",
             "expected": {"required_keys": ["source_file", "done"], "done": True}},
            {"name": "log_pipeline", "goal": "Create a pipeline to transform and filter server access logs",
             "expected": {"required_keys": ["source_file", "done"], "done": True}},
        ],
    },
    "dependency-audit-agent": {
        "tier": "llm_plus_tools",
        "cases": [
            {"name": "python_deps", "goal": "Audit Python package dependencies for known vulnerabilities",
             "expected": {"required_keys": ["packages_scanned", "done"], "done": True}},
            {"name": "node_deps", "goal": "Scan Node.js project dependencies for security issues",
             "expected": {"required_keys": ["packages_scanned", "done"], "done": True}},
        ],
    },
    "deployment-agent": {
        "tier": "llm_plus_tools",
        "cases": [
            {"name": "deploy_check", "goal": "Run pre-deployment checks for a Python web application",
             "expected": {"required_keys": ["ready_to_deploy", "checklist", "done"], "done": True}},
            {"name": "staging_check", "goal": "Verify staging environment readiness for a microservice deployment",
             "expected": {"required_keys": ["ready_to_deploy", "checklist", "done"], "done": True}},
        ],
    },
    "email-triage-agent": {
        "tier": "llm_plus_tools",
        "cases": [
            {"name": "support_email", "goal": "Triage and prioritize incoming customer support emails",
             "expected": {"required_keys": ["summary", "priority", "done"], "done": True}},
            {"name": "sales_email", "goal": "Classify and draft responses for sales inquiry emails",
             "expected": {"required_keys": ["summary", "priority", "done"], "done": True}},
        ],
    },
    "fact-check-agent": {
        "tier": "llm_plus_tools",
        "cases": [
            {"name": "tech_claim", "goal": "Fact-check the claim that quantum computers can break all encryption",
             "expected": {"required_keys": ["claim", "verdict", "done"], "done": True}},
            {"name": "health_claim", "goal": "Verify the claim that intermittent fasting extends lifespan by 30 percent",
             "expected": {"required_keys": ["claim", "verdict", "done"], "done": True}},
        ],
    },
    "log-investigator-agent": {
        "tier": "llm_plus_tools",
        "cases": [
            {"name": "server_logs", "goal": "Investigate server error logs to identify the root cause of 500 errors",
             "expected": {"required_keys": ["findings", "done"], "done": True}},
            {"name": "app_logs", "goal": "Analyze application logs for performance bottlenecks",
             "expected": {"required_keys": ["findings", "done"], "done": True}},
        ],
    },
    "meeting-prep-agent": {
        "tier": "llm_plus_tools",
        "cases": [
            {"name": "board_meeting", "goal": "Prepare materials for a quarterly board meeting on company strategy",
             "expected": {"required_keys": ["agenda", "done"], "done": True}},
            {"name": "client_call", "goal": "Prepare briefing notes for a client discovery call about cloud migration",
             "expected": {"required_keys": ["agenda", "done"], "done": True}},
        ],
    },
    "news-digest-agent": {
        "tier": "llm_plus_tools",
        "cases": [
            {"name": "tech_news", "goal": "Create a digest of the latest artificial intelligence news",
             "expected": {"required_keys": ["digest", "done"], "done": True}},
            {"name": "finance_news", "goal": "Compile a news digest about cryptocurrency market developments",
             "expected": {"required_keys": ["digest", "done"], "done": True}},
        ],
    },
    "newsletter-agent": {
        "tier": "llm_only",
        "cases": [
            {"name": "tech_newsletter", "goal": "Write a weekly newsletter about DevOps best practices",
             "expected": {"required_keys": ["newsletter", "topic", "done"], "done": True, "min_lengths": {"newsletter": 200}}},
            {"name": "marketing_newsletter", "goal": "Create a monthly newsletter about digital marketing trends",
             "expected": {"required_keys": ["newsletter", "topic", "done"], "done": True, "min_lengths": {"newsletter": 200}}},
        ],
    },
    "project-planner-agent": {
        "tier": "llm_only",
        "cases": [
            {"name": "mobile_app", "goal": "Create a project plan for building a mobile fitness tracking app",
             "expected": {"required_keys": ["plan", "project", "done"], "done": True, "min_lengths": {"plan": 200}}},
            {"name": "website_redesign", "goal": "Plan a website redesign project for an e-commerce platform",
             "expected": {"required_keys": ["plan", "project", "done"], "done": True, "min_lengths": {"plan": 200}}},
        ],
    },
    "report-generator-agent": {
        "tier": "llm_only",
        "cases": [
            {"name": "quarterly_report", "goal": "Generate a quarterly business performance report",
             "expected": {"required_keys": ["report", "report_type", "done"], "done": True, "min_lengths": {"report": 200}}},
            {"name": "incident_report", "goal": "Write an incident report for a production database outage",
             "expected": {"required_keys": ["report", "report_type", "done"], "done": True, "min_lengths": {"report": 200}}},
        ],
    },
    "security-scanner-agent": {
        "tier": "llm_plus_tools",
        "cases": [
            {"name": "python_scan", "goal": "Scan a Python web application for security vulnerabilities",
             "expected": {"required_keys": ["scan_results", "done"], "done": True}},
            {"name": "api_scan", "goal": "Perform a security audit on a REST API codebase",
             "expected": {"required_keys": ["scan_results", "done"], "done": True}},
        ],
    },
    "social-media-agent": {
        "tier": "llm_only",
        "cases": [
            {"name": "product_launch", "goal": "Create social media posts for a new product launch campaign",
             "expected": {"required_keys": ["posts", "done"], "done": True, "min_lengths": {"posts": 50}}},
            {"name": "brand_awareness", "goal": "Write social media content to increase brand awareness for a tech startup",
             "expected": {"required_keys": ["posts", "done"], "done": True, "min_lengths": {"posts": 50}}},
        ],
    },
    "spreadsheet-auditor-agent": {
        "tier": "llm_plus_tools",
        "cases": [
            {"name": "financial_audit", "goal": "Audit a financial spreadsheet for data quality issues",
             "expected": {"required_keys": ["audit_summary", "done"], "done": True}},
            {"name": "hr_data_audit", "goal": "Check an HR data spreadsheet for inconsistencies and missing values",
             "expected": {"required_keys": ["audit_summary", "done"], "done": True}},
        ],
    },
    "sql-report-agent": {
        "tier": "llm_plus_tools",
        "cases": [
            {"name": "revenue_query", "goal": "Generate a SQL query to analyze monthly revenue by product category",
             "expected": {"required_keys": ["question", "sql", "done"], "done": True}},
            {"name": "user_query", "goal": "Write a SQL report query for user retention analysis",
             "expected": {"required_keys": ["question", "sql", "done"], "done": True}},
        ],
    },
    "technical-docs-agent": {
        "tier": "llm_plus_tools",
        "cases": [
            {"name": "api_docs", "goal": "Generate technical documentation for a REST API service",
             "expected": {"required_keys": ["documentation", "done"], "done": True}},
            {"name": "library_docs", "goal": "Write developer documentation for a Python utility library",
             "expected": {"required_keys": ["documentation", "done"], "done": True}},
        ],
    },
    "test-writer-agent": {
        "tier": "llm_plus_tools",
        "cases": [
            {"name": "python_tests", "goal": "Generate unit tests for a Python authentication module",
             "expected": {"required_keys": ["tests", "done"], "done": True}},
            {"name": "api_tests", "goal": "Write integration tests for a REST API endpoint",
             "expected": {"required_keys": ["tests", "done"], "done": True}},
        ],
    },
    "seo-research-agent": {
        "tier": "llm_plus_tools",
        "cases": [
            {"name": "website_seo", "goal": "Analyze SEO performance for a SaaS product landing page",
             "expected": {"required_keys": ["seo_analysis", "done"], "done": True}},
            {"name": "content_seo", "goal": "Research SEO opportunities for a technology blog",
             "expected": {"required_keys": ["seo_analysis", "done"], "done": True}},
        ],
    },
    "website-monitor-agent": {
        "tier": "llm_plus_tools",
        "cases": [
            {"name": "monitor_site", "goal": "Monitor a website for uptime and content changes",
             "expected": {"required_keys": ["url", "status", "done"], "done": True}},
            {"name": "check_status", "goal": "Check website availability and detect any downtime",
             "expected": {"required_keys": ["url", "status", "done"], "done": True}},
        ],
    },
}


def update_agent_yaml(slug, config):
    yaml_path = os.path.join(BASE, slug, "agentnode.yaml")
    if not os.path.isfile(yaml_path):
        print(f"SKIP {slug}: no agentnode.yaml")
        return False

    with open(yaml_path) as f:
        content = f.read()

    modified = False

    # Add tier if not present
    if "\n  tier:" not in content:
        tier = config["tier"]
        # Insert after goal line in agent section
        goal_marker = "\n  goal:"
        if goal_marker in content:
            idx = content.index(goal_marker)
            end_of_line = content.index("\n", idx + 1)
            content = content[:end_of_line + 1] + f'  tier: "{tier}"\n' + content[end_of_line + 1:]
            modified = True

    # Add llm.required if not present
    if "required: true" not in content:
        # Find system_prompt line and add llm section before it
        sp_marker = "\n  system_prompt:"
        if sp_marker in content:
            idx = content.index(sp_marker)
            content = content[:idx] + "\n  llm:\n    required: true" + content[idx:]
            modified = True

    # Add verification.cases if not present
    if "verification:" not in content:
        # Insert before isolation or state or termination
        for marker in ("\n  isolation:", "\n  state:", "\n  termination:"):
            if marker in content:
                idx = content.index(marker)
                cases_yaml = "\n  verification:\n    cases:\n"
                for case in config["cases"]:
                    cases_yaml += f'      - name: {case["name"]}\n'
                    cases_yaml += f'        goal: "{case["goal"]}"\n'
                    cases_yaml += f'        expected:\n'
                    exp = case["expected"]
                    keys = exp.get("required_keys", [])
                    cases_yaml += f'          required_keys: {keys}\n'
                    if exp.get("done"):
                        cases_yaml += f'          done: true\n'
                    if exp.get("min_lengths"):
                        cases_yaml += f'          min_lengths:\n'
                        for k, v in exp["min_lengths"].items():
                            cases_yaml += f'            {k}: {v}\n'
                content = content[:idx] + cases_yaml + content[idx:]
                modified = True
                break

    if modified:
        with open(yaml_path, "w") as f:
            f.write(content)
        print(f"OK {slug}")
        return True
    else:
        print(f"SKIP {slug}: already has verification cases")
        return False


def bump_version(slug):
    yaml_path = os.path.join(BASE, slug, "agentnode.yaml")
    with open(yaml_path) as f:
        content = f.read()

    # Find version line and bump patch
    import re
    m = re.search(r'version:\s*"(\d+)\.(\d+)\.(\d+)"', content)
    if m:
        major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
        new_version = f"{major}.{minor}.{patch + 1}"
        content = content[:m.start()] + f'version: "{new_version}"' + content[m.end():]
        with open(yaml_path, "w") as f:
            f.write(content)
        return new_version
    return None


if __name__ == "__main__":
    updated = 0
    for slug, config in AGENTS.items():
        if update_agent_yaml(slug, config):
            new_v = bump_version(slug)
            if new_v:
                print(f"  -> bumped to v{new_v}")
            updated += 1
    print(f"\nUpdated {updated}/{len(AGENTS)} agents")
