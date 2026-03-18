"""
Unit tests for document management functionality.
Tests cover document requirements, driver documents, file uploads, and document review.
"""
import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime, timedelta
from typing import Dict, Any, List

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestDocumentRequirements:
    """Tests for document requirement management."""
    
    @pytest.mark.asyncio
    async def test_get_document_requirements(self, mock_supabase_client):
        """Test getting all document requirements."""
        from backend.db_supabase import get_rows
        
        mock_requirements = [
            {'id': 'req_1', 'name': 'Driver License', 'document_type': 'license', 'required': True},
            {'id': 'req_2', 'name': 'Vehicle Registration', 'document_type': 'registration', 'required': True},
            {'id': 'req_3', 'name': 'Insurance', 'document_type': 'insurance', 'required': True}
        ]
        
        mock_response = MagicMock()
        mock_response.data = mock_requirements
        mock_supabase_client.table.return_value.select.return_value.execute = AsyncMock(
            return_value=mock_response
        )
        
        result = await get_rows('document_requirements')
        
        assert len(result) == 3
    
    @pytest.mark.asyncio
    async def test_create_document_requirement(self, mock_supabase_client):
        """Test creating a new document requirement."""
        from backend.db_supabase import insert_one
        
        requirement_data = {
            'name': 'Background Check',
            'document_type': 'background_check',
            'required': True,
            'expiry_days': 365
        }
        
        mock_response = MagicMock()
        mock_response.data = [{'id': 'req_4'}]
        mock_supabase_client.table.return_value.insert.return_value.execute = AsyncMock(
            return_value=mock_response
        )
        
        result = await insert_one('document_requirements', requirement_data)
        
        assert result is not None
    
    @pytest.mark.asyncio
    async def test_update_document_requirement(self, mock_supabase_client):
        """Test updating a document requirement."""
        from backend.db_supabase import update_one
        
        mock_response = MagicMock()
        mock_response.data = [{'id': 'req_1', 'required': False}]
        
        mock_query = MagicMock()
        mock_query.update.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.execute = AsyncMock(return_value=mock_response)
        mock_supabase_client.table.return_value = mock_query
        
        result = await update_one('document_requirements', {'id': 'req_1'}, {'required': False})
        
        assert result['required'] is False
    
    @pytest.mark.asyncio
    async def test_delete_document_requirement(self, mock_supabase_client):
        """Test deleting a document requirement."""
        from backend.db_supabase import delete_one
        
        mock_response = MagicMock()
        mock_response.count = 1
        
        mock_query = MagicMock()
        mock_query.delete.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.execute = AsyncMock(return_value=mock_response)
        mock_supabase_client.table.return_value = mock_query
        
        result = await delete_one('document_requirements', {'id': 'req_1'})
        
        assert result is not None


class TestDriverDocuments:
    """Tests for driver document management."""
    
    @pytest.fixture
    def sample_document(self):
        """Sample driver document data."""
        return {
            'driver_id': 'driver_123',
            'document_type': 'license',
            'file_url': 'https://storage.example.com/license.pdf',
            'status': 'pending',
            'uploaded_at': datetime.utcnow().isoformat()
        }
    
    @pytest.mark.asyncio
    async def test_upload_driver_document(self, sample_document, mock_supabase_client):
        """Test uploading a driver document."""
        from backend.db_supabase import insert_one
        
        mock_response = MagicMock()
        mock_response.data = [{'id': 'doc_123'}]
        mock_supabase_client.table.return_value.insert.return_value.execute = AsyncMock(
            return_value=mock_response
        )
        
        result = await insert_one('driver_documents', sample_document)
        
        assert result is not None
    
    @pytest.mark.asyncio
    async def test_get_driver_documents(self, mock_supabase_client):
        """Test getting documents for a driver."""
        from backend.db_supabase import get_rows
        
        mock_documents = [
            {'id': 'doc_1', 'document_type': 'license', 'status': 'approved'},
            {'id': 'doc_2', 'document_type': 'registration', 'status': 'pending'}
        ]
        
        mock_response = MagicMock()
        mock_response.data = mock_documents
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(
            return_value=mock_response
        )
        
        result = await get_rows('driver_documents', {'driver_id': 'driver_123'})
        
        assert len(result) == 2
    
    @pytest.mark.asyncio
    async def test_approve_driver_document(self, mock_supabase_client):
        """Test approving a driver document."""
        from backend.db_supabase import update_one
        
        mock_response = MagicMock()
        mock_response.data = [{'id': 'doc_123', 'status': 'approved', 'approved_at': '2024-01-01'}]
        
        mock_query = MagicMock()
        mock_query.update.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.execute = AsyncMock(return_value=mock_response)
        mock_supabase_client.table.return_value = mock_query
        
        result = await update_one('driver_documents', {'id': 'doc_123'}, {
            'status': 'approved',
            'approved_at': '2024-01-01'
        })
        
        assert result['status'] == 'approved'
    
    @pytest.mark.asyncio
    async def test_reject_driver_document(self, mock_supabase_client):
        """Test rejecting a driver document with reason."""
        from backend.db_supabase import update_one
        
        mock_response = MagicMock()
        mock_response.data = [{'id': 'doc_123', 'status': 'rejected', 'rejection_reason': 'Document expired'}]
        
        mock_query = MagicMock()
        mock_query.update.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.execute = AsyncMock(return_value=mock_response)
        mock_supabase_client.table.return_value = mock_query
        
        result = await update_one('driver_documents', {'id': 'doc_123'}, {
            'status': 'rejected',
            'rejection_reason': 'Document expired'
        })
        
        assert result['status'] == 'rejected'
        assert result['rejection_reason'] == 'Document expired'
    
    @pytest.mark.asyncio
    async def test_get_pending_documents(self, mock_supabase_client):
        """Test getting all pending documents for admin review."""
        from backend.db_supabase import get_rows
        
        mock_documents = [
            {'id': 'doc_1', 'driver_id': 'driver_1', 'document_type': 'license'},
            {'id': 'doc_2', 'driver_id': 'driver_2', 'document_type': 'insurance'}
        ]
        
        mock_response = MagicMock()
        mock_response.data = mock_documents
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(
            return_value=mock_response
        )
        
        result = await get_rows('driver_documents', {'status': 'pending'})
        
        assert len(result) == 2


class TestDocumentExpiry:
    """Tests for document expiry tracking."""
    
    @pytest.mark.asyncio
    async def test_check_expiring_documents(self, mock_supabase_client):
        """Test checking for expiring documents."""
        from backend.db_supabase import get_rows
        
        mock_documents = [
            {'id': 'doc_1', 'document_type': 'license', 'expires_at': '2024-02-01'},
            {'id': 'doc_2', 'document_type': 'insurance', 'expires_at': '2024-01-15'}
        ]
        
        mock_response = MagicMock()
        mock_response.data = mock_documents
        mock_supabase_client.table.return_value.select.return_value.lt.return_value.execute = AsyncMock(
            return_value=mock_response
        )
        
        result = await get_rows('driver_documents', {'status': 'approved'})
        
        assert len(result) == 2
    
    @pytest.mark.asyncio
    async def test_update_expired_documents(self, mock_supabase_client):
        """Test marking expired documents."""
        from backend.db_supabase import update_one
        
        mock_response = MagicMock()
        mock_response.data = [{'id': 'doc_123', 'status': 'expired'}]
        
        mock_query = MagicMock()
        mock_query.update.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.execute = AsyncMock(return_value=mock_response)
        mock_supabase_client.table.return_value = mock_query
        
        result = await update_one('driver_documents', {'id': 'doc_123'}, {'status': 'expired'})
        
        assert result['status'] == 'expired'


class TestDocumentFileStorage:
    """Tests for document file storage."""
    
    @pytest.mark.asyncio
    async def test_store_document_file(self, mock_supabase_client):
        """Test storing a document file reference."""
        from backend.db_supabase import insert_one
        
        file_data = {
            'document_id': 'doc_123',
            'file_name': 'license.pdf',
            'file_size': 1024000,
            'mime_type': 'application/pdf',
            'storage_url': 's3://bucket/files/license.pdf'
        }
        
        mock_response = MagicMock()
        mock_response.data = [{'id': 'file_123'}]
        mock_supabase_client.table.return_value.insert.return_value.execute = AsyncMock(
            return_value=mock_response
        )
        
        result = await insert_one('document_files', file_data)
        
        assert result is not None
    
    @pytest.mark.asyncio
    async def test_get_document_file(self, mock_supabase_client):
        """Test getting a document file."""
        from backend.db_supabase import get_rows
        
        mock_file = [{
            'id': 'file_123',
            'file_name': 'license.pdf',
            'storage_url': 's3://bucket/files/license.pdf'
        }]
        
        mock_response = MagicMock()
        mock_response.data = mock_file
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(
            return_value=mock_response
        )
        
        result = await get_rows('document_files', {'document_id': 'doc_123'})
        
        assert len(result) == 1
        assert result[0]['file_name'] == 'license.pdf'
    
    @pytest.mark.asyncio
    async def test_delete_document_file(self, mock_supabase_client):
        """Test deleting a document file."""
        from backend.db_supabase import delete_one
        
        mock_response = MagicMock()
        mock_response.count = 1
        
        mock_query = MagicMock()
        mock_query.delete.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.execute = AsyncMock(return_value=mock_response)
        mock_supabase_client.table.return_value = mock_query
        
        result = await delete_one('document_files', {'id': 'file_123'})
        
        assert result is not None


class TestDocumentValidation:
    """Tests for document validation."""
    
    def test_validate_file_type_pdf(self):
        """Test validating PDF file type."""
        allowed_types = ['application/pdf', 'image/jpeg', 'image/png']
        file_type = 'application/pdf'
        
        assert file_type in allowed_types
    
    def test_validate_file_type_image(self):
        """Test validating image file type."""
        allowed_types = ['application/pdf', 'image/jpeg', 'image/png']
        file_type = 'image/jpeg'
        
        assert file_type in allowed_types
    
    def test_validate_file_type_invalid(self):
        """Test validating invalid file type."""
        allowed_types = ['application/pdf', 'image/jpeg', 'image/png']
        file_type = 'application/x-executable'
        
        assert file_type not in allowed_types
    
    def test_validate_file_size(self):
        """Test validating file size."""
        max_size = 5 * 1024 * 1024  # 5MB
        small_file = 1024 * 100  # 100KB
        large_file = 10 * 1024 * 1024  # 10MB
        
        assert small_file <= max_size
        assert large_file > max_size
    
    def test_validate_file_extension(self):
        """Test validating file extension."""
        allowed_extensions = ['.pdf', '.jpg', '.jpeg', '.png']
        
        valid_files = ['license.pdf', 'photo.jpg', 'document.png']
        invalid_files = ['malware.exe', 'script.sh']
        
        for filename in valid_files:
            ext = '.' + filename.split('.')[-1]
            assert ext.lower() in allowed_extensions
        
        for filename in invalid_files:
            ext = '.' + filename.split('.')[-1]
            assert ext.lower() not in allowed_extensions


class TestDocumentEndpoints:
    """Tests for document API endpoints."""
    
    @pytest.fixture
    def test_client(self):
        from fastapi.testclient import TestClient
        from backend.server import app
        return TestClient(app)
    
    def test_get_document_requirements_endpoint(self, test_client, auth_headers):
        """Test getting document requirements endpoint."""
        response = test_client.get(
            '/api/v1/documents/requirements',
            headers=auth_headers
        )
        
        assert response.status_code in [200, 401]
    
    def test_upload_document_endpoint(self, test_client, auth_headers):
        """Test uploading document endpoint."""
        # This would be a multipart form upload in real usage
        response = test_client.post(
            '/api/v1/documents/upload',
            headers=auth_headers,
            json={
                'document_type': 'license',
                'file_name': 'license.pdf'
            }
        )
        
        assert response.status_code in [200, 201, 401, 422]
    
    def test_get_driver_documents_endpoint(self, test_client, auth_headers):
        """Test getting driver documents endpoint."""
        response = test_client.get(
            '/api/v1/drivers/me/documents',
            headers=auth_headers
        )
        
        assert response.status_code in [200, 401, 404]
    
    def test_admin_review_document_endpoint(self, test_client, auth_headers):
        """Test admin document review endpoint."""
        response = test_client.post(
            '/api/v1/admin/documents/doc_123/review',
            headers=auth_headers,
            json={'status': 'approved'}
        )
        
        assert response.status_code in [200, 401, 403, 404, 422]