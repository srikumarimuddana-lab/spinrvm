---
description: Workflow for keeping all documentation up-to-date with code changes
---

# Documentation Update Workflow

Reference `.agents/roles/documentation-lead.md` for standards.

## When to Run This Workflow
Run after ANY of these changes:
- New or modified API endpoint
- Database schema change
- New component or screen
- Architecture or infra change
- Dependency addition or removal
- Configuration change

## Step 1: Identify What Changed
// turbo
```bash
git diff --name-only HEAD~1
```

Categorize changes:
- `backend/routes/` → Update API reference
- `backend/supabase_schema.sql` or `backend/migrations/` → Update database schema
- `railway.json` or `Dockerfile` or `eas.json` → Update deployment guide
- New directories or major restructuring → Update architecture

## Step 2: Update API Reference
If any route files changed:
1. Read the changed route file
2. Extract: endpoint URL, method, params, auth requirement, response format
3. Update `.agents/docs/api-reference.md` with accurate information
4. Remove docs for deleted endpoints

## Step 3: Update Database Schema
If schema files changed:
1. Read the migration or schema file
2. Update `.agents/docs/database-schema.md` with:
   - New tables and columns
   - Changed relationships
   - New RLS policies
3. Remove docs for dropped tables/columns

## Step 4: Update Architecture
If the structure changed significantly:
1. Update `.agents/docs/architecture.md` with:
   - New components and their relationships
   - Changed communication patterns
   - New third-party integrations
2. Keep the component diagram current

## Step 5: Update Deployment Guide
If deployment config changed:
1. Update `.agents/docs/deployment-guide.md` with:
   - New environment variables
   - Changed deployment steps
   - Updated infrastructure details

## Step 6: Update Code Documentation
- [ ] All new public functions have docstrings (Python) or JSDoc (TypeScript)
- [ ] Updated functions have updated docstrings
- [ ] README files in component directories are accurate

## Step 7: Verify Documentation
- [ ] All file paths in docs point to existing files
- [ ] API endpoint list matches actual routes
- [ ] Database schema matches actual schema
- [ ] Deployment steps work when followed literally
- [ ] No references to removed features
