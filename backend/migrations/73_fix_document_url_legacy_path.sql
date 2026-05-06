-- Rollback: UPDATE driver_documents
--           SET document_url = REPLACE(document_url, '/api/v1/documents/', '/api/documents/')
--           WHERE document_url LIKE '/api/v1/documents/%';

-- Rewrite legacy /api/documents/{id} paths to canonical /api/v1/documents/{id}.
-- Safe to run against live traffic: UPDATE with WHERE clause is row-level;
-- the old path continues to resolve via the backend legacy mount until this
-- migration is applied and the mount is removed in a follow-up deployment.
UPDATE driver_documents
SET document_url = REPLACE(document_url, '/api/documents/', '/api/v1/documents/')
WHERE document_url LIKE '/api/documents/%';
